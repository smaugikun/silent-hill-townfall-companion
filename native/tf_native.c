/* tf_native.dll: the native part of Townfall Companion, loaded by Scripts/tf_native.lua.
 *
 * It copies the CRTV's screen texture from the GPU at the end of a frame, while a phone watches, and publishes the
 * screen's picture as JPEG in shared memory for the companion (capture.c, picture.c); it moves the image in the
 * mini-game's alignment stages (alignment.c); it turns the CRTV's direction to where the phone looks (look.c); and it
 * listens to the CRTV's sound in the game's FMOD mixer (audio.c). Every engine address comes from profile.h, generated from this exact game
 * build's PDB: at load the executable's identity and the first bytes of each function used are checked, and on any
 * difference the DLL stays off. FMOD is called through the C API its own DLLs export. UE4SS gives a DLL no Lua C
 * API, so Lua passes values in small files in %TEMP%. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdatomic.h>
#include "MinHook.h"
#include "profile.h"

typedef void *(*GetResourceFn)(void *texture);
static GetResourceFn get_resource;
static unsigned char *image_base;
/* state: 1 running with the profile of this game; 0 waiting for it (the game's sound runs regardless); < 0 why it
 * stays off (see tf_native_init). */
static int initialized, state;
static wchar_t profile_path[MAX_PATH];     /* next to the DLL: Scripts\tf_native.profile (game_profile.py) */
static const char *waiting_for = "waiting-for-game-profile";
static ULONGLONG profile_tried;
#define PROFILE_RETRY_MS 5000              /* the companion makes the profile after a game update, also mid-game */
static ULONGLONG last_report;
static wchar_t source_path[MAX_PATH], status_path[MAX_PATH];
static HANDLE source_file = INVALID_HANDLE_VALUE;
/* The status file stays open and is rewritten in place, padded to STATUS_BYTES, so tf_native.lua can keep it open
 * too: opening a file in %TEMP% waits for the virus scanner, on the game's thread. */
#define STATUS_BYTES 2048
static HANDLE status_file = INVALID_HANDLE_VALUE;

/* Written by tf_native.lua: the CRTV screen's texture (a UTexture*, 0 for none), the player's radio (its
 * UHandheldRadioComponent, 0 for none), how often and how wide the phone wants the screen, and when (Unix s). */
struct Source { char magic[8]; uint64_t texture, radio; uint32_t fps, width; uint64_t unix_seconds; };
_Static_assert(sizeof(struct Source) == 40, "Lua source packet ABI");
/* The radio's mini-game state, as JSON for the status: what the phone's buttons and pose act on. */
static char radio_json[128] = "null";

/* Whether the `size` bytes at `address` are memory of this process that may be read (or, with `write`, written).
 * What the DLL reads and writes are the game's own objects, which may be gone: asking Windows first keeps a stale
 * address from crashing the game. */
static int accessible(uintptr_t address, SIZE_T size, int write) {
    const DWORD writable = PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
    const DWORD readable = writable | PAGE_READONLY | PAGE_EXECUTE_READ;
    uintptr_t end = address + size;
    if (!address || end < address) return 0;
    while (address < end) {
        MEMORY_BASIC_INFORMATION region;
        if (!VirtualQuery((const void *)address, &region, sizeof(region)) || region.State != MEM_COMMIT ||
            (region.Protect & PAGE_GUARD) || !(region.Protect & (write ? writable : readable))) return 0;
        address = (uintptr_t)region.BaseAddress + region.RegionSize;
    }
    return 1;
}

/* Reads memory that may be gone without crashing: a failed read is an answer. */
static int read_memory(uintptr_t address, void *destination, SIZE_T size) {
    if (!accessible(address, size, 0)) return 0;
    memcpy(destination, (const void *)address, size);
    return 1;
}

static uint64_t unix_ms(void) {
    FILETIME time;
    GetSystemTimeAsFileTime(&time);
    ULARGE_INTEGER ticks;
    ticks.LowPart = time.dwLowDateTime;
    ticks.HighPart = time.dwHighDateTime;
    return ticks.QuadPart / 10000 - 11644473600000ULL;
}

static void close_packet(HANDLE *file) {
    if (*file != INVALID_HANDLE_VALUE) CloseHandle(*file);
    *file = INVALID_HANDLE_VALUE;
}

/* A packet tf_native.lua rewrites in place, read through a handle kept open: opening a file in %TEMP% waits for the
 * virus scanner (milliseconds, on the game thread), reading through an open one doesn't. 1 if all `size` bytes were
 * read; after a failure the file is opened again the next time. */
static int read_packet(HANDLE *file, const wchar_t *path, void *packet, DWORD size) {
    DWORD read = 0;
    if (*file == INVALID_HANDLE_VALUE)
        *file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (*file != INVALID_HANDLE_VALUE && SetFilePointer(*file, 0, NULL, FILE_BEGIN) == 0 &&
        ReadFile(*file, packet, size, &read, NULL) && read == size) return 1;
    close_packet(file);
    return 0;
}

#include "picture.c"
#include "capture.c"
#include "alignment.c"
#include "look.c"
#include "audio.c"

/* The radio's mini-game state into radio_json: whether it is up, fine-tuning, in which mode (EFineTuningState) and
 * at which stage of the advanced mini-game (EAdvancedTuningStage). Private members: only the PDB knows them. */
static void read_radio(uintptr_t radio) {
    unsigned char active = 0, tuning = 0, mode = 0;
    unsigned stage = 0;
    if (!radio || !read_memory(radio + RADIO_ACTIVE_OFFSET, &active, 1) ||
        !read_memory(radio + RADIO_FINE_TUNING_OFFSET, &tuning, 1) || !read_memory(radio + RADIO_FINE_STATE_OFFSET, &mode, 1) ||
        !read_memory(radio + RADIO_STAGE_OFFSET, &stage, 4)) {
        strcpy(radio_json, "null");
        return;
    }
    snprintf(radio_json, sizeof(radio_json), "{\"active\":%u,\"fineTuning\":%u,\"mode\":%u,\"stage\":%u}",
             active, tuning, mode, stage);
}

/* The DLL's state for tf_native.lua's log and the companion's /api/crtv/status, replaced whole. */
static void report(const char *status, int width, int height, unsigned format) {
    char body[STATUS_BYTES], levels[512];
    double radians;
    int looks = look_offset(&radians);
    audio_levels(levels, sizeof(levels));
    int length = snprintf(body, sizeof(body),
        "{\"status\":\"%s\",\"width\":%d,\"height\":%d,\"format\":%u,\"captureStatus\":\"%s\",\"captureWait\":\"%s\","
        "\"requestedFps\":%u,\"frames\":%llu,\"viewportCalls\":%llu,\"trackedAccess\":%u,\"queueMs\":%.3f,\"mapMs\":%.3f,"
        "\"alignmentResult\":%u,\"alignmentCalls\":%llu,\"look\":%s,\"looking\":%s,\"lookYaw\":%.1f,"
        "\"audio\":%d,\"audioRate\":%d,\"audioBuses\":%d,\"audioLevels\":\"%s\",\"audioFrames\":%lld,\"audioMuted\":%d,\"radio\":%s}",
        status, width, height, format, capture_status(), capture_wait_status(),
        atomic_load(&requested_fps), (unsigned long long)atomic_load(&published_frames),
        (unsigned long long)atomic_load(&viewport_calls), atomic_load(&captured_access),
        atomic_load(&queue_cpu_us) / 1000.0, atomic_load(&map_cpu_us) / 1000.0,
        atomic_load(&alignment_result), (unsigned long long)atomic_load(&alignment_calls),
        look_ready ? "true" : "false", looks ? "true" : "false", atomic_load(&look_yaw),
        audio_state, audio_rate, bus_count, levels, audio_shared_frames(),
        GetTickCount64() < atomic_load(&audio_mute_until), radio_json);
    if (length < 0 || length >= (int)sizeof(body)) return;
    memset(body + length, ' ', sizeof(body) - (size_t)length);
    if (status_file == INVALID_HANDLE_VALUE)
        status_file = CreateFileW(status_path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                                  OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD written = 0;
    if (status_file == INVALID_HANDLE_VALUE || SetFilePointer(status_file, 0, NULL, FILE_BEGIN) != 0 ||
        !WriteFile(status_file, body, sizeof(body), &written, NULL) || written != sizeof(body)) close_packet(&status_file);
}

/* Whether the profile is for the game at `base`: its build stamp, size and PDB identity, and its code where the DLL
 * calls in as the companion read it from the game's file (nobody has patched it since). */
static int validate_image(unsigned char *base) {
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0 || dos->e_lfanew > 0x100000) return 0;
    IMAGE_NT_HEADERS64 *nt = (IMAGE_NT_HEADERS64 *)(base + dos->e_lfanew);
    uint32_t size = profile.image_size;
    if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
        nt->FileHeader.TimeDateStamp != profile.timestamp || nt->OptionalHeader.SizeOfImage != size) return 0;
    IMAGE_DATA_DIRECTORY directory = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_DEBUG];
    if (directory.VirtualAddress > size || directory.Size > size - directory.VirtualAddress) return 0;
    IMAGE_DEBUG_DIRECTORY *records = (IMAGE_DEBUG_DIRECTORY *)(base + directory.VirtualAddress);
    int identity_matches = 0;
    for (unsigned i = 0; i < directory.Size / sizeof(*records); ++i) {
        if (records[i].Type == IMAGE_DEBUG_TYPE_CODEVIEW && records[i].SizeOfData >= sizeof(profile.identity) &&
            records[i].AddressOfRawData <= size - sizeof(profile.identity) &&
            !memcmp(base + records[i].AddressOfRawData, profile.identity, sizeof(profile.identity)))
            identity_matches = 1;
    }
    const uint32_t places[] = {LOOK_ENEMY_RETURN_RVA, LOOK_WAYPOINT_RETURN_RVA, EXECUTOR_RVA, IMMEDIATE_LIST_RVA,
                               D3D12_TEXTURE_VTABLE_RVA};
    for (unsigned i = 0; i < sizeof(places) / sizeof(places[0]); ++i)
        if (places[i] >= size) return 0;
    for (unsigned i = 0; i < sizeof(profile_code) / sizeof(profile_code[0]); ++i)
        if (*profile_code[i].rva > size - 16 || memcmp(base + *profile_code[i].rva, profile_code[i].code, 16)) return 0;
    return identity_matches;
}

