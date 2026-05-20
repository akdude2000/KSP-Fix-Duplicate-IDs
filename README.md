[README.md](https://github.com/user-attachments/files/28051575/README.md)
# KSP-Fix-Duplicate-IDs

A Python utility that repairs duplicate `cid` and `persistentId` entries in Kerbal Space Program save folders. Designed to clean up the corruption pattern caused by [ScrapYard](https://github.com/zer0Kerbal/ScrapYard) + [OhScrap](https://github.com/zer0Kerbal/OhScrap) interaction issues.

## What it fixes

Saves affected by the ScrapYard "Reset" cascade end up with duplicate IDs scattered across multiple locations: inside `persistent.sfs`, in `.craft` files under `Ships/` and `Subassemblies/`, in KCT-stored ShipNodes embedded in `persistent.sfs`, and in `temp.craft`, quicksaves, and timestamped backup `.sfs` files. KSP detects the duplicates on every save load and floods `KSP.log` with errors like:

    [ERR] [FlowGraph]: Graph already contains item! Part <name> with id <N>

In bad cases the log spam reaches millions of lines, lags the editor severely, and can crash KSP with a stack overflow inside FlowGraph reconciliation.

The corruption can be triggered by:

- PartModule value drift between flight and craft-file storage — RealChute mutable state, ProceduralPart float drift like `diameter = 5` becoming `5.0000004`. Diagnosis credit: `diffie` on the KSP forum.
- OhScrap `RTAntennaFailureModule.Initialise()` throwing `NullReferenceException` during the ScrapYard apply event, which escapes into ScrapYard's "Resetting" recovery path.

Both paths funnel into the same broken ScrapYard Reset code, which re-applies persisted PART records without regenerating IDs. The corruption then propagates to every file format the save touches.

## Usage

One script, one command, walks the entire save folder:

    python fix_ksp_save_ids.py /path/to/saves/<your-save>/

Add `--dry-run` to preview without writing:

    python fix_ksp_save_ids.py /path/to/saves/<your-save>/ --dry-run

The script backs up every file it modifies as `<filename>.bak.unified_fix` beside the original. **Make a full manual backup of the save folder before running anyway** — hand-editing saves can corrupt in ways automated tools don't always anticipate.

## What the unified script does

Walks the save folder and partitions every `.sfs` and `.craft` file into two zones:

- **Zone A — active game state**: `persistent.sfs` plus every `.craft` file (anywhere under the save folder). These share an ID namespace at runtime: a live vessel with `cid=N` and a loaded craft with `_N` will collide. Cleaned globally — all IDs in Zone A end up unique.
- **Zone B — alternate snapshots**: quicksaves, `KCT_Backup.sfs`, timestamped persistent backups, named saves. Each is internally deduplicated but doesn't have to be unique relative to Zone A (they're never loaded simultaneously).

For Zone A the script runs three passes in a single command:

1. **persistent.sfs intra-file dedup** — every `cid` or `persistentId` line that's a repeat earlier in the file gets a new unique ID.
2. **Craft vs persistent.sfs collisions** — for each `.craft` file, any unique ID that also appears in `persistent.sfs` gets remapped in the craft. All occurrences of the old ID within that craft are updated consistently so cross-references (link, attN, srfN, sym) stay valid.
3. **Inter-craft collisions** — when two `.craft` files share an ID that's not in `persistent.sfs`, the alphabetically-first craft keeps it and the others get remapped.

Zone B gets independent per-file intra-file dedup, with new IDs drawn from the same low-range counter (starting at 100). Each Zone B file becomes internally consistent so it can be loaded as a restore point if needed.

All new IDs come from a single counter starting at 100. KSP's own ID generator clusters values near the top of uint32 (~4.29 billion), so starting low maximizes remaining headroom against the integer limit.

## Recommended workflow

1. **Exit KSP completely** — not just to the main menu, kill the process. KSP holds the save in memory and will overwrite our changes on autosave otherwise.

2. **Copy the whole save folder** to a backup location (`saves/<save>.bak/`).

3. **Block the OhScrap NRE trigger** with a Module Manager patch in `GameData/MyPatches/` (or wherever you keep custom patches):

       @PART[*]:HAS[@MODULE[RTAntennaFailureModule]]:FINAL
       { !MODULE[RTAntennaFailureModule],* {} }

   This kills OhScrap's antenna failure module — one of the two known triggers for the corruption cascade. (Doesn't fix the PartModule value-drift trigger that `diffie` identified — that still requires an upstream fix in ScrapYard's Reset path.)

4. Run the unified script:

       python fix_ksp_save_ids.py /path/to/saves/<your-save>/

5. Launch KSP, load the save, do whatever you were doing when the bug bit you. Check the new `KSP.log` for `Graph already contains` — should be zero lines.

## Requirements

Python 3.8 or newer. No external dependencies. Tested against KSP 1.12 save files; the `.sfs` and `.craft` formats are stable across the 1.x range so it should work on earlier versions too.

## Related issues and discussion

- ScrapYard #52 — active GitHub thread for the FlowGraph spam symptom: <https://github.com/zer0Kerbal/ScrapYard/issues/52>
- ScrapYard #81 — "Parts not applying / FlowGraph Error", a different user-facing path into the same bug: <https://github.com/zer0Kerbal/ScrapYard/issues/81>
- OhScrap #91 — the underlying NRE in `BaseFailureModule.Initialise()`: <https://github.com/zer0Kerbal/OhScrap/issues/91>
- KSP forum thread (currently most active discussion): <https://forum.kerbalspaceprogram.com/topic/192456-1124-scrapyard-syd-the-common-part-inventory-v22990-prerelease-edition-08-jan-2023/>

## License

Public domain / CC0. Use, modify, redistribute freely. No warranty — hand-editing a save can corrupt it in ways automated tools don't always anticipate. Always back up first.
