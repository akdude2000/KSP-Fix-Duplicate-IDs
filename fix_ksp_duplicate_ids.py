#!/usr/bin/env python3
"""
fix_ksp_duplicate_ids.py — Repair duplicate cid / persistentId entries in a
KSP persistent.sfs save file.

Symptom this fixes: FlowGraph spam in the KSP.log of the form
    [ERR] [FlowGraph]: Graph already contains item! Part <partName> with id <N>
that appears thousands or millions of times, lags the editor / flight, and
can eventually crash KSP with a stack overflow inside FlowGraph reconciliation.

Background: ScrapYard's "Found inventory part on vessel that is not in
inventory. Resetting." recovery path (triggered by either @diffie's
PartModule-value-drift scenario or by an OhScrap RTAntennaFailureModule
NullReferenceException during apply) re-applies persisted PART records to
vessels without regenerating cid or child MODULE.persistentId values. The
duplicate IDs then trigger FlowGraph errors on every subsequent save load
until they are removed.

What this script does:
- Reads the persistent.sfs you point it at.
- Finds every `cid = N` and `persistentId = N` line.
- For each duplicate occurrence after the first, assigns a new unique ID
  drawn from the LOW unused range (starting at 100). This maximizes the
  remaining headroom against KSP's uint32 ID limit (4,294,967,295), since
  KSP's own ID generator clusters values near the top of uint32.
- Preserves the first occurrence of each duplicate ID so most references
  in the rest of the save remain valid.
- Writes a backup beside the original before touching it.

Usage:
    python fix_ksp_duplicate_ids.py /path/to/saves/<savename>/persistent.sfs
    python fix_ksp_duplicate_ids.py /path/to/persistent.sfs --dry-run

The --dry-run flag reports what would change without modifying the file.

Always back up your save manually before running, even though this script
also makes its own backup. Hand-edited saves can corrupt in ways automated
tools don't always anticipate.

Tested against KSP 1.12 persistent.sfs files. The .sfs format is stable
across the 1.x range so it should work on earlier versions too.

License: public domain / CC0. Use, modify, redistribute freely.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path


SEED_LOW = 100  # start looking for unused IDs from here

# Matches any of KSP's numeric-ID fields when collecting all IDs to avoid
# collisions when assigning new ones. We do NOT rewrite all of these —
# only cid and persistentId, which are the ones FlowGraph chokes on.
ID_FIELD_RE = re.compile(r"^\s*(?:cid|persistentId|uid|pid|mid|missionId) = (\d+)\s*$")

# Fields we actively rewrite duplicates of.
TARGET_RE = re.compile(r"^(\s*)(cid|persistentId) = (\d+)\s*$")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("save", type=Path, help="Path to persistent.sfs")
    p.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = p.parse_args()

    save: Path = args.save

    if not save.exists():
        print(f"ERROR: {save} not found", file=sys.stderr)
        return 1
    if save.suffix.lower() != ".sfs":
        print(f"ERROR: expected a .sfs file, got {save.name}", file=sys.stderr)
        return 1

    with open(save, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Pass 1: collect every numeric ID so we can pick new IDs that don't collide.
    all_ids: set[int] = set()
    for line in lines:
        m = ID_FIELD_RE.match(line)
        if m:
            all_ids.add(int(m.group(1)))

    def next_unused_low_id() -> int:
        """Pick the smallest unused ID starting from SEED_LOW."""
        candidate = SEED_LOW
        while candidate in all_ids:
            candidate += 1
        all_ids.add(candidate)
        return candidate

    # Pass 2: rewrite duplicate cid and persistentId occurrences.
    seen: set[tuple[str, int]] = set()
    changes: list[tuple[int, str, int, int]] = []
    new_lines: list[str] = []

    for i, line in enumerate(lines, start=1):
        m = TARGET_RE.match(line)
        if m:
            indent, field, value_str = m.group(1), m.group(2), m.group(3)
            value = int(value_str)
            key = (field, value)
            if key in seen:
                new_id = next_unused_low_id()
                changes.append((i, field, value, new_id))
                new_lines.append(f"{indent}{field} = {new_id}\n")
                continue
            seen.add(key)
        new_lines.append(line)

    if not changes:
        print(f"No duplicate cid or persistentId entries found in {save.name}.")
        print("Nothing to do.")
        return 0

    print(f"{len(changes)} duplicate IDs found:")
    for line_no, field, old, new in changes:
        print(f"  line {line_no}: {field} {old} -> {new}")

    print()
    print(f"Largest new ID used:    {max(c[3] for c in changes)}")
    print(f"uint32 headroom:        {4_294_967_295 - max(all_ids)}")

    if args.dry_run:
        print("\n--dry-run specified, no changes written.")
        return 0

    # Back up the original beside it before overwriting.
    backup = save.with_suffix(".sfs.bak.before_dupid_fix")
    shutil.copy2(save, backup)
    print(f"\nBackup: {backup.name}")

    with open(save, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
