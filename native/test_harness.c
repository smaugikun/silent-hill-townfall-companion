/* tf_native.c against a fake engine, for tests/test_native.py: built into tf_native_tests.dll, never installed. */
#include "tf_native.c"

/* The fake engine's layout: a real profile of the game's, so the fakes below fit it. Loaded as the
 * DLL is: the code under test reads the same `profile`. */
static const struct GameProfile test_profile = {
    .get_resource_rva = 18461716u,
    .texture_rhi_offset = 16u,
    .extent_offset = 68u,
    .format_offset = 83u,
    .end_viewport_rva = 23063368u,
    .transition_rva = 19428116u,
    .retain_texture_rva = 20163416u,
    .readback_ctor_rva = 39420528u,
    .enqueue_copy_rva = 81858068u,
    .lock_readback_rva = 81864724u,
    .unlock_readback_rva = 81875212u,
    .poll_fence_rva = 21438304u,
    .angle_2d_rva = 96557924u,
    .radar_space_rva = 101628176u,
    .radar_update_rva = 28662768u,
    .capture_update_rva = 88508632u,
    .radio_active_offset = 1136u,
    .radio_fine_tuning_offset = 1880u,
    .radio_fine_state_offset = 1881u,
    .radio_stage_offset = 1932u,
    .radio_image_offset = 2024u,
    .align_advanced = 1u,
    .align_stabilise = 2u,
    .align_rgb = 3u,
    .radio_capture_offset = 1008u,
    .component_to_world_offset = 480u,
    .look_enemy_return_rva = 101924689u,
    .look_waypoint_return_rva = 101928970u,
    .executor_rva = 165139152u,
    .immediate_list_rva = 165139208u,
    .readback_size = 88u,
    .readback_fence_offset = 8u,
    .readback_mask_offset = 16u,
    .fence_pending_offset = 16u,
    .tracked_access_offset = 24u,
    .inside_pass_offset = 443u,
    .inside_pass_mask = 6u,
    .gpu_mask_offset = 444u,
    .executing_offset = 100u,
    .d3d12_texture_vtable_rva = 127330584u,
    .access_copy_src = 128u,
    .access_srv_mask = 112u,
    .transition_texture = 1u,
    .pixel_rgba8 = 37u,
    .pixel_bgra8 = 2u,
};

__attribute__((constructor)) static void use_test_profile(void) { profile = test_profile; }

/* A radio in the advanced mini-game's stabilise stage, its image at (0.5, -0.4). */
static void fake_radio(unsigned char *radio) {
    unsigned stage = ALIGN_STABILISE;
    double image[2] = {.5, -.4};
    radio[RADIO_ACTIVE_OFFSET] = radio[RADIO_FINE_TUNING_OFFSET] = 1;
    radio[RADIO_FINE_STATE_OFFSET] = ALIGN_ADVANCED;
    memcpy(radio + RADIO_STAGE_OFFSET, &stage, 4);
    memcpy(radio + RADIO_IMAGE_OFFSET, image, sizeof(image));
}

static int image_at(const unsigned char *radio, double x, double y) {
    double image[2];
    memcpy(image, radio + RADIO_IMAGE_OFFSET, sizeof(image));
    return fabs(image[0] - x) < 1e-12 && fabs(image[1] - y) < 1e-12;
}

__declspec(dllexport) int crtv_test_alignment(unsigned scenario) {
    unsigned char radio[4096] = {0};
    fake_radio(radio);
    unsigned stage;
    uintptr_t address = (uintptr_t)radio;
    double x = .1, y = -.2, ex = .6, ey = -.6;
    int expected = 1;
    switch (scenario) {
        case 1: radio[RADIO_ACTIVE_OFFSET] = 0; expected = 3; break;
        case 2: radio[RADIO_FINE_TUNING_OFFSET] = 0; expected = 3; break;
        case 3: radio[RADIO_FINE_STATE_OFFSET] = 0; expected = 3; break;
        case 4: stage = 1; memcpy(radio + RADIO_STAGE_OFFSET, &stage, 4); expected = 3; break;
        case 5: stage = 4; memcpy(radio + RADIO_STAGE_OFFSET, &stage, 4); expected = 3; break;
        case 6: y = .3; ey = -.1; break; /* up is up, whatever device the player used last */
        case 7: address = 1; expected = 3; break;
        case 8: x = NAN; expected = 2; break;
        case 9: y = -3; ey = -1; break; /* a big turn goes whole, and the image stays within its range */
        case 10: stage = ALIGN_RGB; memcpy(radio + RADIO_STAGE_OFFSET, &stage, 4); break;
    }
    int result = apply_alignment(address, x, y);
    return result == expected && (expected == 1 ? image_at(radio, ex, ey) : image_at(radio, .5, -.4));
}

