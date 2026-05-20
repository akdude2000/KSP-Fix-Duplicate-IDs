# KSP-Fix-Duplicate-IDs

A Python utility that repairs duplicate `cid` and `persistentId` entries in
Kerbal Space Program `persistent.sfs` save files.

## What it fixes

KSP installs with [OhScrap](https://github.com/zer0Kerbal/OhScrap) +
[ScrapYard](https://github.com/zer0Kerbal/ScrapYard) (and often
[KCT](https://github.com/linuxgurugamer/KCT)) can develop save-file corruption
where multiple parts share the same `cid` or where multiple module instances
share the same `persistentId`. KSP detects this on every save load and
floods `KSP.log` with errors of the form:
