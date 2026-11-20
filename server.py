"""DriveDoctor routes. Read-only except the clearly labeled, confirmed short self-test."""
import csv
import io
import os
import re
import sys
import threading
import time
from datetime import datetime

import appserver
import drives
import storage
from appserver import BadRequest, NotFound

kit = appserver.Kit("DriveDoctor", "DRIVEDOCTOR_PORT", 8770)
HOST, PORT = appserver.HOST, kit.port
last_seen = kit.last_seen
build_server = kit.build_server
VERSION = "1.0.0"
RANGES = {"week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400, "all": 10 * 365 * 86400}
STORE = None
quit_event = threading.Event()
SNAP = {"at": None, "drives": [], "tools": {}, "error": None}
STATE = {"refreshing": False, "next": None}
_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()


def init(store=None):
    global STORE
    STORE = store or storage.Store()


def _trend_rows(key):
    return STORE.history(key, time.time() - 90 * 86400)


def refresh(manual=False):
    if not _lock.acquire(blocking=False):
        return False
    STATE["refreshing"] = True
    try:
        res = drives.collect()
        out = []
        for d in res["drives"]:
            hist = _trend_rows(d["key"])
            level, head, reasons, advice = drives.verdict(d, hist)
            d["level"], d["level_name"], d["headline"], d["reasons"], d["advice"] = level, drives.LEVELS[level], head, reasons, advice
            if level >= 0 or d.get("temp") is not None:
                STORE.record(d, manual=manual)
            out.append(d)
        order = {2: 0, 1: 1, -1: 2, 0: 3}
        out.sort(key=lambda d: (order.get(d["level"], 4), d.get("model") or ""))
        SNAP.update(at=time.time(), drives=out, tools=res["tools"], error=None)
    except Exception as e:  # noqa: BLE001
        SNAP["error"] = "Could not read drives: %s" % e
    finally:
        STATE["refreshing"] = False
        _lock.release()
    return True


def _loop():
    refresh(manual=False)
    while not _stop.is_set():
        iv = STORE.get_settings()["interval_min"]
        STATE["next"] = (SNAP["at"] or time.time()) + iv * 60 if iv else None
        _wake.wait(2.0)
        _wake.clear()
        if STATE["next"] and time.time() >= STATE["next"] and not STATE["refreshing"]:
            refresh(manual=False)


def on_start():
    if STORE is None:
        init()
    threading.Thread(target=_loop, name="refresh", daemon=True).start()


def on_stop():
    _stop.set()
    _wake.set()


def _public(d):
    keep = {k: v for k, v in d.items() if k not in ("attributes",)}
    keep["attr_count"] = len(d.get("attributes") or [])
    return keep


@kit.get("/api/status")
def status(_q):
    st = STORE.get_settings()
    return {"version": VERSION, "now": time.time(), "settings": st, "refreshing": STATE["refreshing"], "at": SNAP["at"], "next": STATE["next"] if st["interval_min"] else None,
            "tools": SNAP["tools"], "error": SNAP["error"], "intervals": list(storage.INTERVALS), "admin": drives.is_admin(), "windows": drives.IS_WIN,
            "counts": {"drives": len(SNAP["drives"]), "worst": max([d["level"] for d in SNAP["drives"]] + [-1])}}


@kit.get("/api/drives")
def drives_list(_q):
    return {"at": SNAP["at"], "refreshing": STATE["refreshing"], "tools": SNAP["tools"], "drives": [_public(d) for d in SNAP["drives"]]}


def _find(key):
    for d in SNAP["drives"]:
        if d["key"] == key:
            return d
    raise NotFound("unknown drive")


@kit.get("/api/drive")
def drive_detail(q):
    d = _find(q.get("key") or "")
    return {"drive": d}


@kit.get("/api/history")
def history(q):
    d = _find(q.get("key") or "")
    rng = q.get("range") or "month"
    if rng not in RANGES:
        raise BadRequest("range must be one of: " + ", ".join(RANGES))
    rows = STORE.history(d["key"], time.time() - RANGES[rng])
    return {"key": d["key"], "range": rng, "from": time.time() - RANGES[rng], "to": time.time(), "points": rows}


@kit.post("/api/refresh")
def do_refresh(_d):
    if STATE["refreshing"]:
        return {"ok": False}
    threading.Thread(target=refresh, args=(True,), name="refresh-now", daemon=True).start()
    for _ in range(50):
        if STATE["refreshing"]:
            break
        time.sleep(0.01)
    return {"ok": True}


@kit.post("/api/settings")
def settings(d):
    try:
        st = STORE.set_settings({k: d[k] for k in ("interval_min",) if k in d})
    except ValueError as e:
        raise BadRequest(str(e)) from None
    _wake.set()
    return {"ok": True, "settings": st}


@kit.post("/api/selftest")
def selftest(d):
    """Starts a SHORT SMART self-test (a few minutes, read-only scan done by the drive's own firmware)."""
    if d.get("confirm") != "short-test":
        raise BadRequest("The self-test needs explicit confirmation")
    if not drives.is_admin():
        raise BadRequest("Starting a self-test needs administrator rights")
    sc = SNAP["tools"].get("smartctl_path") or (drives.find_smartctl() if drives.FIXTURES else None)
    if not sc:
        raise BadRequest("smartctl is not available")
    dr = _find(d.get("key") or "")
    name, typ = dr.get("device") or "", dr.get("dev_type") or ""
    if dr.get("source") != "smartctl" or dr.get("unreadable") or not drives.DEV_RE.match(name) or not drives.TYPE_RE.match(typ):
        raise BadRequest("This drive cannot be tested through smartctl")
    j, code = drives.run_smartctl(sc, ["-t", "short", "-d", typ, name])
    msgs = [m.get("string", "") for m in ((j or {}).get("smartctl") or {}).get("messages", [])]
    started = any(re.search(r"has begun|will be complete|Testing has begun", m, re.I) for m in msgs)
    return {"ok": bool(started), "messages": msgs[:4], "note": "The drive runs the test by itself in about 2 minutes. Refresh afterwards to see the result under 'Last self-test'."}


@kit.post("/api/elevate")
def elevate(_d):
    """Relaunch this app with a Windows UAC prompt. If the user declines, nothing changes."""
    if not drives.IS_WIN:
        raise BadRequest("Only available on Windows")
    if drives.is_admin():
        raise BadRequest("Already running as administrator")
    import ctypes
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, "--after-pid %d" % os.getpid()
    else:
        exe, params = sys.executable, '"%s" --after-pid %d' % (os.path.abspath(sys.argv[0]), os.getpid())
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    if rc <= 32:
        raise BadRequest("Windows did not start the administrator copy (the prompt was probably declined)")
    threading.Timer(1.0, quit_event.set).start()
    return {"ok": True}


@kit.post("/api/clear")
def clear(d):
    if d.get("confirm") != "clear":
        raise BadRequest("Clearing needs explicit confirmation")
    STORE.clear()
    return {"ok": True}


@kit.post("/api/quit")
def quit_app(_d):
    quit_event.set()
    return {"ok": True}


def _cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@kit.raw("/api/export.csv")
def export(_q):
    models = {d["key"]: d for d in SNAP["drives"]}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["time", "drive", "serial_key", "verdict", "temp_c", "life_used_pct", "reallocated", "pending", "uncorrectable", "crc_errors", "media_errors",
                "power_on_hours", "unsafe_shutdowns", "bytes_written"])
    for r in STORE.all_history():
        w.writerow([datetime.fromtimestamp(r["ts"]).isoformat(sep=" ", timespec="seconds"), _cell(r["model"] or (models.get(r["drive"]) or {}).get("model")), _cell(r["drive"]),
                    drives.LEVELS.get(r["level"], ""), *["" if r[k] is None else r[k] for k in ("temp", "life_used", "realloc", "pending", "uncorrect", "crc", "media_errors", "poh", "unsafe", "written")]])
    name = datetime.now().strftime("drivedoctor-%Y%m%d-%H%M.csv")
    return 200, ("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="{name}"'}


@kit.get("/api/about")
def about(_q):
    p = storage.bundled_dir()
    lic = ""
    try:
        with open(os.path.join(p, "README-SMARTMONTOOLS.txt"), encoding="utf-8") as f:
            lic = f.read()
    except OSError:
        pass
    return {"smartmontools": lic, "data_dir": storage.DATA_DIR}
