#!/usr/bin/env python3
r"""
fix_ksp_save_ids.py — Unified KSP save-folder ID cleanup.

Repairs the corruption produced by ScrapYard + OhScrap (or any other code
path into ScrapYard's broken Reset cascade): duplicate cid / persistentId
fields inside .sfs files, and cross-file collisions between .craft files
and the active persistent.sfs.

Replaces the three previous scripts (fix_ksp_duplicate_ids.py,
fix_ksp_craft_collisions.py, fix_ksp_kct_storage.py) with a single pass
that uses one ID pool shared across every file in the save folder.

How it works
------------

Zone A is the "active game state": persistent.sfs plus every .craft file
anywhere under the save folder. At runtime these share an ID namespace —
a live vessel with cid=N and a loaded craft with `_N` will collide.

Zone B is "alternate snapshots": quicksaves, KCT_Backup.sfs, timestamped
persistent backups, and other named .sfs files. They never coexist with
persistent.sfs, so they only need to be internally consistent.

For Zone A the script runs in three passes:

  Pass A1: intra-file dedup of persistent.sfs (every cid/persistentId
           line that's already been seen earlier in the file gets a new
           ID). After this, persistent.sfs is internally consistent.

  Pass A2: for each .craft, find every UNIQUE id that also appears in
           persistent.sfs (post-A1) and remap it in that craft. The
           remap is per craft: one new id per old id, applied to every
           occurrence of that old id in the craft so cross-references
           (link, attN, srfN, sym) stay valid.

  Pass A3: for .craft files that share IDs with each other (not with
           persistent.sfs — those were already remapped), pick the
           alphabetically-first file to keep the id, remap in the others.

Zone B gets a separate intra-file dedup per file.

All new IDs are drawn from a single counter starting at 100. Already-used
IDs anywhere in the save are added to an "in_use" set up front so new
assignments never collide.

The regex matches `_(\d{3,})` (3+ digit IDs) so it catches the low-range
values previous scripts assigned, not just the 8+ digit ones KSP's own
generator produces. Anchored to `part=`, `link=`, `sym=`, `attN=`,
`srfN=` keywords to avoid false matches on coordinates / animation frames.

Backs up every file it modifies as <filename>.bak.unified_fix beside the
original.

Usage:
    python fix_ksp_save_ids.py /path/to/saves/<savename>/
    python fix_ksp_save_ids.py /path/to/saves/<savename>/ --dry-run

Always make a full manual backup of the save folder before running.

License: public domain / CC0.
"""
import argparse
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path


# Numbers below this aren't considered IDs (could be coordinates / animation
# frames / variant indices). 100 matches the lowest seed used by previous
# scripts in this toolkit.
ID_MIN = 100

# Starting point when assigning new IDs.
SEED_LOW = 100

# Filename fragments that indicate a backup (we never read these).
BACKUP_FRAGMENTS = (
    ".bak.", ".unified_fix",
    ".cross_fix", ".before_fix", ".before_kct_fix",
    ".before_lowid_fix", ".before_dupid_fix",
)

# sfs-style ID fields. Only cid (part-level craft id) and persistentId
# (vessel/part/module unique id) need to be unique. uid, pid, mid, and
# missionId have legitimate shared values across parts (e.g. mid is a
# manufacturer ID shared by all instances of a part type), so excluded.
# Also: value 0 is treated specially — it's a common placeholder for
# uninitialized fields and shouldn't be dedup'd as if it were a real ID.
SFS_ID_RE = re.compile(
    r"^(\s*)(cid|persistentId)\s*=\s*(\d+)\s*$",
    re.MULTILINE,
)

# Craft-format `_<id>` part references. Anchored to known field keywords.
CRAFT_REF_DEFINE_RE = re.compile(
    r"(?<![\w.])((?:part|link|sym)\s*=\s*[\w.-]+_)(\d+)\b",
)
CRAFT_REF_ATTACH_RE = re.compile(
    r"(?<![\w.])((?:attN|srfN)\s*=\s*\w+,[\w.-]+_)(\d+)\b",
)
CRAFT_REF_PATTERNS = (CRAFT_REF_DEFINE_RE, CRAFT_REF_ATTACH_RE)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def is_excluded_backup(path: Path) -> bool:
    s = str(path).lower()
    return any(frag.lower() in s for frag in BACKUP_FRAGMENTS)


def extract_sfs_id_spans(content: str) -> list[tuple[int, str, int, int, int]]:
    """(line_no, field, value, span_start, span_end) for each cid/persistentId/etc."""
    out = []
    for m in SFS_ID_RE.finditer(content):
        line_no = content.count("\n", 0, m.start()) + 1
        out.append((line_no, m.group(2), int(m.group(3)), m.start(3), m.end(3)))
    return out


