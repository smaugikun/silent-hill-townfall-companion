/* Looking around with the phone without turning the character: included by tf_native.c.
 *
 * The game's CRTV takes its direction from the character in three places. How strong a signal comes in: the angle
 * between the character's facing and the signal (URadioStaticSourceComponent and URadioWaypointSourceComponent
 * ::UpdateAudioComponentParameters, through FNoCodeTools::Get2DAngleBetweenVectors). What its screen shows: a scene
 * capture held with the radio that renders only the strongest monster (UHandheldRadioComponent::SceneCaptureComponent),
 * and the arrow towards that monster, measured from the same capture (UHandheldRadioComponent::UpdateRadar). And the
 * radar, from the camera (UHandheldRadioComponent::GetRadarSpaceTransform). While the phone looks around
 * (tf_native.lua writes how far it points from where the character faces, and how far up), all of them turn that far
 * from the character's facing, so they turn along when he turns: the facing is turned, and the capture is aimed, for
 * just the two calls that use it, then put back. The capture is aimed whole, across and up, from the character's facing
 * and the phone's pitch, not from where it is held: switched on without his raising it, the radio hangs in his lowered
 * hands and its capture looks at the ground. The character, the view on the monitor and everything else in the game
 * keep the character's own direction. */
#include <math.h>

typedef float (*Angle2dFn)(const double *a, const double *b);
typedef void *(*RadarSpaceFn)(void *radio, double *out);
typedef void (*RadarUpdateFn)(void *radio);
typedef void (*CaptureUpdateFn)(void *capture, void *scene, void *builder);
static Angle2dFn original_angle_2d;
static RadarSpaceFn original_radar_space;
static RadarUpdateFn original_radar_update;
static CaptureUpdateFn original_capture_update;
static int look_ready;
static wchar_t look_path[MAX_PATH];
static HANDLE look_file = INVALID_HANDLE_VALUE;
static uint64_t look_seq;
static _Atomic int looking;
static _Atomic double look_yaw;            /* degrees from the character's facing, as the game's yaw grows (x to y) */
static _Atomic double look_pitch;          /* degrees above the horizon */
static _Atomic unsigned long long look_at; /* when the phone last said so (GetTickCount64) */
static _Atomic double facing_yaw = NAN;    /* radians: the character's facing, as the CRTV's signals last measured it */
#define LOOK_FRESH_MS 2000                 /* the phone says it every second: older, it went away, and the CRTV looks ahead */
static void *radio_capture;                /* the radio's scene capture, as its last radar update had it (game thread) */

/* Written by tf_native.lua: whether the phone looks around, how far from the character's facing and how far up
 * (degrees), and the command's sequence (the bridge's clock, ms). */
struct LookPacket { char magic[8]; double yaw; double pitch; uint64_t looking; uint64_t unix_ms; };
_Static_assert(sizeof(struct LookPacket) == 40, "Look packet ABI");

/* How far the phone looks from the character's facing, in radians, if it looks around and was heard from lately. */
static int look_offset(double *radians) {
    if (!atomic_load(&looking) || GetTickCount64() - atomic_load(&look_at) > LOOK_FRESH_MS) return 0;
    *radians = atomic_load(&look_yaw) * 3.14159265358979323846 / 180;
    return 1;
}

/* `forward` (x, y, z) turned by `radians` about the vertical, the way the game's yaw grows (x towards y). */
static void turn_forward(const double *forward, double radians, double *out) {
    double c = cos(radians), s = sin(radians);
    out[0] = forward[0] * c - forward[1] * s;
    out[1] = forward[0] * s + forward[1] * c;
    out[2] = forward[2];
}

/* Quaternions as the game keeps them (x, y, z, w). a * b turns by b first, then by a. */
static void quat_multiply(const double *a, const double *b, double *out) {
    double x = a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1];
    double y = a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0];
    double z = a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3];
    double w = a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2];
    out[0] = x; out[1] = y; out[2] = z; out[3] = w;
}

