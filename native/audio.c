/* The CRTV's sound, live from the game's own FMOD mixer: included by tf_native.c.
 *
 * Townfall mixes its sound with FMOD Studio 2 (Engine/Plugins/FMODStudio: fmod.dll, fmodstudio.dll), whose C API
 * those DLLs export. This file declares the few functions and the one structure it uses itself, from FMOD's public
 * documentation; nothing of FMOD is included or shipped. Every frame the game calls Studio::System::update; hooked,
 * that hands over the game's Studio system. Then the mixer's buses are listed (townfall-companion-native-buses.txt,
 * for the log; again every few seconds until the CRTV's turns up), the ones that carry the CRTV and the voices heard
 * over it are kept alive (lockChannelGroup), and a DSP of the DLL's own sits at the head of each: FMOD's mixer
 * thread hands it every block of the bus's output, which it passes on unchanged, measures, and adds to the phone's
 * sound. One more DSP, at the head of the master bus, sees each block after every bus in it: the phone's sound of
 * that block is complete then, and goes to shared memory for the companion (game_audio.py). While the phone plays
 * it, the buses it takes go quiet in the game, right after their sound is taken (tf_native_sound). Without the FMOD
 * DLLs, with another FMOD than 2.x, or when a call fails, the game's sound stays as it is. */

typedef int FmodResult;                 /* FMOD_RESULT: 0 is FMOD_OK */
#define FMOD_OK 0
#define FMOD_PLUGIN_SDK_VERSION 110     /* FMOD 2's plugin SDK version, as FMOD_DSP_DESCRIPTION wants it */
#define FMOD_DSP_HEAD (-1)              /* FMOD_CHANNELCONTROL_DSP_HEAD: after the bus's fader and effects */
#define AUDIO_TAPS 8
#define AUDIO_RETRY_FRAMES 120          /* the buses are listed again this often until the CRTV's turns up */
#define AUDIO_BLOCK_FRAMES 8192         /* the longest mixer block taken whole; FMOD's are 512 to 2048 frames */
#define AUDIO_RING_FRAMES 65536         /* 1.4 s at 48 kHz: the companion takes what is new every few ms */
#define AUDIO_MUTE_HOLD_MS 6000         /* the game's sound quiet this long after the mod last said so */

/* FMOD_DSP_STATE: only its first member is read, the DSP it belongs to. */
struct FmodDspState { void *instance; };
typedef FmodResult (*FmodDspRead)(struct FmodDspState *state, float *in, float *out, unsigned length, int inchannels,
                                  int *outchannels);
/* FMOD_DSP_DESCRIPTION as FMOD 2 lays it out. */
struct FmodDspDescription {
    unsigned pluginsdkversion;
    char name[32];
    unsigned version;
    int numinputbuffers, numoutputbuffers;
    void *create, *release, *reset;
    FmodDspRead read;
    void *process, *setposition;
    int numparameters;
    void **paramdesc;
    void *setparameterfloat, *setparameterint, *setparameterbool, *setparameterdata;
    void *getparameterfloat, *getparameterint, *getparameterbool, *getparameterdata;
    void *shouldiprocess, *userdata, *sys_register, *sys_deregister, *sys_mix;
};
_Static_assert(sizeof(struct FmodDspDescription) == 216, "FMOD_DSP_DESCRIPTION layout (FMOD 2, 64-bit)");

static struct {
    FmodResult (*get_core_system)(void *system, void **core);
    FmodResult (*get_bank_list)(void *system, void **banks, int capacity, int *count);
    FmodResult (*get_bus_list)(void *bank, void **buses, int capacity, int *count);
    FmodResult (*get_bus_path)(void *bus, char *path, int size, int *retrieved);
    FmodResult (*lock_channel_group)(void *bus);
    FmodResult (*get_channel_group)(void *bus, void **group);
    FmodResult (*create_dsp)(void *core, const struct FmodDspDescription *description, void **dsp);
    FmodResult (*add_dsp)(void *group, int index, void *dsp);
    FmodResult (*get_version)(void *core, unsigned *version, unsigned *build);
    FmodResult (*get_software_format)(void *core, int *rate, int *speaker_mode, int *raw_speakers);
    FmodResult (*get_master_group)(void *core, void **group);
} fmod_c;