def extract_craft_ref_spans(content: str) -> list[tuple[int, int, int, int]]:
    """(line_no, value, span_start, span_end) for each `_<id>` craft reference >= ID_MIN."""
    out = []
    for pat in CRAFT_REF_PATTERNS:
        for m in pat.finditer(content):
            v = int(m.group(2))
            if v < ID_MIN:
                continue
            line_no = content.count("\n", 0, m.start()) + 1
            out.append((line_no, v, m.start(2), m.end(2)))
    return out


def apply_span_replacements(content: str,
                            replacements: list[tuple[int, int, str]]) -> str:
    """Apply (start, end, new_text) edits to `content`, processed back-to-front."""
    replacements = sorted(replacements, key=lambda r: r[0], reverse=True)
    out = content
    for s, e, new in replacements:
        out = out[:s] + new + out[e:]
    return out


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("save_dir", type=Path, help="Save folder (containing persistent.sfs)")
    p.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = p.parse_args()

    save_dir: Path = args.save_dir
    if not save_dir.is_dir():
        print(f"ERROR: {save_dir} is not a directory", file=sys.stderr)
        return 1
    persistent = save_dir / "persistent.sfs"
    if not persistent.exists():
        print(f"ERROR: {persistent} not found", file=sys.stderr)
        return 1

    # ----- Discover files -----
    all_files = []
    for ext in ("*.sfs", "*.craft"):
        for path in save_dir.rglob(ext):
            if is_excluded_backup(path):
                continue
            all_files.append(path)

    zone_a_crafts: list[Path] = []
    zone_b: list[Path] = []
    for p_ in all_files:
        if p_.suffix.lower() == ".craft":
            zone_a_crafts.append(p_)
        elif p_.name == "persistent.sfs":
            pass  # tracked separately
        else:
            zone_b.append(p_)
    zone_a_crafts.sort()  # deterministic ordering

    print(f"Discovered {len(all_files)} files:")
    print(f"  Zone A: persistent.sfs + {len(zone_a_crafts)} craft file(s)")
    print(f"  Zone B: {len(zone_b)} quicksave/backup .sfs file(s)")

    # ----- Read all contents -----
    file_contents: dict[Path, str] = {}
    for path in [persistent] + zone_a_crafts + zone_b:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            file_contents[path] = f.read()

    # ----- Build the global in-use set (all IDs anywhere in zone A) -----
    in_use: set[int] = set()

    def harvest_ids(content: str) -> set[int]:
        ids: set[int] = set()
        for _, _, v, _, _ in extract_sfs_id_spans(content):
            if v >= ID_MIN:
                ids.add(v)
        for _, v, _, _ in extract_craft_ref_spans(content):
            ids.add(v)
        return ids

    for path in [persistent] + zone_a_crafts:
        in_use |= harvest_ids(file_contents[path])

    # Also pull loose `_<digits>` tokens (3+ digits) from persistent.sfs in case
    # something inside KCT ShipNodes uses a different anchor than our regexes.
    LOOSE_ID_RE = re.compile(rf"_(\d{{3,}})(?!\d)")
    for m in LOOSE_ID_RE.finditer(file_contents[persistent]):
        in_use.add(int(m.group(1)))

    print(f"Distinct in-use IDs across Zone A (>= {ID_MIN}): {len(in_use):,}")

    next_counter = SEED_LOW
    def next_unused() -> int:
        nonlocal next_counter
        while next_counter in in_use:
            next_counter += 1
        in_use.add(next_counter)
        c = next_counter
        next_counter += 1
        return c

    # ----- Pass A1: intra-file dedup of persistent.sfs -----
    # For each cid/persistentId line that's a repeat of one seen earlier in the
    # same file, remap to a new ID. Apply same remap to any craft-format `_<id>`
    # token inside persistent.sfs that matches (KCT ShipNodes carry these).
    persistent_content = file_contents[persistent]
    sfs_id_spans = extract_sfs_id_spans(persistent_content)
    craft_ref_spans_persistent = extract_craft_ref_spans(persistent_content)

    seen: set[tuple[str, int]] = set()
    a1_remap: dict[int, int] = {}
    a1_replacements: list[tuple[int, int, str]] = []

    for line_no, field, value, span_start, span_end in sfs_id_spans:
        # Skip 0 — it's a placeholder for uninitialized fields, not a real ID
        if value == 0:
            continue
        key = (field, value)
        if key in seen:
            # Each duplicate occurrence gets its own new unique ID. If we
            # reused the same new ID for all repeat occurrences, we'd just
            # be creating fresh duplicates.
            new_id = next_unused()
            a1_remap[value] = new_id  # tracked for reporting only
            a1_replacements.append((span_start, span_end, str(new_id)))
        else:
            seen.add(key)

    # If we remapped any IDs, also rewrite matching `_<id>` tokens inside
    # persistent.sfs (KCT ShipNode embedded references). BUT only when the
    # remapped ID is actually present as both a duplicate sfs cid AND has
    # corresponding craft tokens — we already handled the unique kept copy by
    # leaving it untouched.
    # (Conservative: only remap craft tokens whose old id was actually
    # remapped to a new id via A1. The kept copy's `_<id>` references stay
    # unchanged.)
    for span_line, value, span_start, span_end in craft_ref_spans_persistent:
        if value in a1_remap:
            # Need to decide: this token references a part. If the part's cid
            # was remapped (a1_remap[value]), the token should follow.
            # But if the same `_<value>` exists in MULTIPLE places (the "kept"
            # part and the "remapped" part), it's ambiguous.
            # Heuristic: leave persistent.sfs craft tokens unchanged — they're
            # already pointing to the "first occurrence" cid which keeps its
            # ID. This is the right call for the common case (KCT ShipNode
            # cross-references within the ShipNode).
            pass  # intentionally skip; see comment above

    a1_persistent_content = apply_span_replacements(persistent_content, a1_replacements)
    file_contents[persistent] = a1_persistent_content  # in-memory update

    # Rebuild persistent.sfs's ID inventory after A1
    persistent_ids_after_a1: set[int] = harvest_ids(a1_persistent_content)

    print(f"\nPass A1 (persistent.sfs intra-file dedup): "
          f"{len(a1_replacements)} duplicate(s) remapped")

    # ----- Pass A2: each .craft vs persistent.sfs -----
    craft_remaps: dict[Path, dict[int, int]] = {}  # path -> {old_id: new_id}
    a2_total = 0

    for craft_path in zone_a_crafts:
        content = file_contents[craft_path]
        craft_unique_ids: set[int] = set()
        for _, v, _, _ in extract_craft_ref_spans(content):
            craft_unique_ids.add(v)
        for _, _, v, _, _ in extract_sfs_id_spans(content):
            if v >= ID_MIN:
                craft_unique_ids.add(v)

        collisions = craft_unique_ids & persistent_ids_after_a1
        if not collisions:
            craft_remaps[craft_path] = {}
            continue

        remap: dict[int, int] = {}
        for old in sorted(collisions):
            new = next_unused()
            remap[old] = new
        craft_remaps[craft_path] = remap
        a2_total += len(remap)

    print(f"Pass A2 (craft vs persistent.sfs collisions): "
          f"{a2_total} unique ID(s) remapped across "
          f"{sum(1 for r in craft_remaps.values() if r)} craft file(s)")

    # ----- Pass A3: inter-craft collisions (crafts that share IDs with each
    # other, neither of which was just remapped in A2) -----
    a3_total = 0

    # Build POST-A2 unique-id-set for each craft
    def post_a2_ids(craft_path: Path) -> set[int]:
        content = file_contents[craft_path]
        ids: set[int] = set()
        remap = craft_remaps[craft_path]
        for _, v, _, _ in extract_craft_ref_spans(content):
            ids.add(remap.get(v, v))
        for _, _, v, _, _ in extract_sfs_id_spans(content):
            if v >= ID_MIN:
                ids.add(remap.get(v, v))
        return ids

    craft_id_sets: dict[Path, set[int]] = {c: post_a2_ids(c) for c in zone_a_crafts}

    # For each ID that's in 2+ crafts, keep the first craft (alphabetical) and
    # remap the others.
    id_to_crafts: dict[int, list[Path]] = defaultdict(list)
    for c, ids in craft_id_sets.items():
        for v in ids:
            id_to_crafts[v].append(c)

    for old_id, crafts in id_to_crafts.items():
        if len(crafts) <= 1:
            continue
        # Alphabetically-first craft keeps the ID
        crafts_sorted = sorted(crafts, key=lambda p: p.name)
        for c in crafts_sorted[1:]:
            new_id = next_unused()
            craft_remaps[c][old_id] = new_id
            craft_id_sets[c].discard(old_id)
            craft_id_sets[c].add(new_id)
            a3_total += 1

    print(f"Pass A3 (inter-craft collisions): {a3_total} ID(s) remapped")

    # ----- Apply craft remaps -----
    craft_replacements: dict[Path, list[tuple[int, int, str]]] = {}
    for craft_path, remap in craft_remaps.items():
        if not remap:
            continue
        content = file_contents[craft_path]
        repls: list[tuple[int, int, str]] = []
        for _, v, ss, ee in extract_craft_ref_spans(content):
            if v in remap:
                repls.append((ss, ee, str(remap[v])))
        for _, _, v, ss, ee in extract_sfs_id_spans(content):
            if v in remap and v >= ID_MIN:
                repls.append((ss, ee, str(remap[v])))
        craft_replacements[craft_path] = repls
        if repls and not args.dry_run:
            file_contents[craft_path] = apply_span_replacements(content, repls)

    # ----- Pass B: Zone B intra-file dedup -----
    b_total = 0
    b_replacements: dict[Path, list[tuple[int, int, str]]] = {}
    for path in zone_b:
        content = file_contents[path]
        sfs_ids = extract_sfs_id_spans(content)
        local_in_use: set[int] = {v for _, _, v, _, _ in sfs_ids if v >= ID_MIN}
        local_counter = SEED_LOW
        def b_next() -> int:
            nonlocal local_counter
            while local_counter in local_in_use:
                local_counter += 1
            local_in_use.add(local_counter)
            v = local_counter
            local_counter += 1
            return v

        seen_b: set[tuple[str, int]] = set()
        local_remap: dict[int, int] = {}
        repls: list[tuple[int, int, str]] = []
        for line_no, field, value, ss, ee in sfs_ids:
            if value == 0:
                continue  # placeholder, not a real ID
            key = (field, value)
            if key in seen_b:
                # Each duplicate gets its own new unique ID (otherwise we
                # just create fresh duplicates of the new value).
                new_id = b_next()
                local_remap[value] = new_id  # tracked for reporting only
                repls.append((ss, ee, str(new_id)))
            else:
                seen_b.add(key)
        if repls:
            b_replacements[path] = repls
            b_total += len(repls)

    print(f"Pass B (zone B intra-file dedup): {b_total} duplicate(s) "
          f"across {len(b_replacements)} file(s)")

    # ----- Report summary -----
    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    if a1_replacements:
        print(f"\npersistent.sfs intra-file ({len(a1_replacements)} fix(es)):")
        for ss, _, new in a1_replacements[:10]:
            # find old value by counting newlines is overkill; just report
            pass
        # Show first few remaps
        for old, new in list(a1_remap.items())[:10]:
            print(f"  {old:>12} -> {new}")
        if len(a1_remap) > 10:
            print(f"  ... and {len(a1_remap) - 10} more")

    if any(r for r in craft_remaps.values()):
        print(f"\nCraft files ({sum(len(r) for r in craft_remaps.values())} unique remaps "
              f"across {sum(1 for r in craft_remaps.values() if r)} craft(s)):")
        for c, r in sorted(craft_remaps.items()):
            if not r:
                continue
            print(f"  {c.name}: {len(r)} ID(s)")

    if b_replacements:
        print(f"\nZone B files ({b_total} fix(es) across {len(b_replacements)} file(s)):")
        for path, repls in sorted(b_replacements.items()):
            print(f"  {path.name}: {len(repls)}")

    # ----- Write -----
    if args.dry_run:
        print("\n--dry-run specified, no changes written.")
        return 0

    print()
    files_written = 0

    # persistent.sfs
    if a1_replacements:
        backup = persistent.with_name(persistent.name + ".bak.unified_fix")
        shutil.copy2(persistent, backup)
        with open(persistent, "w", encoding="utf-8", newline="") as f:
            f.write(file_contents[persistent])
        print(f"  wrote {persistent.relative_to(save_dir)} "
              f"(backup: {backup.name})")
        files_written += 1

    # craft files
    for craft_path, repls in craft_replacements.items():
        if not repls:
            continue
        backup = craft_path.with_name(craft_path.name + ".bak.unified_fix")
        shutil.copy2(craft_path, backup)
        with open(craft_path, "w", encoding="utf-8", newline="") as f:
            f.write(file_contents[craft_path])
        print(f"  wrote {craft_path.relative_to(save_dir)} "
              f"(backup: {backup.name})")
        files_written += 1

    # zone B
    for path, repls in b_replacements.items():
        content = file_contents[path]
        new_content = apply_span_replacements(content, repls)
        backup = path.with_name(path.name + ".bak.unified_fix")
        shutil.copy2(path, backup)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_content)
        print(f"  wrote {path.relative_to(save_dir)} "
              f"(backup: {backup.name})")
        files_written += 1

    print(f"\nDone. {files_written} file(s) modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