static void quat_rotate(const double *q, const double *v, double *out) {
    double t0 = 2 * (q[1] * v[2] - q[2] * v[1]), t1 = 2 * (q[2] * v[0] - q[0] * v[2]), t2 = 2 * (q[0] * v[1] - q[1] * v[0]);
    out[0] = v[0] + q[3] * t0 + (q[1] * t2 - q[2] * t1);
    out[1] = v[1] + q[3] * t1 + (q[2] * t0 - q[0] * t2);
    out[2] = v[2] + q[3] * t2 + (q[0] * t1 - q[1] * t0);
}

/* The radar's world-to-radar transform `t` (FTransform<double>: rotation, translation, scale; profile.h checks the
 * layout) as from a camera turned by `radians` about the vertical through it: a world point is first turned the
 * other way about the camera, then taken into the radar's space as before. */
static void turn_radar(double *t, double radians) {
    double *rotation = t, *translation = t + 4, *scale = t + 8;
    if (!scale[0] || !scale[1] || !scale[2]) return;
    /* The camera is the point the transform takes to the radar's centre. */
    double inverse[4] = {-rotation[0], -rotation[1], -rotation[2], rotation[3]};
    double back[3] = {-translation[0], -translation[1], -translation[2]}, camera[3];
    quat_rotate(inverse, back, camera);
    for (int i = 0; i < 3; ++i) camera[i] /= scale[i];
    double half = -radians / 2, turn[4] = {0, 0, sin(half), cos(half)}, turned[3], shift[3], moved[3], composed[4];
    quat_rotate(turn, camera, turned);
    for (int i = 0; i < 3; ++i) shift[i] = (camera[i] - turned[i]) * scale[i];
    quat_rotate(rotation, shift, moved);
    for (int i = 0; i < 3; ++i) translation[i] += moved[i];
    quat_multiply(rotation, turn, composed);
    memcpy(rotation, composed, sizeof(composed));
}

/* A rotation (quaternion) facing the yaw `yaw` and the pitch `pitch` (radians; up positive), level. Its forward,
 * +X, is (cos pitch cos yaw, cos pitch sin yaw, sin pitch). */
static void aim_rotation(double yaw, double pitch, double *out) {
    double turn[4] = {0, 0, sin(yaw / 2), cos(yaw / 2)}, tilt[4] = {0, sin(-pitch / 2), 0, cos(-pitch / 2)};
    quat_multiply(turn, tilt, out);
}

/* The capture aimed where the phone looks, for one call of the game's: `saved` keeps its rotation, `turned` what it
 * got. Across from the character's facing (or, before a signal has measured it, from where the capture looks). 0 if
 * the phone doesn't look around (or there is no capture), and nothing changed. */
static int turn_capture(unsigned char *capture, double *saved, double *turned) {
    double radians, facing = atomic_load(&facing_yaw);
    if (!capture || !look_offset(&radians)) return 0;
    double *rotation = (double *)(capture + COMPONENT_TO_WORLD_OFFSET);
    memcpy(saved, rotation, 4 * sizeof(double));
    if (isnan(facing)) {
        double ahead[3] = {1, 0, 0}, forward[3];
        quat_rotate(saved, ahead, forward);
        facing = atan2(forward[1], forward[0]);
    }
    aim_rotation(facing + radians, atomic_load(&look_pitch) * 3.14159265358979323846 / 180, turned);
    memcpy(rotation, turned, 4 * sizeof(double));
    return 1;
}

/* Puts the capture's rotation back, unless the game itself moved it during the call. */
static void restore_capture(unsigned char *capture, const double *saved, const double *turned) {
    double *rotation = (double *)(capture + COMPONENT_TO_WORLD_OFFSET);
    if (!memcmp(rotation, turned, 4 * sizeof(double))) memcpy(rotation, saved, 4 * sizeof(double));
}

