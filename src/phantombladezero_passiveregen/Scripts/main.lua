-- Passive Regen for Phantom Blade Zero.
--
-- The player's health regenerates by itself: PercentPerTick of the current maximum health (Health_Max) every
-- STEP_MS (2 seconds; the user's choice after 1.0, which healed every second). InCombat = false limits it to the time
-- out of combat (the player's tag CharacterState.InCombat). No regen while the game is paused, while the player is dead
-- (health 0 or the tag CharacterState.Dead), or at full health.
--
-- How: every STEP_MS on the game thread. The amount follows the real time since the last step (os.clock), at most
-- MAX_STEP_SECONDS (two steps), so a hitch or a loading screen gives no burst. The heal goes through the game's cheat
-- command
-- UPBZCheatManager:GM_SetHealth (a number only, so it is safe to call): the health bar shows it at once. The cheat
-- manager exists when UE4SS's CheatManagerEnablerMod made it (a new one after each map load or respawn, so it is read
-- each time). Without it, or when the command did not reach the value, the mod writes the player's Health (BaseValue
-- and CurrentValue); the bar then shows it at the game's next health change. The same way as Live by the Blade.
--
-- Settings: the player edits Scripts\config.lua. The mod reads it at start and again every CONFIG_INTERVAL_MS, so a
-- change shows without a restart; a broken or half-saved file keeps the last settings. DEFAULTS holds every setting
-- with its default. A player (or a mod manager) keeps their config.lua across updates, so a new main.lua can meet an
-- older file: a setting that the file lacks, or has with another type, keeps its default, and the log says so. Read a
-- setting only from Settings, never as `config.X or default`: for a true/false setting, `false or true` is true.

local UEHelpers = require("UEHelpers")

local MOD_NAME = "Passive Regen"
local VERSION = "1.1"
local STEP_MS = 2000
local CONFIG_INTERVAL_MS = 5000
local MAX_STEP_SECONDS = 4
local COMBAT_TAG = "CharacterState.InCombat"
local DEAD_TAG = "CharacterState.Dead"
-- Log at most this many error lines in total.
local MAX_ERROR_LOGS = 5
-- LogDetail: at most this many lines per game start, and a heal line on every n-th heal.
local MAX_DETAIL_LOGS = 400
local HEAL_LOG_EVERY = 10

-- Every setting with its default; config.lua ships the same names and values (tests\test_main.py checks it).
local DEFAULTS = {
    Enable = true,
    PercentPerTick = 1.5,
    InCombat = true,
    LogDetail = false,
}
-- Number settings outside their range are clamped.
local LIMITS = {
    PercentPerTick = { 0.1, 25 },
}

local COMBAT_FNAME = FName(COMBAT_TAG, EFindName.FNAME_Add)
local DEAD_FNAME = FName(DEAD_TAG, EFindName.FNAME_Add)

local errors = 0
local details = 0

local function Log(message)
    print(string.format("[%s] %s\n", MOD_NAME, message))
end

local function LogError(message)
    errors = errors + 1
    if errors <= MAX_ERROR_LOGS then
        Log(message)
    end
end

local Settings = {}

-- LogDetail lines; message is a function, so nothing is built while LogDetail is off.
local function Detail(message)
    if Settings.LogDetail and details < MAX_DETAIL_LOGS then
        details = details + 1
        Log(message())
    end
end

local function Valid(object)
    if object == nil then
        return false
    end
    local ok, valid = pcall(function()
        return object:IsValid()
    end)
    return ok and valid == true
end

---------------------------------------------------------------------------------------------------------------------
-- Settings
---------------------------------------------------------------------------------------------------------------------

local function CopyDefaults()
    local settings = {}
    for name, value in pairs(DEFAULTS) do
        settings[name] = value
    end
    return settings
end