/* A bus the DLL listens to: its DSP, whether it goes into the phone's sound, and what has come through since the DLL
 * started (written by FMOD's mixer thread only; read by the game thread, which takes the difference from the last
 * time). */
struct AudioTap {
    char path[96];
    void *bus, *dsp;
    int phone;
    volatile double energy;    /* the sum of every sample squared */
    volatile uint64_t samples;
    volatile float peak;       /* the loudest sample since the last report (the report sets it back) */
};
static struct AudioTap taps[AUDIO_TAPS];
static _Atomic int tap_count;            /* taps with a DSP, the mixer thread's to look through */
static int taps_chosen;                  /* taps chosen and locked (game thread) */
static void *master_dsp;                 /* the DSP on the master bus, once there */
/* 0 the game hasn't loaded FMOD yet; 1 waiting for the CRTV's bus; 2 attaching; 3 listening. Why it is off: -1 an FMOD
 * function is missing, -2 not FMOD 2, -3 no DSP could be made, -4 one couldn't be attached, -5 no hook on the update,
 * -6 no shared memory for the companion. */
static int audio_state;
static int audio_rate;                   /* the mixer's sample rate */
static unsigned audio_frames;
static wchar_t buses_path[MAX_PATH];
typedef FmodResult (*StudioUpdateFn)(void *system);
static StudioUpdateFn original_studio_update;

/* The phone's sound in shared memory: the tapped buses mixed to stereo, 16-bit, in a ring the mixer thread writes
 * and the companion reads behind it. `written` counts the frames since the DLL started, and is raised after the
 * frames it counts are in. */
struct AudioShared {
    char magic[8];                       /* "TFAUDIO1" */
    uint32_t rate, channels, capacity, header_size;
    volatile int64_t written;
    char reserved[32];
    int16_t ring[];                      /* capacity frames of `channels` samples */
};
_Static_assert(sizeof(struct AudioShared) == 64, "the companion's view of the sound (game_audio.py)");
static wchar_t audio_mapping_name[96] = L"Local\\TownfallCompanionAudio";
static HANDLE audio_mapping;
static struct AudioShared *audio_shared;
static float audio_mix[AUDIO_BLOCK_FRAMES * 2]; /* the block being mixed (mixer thread) */
static unsigned audio_mixed;                    /* frames of it that have sound added */
/* The phone plays the CRTV's sound: the game's is quiet until then (GetTickCount64), unless the mod says so again. */
static _Atomic unsigned long long audio_mute_until;
static wchar_t sound_path[MAX_PATH];
static HANDLE sound_file = INVALID_HANDLE_VALUE;
/* Written by tf_native.lua: whether the game's CRTV sound goes quiet (1) or comes back (0). */
struct SoundPacket { char magic[8]; uint64_t mute; };
_Static_assert(sizeof(struct SoundPacket) == 16, "Lua sound packet ABI");

/* The buses that carry the CRTV and the voices heard over it, as Townfall's mixer names them, most wanted first. `phone`: mixed into the phone's sound. The CRTV's own voices are in the CRTV's bus
 * already (they are its children), and the character's own voice, last, is only measured, to compare levels with. */
static const struct { const char *path; int phone; } TAP_PATHS[] = {
    {"bus:/World/Sfx/Player/CRTV", 1}, {"bus:/Dialogue/WaypointSignals", 1}, {"bus:/Dialogue/Transmissions", 1},
    {"bus:/Cinematics/Sequences/CRTV_Videos", 1}, {"bus:/World/Sfx/Player/CRTV/CRTVActive", 0},
    {"bus:/World/Sfx/Player/CRTV/CRTVEnemyVoice", 0}, {"bus:/World/Sfx/Player/CRTV/CRTVEnemyVoiceNoise", 0},
    {"bus:/Dialogue/Character", 0},
};
#define TAP_PATH_COUNT (sizeof(TAP_PATHS) / sizeof(TAP_PATHS[0]))

/* Whether `path` names a CRTV bus, whatever its case. */
static int is_crtv(const char *path) {
    char lower[96];
    size_t i = 0;
    for (; path[i] && i < sizeof(lower) - 1; ++i) lower[i] = (char)(path[i] >= 'A' && path[i] <= 'Z' ? path[i] + 32 : path[i]);
    lower[i] = 0;
    return strstr(lower, "crtv") != NULL;
}

