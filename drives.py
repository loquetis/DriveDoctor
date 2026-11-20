"""DriveDoctor engine: read drive health (smartctl JSON first, Windows storage cmdlets as fallback), normalise it,
and turn it into a plain-English verdict. Read-only. The only external commands are the fixed ones below."""
import json
import os
import re
import shutil
import subprocess
import sys

import storage

IS_WIN = os.name == "nt"
FIXTURES = os.environ.get("DRIVEDOCTOR_FIXTURES")        # tests: read canned JSON instead of running anything
NO_WINDOW = 0x08000000 if IS_WIN else 0
LEVELS = {-1: "Unknown", 0: "Good", 1: "Watch", 2: "Replace soon"}
DEV_RE = re.compile(r"^[A-Za-z0-9/_:.\-]{1,64}$")
TYPE_RE = re.compile(r"^[A-Za-z0-9,]{1,32}$")
PS_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$o=@();Get-PhysicalDisk|ForEach-Object{$d=$_;$r=$d|Get-StorageReliabilityCounter;"
    "$o+=[pscustomobject]@{Name=$d.FriendlyName;Model=$d.Model;Serial=$d.SerialNumber;Firmware=$d.FirmwareVersion;"
    "MediaType=[string]$d.MediaType;BusType=[string]$d.BusType;Size=[int64]$d.Size;Health=[string]$d.HealthStatus;"
    "Operational=(@($d.OperationalStatus) -join ',');DeviceId=[string]$d.DeviceId;Temperature=$r.Temperature;Wear=$r.Wear;"
    "PowerOnHours=$r.PowerOnHours;ReadErrorsUncorrected=$r.ReadErrorsUncorrected;WriteErrorsUncorrected=$r.WriteErrorsUncorrected;"
    "ReadErrorsTotal=$r.ReadErrorsTotal;WriteErrorsTotal=$r.WriteErrorsTotal;StartStopCycleCount=$r.StartStopCycleCount}};"
    "ConvertTo-Json -InputObject @($o) -Compress -Depth 3")


def is_admin():
    if FIXTURES and os.environ.get("DRIVEDOCTOR_FAKE_ADMIN") is not None:
        return os.environ["DRIVEDOCTOR_FAKE_ADMIN"] == "1"
    if not IS_WIN:
        return os.geteuid() == 0
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- running tools
def find_smartctl():
    if FIXTURES:
        return "fixture"
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(exe_dir, "smartctl.exe"), os.path.join(exe_dir, "smartmontools", "smartctl.exe")):
        if os.path.isfile(c):
            return c
    p = None
    try:
        p = storage.extract_smartctl()
    except OSError:
        pass
    return p or shutil.which("smartctl")


def _run(cmd, timeout=40):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)


def run_smartctl(path, args):
    """Returns (json_or_None, exit_code). smartctl's exit code is a bit mask (non-zero is normal for old drives)."""
    if FIXTURES:
        name = "scan.json" if args[0] == "--scan" else "smart_%s.json" % re.sub(r"[^A-Za-z0-9]", "_", args[-1])
        fp = os.path.join(FIXTURES, name)
        if not os.path.isfile(fp):
            return None, 2
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        return d, (d.get("smartctl") or {}).get("exit_status", 0)
    try:
        r = _run([path] + args)
    except (OSError, subprocess.TimeoutExpired):
        return None, -1
    try:
        return json.loads(r.stdout.decode("utf-8", "replace")), r.returncode
    except ValueError:
        return None, r.returncode


def powershell_disks():
    if FIXTURES:
        fp = os.path.join(FIXTURES, "ps.json")
        if not os.path.isfile(fp):
            return []
        data = json.load(open(fp, encoding="utf-8"))
    elif IS_WIN:
        try:
            r = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", PS_SCRIPT], timeout=60)
            data = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return []
    else:
        return []
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)]