__declspec(dllexport) int crtv_test_alignment_packet(unsigned scenario, const wchar_t *path) {
    unsigned char radio[4096] = {0};
    fake_radio(radio);
    struct AlignmentPacket packet = {{'T','F','A','L','I','G','N','1'}, (uintptr_t)radio, .1, 0, unix_ms()};
    alignment_seq = 0;
    state = 1; game_thread_id = GetCurrentThreadId();
    close_packet(&alignment_file); /* the last test's file */
    swprintf(alignment_path, MAX_PATH, L"%ls", path);
    int applied = 1;
    switch (scenario) {
        case 1: game_thread_id = 0; applied = 0; break;
        case 2: packet.unix_ms -= 3600000; break; /* Lua writes it right before the call: its age doesn't matter */
        case 3: packet.unix_ms += 3600000; break; /* nor a clock set back since */
        case 4: packet.magic[0] = '?'; applied = 0; break;
        case 5: packet.radio = 1; applied = 0; break;
        case 6: packet.x = NAN; applied = 0; break;
    }
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD written;
    if (file == INVALID_HANDLE_VALUE) { state = 0; return 0; }
    int ok = WriteFile(file, &packet, sizeof(packet), &written, NULL) && written == sizeof(packet);
    CloseHandle(file);
    if (ok) { tf_native_align(NULL); tf_native_align(NULL); }
    close_packet(&alignment_file);
    state = 0;
    return ok && image_at(radio, applied ? .6 : .5, -.4); /* called twice: applied once */
}

/* Where `t` (look.c's radar transform: rotation, translation, scale) takes the world point p. */
static void radar_point(const double *t, const double *p, double *out) {
    double scaled[3] = {p[0] * t[8], p[1] * t[9], p[2] * t[10]};
    quat_rotate(t, scaled, out);
    for (int i = 0; i < 3; ++i) out[i] += t[4 + i];
}

static int close_to(const double *v, double x, double y, double z) {
    return fabs(v[0] - x) < 1e-6 && fabs(v[1] - y) < 1e-6 && fabs(v[2] - z) < 1e-6;
}

/* look.c's turns: the CRTV's facing, and the radar as from a turned camera. 1 if all hold. */
__declspec(dllexport) int crtv_test_look_turns(void) {
    const double quarter = 3.14159265358979323846 / 2;
    double forward[3] = {1, 0, 0.5}, turned[3], sideways[3] = {0, 1, 0};
    turn_forward(forward, quarter, turned); /* the phone 90 degrees to the right: the facing turns from +X to +Y */
    if (!close_to(turned, 0, 1, 0.5)) return 0;
    turn_forward(sideways, quarter, turned); /* the character turned to +Y meanwhile: the look turns along, to -X */
    if (!close_to(turned, -1, 0, 0)) return 0;
    /* A camera at (100, 200, 50) facing +X: the radar is its inverse. 300 to its right (+Y), turned 90 degrees to the
     * right, is straight ahead; and the camera itself stays the radar's centre. */
    double t[12] = {0, 0, 0, 1, -100, -200, -50, 0, 1, 1, 1, 0}, p[3] = {100, 500, 50}, camera[3] = {100, 200, 50}, out[3];
    turn_radar(t, quarter);
    radar_point(t, p, out);
    if (!close_to(out, 300, 0, 0)) return 0;
    radar_point(t, camera, out);
    if (!close_to(out, 0, 0, 0)) return 0;
    /* The same camera facing +Y (yaw 90) already: turned 90 more it faces -X, so what lies 300 towards -X is ahead. */
    double s = sin(quarter / 2), c = cos(quarter / 2), inverse[4] = {0, 0, -s, c}, back[3] = {-100, -200, -50}, moved[3];
    quat_rotate(inverse, back, moved);
    double u[12] = {0, 0, -s, c, moved[0], moved[1], moved[2], 0, 1, 1, 1, 0}, q[3] = {-200, 200, 50};
    turn_radar(u, quarter);
    radar_point(u, q, out);
    return close_to(out, 300, 0, 0);
}

/* The radio's scene capture as the game's calls see it while look.c's hooks run them. */
static unsigned char *watched_capture;
static double watched_forward[3];
static int game_moves_capture;