-- One line for the log: every setting, sorted by name.
local function Describe(settings)
    local names = {}
    for name in pairs(settings) do
        names[#names + 1] = name
    end
    table.sort(names)
    local parts = {}
    for i, name in ipairs(names) do
        parts[i] = name .. "=" .. tostring(settings[name])
    end
    return table.concat(parts, ", ")
end

-- Lua's line number and reason, without the chunk name.
local function Reason(message)
    message = tostring(message)
    local line, reason = message:match("config%.lua:(%d+): ([^\n]*)")
    return line and ("line " .. line .. ": " .. reason) or (message:gsub("%s*\n%s*", " "))
end

-- DEFAULTS with the values of config.lua's text over them; nil and the reason when the text is not a usable file.
local function ParseConfig(text)
    local chunk, problem = load(text, "=config.lua", "t", {})
    if not chunk then
        return nil, "config.lua not read (" .. Reason(problem) .. ")"
    end
    local ok, config = pcall(chunk)
    if not ok then
        return nil, "config.lua not read (" .. Reason(config) .. ")"
    end
    if type(config) ~= "table" then
        return nil, "config.lua does not return a table"
    end
    local settings = CopyDefaults()
    for name, value in pairs(config) do
        local default = DEFAULTS[name]
        local limit = LIMITS[name]
        if default == nil then
            Log("config.lua: unknown setting " .. tostring(name) .. " ignored.")
        elseif type(value) ~= type(default) then
            Log(string.format("config.lua: %s must be a %s; the default %s is used.", name, type(default),
                tostring(default)))
        elseif limit and value ~= value then
            Log(string.format("config.lua: %s is not a number; the default %s is used.", name, tostring(default)))
        elseif limit and (value < limit[1] or value > limit[2]) then
            local clamped = math.min(limit[2], math.max(limit[1], value))
            Log(string.format("config.lua: %s = %s is outside %s to %s; %s is used.", name, tostring(value),
                tostring(limit[1]), tostring(limit[2]), tostring(clamped)))
            settings[name] = clamped
        else
            settings[name] = value
        end
    end
    return settings
end

local configPath = nil
-- The text of the last read; false: no file at the last read; nil: not read yet.
local configText = nil

-- Reads config.lua when its text changed since the last read. At start, a missing or broken file means the defaults;
-- later, a broken file keeps the last settings (a half-saved file), and a missing one means the defaults.
local function ReadConfig()
    local atStart = configText == nil
    configPath = configPath or package.searchpath("config", package.path)
    local text = false
    if configPath then
        local file = io.open(configPath, "rb")
        if file then
            text = file:read("a") or false
            file:close()
        end
    end
    if text == configText then
        return
    end
    configText = text
    local settings, problem
    if text == false then
        settings, problem = CopyDefaults(), nil
        Log("No config.lua; every setting uses its default.")
    else
        settings, problem = ParseConfig(text)
    end
    if not settings then
        if atStart then
            Log(problem .. "; every setting uses its default.")
            settings = CopyDefaults()
        else
            Log(problem .. "; the last settings stay.")
            return
        end
    end
    local line = Describe(settings)
    if atStart or line ~= Describe(Settings) then
        Log("Settings: " .. line)
    end
    Settings = settings
end

---------------------------------------------------------------------------------------------------------------------
-- The regen
---------------------------------------------------------------------------------------------------------------------

local controller = nil
local lastStep = nil
local state = nil            -- LogDetail: why the last step healed or not, to log each change once
local heals = 0
local cheatMissingLogged = false

local function Health(pawn)
    local set = pawn.AttributeSetBase
    return set.Health.CurrentValue, set.Health_Max.CurrentValue
end

local function HasTag(pawn, name)
    local abilities = pawn.AbilitySystemComponent
    return Valid(abilities) and abilities:GetGameplayTagCount({ TagName = name }) > 0
end

-- GM_SetHealth of the game's cheat manager: true when it ran.
local function SetHealthByCheat(target)
    local cheat = controller.CheatManager
    if not Valid(cheat) then
        if not cheatMissingLogged then
            cheatMissingLogged = true
            Log("No cheat manager (UE4SS's CheatManagerEnablerMod is off?): the regen is written directly; the health "
                .. "bar shows it at the game's next health change.")
        end
        return false
    end
    local ok, err = pcall(function()
        cheat:GM_SetHealth(target)
    end)
    if not ok then
        LogError("GM_SetHealth failed (" .. tostring(err) .. "); the regen is written directly.")
        return false
    end
    return true
end

local function WriteHealth(pawn, target)
    local data = pawn.AttributeSetBase.Health
    data.BaseValue = target
    data.CurrentValue = target
end

-- Logs the reason when it changed since the last step (LogDetail).
local function Note(reason)
    if reason ~= state then
        state = reason
        Detail(function() return "regen " .. reason end)
    end
end

-- One step: the regen for the real time since the last step.
local function Step()
    local now = os.clock()
    local elapsed = lastStep and math.min(now - lastStep, MAX_STEP_SECONDS) or STEP_MS / 1000
    lastStep = now
    if not Settings.Enable then
        Note("off (Enable = false)")
        return
    end
    if not Valid(controller) then
        controller = UEHelpers.GetPlayerController()
        if not Valid(controller) then
            return
        end
    end
    local pawn = controller.Pawn
    if not Valid(pawn) then
        Note("waits: no player")
        return
    end
    if UEHelpers.GetGameplayStatics():IsGamePaused(controller) then
        Note("waits: the game is paused")
        return
    end
    local health, max = Health(pawn)
    if type(health) ~= "number" or type(max) ~= "number" or max <= 0 then
        Note("waits: no health values")
        return
    end
    if health <= 0 or HasTag(pawn, DEAD_FNAME) then
        Note("waits: dead")
        return
    end
    local inCombat = HasTag(pawn, COMBAT_FNAME)
    if inCombat and not Settings.InCombat then
        Note("waits: in combat (InCombat = false)")
        return
    end
    if health >= max - 0.001 then
        Note("waits: full health")
        return
    end
    -- PercentPerTick for a full step; a shorter or longer step (a late loop) gets its share of it.
    local target = math.min(max, health + max * Settings.PercentPerTick / 100 * elapsed / (STEP_MS / 1000))
    local way = "GM_SetHealth"
    if not SetHealthByCheat(target) then
        way = "direct write"
        WriteHealth(pawn, target)
    else
        local after = Health(pawn)
        if type(after) == "number" and math.abs(after - target) > 0.5 then
            -- The cheat command did not reach the value: write it.
            way = string.format("GM_SetHealth left %.1f, then direct write", after)
            WriteHealth(pawn, target)
        end
    end
    heals = heals + 1
    Note(inCombat and "on (in combat)" or "on (out of combat)")
    if heals == 1 or heals % HEAL_LOG_EVERY == 0 then
        Detail(function() return string.format("heal %d: health %.1f -> %.1f of %.1f (+%.2f in %.2f s) via %s", heals,
            health, target, max, target - health, elapsed, way) end)
    end
end

---------------------------------------------------------------------------------------------------------------------
-- Start
---------------------------------------------------------------------------------------------------------------------

Settings = CopyDefaults()
Log(VERSION .. " loaded.")
local read, problem = pcall(ReadConfig)
if not read then
    LogError("Error while reading config.lua: " .. tostring(problem))
end
if not Settings.Enable then
    Log("Enable = false in config.lua: the mod does nothing until it is true.")
end

local function StepSafe()
    local ok, err = pcall(Step)
    if not ok then
        LogError("Error: " .. tostring(err))
    end
end

local function ConfigStep()
    local ok, err = pcall(ReadConfig)
    if not ok then
        LogError("Error while reading config.lua: " .. tostring(err))
    end
end

RegisterLoadMapPostHook(function()
    local ok, err = pcall(function()
        -- The old world's controller is gone; the next step finds the new one. No burst for the loading time.
        controller = nil
        lastStep = nil
    end)
    if not ok then
        LogError("Error after the map load: " .. tostring(err))
    end
end)

-- Both loops run on the game thread (UE4SS's Delayed Action API): no Lua on a second thread (Witch Time's crash
-- lesson, knowledge\engine\ue4ss.md). A UE4SS without it gets the old LoopAsync loops, and the log says so.
if type(LoopInGameThreadWithDelay) == "function" then
    LoopInGameThreadWithDelay(STEP_MS, StepSafe)
    LoopInGameThreadWithDelay(CONFIG_INTERVAL_MS, ConfigStep)
else
    Log("This UE4SS has no game-thread loops (LoopInGameThreadWithDelay): the older LoopAsync is used; a newer UE4SS "
        .. "is safer.")
    LoopAsync(STEP_MS, function()
        ExecuteInGameThread(StepSafe)
        -- false keeps the loop running.
        return false
    end)
    LoopAsync(CONFIG_INTERVAL_MS, function()
        ExecuteInGameThread(ConfigStep)
        return false
    end)
end
