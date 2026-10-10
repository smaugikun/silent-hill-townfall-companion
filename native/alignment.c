/* The image alignment stages of the fine-tune mini-game, moved by the phone: included by tf_native.c. Moves the image
 * as the game's own stick functions do at their end (AdvancedTuningMotionControlFallbackX/Y: the image's position,
 * clamped to -1..1 on each axis), on the game thread, only while the radio is up and in one of those stages. Not
 * through those functions: the vertical one turns its input round whenever the last device used was the mouse or
 * keyboard, and both ignore input while a controller's gyro steers the image. */
#include <math.h>

static wchar_t alignment_path[MAX_PATH];
static HANDLE alignment_file = INVALID_HANDLE_VALUE;
static uint64_t alignment_seq;
/* The last packet's outcome: 0 none yet, 1 applied, 2 bad packet or move, 3 not in an alignment stage. */
static _Atomic unsigned alignment_result;
static _Atomic uint64_t alignment_calls;

/* Written by tf_alignment.lua right before it calls tf_native_align: the radio (its address, from the live pawn), how
 * far to move in the game's own units (its image spans -1..1 on each axis), and the phone command's sequence (the
 * bridge's clock, ms). */
struct AlignmentPacket { char magic[8]; uint64_t radio; double x, y; uint64_t unix_ms; };
_Static_assert(sizeof(struct AlignmentPacket) == 40, "Alignment packet ABI");

/* Writes memory that may be gone without crashing: a failed write is an answer. */
static int write_memory(uintptr_t address, const void *source, SIZE_T size) {
    SIZE_T written = 0;
    return address && WriteProcessMemory(GetCurrentProcess(), (void *)address, source, size, &written) && written == size;
}

static double clamp_unit(double v) { return v < -1 ? -1 : v > 1 ? 1 : v; }

static int apply_alignment(uintptr_t radio, double x, double y) {
    unsigned char active = 0, tuning = 0, fine_state = 0;
    unsigned stage = 0;
    double image[2];
    if (!radio || !isfinite(x) || !isfinite(y)) return 2;
    if (!read_memory(radio + RADIO_ACTIVE_OFFSET, &active, 1) || active != 1 ||
        !read_memory(radio + RADIO_FINE_TUNING_OFFSET, &tuning, 1) || tuning != 1 ||
        !read_memory(radio + RADIO_FINE_STATE_OFFSET, &fine_state, 1) || fine_state != ALIGN_ADVANCED ||
        !read_memory(radio + RADIO_STAGE_OFFSET, &stage, 4) || (stage != ALIGN_STABILISE && stage != ALIGN_RGB) ||
        !read_memory(radio + RADIO_IMAGE_OFFSET, image, sizeof(image)) || !isfinite(image[0]) || !isfinite(image[1])) return 3;
    image[0] = clamp_unit(image[0] + x);
    image[1] = clamp_unit(image[1] + y);
    if (!write_memory(radio + RADIO_IMAGE_OFFSET, image, sizeof(image))) return 3;
    atomic_fetch_add(&alignment_calls, 1);
    return 1;
}

static void initialize_alignment(const wchar_t *temp) {
    swprintf(alignment_path, MAX_PATH, L"%lstownfall-companion-native-align.bin", temp);
}

/* Right after tf_alignment.lua wrote a packet, on the game thread: applies it once. */
__declspec(dllexport) int tf_native_align(void *lua_state) {
    (void)lua_state;
    if (state <= 0 || GetCurrentThreadId() != game_thread_id) return 0;
    struct AlignmentPacket packet = {0};
    int ok = read_packet(&alignment_file, alignment_path, &packet, sizeof(packet));
    if (!ok || memcmp(packet.magic, "TFALIGN1", 8) || !packet.unix_ms || packet.unix_ms == alignment_seq) {
        atomic_store(&alignment_result, 2); return 0;
    }
    alignment_seq = packet.unix_ms;
    atomic_store(&alignment_result, apply_alignment((uintptr_t)packet.radio, packet.x, packet.y));
    return 0;
}
