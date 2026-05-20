#!/usr/bin/env python3
"""
fix_ksp_craft_collisions.py — Fix cross-file ID collisions between .craft
files and a KSP persistent.sfs.

KSP's FlowGraph is global across the editor and all loaded vessels. When a
.craft file has a part with id X that already exists as a live vessel's cid
X in persistent.sfs, KSP detects the duplicate every frame while the editor
references the craft — flooding KSP.log with errors and lagging the editor.

This is the second half of the ScrapYard / OhScrap corruption remediation.
fix_ksp_duplicate_ids.py handles intra-file duplicates inside persistent.sfs.
This script handles cross-file collisions: .craft files built during the
corrupted period inherit IDs that match live vessels, becoming re-infection
vectors after persistent.sfs is cleaned.

What this script does:
- Reads persistent.sfs, collects every ID in use (cid, persistentId, uid,
  plus any _<digits> tokens embedded in KCT-stored ShipNodes).
- For each .craft under saves/<save>/Ships and Subassemblies, finds IDs
  that collide with the persistent.sfs reserved set.
- Rewrites colliding IDs in the craft to new unused values, starting from
  SEED_LOW. Uses text-level _<old> -> _<new> substitution so link and attN
  cross-references inside the craft stay valid.
- Backs up each touched craft beside the original.

Usage:
    python fix_ksp_craft_collisions.py /path/to/saves/<savename>/
    python fix_ksp_craft_collisions.py /path/to/saves/<savename>/ --dry-run

Always back up your save manually before running, even though this script
also makes per-file backups. Hand-edited saves can corrupt in ways
automated tools don't always anticipate.

Run AFTER fix_ksp_duplicate_ids.py — the intra-file dedup picks low IDs
in the 100-114ish range, so this script starts at 200 to avoid collision.

License: public domain / CC0. Use, modify, redistribute freely.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path


SEED_LOW = 200  # start above the range fix_ksp_duplicate_ids.py uses

# Numeric ID fields inside persistent.sfs
ID_FIELD_RE = re.compile(r"^\s*(?:cid|persistentId|uid|pid|mid|missionId) = (\d+)\s*$")

# Craft-format ID tokens that look like `_<8+digit>` (used in part=, link=, attN=)
ID_TOKEN_RE = re.compile(r"_(\d{8,})(?!\d)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("save_dir", type=Path,
                   help="Save directory (containing persistent.sfs and Ships/, Subassemblies/)")
    p.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = p.parse_args()

    save_dir: Path = args.save_dir
    persistent = save_dir / "persistent.sfs"

    if not persistent.exists():
        print(f"ERROR: {persistent} not found", file=sys.stderr)
        return 1

    # Step 1: collect every ID in persistent.sfs that's reserved.
    reserved: set[int] = set()
    with open(persistent, "r", encoding="utf-8") as f:
        for line in f:
            m = ID_FIELD_RE.match(line)
            if m:
                reserved.add(int(m.group(1)))
    # Also collect _<8+digit> tokens (KCT-stored ShipNodes use these)
    with open(persistent, "r", encoding="utf-8") as f:
        text = f.read()
    for m in ID_TOKEN_RE.finditer(text):
        reserved.add(int(m.group(1)))

    print(f"Reserved IDs from persistent.sfs: {len(reserved):,}")

    def next_unused(start: int = SEED_LOW) -> int:
        """Pick smallest unused ID at-or-above start."""
        c = start
        while c in reserved:
            c += 1
        reserved.add(c)
        return c

    # Step 2: gather .craft files
    craft_files = sorted(set(
        list(save_dir.glob("Ships/**/*.craft"))
        + list(save_dir.glob("Subassemblies/**/*.craft"))
    ))

    if not craft_files:
        print(f"No .craft files found under {save_dir / 'Ships'} or {save_dir / 'Subassemblies'}.")
        return 0

    print(f"Scanning {len(craft_files)} craft files...")

    total_fixed = 0
    files_touched = 0

    for craft in craft_files:
        with open(craft, "r", encoding="utf-8", errors="replace", newline="") as f:
            content = f.read()

        craft_ids = {int(m.group(1)) for m in ID_TOKEN_RE.finditer(content)}
        collisions = sorted(cid for cid in craft_ids if cid in reserved)

        if not collisions:
            continue

        remap = {old: next_unused() for old in collisions}

        if args.dry_run:
            print(f"\n[dry-run] {craft.relative_to(save_dir)}: {len(remap)} colliding IDs")
            for old, new in remap.items():
                print(f"  {old} -> {new}")
            total_fixed += len(remap)
            files_touched += 1
            continue

        # Backup the craft before modifying
        backup = craft.with_suffix(".craft.bak.cross_fix")
        shutil.copy2(craft, backup)

        def replacer(match: re.Match) -> str:
            old = int(match.group(1))
            return f"_{remap[old]}" if old in remap else match.group(0)

        new_content = ID_TOKEN_RE.sub(replacer, content)

        with open(craft, "w", encoding="utf-8", newline="") as f:
            f.write(new_content)

        print(f"\n{craft.relative_to(save_dir)}: {len(remap)} colliding IDs remapped")
        for old, new in remap.items():
            print(f"  {old} -> {new}")
        total_fixed += len(remap)
        files_touched += 1

    print()
    if total_fixed == 0:
        print("No cross-file collisions found. Nothing to do.")
    else:
        verb = "would be" if args.dry_run else "have been"
        print(f"{total_fixed} IDs across {files_touched} craft {verb} remapped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
