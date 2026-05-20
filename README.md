KSP-Fix-Duplicate-IDs
A Python utility that repairs duplicate cid and persistentId entries in Kerbal Space Program persistent.sfs save files.

What it fixes
KSP installs with OhScrap + ScrapYard (and often KCT) can develop save-file corruption where multiple parts share the same cid or where multiple module instances share the same persistentId. KSP detects this on every save load and floods KSP.log with errors of the form:

[ERR] [FlowGraph]: Graph already contains item! Part <name> with id <N>
In bad cases the log spam reaches millions of lines, lags the editor severely, and can crash KSP with a stack overflow inside FlowGraph reconciliation.

The corruption is caused by ScrapYard's "Found inventory part on vessel that is not in inventory. Resetting." recovery path, which can be triggered either by:

PartModule value drift between flight and craft-file storage (RealChute mutable state, ProceduralPart float drift — diagnosis credit: diffie on the KSP forum)
OhScrap RTAntennaFailureModule.Initialise() throwing NullReferenceException during the inventory-apply event
Both paths leave duplicate IDs in persistent.sfs that persist across loads.

Usage
python fix_ksp_duplicate_ids.py /path/to/saves/<save-name>/persistent.sfs
Add --dry-run to see what would change without modifying the file:

python fix_ksp_duplicate_ids.py /path/to/persistent.sfs --dry-run
The script makes its own backup (persistent.sfs.bak.before_dupid_fix) beside the original before overwriting. Make a manual backup too — save edits are inherently risky.

Requirements
Python 3.8 or newer. No external dependencies.

Related issues
ScrapYard #52: https://github.com/zer0Kerbal/ScrapYard/issues/52
ScrapYard #81: https://github.com/zer0Kerbal/ScrapYard/issues/81
OhScrap #91: https://github.com/zer0Kerbal/OhScrap/issues/91
KSP forum thread: https://forum.kerbalspaceprogram.com/topic/192456-1124-scrapyard-syd-the-common-part-inventory-v22990-prerelease-edition-08-jan-2023/
License
Public domain / CC0. Use, modify, redistribute freely.