static void watch_capture(void) {
    double ahead[3] = {1, 0, 0}, moved[4] = {0, 0, 0, 1};
    quat_rotate((double *)(watched_capture + COMPONENT_TO_WORLD_OFFSET), ahead, watched_forward);
    if (game_moves_capture) memcpy(watched_capture + COMPONENT_TO_WORLD_OFFSET, moved, sizeof(moved));
}

static void watch_radar_update(void *radio) { (void)radio; watch_capture(); }
static void watch_capture_update(void *capture, void *scene, void *builder) { (void)capture; (void)scene; (void)builder; watch_capture(); }

/* The scene capture aimed for the game's calls while the phone looks 90 degrees to the right and 10 degrees up, the
 * capture hanging in the lowered hands (facing +Y, 30 degrees down): scenario 0 the radar update, the character's
 * facing not measured yet (it faces -X, 10 up, for the call, and is put back after), 1 the capture's own update
 * likewise, 2 another capture's update (untouched), 3 the phone not looking (untouched), 4 the game moving the
 * capture during the call (its move stays), 5 the character facing +X (it faces +Y, 10 up). 1 if right. */
__declspec(dllexport) int crtv_test_look_capture(unsigned scenario) {
    static unsigned char radio[4096], capture[4096], other[4096];
    const double down = 3.14159265358979323846 / 6, quarter = 3.14159265358979323846 / 2;
    const double up = 10 * 3.14159265358979323846 / 180;
    double pitch[4] = {0, sin(down / 2), 0, cos(down / 2)}, yaw[4] = {0, 0, sin(quarter / 2), cos(quarter / 2)};
    double pitched[4], moved[4] = {0, 0, 0, 1}, after[4];
    quat_multiply(yaw, pitch, pitched);
    unsigned char *own = capture, *seen = scenario == 2 ? other : capture;
    memcpy(capture + COMPONENT_TO_WORLD_OFFSET, pitched, sizeof(pitched));
    memcpy(other + COMPONENT_TO_WORLD_OFFSET, pitched, sizeof(pitched));
    memcpy(radio + RADIO_CAPTURE_OFFSET, &own, sizeof(own));
    original_radar_update = watch_radar_update;
    original_capture_update = watch_capture_update;
    atomic_store(&looking, scenario != 3);
    atomic_store(&look_yaw, 90.0);
    atomic_store(&look_pitch, 10.0);
    atomic_store(&look_at, GetTickCount64());
    atomic_store(&facing_yaw, scenario == 5 ? 0.0 : NAN);
    game_moves_capture = scenario == 4;
    watched_capture = seen;
    look_radar_update(radio); /* also how the hooks learn the radio's capture */
    if (scenario == 1 || scenario == 2) look_capture_update(seen, NULL, NULL);
    int during = scenario == 2 || scenario == 3 ? close_to(watched_forward, 0, cos(down), -sin(down))
               : scenario == 5 ? close_to(watched_forward, 0, cos(up), sin(up))
               : close_to(watched_forward, -cos(up), 0, sin(up));
    memcpy(after, seen + COMPONENT_TO_WORLD_OFFSET, sizeof(after));
    int restored = !memcmp(after, scenario == 4 ? moved : pitched, sizeof(after));
    atomic_store(&looking, 0);
    atomic_store(&facing_yaw, NAN);
    radio_capture = NULL;
    return during && restored;
}

/* tf_native_look against a packet file: scenario 0 a good one (applied, and only once fresh), 1 not a number,
 * 2 beyond a half turn, 3 the wrong magic, 4 the same command again, 5 the phone stopped looking (a look ends),
 * 6 a bad flag, 7 a pitch past straight up. 1 if the outcome is right. */