/* The profile at `path` (game_profile.py) into `profile`: 1 if it is whole and of this DLL's layout. */
static int load_profile(const wchar_t *path) {
    struct GameProfile loaded;
    DWORD read = 0;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    int ok = GetFileSize(file, NULL) == sizeof(loaded) && ReadFile(file, &loaded, sizeof(loaded), &read, NULL) &&
             read == sizeof(loaded) && !memcmp(loaded.magic, "TFPROF01", 8) && loaded.version == PROFILE_VERSION &&
             loaded.size == sizeof(loaded);
    CloseHandle(file);
    if (ok) profile = loaded;
    return ok;
}

/* What needs the game's own functions (the picture, the look, the mini-game's image), once the profile of this game
 * is there and fits it: state 1. Until then the state stays 0, and the status says what it waits for. */
static void start_with_profile(void) {
    if (!load_profile(profile_path)) { waiting_for = "waiting-for-game-profile"; return; }
    if (!validate_image(image_base)) { waiting_for = "game-profile-for-another-version"; return; }
    get_resource = (GetResourceFn)(image_base + GET_RESOURCE_RVA);
    initialize_capture();
    if (MH_CreateHook(image_base + END_VIEWPORT_RVA, observe_end_viewport, (void **)&original_end_viewport) != MH_OK ||
        MH_EnableHook(image_base + END_VIEWPORT_RVA) != MH_OK) {
        state = -4; report("hook-install-failed", 0, 0, 0); return;
    }
    install_look();
    state = 1;
}

/* Once, on the game thread. The game's sound needs none of the game's own code (FMOD's functions, by name): it starts
 * now. The rest waits for this game's profile, and stays off on any doubt. */
