#!/usr/bin/env python3
"""
fix_ksp_kct_storage.py — Fix ID collisions in KCT-stored vessel ShipNodes
embedded inside KSP persistent.sfs.

This is the third and final piece of the ScrapYard / OhScrap corruption
remediation toolkit:

  1. fix_ksp_duplicate_ids.py — dedups cid/persistentId inside persistent.sfs
  2. fix_ksp_craft_collisions.py — fixes collisions between .craft files
     in Ships/Subassemblies and persistent.sfs live vessels
  3. THIS SCRIPT — fixes collisions between KCT-stored ShipNodes inside
     persistent.sfs and the live vessel state, plus collisions between
     ShipNodes themselves.

Why this is needed: KCT stores in-build / built-not-rolled-out vessels as
craft-format text inside persistent.sfs at
    SCENARIO[KerbalConstructionTime] -> KSC -> VABPlans/SPHPlans ->
    KCTVessel -> ShipNode { ... }
These ShipNodes use the same _<id> craft-format part references as .craft
files. When a vessel rolls out, those _<id> tokens become live vessel cids.
If they collide with existing live vessels (or with another stored ShipNode
that has already been rolled out), FlowGraph spam returns.

fix_ksp_duplicate_ids.py already deduplicates cid/persistentId LINES anywhere
in persistent.sfs (including inside ShipNodes), but it does NOT catch the
_<id> craft-format tokens in part=/link=/attN= lines — those are a separate
namespace that surfaces as a duplicate only at rollout time.

What this script does:
- Locates every ShipNode { ... } block inside persistent.sfs by brace-tracking.
- Collects all IDs OUTSIDE any ShipNode (live vessel cid/persistentId/uid
  fields, plus _<id> tokens that appear in live vessel serialization) as
  the "reserved" set.
- For each ShipNode in order:
    * Finds every distinct ID referenced inside it (across _<id> tokens,
      cid=, persistentId=).
    * Any ID that's already reserved gets a new unique low-range ID assigned.
    * All occurrences of that ID within the ShipNode are rewritten
      consistently — _<id> tokens AND cid/persistentId/uid lines — so the
      ShipNode's internal cross-references stay valid.
    * The ShipNode's final ID set (kept + remapped) is added to reserved
      for subsequent ShipNodes, preventing inter-ShipNode collisions.
- Backs up persistent.sfs beside the original before writing.

Usage:
    python fix_ksp_kct_storage.py /path/to/saves/<savename>/persistent.sfs
    python fix_ksp_kct_storage.py /path/to/persistent.sfs --dry-run

Run order in the toolkit:
    1. fix_ksp_duplicate_ids.py persistent.sfs
    2. fix_ksp_kct_storage.py persistent.sfs
    3. fix_ksp_craft_collisions.py <save-dir>

(Step 2 should run after step 1 so its reserved set reflects the
deduplicated state. Step 3 reads persistent.sfs as fixed and treats its
IDs as reserved when remapping .craft files.)

Always back up your save manually before running, even though this script
also makes its own backup.

License: public domain / CC0. Use, modify, redistribute freely.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path


SEED_LOW = 300  # start above ranges used by the other two scripts (100, 200)

# craft-format part references like `part = name_4289964728`, `link = name_4289964728`
ID_TOKEN_RE = re.compile(r"_(\d{8,})(?!\d)")

# sfs-format ID fields. MULTILINE so ^/$ match line boundaries inside a
# multi-line `text` passed to finditer.
ID_FIELD_RE = re.compile(r"^(\s*)(cid|persistentId|uid|pid|mid|missionId) = (\d+)\s*$", re.MULTILINE)

# ShipNode opening (start of an embedded vessel block)
SHIPNODE_OPEN_RE = re.compile(r"^\s*ShipNode\s*$")


def find_shipnode_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """
    Identify (start_line, end_line) tuples for each ShipNode block by brace
    tracking. Both indices are inclusive, into the lines list (0-based).
    The start_line is the `ShipNode` keyword line; end_line is the matching
    `}` of the ShipNode block.
    """
    ranges = []
    i = 0
    n = len(lines)
    while i < n:
        if SHIPNODE_OPEN_RE.match(lines[i]):
            # Find the opening { on the next non-empty line
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j >= n or "{" not in lines[j]:
                i += 1
                continue
            # Track braces from here
            depth = 0
            k = j
            while k < n:
                depth += lines[k].count("{") - lines[k].count("}")
                if depth == 0:
                    ranges.append((i, k))
                    i = k + 1
                    break
                k += 1
            else:
                # Unbalanced — bail at end of file
                break
        else:
            i += 1
    return ranges


def collect_ids_in_text(text: str) -> set[int]:
    """All IDs appearing as either _<id> tokens or cid=/persistentId=/... lines."""
    ids: set[int] = set()
    for m in ID_TOKEN_RE.finditer(text):
        ids.add(int(m.group(1)))
    for m in ID_FIELD_RE.finditer(text):
        ids.add(int(m.group(3)))
    return ids


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

    ranges = find_shipnode_ranges(lines)
    if not ranges:
        print("No KCT ShipNode blocks found in this save.")
        print("(Either KCT isn't installed/used here, or there are no stored vessels.)")
        return 0

    print(f"Found {len(ranges)} ShipNode block(s).")

    # Build set of "outside" lines (everything NOT inside a ShipNode body)
    in_shipnode = [False] * len(lines)
    for s, e in ranges:
        for idx in range(s, e + 1):
            in_shipnode[idx] = True

    outside_text = "".join(line for idx, line in enumerate(lines) if not in_shipnode[idx])
    reserved: set[int] = collect_ids_in_text(outside_text)
    print(f"Reserved IDs from outside ShipNodes: {len(reserved):,}")

    def next_unused(start: int = SEED_LOW) -> int:
        c = start
        while c in reserved:
            c += 1
        reserved.add(c)
        return c

    total_remapped = 0
    shipnodes_touched = 0
    all_changes: list[tuple[int, dict[int, int]]] = []

    # Process each ShipNode in order
    for sn_idx, (start, end) in enumerate(ranges):
        sn_lines = lines[start:end + 1]
        sn_text = "".join(sn_lines)
        sn_ids = collect_ids_in_text(sn_text)

        # Find IDs that collide with reserved (live vessels OR earlier ShipNodes)
        collisions = sorted(i for i in sn_ids if i in reserved)
        if not collisions:
            # Still need to add this ShipNode's IDs to reserved for next iteration
            reserved.update(sn_ids)
            continue

        # Build remap
        remap: dict[int, int] = {}
        for old in collisions:
            remap[old] = next_unused()
        # Non-colliding IDs also reserved for subsequent ShipNodes
        reserved.update(sn_ids - set(collisions))

        # Apply remap to the ShipNode's lines (text-level for _<id> tokens,
        # line-level for sfs ID fields)
        new_sn_lines: list[str] = []
        for line in sn_lines:
            # Rewrite _<id> tokens
            def token_repl(m: re.Match) -> str:
                v = int(m.group(1))
                return f"_{remap[v]}" if v in remap else m.group(0)
            new_line = ID_TOKEN_RE.sub(token_repl, line)

            # Rewrite cid=, persistentId=, uid=, etc. if they match a remapped ID
            m = ID_FIELD_RE.match(new_line)
            if m:
                indent, field, value_str = m.group(1), m.group(2), m.group(3)
                v = int(value_str)
                if v in remap:
                    new_line = f"{indent}{field} = {remap[v]}\n"
            new_sn_lines.append(new_line)

        # Splice back into the lines list
        lines[start:end + 1] = new_sn_lines
        total_remapped += len(remap)
        shipnodes_touched += 1
        all_changes.append((sn_idx, remap))

    if total_remapped == 0:
        print("No KCT ShipNode collisions found. Nothing to do.")
        return 0

    print(f"\n{total_remapped} IDs across {shipnodes_touched} ShipNode(s) "
          f"{'would be' if args.dry_run else 'have been'} remapped:")
    for sn_idx, remap in all_changes:
        print(f"  ShipNode #{sn_idx + 1}:")
        for old, new in remap.items():
            print(f"    {old} -> {new}")

    if args.dry_run:
        print("\n--dry-run specified, no changes written.")
        return 0

    # Backup and write
    backup = save.with_suffix(".sfs.bak.before_kct_fix")
    shutil.copy2(save, backup)
    print(f"\nBackup: {backup.name}")

    with open(save, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