__declspec(dllexport) int crtv_test_look_packet(unsigned scenario, const wchar_t *path) {
    struct LookPacket packet = {{'T','F','L','O','O','K','0','4'}, -150, 12.5, 1, 1000 + scenario};
    if (scenario == 1) packet.yaw = NAN;
    if (scenario == 2) packet.yaw = 181;
    if (scenario == 7) packet.pitch = 91;
    if (scenario == 3) packet.magic[0] = '?';
    if (scenario == 5) packet.looking = 0;
    if (scenario == 6) packet.looking = 2;
    state = 1; look_ready = 1; game_thread_id = GetCurrentThreadId();
    look_seq = scenario == 4 ? packet.unix_ms : 0;
    atomic_store(&looking, scenario == 5);
    atomic_store(&look_yaw, 0.0);
    atomic_store(&look_at, GetTickCount64());
    close_packet(&look_file); /* the last test's file */
    swprintf(look_path, MAX_PATH, L"%ls", path);
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD written;
    if (file == INVALID_HANDLE_VALUE) { state = 0; return 0; }
    int ok = WriteFile(file, &packet, sizeof(packet), &written, NULL) && written == sizeof(packet);
    CloseHandle(file);
    if (ok) tf_native_look(NULL);
    double radians = 0;
    int looks = look_offset(&radians);
    int result = ok && (scenario == 0 ? looks && fabs(radians + 150 * 3.14159265358979323846 / 180) < 1e-9
                                         && atomic_load(&look_pitch) == 12.5 : !looks);
    if (scenario == 0) {
        /* The next look, written over this one in place as tf_native.lua does: read through the handle kept open. */
        packet.yaw = 30;
        packet.unix_ms += 1;
        file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        ok = file != INVALID_HANDLE_VALUE && WriteFile(file, &packet, sizeof(packet), &written, NULL);
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
        if (ok) tf_native_look(NULL);
        result = result && ok && look_offset(&radians) && fabs(radians - 30 * 3.14159265358979323846 / 180) < 1e-9;
        /* Heard from too long ago: the CRTV looks ahead again. */
        atomic_store(&look_at, GetTickCount64() - LOOK_FRESH_MS - 1);
        result = result && !look_offset(&radians);
    }
    close_packet(&look_file);
    state = 0; look_ready = 0;
    atomic_store(&looking, 0);
    return result;
}

/* audio.c's choice of buses by path. 1 if right. */
__declspec(dllexport) int crtv_test_audio_rank(void) {
    int crtv = tap_rank("bus:/World/Sfx/Player/CRTV"), signals = tap_rank("bus:/Dialogue/WaypointSignals");
    int character = tap_rank("bus:/Dialogue/Character"), other_crtv = tap_rank("bus:/Somewhere/NewCRTVBus");
    return crtv > signals && signals > character && character > other_crtv && other_crtv == 1 &&
           tap_rank("bus:/Dialogue/Cinematics") == 0 && tap_rank("bus:/World/Sfx/Player/Pager") == 0 &&
           tap_rank("bus:/") == 0;
}

/* One block through a tapped bus's DSP: scenario 0 stereo in, stereo out (passed on unchanged, measured), 1 FMOD
 * wanting more channels out than came in (the rest silent), 2 a DSP that isn't one of the taps (passed on, not
 * measured). Then the level report. 1 if right. */
__declspec(dllexport) int crtv_test_audio_tap(unsigned scenario) {
    static int dsp;
    float in[8] = {.5f, -.5f, .5f, -.5f, .5f, -.5f, .5f, -.5f}, out[16];
    struct FmodDspState dsp_state = {scenario == 2 ? (void *)&out : (void *)&dsp};
    memset(taps, 0, sizeof(taps));
    snprintf(taps[0].path, sizeof(taps[0].path), "bus:/PreMaster/SFX/CRTV");
    taps[0].dsp = &dsp;
    atomic_store(&tap_count, 1);
    char levels[128];
    audio_levels(levels, sizeof(levels)); /* where the report starts */
    int outchannels = scenario == 1 ? 4 : 2;
    int ok = tap_read(&dsp_state, in, out, 4, 2, &outchannels) == FMOD_OK;
    for (int frame = 0; frame < 4; ++frame) {
        ok = ok && out[frame * outchannels] == .5f && out[frame * outchannels + 1] == -.5f;
        for (int c = 2; c < outchannels; ++c) ok = ok && out[frame * outchannels + c] == 0;
    }
    audio_levels(levels, sizeof(levels));
    double expected = scenario == 1 ? sqrt(.125) : scenario == 2 ? 0 : .5;
    char wanted[64];
    snprintf(wanted, sizeof(wanted), "CRTV %.4f/%.4f", expected, scenario == 2 ? 0. : .5);
    ok = ok && !strcmp(levels, wanted);
    atomic_store(&tap_count, 0);
    return ok;
}

/* A bus goes into the phone's sound once: not when a bus above it does. 1 if right. */
__declspec(dllexport) int crtv_test_audio_nested(void) {
    const char *paths[] = {"bus:/X/Radio/CRTVVoice", "bus:/X/Radio", "bus:/X/RadioOther",
                           "bus:/World/Sfx/Player/CRTV/CRTVActive", "bus:/World/Sfx/Player/CRTV", "bus:/Dialogue/Character"};
    memset(taps, 0, sizeof(taps));
    for (int i = 0; i < 6; ++i) {
        snprintf(taps[i].path, sizeof(taps[i].path), "%s", paths[i]);
        taps[i].phone = to_phone(paths[i]);
    }
    taps_chosen = 6;
    drop_nested();
    int ok = !taps[0].phone && taps[1].phone && taps[2].phone && !taps[3].phone && taps[4].phone && !taps[5].phone;
    taps_chosen = 0;
    memset(taps, 0, sizeof(taps));
    return ok;
}