# ---------------------------------------------------------------- normalising
def norm_serial(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _attr_map(j):
    out = {}
    for a in ((j.get("ata_smart_attributes") or {}).get("table") or []):
        out[a.get("id")] = a
    return out


def _raw(a):
    return None if a is None else _num((a.get("raw") or {}).get("value"))


def _raw16(a):
    """Low 16 bits of a raw value: some vendors pack extra counters in the high bytes (e.g. temperature min/max)."""
    v = _raw(a)
    return None if v is None else v & 0xFFFF


def drive_type(j, ps=None):
    proto = ((j.get("device") or {}).get("protocol") or "").upper()
    dtype = ((j.get("device") or {}).get("type") or "").lower()
    if proto == "NVME" or dtype.startswith("nvme"):
        return "NVMe"
    rot = j.get("rotation_rate")
    if rot == 0 or (isinstance(rot, int) and rot == 0):
        return "SSD"
    if isinstance(rot, int) and rot > 0:
        return "HDD"
    mt = ((ps or {}).get("MediaType") or "").upper()
    if mt in ("SSD", "HDD"):
        return mt
    if (ps or {}).get("BusType", "").upper() == "NVME":
        return "NVMe"
    return "Unknown"


def from_smartctl(j, dev):
    """Normalise one `smartctl -a -j` result."""
    msgs = [m.get("string", "") for m in (j.get("smartctl") or {}).get("messages", []) if m.get("severity") in ("error", "warning")]
    d = {"source": "smartctl", "device": dev.get("name"), "dev_type": dev.get("type"), "messages": msgs,
         "model": j.get("model_name") or j.get("scsi_model_name"), "serial": j.get("serial_number"), "firmware": j.get("firmware_version"),
         "capacity": _num((j.get("user_capacity") or {}).get("bytes")) or _num(j.get("nvme_total_capacity")),
         "interface": (j.get("device") or {}).get("protocol")}
    d["type"] = drive_type(j)
    if not d["model"] and not d["serial"]:
        d["denied"] = any(re.search(r"denied|permission|privilege|administrator|Error=5\b", m, re.I) for m in msgs)
        d["unreadable"] = True
        return d
    d["smart_passed"] = (j.get("smart_status") or {}).get("passed")
    d["temp"] = _num((j.get("temperature") or {}).get("current"))
    d["poh"] = _num((j.get("power_on_time") or {}).get("hours"))
    d["power_cycles"] = _num(j.get("power_cycle_count"))
    nv = j.get("nvme_smart_health_information_log")
    attrs = _attr_map(j)
    d["attributes"] = [{"id": a.get("id"), "name": a.get("name"), "value": a.get("value"), "worst": a.get("worst"), "thresh": a.get("thresh"),
                        "raw": (a.get("raw") or {}).get("string"), "failing": bool(a.get("when_failed"))} for a in attrs.values()]
    if nv:
        d["nvme"] = True
        d["critical_warning"] = _num(nv.get("critical_warning"))
        d["life_used"] = _num(nv.get("percentage_used"))
        d["spare"] = _num(nv.get("available_spare"))
        d["spare_threshold"] = _num(nv.get("available_spare_threshold"))
        d["media_errors"] = _num(nv.get("media_errors"))
        d["error_log_entries"] = _num(nv.get("num_err_log_entries"))
        d["unsafe"] = _num(nv.get("unsafe_shutdowns"))
        d["poh"] = _num(nv.get("power_on_hours")) if d["poh"] is None else d["poh"]
        d["power_cycles"] = _num(nv.get("power_cycles")) if d["power_cycles"] is None else d["power_cycles"]
        d["temp"] = _num(nv.get("temperature")) if d["temp"] is None else d["temp"]
        du = _num(nv.get("data_units_written"))
        d["written"] = du * 512000 if du is not None else None
        d["warning_temp_time"] = _num(nv.get("warning_temp_time"))
        d["critical_temp_time"] = _num(nv.get("critical_comp_time"))
    else:
        d["realloc"] = _raw(attrs.get(5))
        d["pending"] = _raw(attrs.get(197))
        d["offline_uncorrectable"] = _raw(attrs.get(198))
        d["uncorrect"] = _raw(attrs.get(187))
        d["crc"] = _raw(attrs.get(199))
        d["unsafe"] = _raw(attrs.get(192))
        if d["temp"] is None:
            d["temp"] = _raw16(attrs.get(194)) if 194 in attrs else _raw16(attrs.get(190))
        if d["poh"] is None:
            d["poh"] = _raw(attrs.get(9))
        best = None
        for aid in (231, 233, 177, 202, 169):          # SSD "life left" attributes: normalised value counts down from 100
            a = attrs.get(aid)
            if a and _num(a.get("value")) is not None and 0 <= a["value"] <= 100 and (best is None or a["value"] < best["value"]):
                best = a                                  # vendors differ in which one is real; the lowest is the safe choice
        if best:
            d["life_used"] = 100 - best["value"]
            d["life_attr"] = best.get("name")
        w = attrs.get(241) or attrs.get(246)
        if w and _raw(w) is not None and d["capacity"]:
            d["written"] = _raw(w) * 512 if "LBA" in (w.get("name") or "").upper() else None
    st = ((j.get("ata_smart_self_test_log") or {}).get("standard") or {}).get("table") or []
    if st:
        last = st[0]
        d["last_selftest"] = {"type": (last.get("type") or {}).get("string"), "status": (last.get("status") or {}).get("string"),
                              "passed": (last.get("status") or {}).get("passed"), "hours": last.get("lifetime_hours")}
    d["error_log_count"] = _num((j.get("ata_smart_error_log") or {}).get("summary", {}).get("count"))
    return d


def from_windows(ps):
    """A drive known only from Get-PhysicalDisk / Get-StorageReliabilityCounter."""
    d = {"source": "windows", "model": ps.get("Model") or ps.get("Name"), "serial": (ps.get("Serial") or "").strip(),
         "firmware": ps.get("Firmware"), "capacity": _num(ps.get("Size")), "interface": ps.get("BusType"), "messages": [],
         "temp": _num(ps.get("Temperature")), "poh": _num(ps.get("PowerOnHours")), "windows_health": ps.get("Health"),
         "windows_operational": ps.get("Operational"), "attributes": [], "device": None}
    wear = _num(ps.get("Wear"))
    d["life_used"] = wear
    unc = [_num(ps.get("ReadErrorsUncorrected")), _num(ps.get("WriteErrorsUncorrected"))]
    d["uncorrect"] = sum(x for x in unc if x) if any(x is not None for x in unc) else None
    d["power_cycles"] = _num(ps.get("StartStopCycleCount"))
    d["type"] = {"SSD": "SSD", "HDD": "HDD"}.get((ps.get("MediaType") or "").upper(), "NVMe" if (ps.get("BusType") or "").upper() == "NVME" else "Unknown")
    if d["temp"] == 0:
        d["temp"] = None
    return d


def merge_windows(d, ps):
    """Add what Windows knows to a smartctl drive (health flag, bus type, media type, missing numbers)."""
    d["windows_health"] = ps.get("Health")
    d["windows_operational"] = ps.get("Operational")
    d["bus"] = ps.get("BusType")
    if d.get("type") == "Unknown":
        d["type"] = from_windows(ps)["type"]
    if d.get("temp") is None and _num(ps.get("Temperature")):
        d["temp"] = _num(ps.get("Temperature"))
    if d.get("poh") is None:
        d["poh"] = _num(ps.get("PowerOnHours"))
    if d.get("life_used") is None and _num(ps.get("Wear")) is not None:
        d["life_used"] = _num(ps.get("Wear"))
    return d


def match_ps(d, ps_list, used):
    s = norm_serial(d.get("serial"))
    for i, p in enumerate(ps_list):
        if i in used:
            continue
        ps_s = norm_serial(p.get("Serial"))
        if s and ps_s and (s == ps_s or s in ps_s or ps_s in s):
            return i
    m = (d.get("model") or "").strip().lower()
    for i, p in enumerate(ps_list):
        if i in used:
            continue
        pm = ((p.get("Model") or p.get("Name") or "")).strip().lower()
        if m and pm and (m == pm or m in pm or pm in m) and (not d.get("capacity") or not p.get("Size") or abs(d["capacity"] - p["Size"]) < 0.06 * d["capacity"]):
            return i
    return None


def drive_key(d):
    s = norm_serial(d.get("serial"))
    if s:
        return s
    return re.sub(r"[^a-z0-9]", "", ((d.get("model") or "drive") + str(d.get("capacity") or "")).lower())


# ---------------------------------------------------------------- verdict
def verdict(d, history=None):
    """Returns (level, headline, reasons[{level,text}], advice). level: -1 unknown, 0 good, 1 watch, 2 replace soon."""
    reasons = []

    def add(level, text):
        reasons.append({"level": level, "text": text})
    t = d.get("type")
    if d.get("unreadable"):
        why = "Windows blocked the read: run DriveDoctor as administrator." if d.get("denied") else "This drive did not answer SMART requests (common with some USB enclosures)."
        return -1, "Could not read health data", [{"level": -1, "text": why}] + [{"level": -1, "text": m} for m in d.get("messages", [])[:2]], \
            "Use the Restart as administrator button, then refresh." if d.get("denied") else "Nothing is wrong that we can see. Windows' own status is shown below if it has one."
    if d.get("smart_passed") is False:
        add(2, "The drive's own SMART self-assessment says it is FAILING.")
    if d.get("windows_health") == "Unhealthy":
        add(2, "Windows reports this drive as Unhealthy.")
    elif d.get("windows_health") == "Warning":
        add(1, "Windows reports a health Warning for this drive.")
    if d.get("critical_warning"):
        add(2, "The NVMe drive raised a critical warning flag (code %d)." % d["critical_warning"])
    lu = d.get("life_used")
    if lu is not None:
        left = max(0, 100 - lu)
        if left <= 10:
            add(2, "Only about %d%% of the drive's rated lifetime is left." % left)
        elif left <= 30:
            add(1, "About %d%% of the drive's rated lifetime is left." % left)
    if d.get("spare") is not None:
        thr = d.get("spare_threshold") if d.get("spare_threshold") is not None else 10
        if d["spare"] <= thr:
            add(2, "Spare capacity (%d%%) has dropped to the drive's warning level." % d["spare"])
        elif d["spare"] < 50:
            add(1, "Spare capacity is down to %d%%." % d["spare"])
    me = d.get("media_errors")
    if me:
        add(2 if me >= 10 else 1, "%d media/data integrity error%s logged." % (me, "" if me == 1 else "s"))
    for key, label, hi in (("realloc", "reallocated sector", 50), ("pending", "sector waiting to be reallocated", 10),
                           ("offline_uncorrectable", "uncorrectable sector", 10), ("uncorrect", "uncorrectable read error", 10)):
        v = d.get(key)
        if v:
            add(2 if v >= hi else 1, "%d %s%s." % (v, label, "" if v == 1 else "s") + (" Sectors that fail are a classic sign a hard drive is dying." if key in ("pending", "realloc") and t == "HDD" else ""))
    if d.get("crc") and d["crc"] >= 10:
        add(1, "%d interface CRC errors: usually a loose or bad SATA cable, not the drive itself." % d["crc"])
    temp = d.get("temp")
    if temp is not None and temp > 0:
        hot, crit = (50, 60) if t == "HDD" else (70, 80)
        if temp >= crit:
            add(2, "Running very hot (%d °C)." % temp)
        elif temp >= hot:
            add(1, "Running warm (%d °C)." % temp)
    if d.get("warning_temp_time") or d.get("critical_temp_time"):
        add(1, "The drive has spent time above its temperature limits (%d min warning, %d min critical)." % (d.get("warning_temp_time") or 0, d.get("critical_temp_time") or 0))
    poh = d.get("poh")
    if poh and t == "HDD" and poh > 50000:
        add(1, "Old for a hard drive: %s powered-on hours (about %.1f years)." % (f"{poh:,}", poh / 8766))
    for x in d.get("attributes", []):
        if x.get("failing"):
            add(2, "SMART attribute %s (%s) has failed its threshold." % (x.get("name"), x.get("id")))
    for w in trend_warnings(d, history or []):
        reasons.append(w)
    level = max([r["level"] for r in reasons] + [0])
    if all(d.get(k) is None for k in ("temp", "life_used", "realloc", "pending", "uncorrect", "media_errors", "poh")) and d.get("smart_passed") is None \
            and not d.get("windows_health"):
        return -1, "No health data available", [{"level": -1, "text": "Neither SMART nor Windows returned health numbers for this drive."}], \
            "Try Restart as administrator."
    if not reasons:
        reasons.append({"level": 0, "text": "SMART self-assessment passed and no warning signs in the numbers we can read." if d.get("smart_passed") else "No warning signs in the numbers we can read."})
    head = {0: "Healthy", 1: "Keep an eye on this drive", 2: "Back up now and plan to replace this drive"}[level]
    advice = {0: "Nothing to do. Keep regular backups anyway: drives can fail without warning.",
              1: "It still works, but back up anything important and check again in a few days.",
              2: "Back up everything on it now. Replace it soon, before it fails completely."}[level]
    return level, head, sorted(reasons, key=lambda r: -r["level"]), advice


def trend_warnings(d, history):
    """Worsening values compared with the oldest reading from at least a day earlier (history rows ascending)."""
    out = []
    if len(history) < 2:
        return out
    import datetime as _dt
    newest = history[-1]
    old = None
    for r in history:
        if newest["ts"] - r["ts"] >= 20 * 3600:
            old = r
            break
    if old is None:
        return out
    when = _dt.datetime.fromtimestamp(old["ts"]).strftime("%b %d")
    cur = {k: d.get(k) if d.get(k) is not None else newest.get(k) for k in ("realloc", "pending", "uncorrect", "media_errors", "life_used", "crc")}
    for key, label, hi in (("realloc", "Reallocated sectors", 10), ("pending", "Pending sectors", 5), ("uncorrect", "Uncorrectable errors", 5), ("media_errors", "Media errors", 5)):
        a, b = old.get(key), cur.get(key)
        if a is not None and b is not None and b > a:
            out.append({"level": 2 if b - a >= hi else 1, "text": "%s went up from %d to %d since %s: the drive is getting worse." % (label, a, b, when), "trend": True})
    a, b = old.get("life_used"), cur.get("life_used")
    days = (newest["ts"] - old["ts"]) / 86400
    if a is not None and b is not None and b > a and days >= 7:
        rate = (b - a) / days
        left_days = (100 - b) / rate if rate > 0 else None
        if left_days is not None and left_days < 365 and b - a >= 1:
            out.append({"level": 2 if left_days < 90 else 1, "trend": True,
                        "text": "Wear is rising (%.0f%% to %.0f%% since %s). At this pace the rated life runs out in about %d months." % (a, b, when, max(1, round(left_days / 30)))})
    a, b = old.get("crc"), cur.get("crc")
    if a is not None and b is not None and b - a >= 20:
        out.append({"level": 1, "trend": True, "text": "Interface CRC errors keep growing (%d to %d): check the SATA cable." % (a, b)})
    return out


# ---------------------------------------------------------------- collection
def collect():
    """Read every drive once. Returns {drives:[...], tools:{...}}."""
    smartctl = find_smartctl()
    admin = is_admin()
    ps_list = powershell_disks()
    tools = {"smartctl": bool(smartctl), "smartctl_path": None if smartctl in (None, "fixture") else smartctl, "admin": admin,
             "powershell": bool(ps_list), "notes": []}
    drives, used = [], set()
    if smartctl:
        scan, _ = run_smartctl(smartctl, ["--scan", "-j"])
        devs = (scan or {}).get("devices") or []
        if not devs:
            tools["notes"].append("smartctl found no drives" + ("" if admin else " (it usually needs administrator rights)"))
        seen = set()
        for dev in devs:
            name, typ = dev.get("name") or "", dev.get("type") or ""
            if not DEV_RE.match(name) or not TYPE_RE.match(typ) or name in seen:
                continue
            seen.add(name)
            j, code = run_smartctl(smartctl, ["-a", "-j", "-d", typ, name])
            if j is None:
                d = {"source": "smartctl", "device": name, "dev_type": typ, "unreadable": True, "messages": ["smartctl gave no answer"], "type": "Unknown"}
            else:
                d = from_smartctl(j, dev)
            d["exit_code"] = code
            i = match_ps(d, ps_list, used) if not d.get("unreadable") or d.get("serial") else None
            if i is not None:
                used.add(i)
                d = merge_windows(d, ps_list[i])
            drives.append(d)
    nameless = [x for x in drives if x.get("unreadable") and not x.get("model") and not x.get("serial")]
    for i, p in enumerate(ps_list):
        if i not in used:
            drives.append(from_windows(p))             # a drive only Windows knows about (smartctl could not identify it)
    if ps_list and nameless:
        # smartctl could not even identify these devices, and Windows lists every drive anyway: show the Windows entries and say why.
        denied = any(x.get("denied") for x in nameless)
        for x in nameless:
            drives.remove(x)
        tools["notes"].append("smartctl could not open %d drive%s (%s), so only Windows' own health status is shown." %
                              (len(nameless), "" if len(nameless) == 1 else "s", "Windows needs administrator rights for that" if denied else "often a USB or RAID controller limitation"))
        tools["smartctl_blocked"] = len(nameless)
    for d in drives:
        d["key"] = drive_key(d)
        d.setdefault("type", "Unknown")
    return {"drives": drives, "tools": tools}
