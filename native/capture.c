/* The CRTV screen's copy, included by tf_native.c. Runs at the end of each frame on the render thread
 * (EndDrawingViewport, hooked): queues a GPU copy of the texture, and frames later, once the GPU has done it,
 * maps it and hands the pixels to the picture thread (picture.c). One copy in flight; buffers reused; never on the
 * game thread, never inside a render pass, never on a list that executes; unknown resource states are refused, not
 * guessed. */
#include <stddef.h>

typedef void (*EndViewportFn)(void *, void *, unsigned char, unsigned char);
typedef void *(*RetainTextureFn)(void **, void *);
typedef void (*ReadbackCtorFn)(void *, uint64_t);
typedef void (*EnqueueCopyFn)(void *, void *, void *, const int *, unsigned, const int *);
typedef void *(*LockReadbackFn)(void *, int *, int *);
typedef void (*UnlockReadbackFn)(void *);
typedef unsigned char (*PollFenceFn)(void *, unsigned);
struct TransitionInfo {
    uint16_t mip, slice, plane, padding;
    void *texture;
    unsigned char type, type_padding[3];
    uint32_t before, after, flags;
    unsigned char commit[16];
};
struct TransitionView { struct TransitionInfo *data; int count; int padding; };
typedef void (*TransitionFn)(void *, const struct TransitionView *, unsigned);
_Static_assert(sizeof(struct TransitionInfo) == 48 && offsetof(struct TransitionInfo, before) == 20, "Transition ABI");
_Static_assert(sizeof(struct TransitionView) == 16, "Transition view ABI");

static EndViewportFn original_end_viewport;
static RetainTextureFn retain_texture;
static ReadbackCtorFn readback_ctor;
static EnqueueCopyFn enqueue_copy;
static LockReadbackFn lock_readback;
static UnlockReadbackFn unlock_readback;
static PollFenceFn poll_fence;
static TransitionFn transition;
static SRWLOCK source_lock = SRWLOCK_INIT;
static void *retained_source, *pending_source;
static int source_width, source_height;
static unsigned source_format;
static _Atomic DWORD game_thread_id;
static _Atomic int capture_state;
static _Atomic uint64_t viewport_calls;
static _Atomic unsigned capture_wait_reason;
static _Atomic unsigned captured_access;
static _Atomic uint64_t queue_cpu_us, map_cpu_us;
static _Atomic unsigned requested_fps, capture_fault;
static _Atomic uint64_t config_tick, published_frames;
static uint64_t frame_sequence;
static ULONGLONG next_capture_at;
static int allocated_width, allocated_height;
static unsigned allocated_format;
static HANDLE frame_mapping;
static unsigned char *shared_frame;
static wchar_t frame_mapping_name[128] = L"Local\\TownfallCompanionPicture";
static HANDLE picture_wake; /* set once frame_pixels holds a frame for the picture thread */
static ULONGLONG queued_at;
static void *readback;
static unsigned char *frame_pixels;
static int frame_width, frame_height;
static unsigned frame_format;

static uint64_t performance_ticks(void) {
    LARGE_INTEGER ticks;
    QueryPerformanceCounter(&ticks);
    return (uint64_t)ticks.QuadPart;
}

static uint64_t elapsed_us(uint64_t began) {
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    return (performance_ticks() - began) * 1000000 / (uint64_t)frequency.QuadPart;
}

static const char *capture_status(void) {
    int phase = atomic_load(&capture_state);
    if (phase >= 0 && atomic_load(&requested_fps)) return "streaming";
    switch (atomic_load_explicit(&capture_state, memory_order_acquire)) {
        case 0: return "disabled";
        case 1: return "armed";
        case 2: return "gpu-copy-pending";
        case 3: return "writing-frame";
        case 4: return "frame-ready";
        case -1: return "gpu-timeout";
        case -2: return "source-state-unverified";
        case -3: return "picture-failed";
        case -4: return "unsupported-format";
        case -5: return "readback-map-failed";
        case -6: return "allocation-failed";
        case -7: return "capture-too-slow";
        case -8: return "shared-memory-failed";
        case -9: return "source-dimensions-changed";
        default: return "invalid";
    }
}

static const char *capture_wait_status(void) {
    switch (atomic_load(&capture_wait_reason)) {
        case 1: return "not-immediate-list";
        case 2: return "game-thread";
        case 3: return "unreadable-list-state";
        case 4: return "inside-pass";
        case 5: return "executing-list";
        case 6: return "multi-gpu-or-unreadable-mask";
        case 7: return "no-source";
        default: return "none";
    }
}

static void publish_capture_source(void *rhi, int width, int height, unsigned format) {
    int phase = atomic_load(&capture_state);
    if (phase != 1 && phase != 2 && !(phase >= 0 && atomic_load(&requested_fps))) rhi = NULL;
    AcquireSRWLockExclusive(&source_lock);
    retain_texture(&retained_source, rhi);
    source_width = width;
    source_height = height;
    source_format = format;
    ReleaseSRWLockExclusive(&source_lock);
}