/* audio.c's phone sound, into a mapping named by the test: a stereo bus and a 5.1 one folded down, added up (too loud:
 * clipped), a bus that is only measured left out; the block in the companion's ring once the master bus has passed
 * it on, and the mix empty after; then a block across the ring's end. Left open for the test to read
 * (crtv_test_audio_close). 1 if right. */
__declspec(dllexport) int crtv_test_audio_mix(const wchar_t *mapping) {
    static int dsp[3];
    const char *paths[3] = {"bus:/World/Sfx/Player/CRTV", "bus:/Dialogue/WaypointSignals", "bus:/Dialogue/Character"};
    wcsncpy(audio_mapping_name, mapping, sizeof(audio_mapping_name) / sizeof(audio_mapping_name[0]) - 1);
    audio_rate = 48000;
    if (!open_audio_shared()) return 0;
    memset(taps, 0, sizeof(taps));
    for (int i = 0; i < 3; ++i) {
        snprintf(taps[i].path, sizeof(taps[i].path), "%s", paths[i]);
        taps[i].dsp = &dsp[i];
        taps[i].phone = to_phone(paths[i]);
    }
    atomic_store(&tap_count, 3);
    float stereo[4] = {.25f, -.25f, .9f, .9f};
    float surround[12] = {0, 0, .2f, 0, 0, 0, .1f, .1f, 0, .5f, .4f, 0}; /* FL FR C LFE SL SR, two frames */
    float voice[4] = {.5f, .5f, .5f, .5f}, out[16];
    struct FmodDspState crtv = {&dsp[0]}, signals = {&dsp[1]}, character = {&dsp[2]}, master = {NULL};
    int channels = 2;
    tap_read(&crtv, stereo, out, 2, 2, &channels);
    channels = 6;
    tap_read(&signals, surround, out, 2, 6, &channels);
    channels = 2;
    tap_read(&character, voice, out, 2, 2, &channels);
    channels = 2;
    master_read(&master, voice, out, 2, 2, &channels);
    const int16_t *ring = audio_shared->ring;
    int ok = audio_shared->written == 2 && out[0] == .5f && /* the master's block passed on as it came */
             ring[0] == to_sample(.25f + .70710678f * .2f) && ring[1] == to_sample(-.25f + .70710678f * .2f) &&
             ring[2] == 32767 && ring[3] == 32767;
    master_read(&master, voice, out, 2, 2, &channels);
    ok = ok && audio_shared->written == 4 && !ring[4] && !ring[5] && !ring[6] && !ring[7];
    audio_shared->written = AUDIO_RING_FRAMES - 1;
    tap_read(&crtv, voice, out, 2, 2, &channels);
    master_read(&master, voice, out, 2, 2, &channels);
    return ok && audio_shared->written == AUDIO_RING_FRAMES + 1 && ring[(AUDIO_RING_FRAMES - 1) * 2] == 16384 &&
           ring[0] == 16384 && ring[1] == 16384;
}

/* The game's side of the phone's buses quiet while the phone plays them, their sound still taken for the phone; a bus
 * only measured left alone. Scenario 0 a packet saying so; 1 one saying so, lapsed (the mod stopped saying it);
 * 2 then one saying the sound comes back; 3 a bad one. 1 if right. */
