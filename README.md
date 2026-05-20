# KSP-Fix-Duplicate-IDs

A small Python toolkit that repairs duplicate `cid` and `persistentId` entries
in Kerbal Space Program save files. Specifically designed to clean up the
corruption pattern caused by [ScrapYard](https://github.com/zer0Kerbal/ScrapYard) +
[OhScrap](https://github.com/zer0Kerbal/OhScrap) interaction issues.

## What it fixes

Save files affected by the ScrapYard "Reset" cascade end up with multiple
parts sharing the same `cid`, or multiple module instances sharing the same
`persistentId`. KSP detects this on every save load and floods `KSP.log`
with errors of the form:

    [ERR] [FlowGraph]: Graph already contains item! Part <name> with id <N>

In bad cases the log spam reaches millions of lines, lags the editor
severely, and can crash KSP with a stack overflow inside FlowGraph
reconciliation.

The corruption can be triggered by:

- PartModule value drift between flight and craft-file storage — RealChute
  mutable state, ProceduralPart float drift like `diameter = 5` becoming
  `5.0000004`. Diagnosis credit: `diffie` on the KSP forum.
- OhScrap `RTAntennaFailureModule.Initialise()` throwing
  `NullReferenceException` during the ScrapYard apply event, which escapes
  into ScrapYard's "Resetting" recovery path.

Both paths funnel into the same broken ScrapYard Reset code, which re-applies
persisted PART records without regenerating IDs. The corruption then
propagates to three places:

1. `persistent.sfs` itself — duplicate `cid` and `persistentId` lines
2. `.craft` files in `saves/<save>/Ships/` and `Subassemblies/` — they
   inherit IDs that collide with live vessel cids
3. KCT-stored vessels inside `persistent.sfs` (the `ShipNode` blocks
   under `SCENARIO[KerbalConstructionTime]`) — embedded craft-format
   text whose `_<id>` tokens collide with live vessels

This toolkit has one script per location.

## Scripts

| Script | What it cleans | When to run |
|---|---|---|
| `fix_ksp_duplicate_ids.py` | Duplicate `cid` and `persistentId` LINES inside `persistent.sfs` | Run first |
| `fix_ksp_kct_storage.py` | `_<id>` craft-format tokens inside KCT-stored `ShipNode` blocks in `persistent.sfs` that collide with live vessels or each other | Run second |
| `fix_ksp_craft_collisions.py` | IDs in `.craft` files under `Ships/` and `Subassemblies/` that collide with live vessels in `persistent.sfs` | Run third |

Each script accepts `--dry-run` to report what it would change without writing.
Each makes its own backup of any file it modifies.

## Usage

Run in order against your save directory:

    cd /path/to/Kerbal\ Space\ Program/saves/<your-save>/

    python fix_ksp_duplicate_ids.py persistent.sfs
    python fix_ksp_kct_storage.py persistent.sfs
    python fix_ksp_craft_collisions.py .

The craft-collisions script takes the save directory (it walks `Ships/` and
`Subassemblies/` under it); the other two take the `persistent.sfs` path.

To preview without changes:

    python fix_ksp_duplicate_ids.py persistent.sfs --dry-run
    python fix_ksp_kct_storage.py persistent.sfs --dry-run
    python fix_ksp_craft_collisions.py . --dry-run

The scripts pick new IDs from intentionally-low ranges (100, 200, 300
respectively) so they don't collide with each other and so they maximize
remaining headroom against KSP's uint32 ID limit (4,294,967,295). KSP's
own ID generator clusters values near the top of that range, so picking
low values gives effectively unlimited room.

**Always back up your save manually before running.** Each script also
makes its own backup of any file it touches, but a manual full-save backup
is the safety net you actually want.

## Recommended workflow

1. **Exit KSP completely** — not just to the main menu, kill the process.
   KSP holds your save in memory and will overwrite our changes on autosave
   otherwise.
2. **Copy the whole save folder** to a backup location (`saves/<save>.bak/`).
3. **Block the underlying corruption trigger** with a Module Manager patch
   in `GameData/MyPatches/` (or wherever you keep custom patches):

       @PART[*]:HAS[@MODULE[RTAntennaFailureModule]]:FINAL
       { !MODULE[RTAntennaFailureModule],* {} }

   This kills OhScrap's antenna failure module — one of the two known
   triggers for the corruption cascade. (Doesn't fix the PartModule
   value-drift trigger that @diffie identified — that still requires an
   upstream fix in ScrapYard's Reset path.)
4. Run the three scripts in the order above.
5. Launch KSP, load the save, do whatever you were doing when the bug bit
   you. Check the new `KSP.log` for `Graph already contains` — should be
   zero lines.

## Requirements

Python 3.8 or newer. No external dependencies. Tested against KSP 1.12 save
files; the .sfs and .craft formats are stable across the 1.x range so it
should work on earlier versions too.

## Related issues and discussion

- ScrapYard #52 — the active GitHub thread tracking the FlowGraph spam
  symptom: <https://github.com/zer0Kerbal/ScrapYard/issues/52>
- ScrapYard #81 — "Parts not applying / FlowGraph Error", a different
  user-facing path into the same bug: <https://github.com/zer0Kerbal/ScrapYard/issues/81>
- OhScrap #91 — the underlying NRE in BaseFailureModule.Initialise:
  <https://github.com/zer0Kerbal/OhScrap/issues/91>
- KSP forum thread (currently most active discussion):
  <https://forum.kerbalspaceprogram.com/topic/192456-1124-scrapyard-syd-the-common-part-inventory-v22990-prerelease-edition-08-jan-2023/>

## License

Public domain / CC0. Use, modify, redistribute freely. No warranty —
hand-editing a save can corrupt it in ways automated tools don't always
anticipate. Always back up first.