static void transition_source(void *command_list, unsigned before, unsigned after) {
    struct TransitionInfo info = {0};
    info.mip = info.slice = info.plane = UINT16_MAX;
    info.texture = pending_source;
    info.type = TRANSITION_TEXTURE;
    info.before = before;
    info.after = after;
    struct TransitionView view = {&info, 1, 0};
    transition(command_list, &view, 0);
}

/* The picture thread, one for as long as the game runs: the phone's picture of each frame captured (picture.c),
 * then frame_pixels is the render thread's again. */
static DWORD WINAPI make_pictures(void *unused) {
    (void)unused;
    CoInitializeEx(NULL, COINIT_MULTITHREADED); /* WIC, on this thread only */
    for (;;) {
        WaitForSingleObject(picture_wake, INFINITE);
        int ok = publish_picture(shared_frame, frame_pixels, frame_width, frame_height, frame_format == PIXEL_BGRA8,
                                 frame_sequence);
        if (ok) atomic_store(&published_frames, frame_sequence);
        atomic_store_explicit(&capture_state, ok ? 4 : -3, memory_order_release);
    }
    return 0;
}

static int start_pictures(void) {
    if (picture_wake) return 1;
    picture_wake = CreateEventW(NULL, FALSE, FALSE, NULL);
    HANDLE thread = picture_wake ? CreateThread(NULL, 0, make_pictures, NULL, 0, NULL) : NULL;
    if (!thread) {
        if (picture_wake) CloseHandle(picture_wake);
        picture_wake = NULL;
        return 0;
    }
    CloseHandle(thread);
    return 1;
}

static void capture_at_frame_end(void *command_list) {
    int phase = atomic_load_explicit(&capture_state, memory_order_acquire);
    unsigned fps = atomic_load(&requested_fps);
    ULONGLONG now = GetTickCount64();
    if (now - atomic_load(&config_tick) > 3000) fps = 0;
    if (phase >= 0) {
        if (phase == 4 && atomic_load(&capture_fault)) { atomic_store(&capture_state, -7); return; }
        if ((phase == 0 || phase == 4) && fps && now >= next_capture_at) {
            phase = 1;
            atomic_store(&capture_state, phase);
        } else if ((phase == 1 || phase == 4) && !fps) {
            atomic_store(&capture_state, 0); return;
        }
        if (phase == 1 && now < next_capture_at) return;
    }
    if (phase != 1 && phase != 2) return;
    /* Never append commands to a parallel list, an executing list, or an active pass. */
    unsigned char inside = 0;
    unsigned char executing = 1;
    unsigned mask = 0;
    unsigned reason = 0;
    if (command_list != image_base + IMMEDIATE_LIST_RVA) reason = 1;
    else if (GetCurrentThreadId() == game_thread_id) reason = 2;
    else if (!read_memory((uintptr_t)command_list + INSIDE_PASS_OFFSET, &inside, 1) ||
             !read_memory((uintptr_t)command_list + EXECUTING_OFFSET, &executing, 1)) reason = 3;
    else if (inside & INSIDE_PASS_MASK) reason = 4;
    else if (executing) reason = 5;
    else if (!read_memory((uintptr_t)command_list + GPU_MASK_OFFSET, &mask, sizeof(mask)) || mask != 1) reason = 6;
    atomic_store(&capture_wait_reason, reason);
    if (reason) return;
    if (phase == 1) {
        uint64_t began = performance_ticks();
        AcquireSRWLockShared(&source_lock);
        if (retained_source) {
            retain_texture(&pending_source, retained_source);
            frame_width = source_width;
            frame_height = source_height;
            frame_format = source_format;
        }
        ReleaseSRWLockShared(&source_lock);
        if (!pending_source) { atomic_store(&capture_wait_reason, 7); return; }
        if (frame_width <= 0 || frame_width > 2048 || frame_height <= 0 || frame_height > 2048 ||
            (frame_format != PIXEL_RGBA8 && frame_format != PIXEL_BGRA8)) {
            retain_texture(&pending_source, NULL);
            atomic_store(&capture_state, -4); return;
        }
        unsigned access = 0;
        read_memory((uintptr_t)pending_source + TRACKED_ACCESS_OFFSET, &access, sizeof(access));
        atomic_store(&captured_access, access);
        /* Refuse untracked/write states: do not guess a source barrier or its restoration state. */
        if (!access || (access & ~ACCESS_SRV_MASK)) {
            retain_texture(&pending_source, NULL);
            atomic_store(&capture_state, -2); return;
        }
        if (readback && (allocated_width != frame_width || allocated_height != frame_height || allocated_format != frame_format)) {
            retain_texture(&pending_source, NULL);
            atomic_store(&capture_state, -9); return;
        }
        if (!readback) {
            readback = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, READBACK_SIZE);
            frame_pixels = HeapAlloc(GetProcessHeap(), 0, (SIZE_T)frame_width * frame_height * 4);
            if (!readback || !frame_pixels) {
                if (readback) HeapFree(GetProcessHeap(), 0, readback);
                if (frame_pixels) HeapFree(GetProcessHeap(), 0, frame_pixels);
                readback = NULL; frame_pixels = NULL;
                retain_texture(&pending_source, NULL);
                atomic_store(&capture_state, -6); return;
            }
            readback_ctor(readback, 0); /* FName(NAME_None), verified eight-byte by-value ABI. */
            allocated_width = frame_width; allocated_height = frame_height; allocated_format = frame_format;
        }
        int position[3] = {0, 0, 0}, size[3] = {frame_width, frame_height, 1};
        transition_source(command_list, access, ACCESS_COPY_SRC);
        enqueue_copy(readback, command_list, pending_source, position, 0, size);
        transition_source(command_list, ACCESS_COPY_SRC, access);
        queued_at = GetTickCount64();
        atomic_store(&queue_cpu_us, elapsed_us(began));
        if (atomic_load(&queue_cpu_us) > 50000) atomic_store(&capture_fault, 1);
        ++frame_sequence;
        atomic_store(&capture_state, 2);
        return;
    }
    if (GetTickCount64() - queued_at > 5000) {
        /* Preserve outstanding GPU resources until process exit; never free an in-flight copy. */
        atomic_store(&capture_state, -1); return;
    }
    uintptr_t fence = 0;
    LONG pending = 1;
    unsigned copy_mask = 0;
    if (!read_memory((uintptr_t)readback + READBACK_FENCE_OFFSET, &fence, sizeof(fence)) || !fence ||
        !read_memory(fence + FENCE_PENDING_OFFSET, &pending, sizeof(pending)) || pending != 0 ||
        !read_memory((uintptr_t)readback + READBACK_MASK_OFFSET, &copy_mask, sizeof(copy_mask)) || copy_mask != 1) return;
    MemoryBarrier();
    if (!poll_fence((void *)fence, copy_mask)) return;
    uint64_t began = performance_ticks();
    int pitch = 0, buffer_height = 0;
    unsigned char *mapped = lock_readback(readback, &pitch, &buffer_height);
    if (!mapped || pitch < frame_width || pitch > 32768 || buffer_height < frame_height) {
        if (mapped) unlock_readback(readback);
        atomic_store(&capture_state, -5); return;
    }
    for (int row = 0; row < frame_height; ++row)
        memcpy(frame_pixels + (SIZE_T)row * frame_width * 4, mapped + (SIZE_T)row * pitch * 4, (SIZE_T)frame_width * 4);
    unlock_readback(readback);
    atomic_store(&map_cpu_us, elapsed_us(began));
    if (atomic_load(&map_cpu_us) > 50000) atomic_store(&capture_fault, 1);
    if (fps) {
        uint64_t pace_ms = 1000 / fps;
        uint64_t budget_ms = (atomic_load(&queue_cpu_us) + atomic_load(&map_cpu_us)) / 100;
        if (budget_ms > pace_ms) pace_ms = budget_ms;
        next_capture_at = queued_at + pace_ms;
    }
    retain_texture(&pending_source, NULL);
    atomic_store_explicit(&capture_state, 3, memory_order_release);
    if (start_pictures()) SetEvent(picture_wake);
    else atomic_store(&capture_state, -3);
}