__declspec(dllexport) int crtv_test_audio_mute(unsigned scenario, const wchar_t *path) {
    static int dsp[2];
    struct SoundPacket packet = {{'T', 'F', 'S', 'O', 'U', 'N', 'D', '1'}, 1};
    if (scenario == 3) packet.magic[0] = '?';
    initialized = state = 1;
    close_packet(&sound_file); /* the last test's file */
    swprintf(sound_path, MAX_PATH, L"%ls", path);
    int ok = 1;
    for (int write = 0; write < (scenario == 2 ? 2 : 1) && ok; ++write) {
        if (write) packet.mute = 0;
        HANDLE file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, 0, NULL);
        DWORD written = 0;
        ok = file != INVALID_HANDLE_VALUE && WriteFile(file, &packet, sizeof(packet), &written, NULL) && written == sizeof(packet);
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
        if (ok) tf_native_sound(NULL);
    }
    if (scenario == 1) atomic_store(&audio_mute_until, GetTickCount64() - 1);
    memset(taps, 0, sizeof(taps));
    snprintf(taps[0].path, sizeof(taps[0].path), "bus:/World/Sfx/Player/CRTV");
    snprintf(taps[1].path, sizeof(taps[1].path), "bus:/Dialogue/Character");
    taps[0].dsp = &dsp[0];
    taps[1].dsp = &dsp[1];
    taps[0].phone = 1;
    atomic_store(&tap_count, 2);
    float in[4] = {.5f, .5f, .5f, .5f}, out[4], measured[4];
    struct FmodDspState crtv = {&dsp[0]}, character = {&dsp[1]};
    int channels = 2;
    tap_read(&crtv, in, out, 2, 2, &channels);
    tap_read(&character, in, measured, 2, 2, &channels);
    int quiet = scenario == 0;
    ok = ok && audio_mix[0] == .5f && audio_mixed == 2 && measured[0] == .5f && out[0] == (quiet ? 0 : .5f) &&
         out[3] == (quiet ? 0 : .5f);
    close_packet(&sound_file);
    initialized = state = 0;
    atomic_store(&audio_mute_until, 0);
    atomic_store(&tap_count, 0);
    memset(taps, 0, sizeof(taps));
    memset(audio_mix, 0, sizeof(audio_mix));
    audio_mixed = 0;
    return ok;
}

__declspec(dllexport) void crtv_test_audio_close(void) {
    atomic_store(&tap_count, 0);
    memset(taps, 0, sizeof(taps));
    if (audio_shared) UnmapViewOfFile(audio_shared);
    if (audio_mapping) CloseHandle(audio_mapping);
    audio_shared = NULL;
    audio_mapping = NULL;
}

/* The texture: 16 x 8 RGBA, its left half red and its right half blue, its rows 20 pixels apart in the readback (the
 * other 4 gray, never shown). */
#define FAKE_WIDTH 16
#define FAKE_HEIGHT 8
#define FAKE_PITCH 20
static unsigned char fake_command_list[1256], fake_texture[128], fake_fence[64];
static unsigned char fake_pixels[FAKE_PITCH * FAKE_HEIGHT * 4];

static void fill_fake_pixels(void) {
    for (int y = 0; y < FAKE_HEIGHT; ++y)
        for (int x = 0; x < FAKE_PITCH; ++x) {
            unsigned char *texel = fake_pixels + (y * FAKE_PITCH + x) * 4;
            texel[0] = x >= FAKE_WIDTH ? 99 : x < FAKE_WIDTH / 2 ? 255 : 0;
            texel[1] = x >= FAKE_WIDTH ? 99 : 0;
            texel[2] = x >= FAKE_WIDTH ? 99 : x < FAKE_WIDTH / 2 ? 0 : 255;
            texel[3] = 0; /* the game's alpha: unused */
        }
}
static int refs, transitions, queues, locks, polls, ready, constructors;
static unsigned restored;

static void *fake_retain(void **destination, void *source) {
    if (*destination != source) {
        if (*destination) --refs;
        if (source) ++refs;
        *destination = source;
    }
    return destination;
}
static void fake_ctor(void *object, uint64_t name) {
    ++constructors;
    (void)name;
    uintptr_t fence = (uintptr_t)fake_fence;
    unsigned mask = 1;
    memcpy((unsigned char *)object + READBACK_FENCE_OFFSET, &fence, sizeof(fence));
    memcpy((unsigned char *)object + READBACK_MASK_OFFSET, &mask, sizeof(mask));
}
static void fake_enqueue(void *object, void *command, void *source, const int *position, unsigned slice, const int *size) {
    (void)object; (void)command; (void)source; (void)position; (void)slice; (void)size;
    ++queues;
}
static void fake_transition(void *command, const struct TransitionView *view, unsigned flags) {
    (void)command; (void)flags;
    if (view->count != 1 || view->data->texture != fake_texture || view->data->type != TRANSITION_TEXTURE) return;
    ++transitions;
    restored = view->data->after;
}
static unsigned char fake_poll(void *fence, unsigned mask) {
    (void)fence; (void)mask;
    ++polls;
    return ready != 0;
}
static void *fake_lock(void *object, int *pitch, int *height) {
    (void)object;
    ++locks;
    *pitch = FAKE_PITCH; *height = FAKE_HEIGHT;
    return fake_pixels;
}
static void fake_unlock(void *object) { (void)object; }

/* picture.c's cut and scale. The game's 2048 x 2048 texture: green outside the screen's box, inside it red above
 * and blue below; at 640 wide and at 320 the picture is the box's alone, its proportions kept, red at the top and
 * blue at the bottom. A small texture: taken whole, never made bigger. 1 if right. */