/* How much a bus matters to the phone: the named ones in their order, then (for a game whose mixer has changed) any
 * other CRTV bus; 0 not at all. */
static int tap_rank(const char *path) {
    for (size_t i = 0; i < TAP_PATH_COUNT; ++i)
        if (!strcmp(path, TAP_PATHS[i].path)) return (int)(TAP_PATH_COUNT - i) + 1;
    return is_crtv(path);
}

/* Whether a chosen bus goes into the phone's sound: a named one as listed, another CRTV bus (a game whose mixer has
 * changed) too, unless a bus above it goes in (its sound is in that one already); see drop_nested. */
static int to_phone(const char *path) {
    for (size_t i = 0; i < TAP_PATH_COUNT; ++i)
        if (!strcmp(path, TAP_PATHS[i].path)) return TAP_PATHS[i].phone;
    return 1;
}

static void drop_nested(void) {
    for (int i = 0; i < taps_chosen; ++i)
        for (int j = 0; j < taps_chosen; ++j) {
            size_t length = strlen(taps[j].path);
            if (i != j && taps[j].phone && !strncmp(taps[i].path, taps[j].path, length) && taps[i].path[length] == '/')
                taps[i].phone = 0;
        }
}

/* Adds a block of a bus's sound to the phone's, folded down to stereo: front left and right as they are, the centre
 * and the surrounds into both at -3 dB, the bass channel left out (FMOD's order: FL FR C LFE SL SR BL BR; quad is
 * FL FR SL SR, 5.0 FL FR C SL SR). */
static void mix_in(const float *in, int channels, unsigned length) {
    const float half = 0.70710678f;
    if (channels <= 0) return;
    if (length > AUDIO_BLOCK_FRAMES) length = AUDIO_BLOCK_FRAMES;
    for (unsigned frame = 0; frame < length; ++frame) {
        const float *s = in + (size_t)frame * channels;
        float left, right;
        if (channels == 1) left = right = s[0];
        else if (channels == 4) { left = s[0] + half * s[2]; right = s[1] + half * s[3]; }
        else if (channels == 5) { left = s[0] + half * (s[2] + s[3]); right = s[1] + half * (s[2] + s[4]); }
        else if (channels >= 6) {
            left = s[0] + half * (s[2] + s[4]);
            right = s[1] + half * (s[2] + s[5]);
            if (channels >= 8) { left += half * s[6]; right += half * s[7]; }
        } else { left = s[0]; right = s[1]; }
        audio_mix[frame * 2] += left;
        audio_mix[frame * 2 + 1] += right;
    }
    if (length > audio_mixed) audio_mixed = length;
}

static int16_t to_sample(float value) {
    if (value >= 1) return 32767;
    if (value <= -1) return -32767;
    return (int16_t)(value * 32767.0f + (value >= 0 ? 0.5f : -0.5f));
}

/* The block's phone sound to the ring, `length` frames of it (silence where no bus added any), and the mix cleared
 * for the next block. */
static void publish_mix(unsigned length) {
    if (length > AUDIO_BLOCK_FRAMES) length = AUDIO_BLOCK_FRAMES;
    struct AudioShared *shared = audio_shared;
    if (shared) {
        int64_t at = shared->written;
        for (unsigned frame = 0; frame < length; ++frame) {
            size_t slot = (size_t)((uint64_t)(at + frame) % shared->capacity) * 2;
            shared->ring[slot] = to_sample(audio_mix[frame * 2]);
            shared->ring[slot + 1] = to_sample(audio_mix[frame * 2 + 1]);
        }
        InterlockedExchange64((volatile LONG64 *)&shared->written, at + length); /* after the frames it counts */
    }
    memset(audio_mix, 0, sizeof(float) * 2 * (audio_mixed > length ? audio_mixed : length));
    audio_mixed = 0;
}

/* A block passed on as it came: FMOD may want more channels out than came in (the rest silent). */
static void pass_on(const float *in, float *out, unsigned length, int inchannels, int *outchannels) {
    int channels = *outchannels > 0 ? *outchannels : inchannels;
    for (unsigned frame = 0; frame < length; ++frame)
        for (int c = 0; c < channels; ++c) out[frame * channels + c] = c < inchannels ? in[frame * inchannels + c] : 0;
    *outchannels = channels;
}