static void observe_end_viewport(void *command_list, void *viewport, unsigned char present, unsigned char vsync) {
    atomic_fetch_add_explicit(&viewport_calls, 1, memory_order_relaxed);
    capture_at_frame_end(command_list);
    original_end_viewport(command_list, viewport, present, vsync);
}

static void initialize_capture(void) {
    retain_texture = (RetainTextureFn)(image_base + RETAIN_TEXTURE_RVA);
    readback_ctor = (ReadbackCtorFn)(image_base + READBACK_CTOR_RVA);
    enqueue_copy = (EnqueueCopyFn)(image_base + ENQUEUE_COPY_RVA);
    lock_readback = (LockReadbackFn)(image_base + LOCK_READBACK_RVA);
    unlock_readback = (UnlockReadbackFn)(image_base + UNLOCK_READBACK_RVA);
    poll_fence = (PollFenceFn)(image_base + POLL_FENCE_RVA);
    transition = (TransitionFn)(image_base + TRANSITION_RVA);
    game_thread_id = GetCurrentThreadId();
}

static void configure_capture(unsigned fps, unsigned width) {
    if (fps > 30) fps = 30;
    if (width >= 160 && width <= PICTURE_MAX_WIDTH) atomic_store(&requested_width, width);
    if (fps && !shared_frame) {
        frame_mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, PICTURE_MAPPING_SIZE,
                                           frame_mapping_name);
        if (frame_mapping) shared_frame = MapViewOfFile(frame_mapping, FILE_MAP_WRITE, 0, 0, PICTURE_MAPPING_SIZE);
        if (!shared_frame) { atomic_store(&capture_state, -8); fps = 0; }
        else InterlockedExchange((volatile LONG *)shared_frame, 0);
    }
    atomic_store(&requested_fps, fps);
    atomic_store(&config_tick, GetTickCount64());
}
