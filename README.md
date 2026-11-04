# Passive Regen

Health regenerates by itself: 1.5% of the maximum every 2 seconds. The percent and whether it also works in combat are set in config.lua, live in game.

A [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) Lua mod for Phantom Blade Zero (Unreal Engine 5.7), made with
game version `CL-302256_JKS-17280` and UE4SS `v3.0.1-1195-g1ceefac8`. This README is for developers; what the mod does and
how to install it are in [openmods.md](openmods.md), the mod page on [openmods](https://openmods.nexus).

## How it works

Every 2 seconds, on the game thread, `Scripts/main.lua` heals the player by `PercentPerTick` of the current maximum
health (`AttributeSetBase.Health_Max.CurrentValue`), scaled to the real time since the last step (at most two steps,
so a hitch or a loading screen gives no burst). It skips the step while the game is paused, while the player is dead
(health 0 or the tag `CharacterState.Dead`), at full health, and in combat (the tag `CharacterState.InCombat`) when
`InCombat = false`.

The mod writes `Health.BaseValue` and `CurrentValue` directly. A direct write sends none of the game's health events, so
the mod then calls the health bar's own handler of that event, `OnHeathChanged` of `WB_PlayerHPBar_V1_C` (the game's
spelling), and the bar shows the step at once. It keeps the widgets it found and searches again after a map load, when
they are gone, and after 15 heals. Up to 1.1 the heal went through the cheat command `GM_SetHealth`, which leaves the
health as it is since the game update of 2026-11-04 (Steam build 26367565).

## Deploy and test

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1     # copy the mod folder into the game's ue4ss\Mods
python tests\test_main.py                                 # offline test of main.lua (needs lupa)
```

`deploy.ps1` copies `src\phantombladezero_passiveregen\` to `<game folder>\platform\steam\ue4ss\Mods\phantombladezero_passiveregen\`. The game
folder is `$env:UE5_GAMES_DIR\PBZ`; `-GameDir` or `-ModsDir` override it. The mod folder holds
`enabled.txt`, so UE4SS starts it without an entry in `mods.txt`. An existing `Scripts\config.lua` in the game stays
(the tester's settings); `-ResetConfig` copies the shipped one. UE4SS loads Lua mods at game start. The mod writes
its lines into `ue4ss\UE4SS.log`, for example `[Passive Regen] 1.0 loaded.`

`tests\test_main.py` runs `main.lua` in Lua 5.4 ([lupa](https://pypi.org/project/lupa/)) with mocks of the UE4SS
functions that the script uses. It finds syntax and logic errors before a game start.

Versioning: every change raises the second number (1.0 -> 1.1) in `mod.json` and in `main.lua`:
`python scripts\bump_version.py`.

## Settings

`Scripts/config.lua` holds the player's settings, `DEFAULTS` in `main.lua` every setting with its default and
`LIMITS` the range of the number settings. The mod reads the file at start and again every 5 seconds (`ReadConfig`):
a changed file applies at the next step, a broken or half-saved one keeps the last settings, a missing one means the
defaults. Players and mod managers keep their own `config.lua` across updates, so a new `main.lua` can meet an older
file: a setting that the file lacks, or has with another type, keeps its default, and the log says so
(`ParseConfig`). Add each setting to
both files with the same value, and read it only from `Settings`, never as `config.X or default` (for a true/false
setting, `false or true` is true). `tests\test_main.py` checks that `config.lua` ships exactly the defaults, and runs
`main.lua` with a missing, empty, broken and wrong `config.lua`.

## Project layout

```
src/phantombladezero_passiveregen/   The UE4SS mod folder: Scripts/main.lua, Scripts/config.lua, enabled.txt
tests/test_main.py    Offline test of main.lua with mocks of UE4SS and the game
docs/                 Cover and screenshots
scripts/              bump_version.py
deploy.ps1            Copies the mod folder into the game's ue4ss\Mods
mod.json              Name, version, game, engine and UE4SS version
```

## License

MIT, see [LICENSE](LICENSE).
