"""Offline test of Scripts/main.lua with lupa (Lua 5.4) and small mocks of UE4SS and the game.

The mocks stand in for the UE4SS functions that the script uses (the game-thread loops LoopInGameThreadWithDelay, the
old LoopAsync, RegisterLoadMapPostHook, FName, FindAllOf, UEHelpers) and for the game: the player controller, the player
with its health attributes and its ability system (tags; a parent tag counts its children, as in UE), the health bar
widget (OnHeathChanged counts its calls, or throws), the pause, and a clock (os.clock) that the test moves.

The checks: the loops; the regen per step (2 s) and per real time (capped after a hitch); full health, death, the pause;
combat with InCombat on and off; live settings; the direct write and the bar's handler, with its kept widgets and its
searches; LogDetail lines. Then the settings files a player may have instead of the shipped config.lua: main.lua must
start, and every setting the file does not give keeps its default; the shipped config.lua must hold exactly the defaults
of main.lua.

Usage:
    python tests/test_main.py        (needs lupa: python -m pip install lupa)

Exit code: 0 = all checks passed, 1 = a check failed.
"""

import json
import sys
import tempfile
from pathlib import Path

from lupa import lua54

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "src" / "phantombladezero_passiveregen" / "Scripts" / "main.lua"
CONFIG = MAIN.parent / "config.lua"

MOCKS = r"""
LOG = {}
print = function(...) LOG[#LOG + 1] = table.concat({...}, "") end
CLOCK = 100
os.clock = function() return CLOCK end

local FNameMeta = { __index = { ToString = function(self) return self.text end } }
EFindName = { FNAME_Find = 0, FNAME_Add = 1 }
function FName(text, findType) return setmetatable({ text = text }, FNameMeta) end

LOOPS = {}
DELAYS = {}
ASYNC_LOOPS = 0
function LoopInGameThreadWithDelay(ms, callback) LOOPS[#LOOPS + 1] = callback DELAYS[#DELAYS + 1] = ms return #LOOPS end
function LoopAsync(ms, callback) ASYNC_LOOPS = ASYNC_LOOPS + 1 LOOPS[#LOOPS + 1] = callback DELAYS[#DELAYS + 1] = ms end
function ExecuteInGameThread(callback) callback() end
MAP_HOOKS = {}
function RegisterLoadMapPostHook(callback) MAP_HOOKS[#MAP_HOOKS + 1] = callback end
function RUN(ms) for i = 1, #LOOPS do if DELAYS[i] == ms then LOOPS[i]() end end end
-- One step after `seconds` of real time (default 2, one full step).
function STEP(seconds) CLOCK = CLOCK + (seconds or 2) RUN(2000) end
function CONFIG() RUN(5000) end

local ObjectMeta = { __index = { IsValid = function(self) return self.valid ~= false end } }
function Object(fields) return setmetatable(fields or {}, ObjectMeta) end

-- The player: health 500 of 1000, tags.
TAGS = {}
ABILITIES = Object({ GetGameplayTagCount = function(self, tag)
    local n, wanted = 0, tag.TagName:ToString()
    for _, t in ipairs(TAGS) do
        if t == wanted or t:sub(1, #wanted + 1) == wanted .. "." then n = n + 1 end
    end
    return n
end })
HEALTH = { BaseValue = 500, CurrentValue = 500 }
PAWN = Object({ AttributeSetBase = { Health = HEALTH, Health_Max = { BaseValue = 1000, CurrentValue = 1000 } },
    AbilitySystemComponent = ABILITIES })
-- The health bar: its handler counts the calls and keeps the last values; "throws" errors. FindAllOf gives UE4SS's nil
-- when there is no widget.
BAR_MODE = "works"
BAR_CALLS = 0
BAR_NEW, BAR_OLD = nil, nil
BAR = Object({ OnHeathChanged = function(self, new, old)
    if BAR_MODE == "throws" then error("no such function") end
    BAR_CALLS = BAR_CALLS + 1
    BAR_NEW, BAR_OLD = new, old
end })
BARS = { BAR }
SEARCHES = 0
SEARCHED = nil
function FindAllOf(name)
    SEARCHES = SEARCHES + 1
    SEARCHED = name
    if #BARS == 0 then return nil end
    return BARS
end
CONTROLLER = Object({ Pawn = PAWN })
CONTROLLER_CALLS = 0
PAUSED = false
STATICS = {}
function STATICS:IsGamePaused(context) return PAUSED end
package.preload["UEHelpers"] = function()
    return {
        GetPlayerController = function() CONTROLLER_CALLS = CONTROLLER_CALLS + 1 return CONTROLLER end,
        GetGameplayStatics = function() return STATICS end,
    }
end
function HP() return string.format("%g", HEALTH.CurrentValue) end

function DESCRIBE(t)
    local names = {}
    for name in pairs(t) do names[#names + 1] = tostring(name) end
    table.sort(names)
    local parts = {}
    for i, name in ipairs(names) do parts[i] = name .. "=" .. tostring(t[name]) end
    return table.concat(parts, ", ")
end
"""