/* One block of a tapped bus's output, on FMOD's mixer thread: passed on as it came, measured, and added to the
 * phone's sound; while the phone plays it, silence goes on to the game instead. */
static FmodResult tap_read(struct FmodDspState *dsp_state, float *in, float *out, unsigned length, int inchannels,
                           int *outchannels) {
    int count = atomic_load(&tap_count);
    struct AudioTap *tap = NULL;
    for (int i = 0; i < count; ++i)
        if (taps[i].dsp == dsp_state->instance) tap = &taps[i];
    pass_on(in, out, length, inchannels, outchannels);
    if (!tap) return FMOD_OK;
    double energy = 0;
    float peak = 0;
    for (unsigned i = 0; i < length * (unsigned)*outchannels; ++i) {
        energy += (double)out[i] * out[i];
        if (fabsf(out[i]) > peak) peak = fabsf(out[i]);
    }
    tap->energy += energy;
    tap->samples += (uint64_t)length * *outchannels;
    if (peak > tap->peak) tap->peak = peak;
    if (!tap->phone) return FMOD_OK;
    mix_in(in, inchannels, length);
    if (GetTickCount64() < atomic_load(&audio_mute_until)) memset(out, 0, sizeof(float) * length * (unsigned)*outchannels);
    return FMOD_OK;
}

/* One block of the master bus, the game's whole sound, after every bus in it (FMOD's mixer thread): passed on as it
 * came. The phone's sound of the block is complete now. */
static FmodResult master_read(struct FmodDspState *dsp_state, float *in, float *out, unsigned length, int inchannels,
                              int *outchannels) {
    (void)dsp_state;
    pass_on(in, out, length, inchannels, outchannels);
    publish_mix(length);
    return FMOD_OK;
}

static const struct FmodDspDescription tap_description = {
    .pluginsdkversion = FMOD_PLUGIN_SDK_VERSION, .name = "Townfall Companion tap", .version = 1,
    .numinputbuffers = 1, .numoutputbuffers = 1, .read = tap_read,
};
static const struct FmodDspDescription master_description = {
    .pluginsdkversion = FMOD_PLUGIN_SDK_VERSION, .name = "Townfall Companion mix", .version = 1,
    .numinputbuffers = 1, .numoutputbuffers = 1, .read = master_read,
};

/* The shared memory the phone's sound goes to, made empty for the companion: 1 if it is there. */
static int open_audio_shared(void) {
    if (audio_shared) return 1;
    size_t size = sizeof(struct AudioShared) + sizeof(int16_t) * 2 * AUDIO_RING_FRAMES;
    audio_mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, (DWORD)size, audio_mapping_name);
    struct AudioShared *shared = audio_mapping ? MapViewOfFile(audio_mapping, FILE_MAP_WRITE, 0, 0, size) : NULL;
    if (!shared) {
        if (audio_mapping) CloseHandle(audio_mapping);
        audio_mapping = NULL;
        return 0;
    }
    memset(shared, 0, sizeof(*shared)); /* from a companion that kept it open over a game before: started anew */
    shared->rate = (uint32_t)audio_rate;
    shared->channels = 2;
    shared->capacity = AUDIO_RING_FRAMES;
    shared->header_size = sizeof(*shared);
    MemoryBarrier();
    memcpy(shared->magic, "TFAUDIO1", 8); /* last: the companion reads nothing before it */
    audio_shared = shared;
    return 1;
}

/* The buses of every loaded bank, each once. */
#define AUDIO_BUSES 256
static struct { void *bus; char path[96]; int rank, tapped; } buses[AUDIO_BUSES];
static int bus_count;

static void list_buses(void *system) {
    void *banks[64], *listed[AUDIO_BUSES];
    int bank_count = 0;
    bus_count = 0;
    if (fmod_c.get_bank_list(system, banks, 64, &bank_count) != FMOD_OK) return;
    for (int b = 0; b < bank_count; ++b) {
        int count = 0;
        if (fmod_c.get_bus_list(banks[b], listed, AUDIO_BUSES, &count) != FMOD_OK) continue;
        for (int i = 0; i < count && bus_count < AUDIO_BUSES; ++i) {
            int known = 0;
            for (int j = 0; j < bus_count; ++j) known |= buses[j].bus == listed[i];
            int retrieved = 0;
            if (known || fmod_c.get_bus_path(listed[i], buses[bus_count].path, sizeof(buses[0].path), &retrieved) != FMOD_OK)
                continue;
            buses[bus_count].bus = listed[i];
            buses[bus_count].rank = tap_rank(buses[bus_count].path);
            buses[bus_count++].tapped = 0;
        }
    }
}

