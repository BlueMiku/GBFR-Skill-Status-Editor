# GBFR Skill Status Editor

A standalone tool for editing `skill_status.tbl` in **Granblue Fantasy: Relink**
(Endless Ragnarok / game version 2.0.x). Lets you browse and bulk-edit skill
level values with readable skill names instead of raw internal IDs, then
export straight back to a game-ready `.tbl` file.

## Features

- Converts `skill_status.tbl` to an editable database and back, without
  needing to touch SQLite tools directly
- Automatically patches GBFRDataTools' `skill_status.headers` to understand
  Endless Ragnarok's table format (the ER table added 16 extra bytes per row
  that the community header definition didn't originally account for),
  backing up the original file first
- Resolves internal skill IDs and level descriptions into readable names
  using bundled localization data, including brute-force hash matching to
  recover names GBFRDataTools' own dictionary doesn't know about yet
- Click-to-select / shift-click range select on a single column, bulk-edit
  many cells at once
- Caches the converted database so re-opening the same file is instant
- Logs every edit (old value, new value, timestamp) to a changelog CSV

## Requirements
- [GBFRDataTools](https://github.com/Nenkai/GBFRDataTools/releases)
- Your own extracted `skill_status.tbl` (from `system/table/skill_status.tbl`
  in the game's data archive - use GBFRDataTools' `extract`/`extract-all`
  commands against your own copy of the game)
- If running from source: Python 3.9+ with tkinter (bundled with most
  Windows Python installs)

## Usage

1. Extract `skill_status.tbl` from your own game install using GBFRDataTools.
2. Run `GBFR_Skill_Status_Editor.exe` (or `python gbfr_skill_status_editor.py`
   if running from source).
3. In the setup screen, point it at your `GBFRDataTools.exe` and your
   extracted `skill_status.tbl`. It will offer to patch GBFRDataTools' header
   definition for Endless Ragnarok if needed (original file is backed up).
4. Search, select cells, and edit values. Shift-click extends a selection
   within the same column; Enter opens the bulk edit popup.
5. Click **Export to .tbl...** and choose an output folder.
6. Package the exported `skill_status.tbl` into a mod using
   [Reloaded-II](https://github.com/Reloaded-Project/Reloaded-II) with the
   `gbfrelink.utility.manager` mod loader dependency, placing the file at
   `<Mod Folder>\GBFR\data\system\table\skill_status.tbl`.

## Credits

This tool builds entirely on the reverse-engineering and tooling work of the
Granblue Fantasy: Relink modding community:

- **[Nenkai](https://github.com/Nenkai)** and **WistfulHopes** - creators of
  [GBFRDataTools](https://github.com/Nenkai/GBFRDataTools)
  and [MsgPack2Json](https://github.com/Nenkai/MsgPack2Json)
  
## Known limitations

- Endless Ragnarok added content the community's ID dictionaries don't fully
  cover yet - some skill names/descriptions may still show as raw hex hashes
- The Endless Ragnarok header patch is specific to `skill_status.tbl`'s
  current format; if a future game update changes the table again, this tool
  (and GBFRDataTools itself) will need updating