PLAYER_CONFIGS = [
    ("no config.lua", None, "No config.lua"),
    ("an empty or older config.lua", "return {}\n", None),
    ("a broken config.lua", "return {\n    Enable =\n", "config.lua not read"),
    ("a config.lua without a table", "-- nothing here\n", "does not return a table"),
    ("a config.lua with wrong types", "wrong types", "must be a"),
    ("a config.lua with an unknown setting", "return { NoSuchSetting = 1 }\n", "unknown setting NoSuchSetting"),
]


def lua_path(path: Path) -> str:
    return "[[" + path.as_posix() + "]]"


class Run:
    """main.lua in a fresh Lua state, with config.lua read from config_folder; before: Lua run before main.lua."""

    def __init__(self, config_folder: Path, before: str = ""):
        self.folder = config_folder
        self.lua = lua54.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(MOCKS)
        self.lua.execute(f'package.path = {lua_path(config_folder)} .. "/?.lua"; package.cpath = ""')
        if before:
            self.lua.execute(before)
        self.g = self.lua.globals()
        self.do(MAIN.read_text(encoding="utf-8"))

    def do(self, code: str):
        """Runs Lua code; a Lua error that stops it becomes an "Error" line in the log, so the checks fail."""
        try:
            return self.lua.execute(code)
        except lua54.LuaError as error:
            self.g.LOG[len(self.g.LOG) + 1] = "Error: main.lua stopped: " + str(error).splitlines()[0]

    def eval(self, expression: str):
        return self.lua.eval(expression)

    def log(self):
        return [self.g.LOG[i].rstrip() for i in range(1, len(self.g.LOG) + 1)]

    def write_config(self, text: str):
        (self.folder / "config.lua").write_text(text, encoding="utf-8")


def settings_lines(log):
    return [line.split("] Settings: ", 1)[1] for line in log if "] Settings: " in line]