/* Get2DAngleBetweenVectors(facing, towards the signal): for the CRTV's two calls, the facing turned by the phone's
 * look; for every other caller (the AI among them), as the game has it. */
static float look_angle_2d(const double *a, const double *b) {
    unsigned char *from = __builtin_return_address(0);
    double radians, turned[3];
    if (from != image_base + LOOK_ENEMY_RETURN_RVA && from != image_base + LOOK_WAYPOINT_RETURN_RVA)
        return original_angle_2d(a, b);
    if (a[0] || a[1]) atomic_store(&facing_yaw, atan2(a[1], a[0]));
    if (!look_offset(&radians)) return original_angle_2d(a, b);
    turn_forward(a, radians, turned);
    return original_angle_2d(turned, b);
}

static void *look_radar_space(void *radio, double *out) {
    void *result = original_radar_space(radio, out);
    double radians;
    if (look_offset(&radians)) turn_radar(out, radians);
    return result;
}

static void look_radar_update(void *radio) {
    unsigned char *capture = *(unsigned char **)((unsigned char *)radio + RADIO_CAPTURE_OFFSET);
    double saved[4], turned[4];
    radio_capture = capture;
    int turns = turn_capture(capture, saved, turned);
    original_radar_update(radio);
    if (turns) restore_capture(capture, saved, turned);
}

static void look_capture_update(void *capture, void *scene, void *builder) {
    double saved[4], turned[4];
    int turns = capture == radio_capture && turn_capture(capture, saved, turned);
    original_capture_update(capture, scene, builder);
    if (turns) restore_capture(capture, saved, turned);
}

static void initialize_look(const wchar_t *temp) {
    swprintf(look_path, MAX_PATH, L"%lstownfall-companion-native-look.bin", temp);
}

/* After MH_Initialize: without these hooks the CRTV just keeps the character's direction. */
static void install_look(void) {
    struct { unsigned rva; void *detour; void **original; } hooks[] = {
        {ANGLE_2D_RVA, look_angle_2d, (void **)&original_angle_2d},
        {RADAR_SPACE_RVA, look_radar_space, (void **)&original_radar_space},
        {RADAR_UPDATE_RVA, look_radar_update, (void **)&original_radar_update},
        {CAPTURE_UPDATE_RVA, look_capture_update, (void **)&original_capture_update},
    };
    int ok = 1;
    for (size_t i = 0; i < sizeof(hooks) / sizeof(hooks[0]); ++i)
        ok = ok && MH_CreateHook(image_base + hooks[i].rva, hooks[i].detour, hooks[i].original) == MH_OK;
    for (size_t i = 0; i < sizeof(hooks) / sizeof(hooks[0]); ++i)
        ok = ok && MH_EnableHook(image_base + hooks[i].rva) == MH_OK;
    look_ready = ok;
}

/* Right after tf_native.lua wrote a look packet, on the game thread. */
__declspec(dllexport) int tf_native_look(void *lua_state) {
    (void)lua_state;
    if (state <= 0 || !look_ready || GetCurrentThreadId() != game_thread_id) return 0;
    struct LookPacket packet = {0};
    int ok = read_packet(&look_file, look_path, &packet, sizeof(packet));
    if (!ok || memcmp(packet.magic, "TFLOOK04", 8) || !isfinite(packet.yaw) || fabs(packet.yaw) > 180 ||
        !isfinite(packet.pitch) || fabs(packet.pitch) > 90 || packet.looking > 1 || !packet.unix_ms ||
        packet.unix_ms == look_seq) return 0;
    look_seq = packet.unix_ms;
    atomic_store(&look_yaw, packet.yaw);
    atomic_store(&look_pitch, packet.pitch);
    atomic_store(&looking, (int)packet.looking);
    atomic_store(&look_at, GetTickCount64());
    return 0;
}
