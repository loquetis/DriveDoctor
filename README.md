# DriveDoctor 1.0.0

Shows the health of every physical drive in plain English (Good / Watch / Replace soon), keeps a history and warns when things get worse. **Read-only**, except one optional, clearly labelled and confirmed "short self-test".

- **How it works:** Python localhost-only server (127.0.0.1:8770, env `DRIVEDOCTOR_PORT`), private Edge app window, random token on changes, strict CSP.
- **Data sources:** bundled `smartctl` (smartmontools 7.5, 64-bit) called with `-j` for model, serial, type, capacity, temperature, SMART/NVMe health (reallocated/pending sectors, wear, spare, media errors, power-on hours...). Falls back to Windows PowerShell `Get-PhysicalDisk` / `Get-StorageReliabilityCounter` when smartctl cannot read a drive.
- **Administrator:** Windows only lets smartctl read SMART as admin. Without it the app still lists every drive with Windows' own health status and shows a banner with **Restart as administrator** (UAC prompt). Verdicts from Windows-only data are less detailed.
- **History:** SQLite at `%APPDATA%\DriveDoctor\drivedoctor.db` (at most one reading per drive per 20 min), trend charts, and warnings such as reallocated sectors or wear growing, temperature climbing. CSV export. Override location with `DRIVEDOCTOR_DATA`.
- **Self-test:** "Run short self-test" needs administrator rights and a confirm; only on drives smartctl can reach; strictly the drive's own 2-minute test.
- **smartmontools licence:** smartctl.exe, drivedb.h, COPYING.txt (GNU GPL v2+), AUTHORS.txt and README-SMARTMONTOOLS.txt are shipped inside the exe as a **separate, unmodified binary** and extracted to `%APPDATA%\DriveDoctor\smartmontools` next to their license; DriveDoctor only runs it as a subprocess. Source: https://www.smartmontools.org/
- **Tests:** `python tests/test_engine.py` (canned smartctl/PowerShell fixtures for ATA, NVMe, failing, USB), `python tests/uitest.py`, `python tests/test_should_exit.py`.

## Bugs / edge cases caught in review
1. smartctl exit status is a bitmask (non-zero even on success), so JSON is parsed regardless of exit code.
2. SSD "life left" lives in different attributes (177/202/231/233); takes the lowest candidate rather than the first.
3. On a real non-admin PC smartctl reports `Open failed, Error=5` for every drive, which the "needs administrator" detection did not recognise, and left an empty-name card. Now recognised, nameless unreadable entries replaced by the Windows entries plus an explanatory note.
4. Device names from the UI are validated against a strict pattern (no argument/command injection) before reaching smartctl; self-test needs `confirm` and admin.
5. Frequent refreshes would flood history; one reading per drive per 20 minutes.

## Limitations
Not fully verified: the elevated (UAC) path, smartctl on the RAID-bus drives and the self-test could not be exercised without approving a UAC prompt, so they are covered only by fixtures. Drives behind some USB bridges or RAID controllers may not expose SMART. A card reader with no media shows as "Unknown, 0 GB". Not code-signed.
