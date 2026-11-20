"""Engine tests on canned smartctl/PowerShell JSON. Run: python tests/test_engine.py"""
import os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(); fx = os.path.join(tmp, "fx"); os.system(f'"{sys.executable}" "{HERE}/make_fixtures.py" "{fx}"')
os.environ["DRIVEDOCTOR_FIXTURES"] = fx; os.environ["DRIVEDOCTOR_FAKE_ADMIN"] = "1"; os.environ["DRIVEDOCTOR_DATA"] = tmp
import drives, storage
bad = 0
def ok(name, cond, detail=""):
    global bad; bad += (not cond); print(("PASS " if cond else "FAIL ") + name, detail if not cond else "")
res = drives.collect(); D = {d["model"]: d for d in res["drives"]}
ok("five entries (4 smartctl + Windows-only duplicate merged)", len(res["drives"]) == 5, [d.get("model") for d in res["drives"]])
for d in res["drives"]:
    d["level"], _, d["reasons"], _ = drives.verdict(d)[0], *drives.verdict(d)[1:]
V = {m: drives.verdict(d) for m, d in D.items()}
ok("NVMe healthy = Good", V["Samsung SSD 980 PRO 1TB"][0] == 0, V["Samsung SSD 980 PRO 1TB"])
nv = D["Samsung SSD 980 PRO 1TB"]
ok("NVMe fields: type, temp, life used, written", nv["type"] == "NVMe" and nv["temp"] == 41 and nv["life_used"] == 4 and abs(nv["written"] - 21e6 * 512000) < 1, nv)
ok("serial match merged Windows health", nv.get("windows_health") == "Healthy" and nv.get("bus") == "NVMe")
sd = D["CT500MX500SSD1"]; ok("SATA SSD: type from rotation_rate 0, life used from attr 202", sd["type"] == "SSD" and sd["life_used"] == 9 and V["CT500MX500SSD1"][0] == 0, sd.get("life_used"))
hd = D["WDC WD40EZRZ-00GXCB0"]; ok("HDD sector counts parsed", hd["type"] == "HDD" and hd["realloc"] == 24 and hd["pending"] == 8, hd)
ok("HDD with some bad sectors + Windows Warning = Watch", V["WDC WD40EZRZ-00GXCB0"][0] == 1, V["WDC WD40EZRZ-00GXCB0"])
ok("old HDD age noted", any("Old for a hard drive" in r["text"] for r in V["WDC WD40EZRZ-00GXCB0"][2]))
ok("failing SMART = Replace soon", V["ST2000DM008-2FR102"][0] == 2 and "FAILING" in V["ST2000DM008-2FR102"][2][0]["text"], V["ST2000DM008-2FR102"])
usb = [d for d in res["drives"] if d.get("model") == "Generic USB Drive"][0]
ok("USB without SMART falls back to Windows data, verdict Unknown/Good not crash", usb["source"] == "windows" and drives.verdict(usb)[0] in (-1, 0), drives.verdict(usb))
# non-admin denial
raw = {"smartctl": {"exit_status": 2, "messages": [{"string": "Smartctl open device: /dev/sda failed: Permission denied", "severity": "error"}]}, "device": {"name": "/dev/sda", "type": "sat"}}
nd = drives.from_smartctl(raw, {"name": "/dev/sda", "type": "sat"}); lv = drives.verdict(nd)
ok("permission denied -> unreadable, admin hint, level -1", nd["unreadable"] and nd["denied"] and lv[0] == -1 and "administrator" in lv[1 + 1][0]["text"], lv)
raw2 = {"smartctl": {"exit_status": 2, "messages": [{"string": "Smartctl open device: /dev/sda failed: \\\\.\\PhysicalDrive0: Open failed, Error=5", "severity": "error"}]}, "device": {"name": "/dev/sda", "type": "scsi"}}
ok("real Windows 'Open failed, Error=5' recognised as admin-needed", drives.from_smartctl(raw2, {"name": "/dev/sda", "type": "scsi"})["denied"])
tools_note = [n for n in res["tools"]["notes"]]
ok("USB device smartctl could not identify -> Windows entry used + explanatory note", any("could not open 1 drive" in n for n in tools_note), tools_note)
# command injection / bad device names never reach the shell
ok("device name validation", not drives.DEV_RE.match("/dev/sda; calc") and not drives.DEV_RE.match("/dev/sda && x") and drives.DEV_RE.match("/dev/nvme0") and not drives.TYPE_RE.match("sat;x"))
# thresholds
base = {"type": "SSD", "smart_passed": True, "temp": 30, "attributes": []}
ok("hot SSD watch/replace", drives.verdict(dict(base, temp=72))[0] == 1 and drives.verdict(dict(base, temp=85))[0] == 2)
ok("low life left", drives.verdict(dict(base, life_used=75))[0] == 1 and drives.verdict(dict(base, life_used=95))[0] == 2)
ok("nvme critical warning", drives.verdict(dict(base, nvme=True, critical_warning=4))[0] == 2)
ok("nvme spare below threshold", drives.verdict(dict(base, spare=8, spare_threshold=10))[0] == 2)
ok("media errors", drives.verdict(dict(base, media_errors=2))[0] == 1 and drives.verdict(dict(base, media_errors=40))[0] == 2)
ok("CRC alone is only Watch (cable)", drives.verdict(dict(base, crc=500))[0] == 1)
ok("temp 0 / missing ignored", drives.verdict(dict(base, temp=0))[0] == 0)
ok("no data at all -> Unknown", drives.verdict({"type": "Unknown", "attributes": []})[0] == -1)
# trend / worsening
now = time.time(); H = lambda days, **k: dict(ts=now - days * 86400, **k)
hist = [H(10, realloc=0, pending=0, life_used=10), H(5, realloc=2, pending=0, life_used=12), H(0, realloc=9, pending=3, life_used=15)]
d = dict(base, type="HDD", realloc=9, pending=3, life_used=15); lv, head, reasons, adv = drives.verdict(d, hist)
ok("worsening reallocated sectors flagged", any(r.get("trend") and "Reallocated" in r["text"] for r in reasons) and lv >= 1, reasons)
ok("wear pace warning (5 points in 10 days)", any(r.get("trend") and "Wear is rising" in r["text"] for r in reasons), reasons)
ok("same values = no trend warning", not drives.trend_warnings(d, [H(10, realloc=9, pending=3, life_used=15), H(0, realloc=9, pending=3, life_used=15)]))
ok("less than a day of history = no trend", not drives.trend_warnings(d, [H(0.2, realloc=0), H(0, realloc=9)]))
# history store
st = storage.Store(os.path.join(tmp, "h.db")); nv["level"] = 0
ok("record stored", st.record(nv, manual=True) and len(st.history(nv["key"])) == 1)
ok("auto reading within 20 min skipped, manual allowed", not st.record(nv) and st.record(nv, manual=True) and len(st.history(nv["key"])) == 2)
ok("unreadable drive not recorded", not st.record({"key": "x", "model": "m"}, manual=True))
print("FAILED %d" % bad if bad else "ALL PASS"); sys.exit(1 if bad else 0)