/* Every bus written to the buses file (one path a line, the tapped ones marked), and, once the CRTV's bus is
 * loaded, the most relevant chosen as taps, their channel groups kept alive. 1 if chosen. */
static int choose_taps(void *system) {
    list_buses(system);
    int crtv = 0;
    for (int i = 0; i < bus_count; ++i) crtv |= is_crtv(buses[i].path);
    for (int rank = (int)TAP_PATH_COUNT + 1; crtv && rank >= 1; --rank) {
        for (int i = 0; i < bus_count && taps_chosen < AUDIO_TAPS; ++i) {
            if (buses[i].rank != rank || fmod_c.lock_channel_group(buses[i].bus) != FMOD_OK) continue;
            snprintf(taps[taps_chosen].path, sizeof(taps[0].path), "%s", buses[i].path);
            taps[taps_chosen].phone = to_phone(buses[i].path);
            taps[taps_chosen++].bus = buses[i].bus;
            buses[i].tapped = 1;
        }
    }
    drop_nested();
    static int listed = -1; /* buses in the file: rewritten only when that changes, or with the taps marked */
    if (bus_count == listed && !crtv) return 0;
    listed = bus_count;
    HANDLE file = CreateFileW(buses_path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    for (int i = 0; file != INVALID_HANDLE_VALUE && i < bus_count; ++i) {
        char line[128];
        int length = snprintf(line, sizeof(line), "%s%s\n", buses[i].path, buses[i].tapped ? " (tapped)" : "");
        DWORD written;
        if (length > 0) WriteFile(file, line, (DWORD)length, &written, NULL);
    }
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    return crtv;
}

/* Puts a DSP on each chosen bus whose channel group FMOD has made by now, then the one on the master bus. 1 once all
 * are there. */
static int attach_taps(void *core) {
    for (int i = atomic_load(&tap_count); i < taps_chosen; ++i) {
        void *group = NULL, *dsp = NULL;
        if (fmod_c.get_channel_group(taps[i].bus, &group) != FMOD_OK || !group) return 0;
        if (fmod_c.create_dsp(core, &tap_description, &dsp) != FMOD_OK) { audio_state = -3; return 0; }
        taps[i].dsp = dsp;
        atomic_store(&tap_count, i + 1); /* the mixer may run it as soon as it is added */
        if (fmod_c.add_dsp(group, FMOD_DSP_HEAD, dsp) != FMOD_OK) { audio_state = -4; return 0; }
    }
    if (master_dsp) return 1;
    void *master = NULL;
    if (fmod_c.get_master_group(core, &master) != FMOD_OK || !master) return 0;
    if (fmod_c.create_dsp(core, &master_description, &master_dsp) != FMOD_OK) { audio_state = -3; return 0; }
    if (fmod_c.add_dsp(master, FMOD_DSP_HEAD, master_dsp) != FMOD_OK) { audio_state = -4; return 0; }
    return 1;
}

/* Studio::System::update, the game's every frame (game thread): the FMOD work above, a step at a time. */
static FmodResult audio_studio_update(void *system) {
    FmodResult result = original_studio_update(system);
    if (audio_state <= 0 || audio_state == 3) return result;
    void *core = NULL;
    if (fmod_c.get_core_system(system, &core) != FMOD_OK || !core) return result;
    if (audio_state == 1) {
        unsigned version = 0, build = 0;
        if (fmod_c.get_version(core, &version, &build) != FMOD_OK || version >> 16 != 2) { audio_state = -2; return result; }
        if (!audio_rate) fmod_c.get_software_format(core, &audio_rate, &(int){0}, &(int){0});
        if (audio_frames++ % AUDIO_RETRY_FRAMES == 0 && choose_taps(system)) audio_state = open_audio_shared() ? 2 : -6;
        return result;
    }
    if (attach_taps(core) && audio_state == 2) audio_state = 3;
    return result;
}

/* The levels of the tapped buses since the last call (0..1) as "name rms/peak, ..." for the status. */
static void audio_levels(char *out, size_t size) {
    static double energy[AUDIO_TAPS];
    static uint64_t samples[AUDIO_TAPS];
    size_t used = 0;
    out[0] = 0;
    for (int i = 0; i < atomic_load(&tap_count) && used < size; ++i) {
        double e = taps[i].energy;
        uint64_t n = taps[i].samples;
        double rms = n > samples[i] ? sqrt((e - energy[i]) / (double)(n - samples[i])) : 0;
        energy[i] = e;
        samples[i] = n;
        float peak = taps[i].peak;
        taps[i].peak = 0;
        const char *name = strrchr(taps[i].path, '/');
        int length = snprintf(out + used, size - used, "%s%s %.4f/%.4f", used ? ", " : "", name ? name + 1 : taps[i].path,
                              rms, peak);
        if (length > 0) used += (size_t)length;
    }
}

/* Frames of the phone's sound handed to the companion so far, for the status. */
static long long audio_shared_frames(void) {
    return audio_shared ? (long long)audio_shared->written : 0;
}

static void initialize_audio(const wchar_t *temp) {
    swprintf(buses_path, MAX_PATH, L"%lstownfall-companion-native-buses.txt", temp);
    swprintf(sound_path, MAX_PATH, L"%lstownfall-companion-native-sound.bin", temp);
}

/* Right after tf_native.lua wrote a sound packet, on the game thread: the game's CRTV sound goes quiet for
 * AUDIO_MUTE_HOLD_MS (the mod says it again every couple of seconds while the phone plays it, and the game's comes
 * back if it stops), or comes back now. */
__declspec(dllexport) int tf_native_sound(void *lua_state) {
    (void)lua_state;
    struct SoundPacket packet = {0};
    if (!initialized || state < 0 || !read_packet(&sound_file, sound_path, &packet, sizeof(packet)) ||
        memcmp(packet.magic, "TFSOUND1", 8) || packet.mute > 1) return 0;
    atomic_store(&audio_mute_until, packet.mute ? GetTickCount64() + AUDIO_MUTE_HOLD_MS : 0);
    return 0;
}

/* After MH_Initialize, and again (tf_native_update) until the game has loaded its FMOD plugin: finds FMOD's
 * functions in its DLLs and hooks the Studio system's update. */
static void install_audio(void) {
    HMODULE core = GetModuleHandleW(L"fmod.dll"), studio = GetModuleHandleW(L"fmodstudio.dll");
    if (!core || !studio) return;
    struct { HMODULE module; const char *name; void **function; } imports[] = {
        {studio, "FMOD_Studio_System_GetCoreSystem", (void **)&fmod_c.get_core_system},
        {studio, "FMOD_Studio_System_GetBankList", (void **)&fmod_c.get_bank_list},
        {studio, "FMOD_Studio_Bank_GetBusList", (void **)&fmod_c.get_bus_list},
        {studio, "FMOD_Studio_Bus_GetPath", (void **)&fmod_c.get_bus_path},
        {studio, "FMOD_Studio_Bus_LockChannelGroup", (void **)&fmod_c.lock_channel_group},
        {studio, "FMOD_Studio_Bus_GetChannelGroup", (void **)&fmod_c.get_channel_group},
        {core, "FMOD_System_CreateDSP", (void **)&fmod_c.create_dsp},
        {core, "FMOD_ChannelGroup_AddDSP", (void **)&fmod_c.add_dsp},
        {core, "FMOD_System_GetVersion", (void **)&fmod_c.get_version},
        {core, "FMOD_System_GetSoftwareFormat", (void **)&fmod_c.get_software_format},
        {core, "FMOD_System_GetMasterChannelGroup", (void **)&fmod_c.get_master_group},
    };
    for (size_t i = 0; i < sizeof(imports) / sizeof(imports[0]); ++i) {
        *imports[i].function = (void *)GetProcAddress(imports[i].module, imports[i].name);
        if (!*imports[i].function) { audio_state = -1; return; }
    }
    void *update = (void *)GetProcAddress(studio, "?update@System@Studio@FMOD@@QEAA?AW4FMOD_RESULT@@XZ");
    if (!update || MH_CreateHook(update, audio_studio_update, (void **)&original_studio_update) != MH_OK ||
        MH_EnableHook(update) != MH_OK) { audio_state = -5; return; }
    audio_state = 1;
}