def main() -> None:
    version = json.loads((ROOT / "mod.json").read_text(encoding="utf-8"))["version"]
    failures = []

    def check(condition, text):
        print(("ok    " if condition else "FAIL  ") + text)
        if not condition:
            failures.append(text)

    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        shipped_text = CONFIG.read_text(encoding="utf-8")

        def folder(name, text=shipped_text):
            f = temp / name
            f.mkdir()
            if text is not None:
                (f / "config.lua").write_text(text, encoding="utf-8")
            return f

        # --- The mod as players get it.
        r = Run(folder("shipped"))
        log = r.log()
        for line in log:
            print("      log: " + line)
        check(list(r.g.DELAYS.values()) == [2000, 5000] and r.g.ASYNC_LOOPS == 0 and len(r.g.MAP_HOOKS) == 1,
              "game-thread loops: the regen every 2 s, the config read every 5 s; one map load hook")
        check(any(f"{version} loaded." in line for line in log), f"load line with the version {version} of mod.json")
        shipped = r.eval(f"DESCRIBE(dofile({lua_path(CONFIG)}))")
        check(settings_lines(log) == [shipped] and not any("config.lua" in line for line in log),
              "one settings line with the shipped values, no warning")

        e = r.eval
        r.do("STEP()")
        check(e("HP()") == "515" and e("HEALTH.BaseValue") == 515,
              "one step (2 s): +1.5% of the max health (1000), BaseValue and CurrentValue written")
        check(e("BAR_CALLS") == 1 and e("BAR_NEW") == 515 and e("BAR_OLD") == 500
              and e("SEARCHED") == "WB_PlayerHPBar_V1_C", "the bar's handler gets the new and the old health")
        r.do("STEP(1)")
        check(e("HP()") == "522.5", "a step after 1 s: half the amount (real time)")
        r.do("STEP(30)")
        check(e("HP()") == "552.5", "after a 30 s hitch: at most two steps (4 s) of regen, no burst")
        r.do("HEALTH.CurrentValue = 995 STEP()")
        check(e("HP()") == "1000", "never above the max health")
        calls = e("BAR_CALLS")
        r.do("STEP() STEP()")
        check(e("BAR_CALLS") == calls, "full health: no write, no call")
        r.do("PAWN.AttributeSetBase.Health_Max.CurrentValue = 2000 STEP()")
        check(e("HP()") == "1030", "a new max health (2000): 1.5% of the current max")
        r.do("HEALTH.CurrentValue = 0 STEP()")
        check(e("HP()") == "0", "health 0 (dead): no regen")
        r.do("HEALTH.CurrentValue = 100 TAGS = { 'CharacterState.Dead' } STEP()")
        check(e("HP()") == "100", "the tag CharacterState.Dead: no regen")
        r.do("TAGS = {} PAUSED = true STEP()")
        check(e("HP()") == "100", "the game is paused: no regen")
        r.do("PAUSED = false TAGS = { 'CharacterState.InCombat' } STEP()")
        check(e("HP()") == "130", "in combat with InCombat = true (the default): regen")
        r.do("PAWN.AttributeSetBase.Health_Max.CurrentValue = 1000 HEALTH.CurrentValue = 500")

        # --- Live settings.
        r.write_config(shipped_text.replace("InCombat = true,", "InCombat = false,"))
        r.do("CONFIG() STEP()")
        check(any("InCombat=false" in line for line in settings_lines(r.log())) and e("HP()") == "500",
              "InCombat = false (live): no regen in combat")
        r.do("TAGS = {} STEP()")
        check(e("HP()") == "515", "InCombat = false: regen out of combat")
        r.write_config(shipped_text.replace("PercentPerTick = 1.5,", "PercentPerTick = 3,"))
        r.do("CONFIG() STEP()")
        check(e("HP()") == "545", "PercentPerTick = 3 (live): +3% per step")
        r.write_config(shipped_text.replace("Enable = true,", "Enable = false,"))
        r.do("CONFIG() STEP() STEP()")
        check(e("HP()") == "545", "Enable = false (live): no regen")
        r.write_config("return {\n    Enable =\n")
        r.do("CONFIG()")
        check(any("config.lua not read (line" in line and "the last settings stay" in line for line in r.log()),
              "a broken (half-saved) file keeps the last settings")
        r.write_config("return { PercentPerTick = 80 }\n")
        r.do("CONFIG() STEP()")
        check(any("PercentPerTick = 80 is outside 0.1 to 25; 25 is used" in line for line in r.log())
              and e("HP()") == "795", "a percent out of range is clamped (25)")

        # --- The bar's widgets.
        kept = Run(folder("kept"))
        kept.do("for i = 1, 15 do STEP() end")
        check(kept.eval("SEARCHES") == 1 and kept.eval("BAR_CALLS") == 15, "15 heals: one search, the widget is kept")
        kept.do("STEP()")
        check(kept.eval("SEARCHES") == 2 and kept.eval("BAR_CALLS") == 16, "15 heals (30 s) after a search: a new search")
        nobar = Run(folder("nobar"), "BARS = {}")
        nobar.do("for i = 1, 14 do STEP() end")
        check(nobar.eval("HP()") == "710" and nobar.eval("SEARCHES") == 1
              and not any("Error" in line for line in nobar.log()),
              "no bar widget: the regen goes on, no search on every step, no error")
        gone = Run(folder("gone"))
        gone.do("STEP() BAR.valid = false STEP()")
        check(gone.eval("HP()") == "530" and gone.eval("BAR_CALLS") == 1 and gone.eval("SEARCHES") == 1,
              "a widget that is gone gets no call")
        gone.do("BAR.valid = true STEP()")
        check(gone.eval("SEARCHES") == 2 and gone.eval("BAR_CALLS") == 2,
              "all widgets gone: the next heal searches at once")
        throws = Run(folder("throws"), "BAR_MODE = 'throws'")
        throws.do("STEP()")
        check(throws.eval("HP()") == "515" and any("OnHeathChanged failed" in line for line in throws.log())
              and not any("Error" in line for line in throws.log()),
              "a handler that throws: logged, the value is written")
        mapload = Run(folder("mapload"))
        mapload.do("STEP() MAP_HOOKS[1]() STEP(3)")
        check(mapload.eval("HP()") == "530" and mapload.eval("CONTROLLER_CALLS") == 2
              and mapload.eval("SEARCHES") == 2, "after a map load: controller and bar found again; the first step counts one step, not the loading time")

        # --- LogDetail.
        d = Run(folder("detail", shipped_text.replace("LogDetail = false,", "LogDetail = true, ")))
        d.do("STEP() TAGS = { 'CharacterState.InCombat' } STEP() HEALTH.CurrentValue = 1000 STEP()")
        log = d.log()
        for line in log[-4:]:
            print("      log: " + line)
        check(any("regen on (out of combat)" in line for line in log)
              and any("heal 1: health 500.0 -> 515.0 of 1000.0 (+15.00 in 2.00 s), bar: 1 widget(s)" in line
                      for line in log)
              and any("regen on (in combat)" in line for line in log)
              and any("regen waits: full health" in line for line in log)
              and not any("Error" in line for line in log),
              "LogDetail: the state changes and the first heal")

        # --- The settings files a player may have.
        folders = {}
        for case, content, _ in PLAYER_CONFIGS:
            if content == "wrong types":
                content = r.eval(f"""(function()
                    local lines = {{}}
                    for name, value in pairs(dofile({lua_path(CONFIG)})) do
                        lines[#lines + 1] = string.format("    [%q] = %s,", name, type(value) == "string" and "0" or '"wrong"')
                    end
                    return "return {{\\n" .. table.concat(lines, "\\n") .. "\\n}}\\n"
                end)()""")
            folders[case] = folder(f"player{len(folders)}", content)
        defaults = settings_lines(Run(folders["an empty or older config.lua"]).log())
        check(defaults == [shipped], f"config.lua ships exactly the defaults of main.lua ({defaults})")
        count = len(list(r.eval(f"dofile({lua_path(CONFIG)})").keys()))
        for case, _, expected in PLAYER_CONFIGS:
            run = Run(folders[case])
            run.do("STEP() CONFIG()")
            log = run.log()
            fine = (settings_lines(log) == defaults and len(run.g.LOOPS) == 2 and run.eval("HP()") == "515"
                    and not any("Error" in line for line in log))
            if expected is None:
                fine = fine and not any("config.lua" in line for line in log)
            else:
                fine = fine and sum(expected in line for line in log) == (count if case.endswith("wrong types") else 1)
            check(fine, f"{case}: starts with the defaults and heals"
                  + (f", logs '{expected}' once per setting" if case.endswith("wrong types")
                     else (f", logs '{expected}' once" if expected else ", no warning")))
            if not fine:
                for line in log:
                    print("      log: " + line)
        off = Run(folder("off", "return { Enable = false }\n"))
        off.do("STEP() STEP()")
        check(len(off.g.LOOPS) == 2 and off.eval("HP()") == "500"
              and any("does nothing until it is true" in line for line in off.log()),
              "Enable = false at start: the loops run (live settings), no regen")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
