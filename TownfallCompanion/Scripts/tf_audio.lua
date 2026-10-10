-- The CRTV's sound in the game, silenced while the phone plays it (the phone's "Silence it in the game"). The phone
-- asks again every couple of seconds; once it stops asking (closed, out of Wi-Fi) the game's sound comes back.
--
-- tf_native.dll does the silencing, in the game's FMOD mixer (native/audio.c): the buses the phone's sound comes from
-- (the CRTV and the voices heard through it, the waypoints' and transmissions' talking, the CRTV's videos) go quiet
-- in the game right after the DLL took their sound for the phone, so the phone plays what the game would have.
-- Everything else stays in the game: the character's own voice, the story's cutscenes, the world. The DLL lets the
-- game's sound back by itself 6 s after the mod last said otherwise.

local common = require("tf_common")
local native = require("tf_native")

local HOLD_S = 5  -- the phone's request lasts this long
local RESAY_S = 2 -- the DLL is told again this often while it lasts

local M = {}

local wanted, askedAt, off, saidAt = false, -math.huge, false, -math.huge

-- A request from the phone: true silences the game's CRTV sound for HOLD_S, false brings it back.
function M.request(muteGame)
    wanted, askedAt = muteGame, os.clock()
end

function M.isOff()
    return off
end

-- A few times a second: the phone's request carried out, and the game's sound back once the phone stops asking.
function M.update()
    local keep = wanted and os.clock() - askedAt < HOLD_S
    if keep and (not off or os.clock() - saidAt >= RESAY_S) then
        native.sound(true)
        saidAt = os.clock()
        if not off then common.log("TF-AUDIO", "game CRTV sound off, the phone plays it") end
        off = true
    elseif off and not keep then
        native.sound(false)
        off = false
        common.log("TF-AUDIO", "game CRTV sound back on%s", wanted and " (the phone stopped asking)" or "")
    end
end

return M