__declspec(dllexport) int crtv_test_picture_scale(void) {
    unsigned char *texture = HeapAlloc(GetProcessHeap(), 0, 2048u * 2048u * 4u);
    if (!texture) return 0;
    for (int y = 0; y < 2048; ++y)
        for (int x = 0; x < 2048; ++x) {
            unsigned char *texel = texture + ((size_t)y * 2048 + x) * 4;
            int inside = x >= SCREEN_BOX[0] && x < SCREEN_BOX[2] && y >= SCREEN_BOX[1] && y < SCREEN_BOX[3];
            int upper = y < (SCREEN_BOX[1] + SCREEN_BOX[3]) / 2;
            texel[0] = inside && upper ? 255 : 0;  /* RGBA */
            texel[1] = inside ? 0 : 255;
            texel[2] = inside && !upper ? 255 : 0;
            texel[3] = 0;
        }
    int ok = 1;
    for (int wanted = 640; wanted >= 320; wanted -= 320) {
        int width = wanted;
        UINT stride = 0;
        int height = scale_screen(texture, 2048, 2048, 0, &width, &stride);
        ok = ok && width == wanted && height == (wanted == 640 ? 434 : 217);
        for (int y = 0; ok && y < height; ++y)
            for (int x = 0; x < width; ++x) {
                const unsigned char *bgr = picture_rows + (size_t)y * stride + x * 3;
                ok = ok && bgr[1] == 0; /* nothing from outside the box */
            }
        const unsigned char *top = picture_rows, *bottom = picture_rows + (size_t)(height - 1) * stride;
        ok = ok && top[2] == 255 && top[0] == 0 && bottom[0] == 255 && bottom[2] == 0;
    }
    HeapFree(GetProcessHeap(), 0, texture);
    fill_fake_pixels();
    int width = 640;
    UINT stride = 0;
    int height = scale_screen(fake_pixels, FAKE_PITCH, FAKE_HEIGHT, 1, &width, &stride); /* BGRA: blue on the left */
    return ok && width == FAKE_PITCH && height == FAKE_HEIGHT && picture_rows[0] == 255 && picture_rows[2] == 0;
}