__declspec(dllexport) int tf_native_init(void *lua_state) {
    (void)lua_state;
    if (initialized) return 0;
    initialized = 1;
    wchar_t temp[MAX_PATH];
    DWORD length = GetTempPathW(MAX_PATH, temp);
    if (!length || length >= MAX_PATH - 64) { state = -5; return 0; }
    swprintf(source_path, MAX_PATH, L"%lstownfall-companion-native-source.bin", temp);
    swprintf(status_path, MAX_PATH, L"%lstownfall-companion-native.json", temp);
    image_base = (unsigned char *)GetModuleHandleW(L"Townfall-Win64-Shipping.exe");
    if (!image_base) { state = -1; report("game-not-present", 0, 0, 0); return 0; }
    /* A Lua reload must not unload the DLL while its hooks are installed: pinned until the game exits. */
    HMODULE self;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                            (LPCWSTR)&tf_native_init, &self)) { state = -3; return 0; }
    DWORD name = GetModuleFileNameW(self, profile_path, MAX_PATH);
    wchar_t *folder_end = wcsrchr(profile_path, L'\\');
    if (!name || name >= MAX_PATH - 32 || !folder_end) { state = -5; return 0; }
    wcscpy(folder_end + 1, L"tf_native.profile");
    initialize_alignment(temp);
    initialize_look(temp);
    initialize_audio(temp);
    if (MH_Initialize() != MH_OK) { state = -4; report("hook-install-failed", 0, 0, 0); return 0; }
    install_audio();
    start_with_profile();
    profile_tried = GetTickCount64();
    report(state == 1 ? "waiting-for-crtv-source" : waiting_for, 0, 0, 0);
    return 0;
}

/* Every half second, on the game thread, after tf_native.lua wrote the source packet: takes the texture it
 * names (checked to be a live D3D12 texture) as the one to copy, and the phone's wishes. */
__declspec(dllexport) int tf_native_update(void *lua_state) {
    (void)lua_state;
    if (!initialized || state < 0) return 0;
    game_thread_id = GetCurrentThreadId();
    if (audio_state == 0) install_audio(); /* FMOD comes with a plugin the game may load after the mod */
    ULONGLONG now = GetTickCount64();
    if (now - last_report < 1000) return 0;
    last_report = now;
    if (state == 0 && now - profile_tried >= PROFILE_RETRY_MS) {
        profile_tried = now;
        start_with_profile();
    }
    if (state != 1) { report(state < 0 ? "hook-install-failed" : waiting_for, 0, 0, 0); return 0; }
    struct Source source = {0};
    int ok = read_packet(&source_file, source_path, &source, sizeof(source));
    uint64_t unix_seconds = unix_ms() / 1000;
    int packet_valid = ok && !memcmp(source.magic, "TFNATV03", 8) && source.fps <= 30 &&
        (source.width == 320 || source.width == 480 || source.width == 640) &&
        source.unix_seconds <= unix_seconds + 1 && source.unix_seconds + 4 >= unix_seconds;
    configure_capture(packet_valid ? source.fps : 0, packet_valid ? source.width : 0);
    read_radio(packet_valid ? (uintptr_t)source.radio : 0);
    if (!packet_valid || !source.texture) {
        publish_capture_source(NULL, 0, 0, 0);
        report("waiting-for-crtv-source", 0, 0, 0); return 0;
    }
    uintptr_t object_vtable = 0, resource = 0, rhi = 0, vtable = 0;
    int extent[2] = {0};
    unsigned char format = 0;
    if (!read_memory((uintptr_t)source.texture, &object_vtable, sizeof(object_vtable)) || !object_vtable) {
        publish_capture_source(NULL, 0, 0, 0);
        report("invalid-source", 0, 0, 0); return 0;
    }
    resource = (uintptr_t)get_resource((void *)(uintptr_t)source.texture);
    if (!read_memory(resource + TEXTURE_RHI_OFFSET, &rhi, sizeof(rhi)) ||
        !read_memory(rhi, &vtable, sizeof(vtable)) || !read_memory(rhi + EXTENT_OFFSET, extent, sizeof(extent)) ||
        !read_memory(rhi + FORMAT_OFFSET, &format, sizeof(format)) ||
        extent[0] <= 0 || extent[0] > 8192 || extent[1] <= 0 || extent[1] > 8192) {
        publish_capture_source(NULL, 0, 0, 0);
        report("rhi-source-not-ready", 0, 0, 0); return 0;
    }
    if (vtable == (uintptr_t)image_base + D3D12_TEXTURE_VTABLE_RVA)
        publish_capture_source((void *)rhi, extent[0], extent[1], format);
    else publish_capture_source(NULL, 0, 0, 0);
    report("rhi-source-identified", extent[0], extent[1], format);
    return 0;
}

/* For the tests: the state above, and the profile at `path` checked against a mapped game image. */
__declspec(dllexport) int tf_native_state(void) { return state; }
__declspec(dllexport) int tf_native_validate(void *mapped_image, const wchar_t *path) {
    return load_profile(path) && validate_image(mapped_image);
}
