-- Settings of Passive Regen. Change a value and save the file: the mod reads it again every 5 seconds, so the change
-- shows in the game without a restart. A file it cannot read (for example while it is saved) keeps the last settings.
-- A setting that this file does not have keeps its default, so an older copy of the file still works after an update.
return {
    Enable = true,          -- false: no regen, the game as normal
    PercentPerTick = 1.5,   -- the share of your max health you get back every 2 seconds (0.1 to 25)
    InCombat = true,        -- false: regen only out of combat
    LogDetail = false,      -- true: ue4ss\UE4SS.log names when the regen runs or waits, and every 10th heal (for tests)
}