__declspec(dllexport) int run_capture_test(int scenario, const wchar_t *output) {
    int result = 0;
    memset(fake_command_list, 0, sizeof(fake_command_list));
    memset(fake_texture, 0, sizeof(fake_texture));
    memset(fake_fence, 0, sizeof(fake_fence));
    refs = transitions = queues = locks = polls = ready = constructors = 0;
    atomic_store(&requested_fps, 0); atomic_store(&capture_fault, 0);
    atomic_store(&published_frames, 0);
    frame_sequence = next_capture_at = 0;
    allocated_width = allocated_height = allocated_format = 0;
    readback = NULL; frame_pixels = NULL; retained_source = pending_source = NULL;
    image_base = (unsigned char *)((uintptr_t)fake_command_list - IMMEDIATE_LIST_RVA);
    game_thread_id = GetCurrentThreadId() + 1;
    unsigned value = 1;
    memcpy(fake_command_list + GPU_MASK_OFFSET, &value, 4);
    value = ACCESS_SRV_MASK;
    memcpy(fake_texture + TRACKED_ACCESS_OFFSET, &value, 4);
    retain_texture = fake_retain; readback_ctor = fake_ctor; enqueue_copy = fake_enqueue;
    transition = fake_transition; poll_fence = fake_poll; lock_readback = fake_lock; unlock_readback = fake_unlock;
    /* A phone watching, as in the game: frames go to shared memory (this process's own), and copies start. */
    swprintf(frame_mapping_name, 128, L"Local\\TownfallCRTVTest_%lu", GetCurrentProcessId());
    fill_fake_pixels();
    configure_capture(15, 640);
    publish_capture_source(fake_texture, FAKE_WIDTH, FAKE_HEIGHT, PIXEL_RGBA8);
    if (scenario == 12) atomic_store(&config_tick, GetTickCount64() - 6000);
    if (scenario == 2) memset(fake_texture + TRACKED_ACCESS_OFFSET, 0, 4);
    if (scenario == 3) fake_command_list[INSIDE_PASS_OFFSET] = INSIDE_PASS_MASK;
    if (scenario == 4) source_format = 255;
    if (scenario == 5) fake_command_list[EXECUTING_OFFSET] = 1;
    if (scenario == 6) {
        value = 3;
        memcpy(fake_command_list + GPU_MASK_OFFSET, &value, 4);
    }
    if (scenario == 7) game_thread_id = GetCurrentThreadId();
    capture_at_frame_end(scenario == 10 ? image_base + EXECUTOR_RVA : fake_command_list);
    if (scenario == 2) { result = atomic_load(&capture_state) == -2 && queues == 0 && locks == 0; goto done; }
    if (scenario == 3 || scenario == 5 || scenario == 6 || scenario == 7) {
        result = atomic_load(&capture_state) == 1 && queues == 0 && locks == 0; goto done;
    }
    if (scenario == 4) { result = atomic_load(&capture_state) == -4 && queues == 0 && locks == 0; goto done; }
    if (scenario == 10) {
        result = atomic_load(&capture_state) == 1 && queues == 0 && locks == 0 && atomic_load(&capture_wait_reason) == 1;
        goto done;
    }
    if (scenario == 12) { result = atomic_load(&capture_state) == 0 && queues == 0; goto done; }
    if (queues != 1 || transitions != 2 || restored != ACCESS_SRV_MASK || refs != 2 || atomic_load(&capture_state) != 2) goto done;
    if (scenario == 8) {
        queued_at = GetTickCount64() - 6000;
        capture_at_frame_end(fake_command_list);
        result = atomic_load(&capture_state) == -1 && refs == 2 && locks == 0;
        goto done;
    }
    if (scenario == 9) {
        publish_capture_source(NULL, 0, 0, 0);
        if (refs != 1 || !pending_source) goto done;
    }
    /* An unsubmitted fence must not even be polled, much less mapped. */
    LONG pending = 1;
    memcpy(fake_fence + FENCE_PENDING_OFFSET, &pending, 4);
    capture_at_frame_end(fake_command_list);
    if (polls || locks) goto done;
    pending = 0;
    memcpy(fake_fence + FENCE_PENDING_OFFSET, &pending, 4);
    capture_at_frame_end(fake_command_list);
    if (polls != 1 || locks) goto done;
    ready = 1;
    capture_at_frame_end(fake_command_list);
    ULONGLONG deadline = GetTickCount64() + 3000;
    while (atomic_load(&capture_state) == 3 && GetTickCount64() < deadline) Sleep(1);
    if (atomic_load(&capture_state) == 3) return 0;
    result = atomic_load(&capture_state) == 4 && locks == 1 && refs == (scenario == 9 ? 0 : 1);
    if (scenario == 11 && result) {
        void *first_readback = readback, *first_pixels = frame_pixels;
        next_capture_at = GetTickCount64() + 10000;
        capture_at_frame_end(fake_command_list);
        if (queues != 1) { result = 0; goto done; }
        next_capture_at = 0;
        capture_at_frame_end(fake_command_list);
        if (queues != 2 || constructors != 1 || readback != first_readback || frame_pixels != first_pixels) { result = 0; goto done; }
        capture_at_frame_end(fake_command_list);
        deadline = GetTickCount64() + 3000;
        while (atomic_load(&capture_state) == 3 && GetTickCount64() < deadline) Sleep(1);
        if (atomic_load(&capture_state) == 3) return 0;
        configure_capture(0, 0);
        capture_at_frame_end(fake_command_list);
        result = atomic_load(&capture_state) == 0 && atomic_load(&published_frames) == 2 && queues == 2 && locks == 2;
    }
done:
    /* The picture published, if any, for test_native.py to look at: as the bridge reads it, header and JPEG. */
    if (atomic_load(&published_frames) && shared_frame) {
        struct PictureHeader header;
        memcpy(&header, shared_frame + PICTURE_HEADER_AT, sizeof(header));
        HANDLE file = CreateFileW(output, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        DWORD written;
        if (file == INVALID_HANDLE_VALUE) result = 0;
        else {
            result = result && WriteFile(file, &header, sizeof(header), &written, NULL) &&
                     WriteFile(file, shared_frame + PICTURE_AT, header.bytes, &written, NULL);
            CloseHandle(file);
        }
    }
    atomic_store(&capture_state, 0);
    fake_retain(&pending_source, NULL);
    fake_retain(&retained_source, NULL);
    if (readback) HeapFree(GetProcessHeap(), 0, readback);
    if (frame_pixels && atomic_load(&capture_state) != 3) HeapFree(GetProcessHeap(), 0, frame_pixels);
    frame_pixels = NULL; readback = NULL;
    if (shared_frame) UnmapViewOfFile(shared_frame);
    if (frame_mapping) CloseHandle(frame_mapping);
    shared_frame = NULL; frame_mapping = NULL;
    return result && refs == 0;
}
