"""DriveDoctor storage: SQLite history in %APPDATA%\\DriveDoctor + extraction of the bundled smartctl."""
import hashlib
import os
import shutil
import sqlite3
import sys
import threading
import time

APP = "DriveDoctor"
DATA_DIR = os.environ.get("DRIVEDOCTOR_DATA") or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.local/share"), APP)
INTERVALS = (0, 30, 60, 360, 1440)
DEFAULTS = {"interval_min": 60}
MIN_GAP = 20 * 60          # automatic readings closer than this to the previous one are not stored
SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, drive TEXT NOT NULL, model TEXT, level INTEGER,
  temp REAL, life_used REAL, realloc INTEGER, pending INTEGER, uncorrect INTEGER, crc INTEGER, media_errors INTEGER,
  poh INTEGER, unsafe INTEGER, written REAL, source TEXT);
CREATE INDEX IF NOT EXISTS readings_drive_ts ON readings(drive, ts);
CREATE TABLE IF NOT EXISTS drives (key TEXT PRIMARY KEY, model TEXT, serial TEXT, last_seen REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""
FIELDS = ("temp", "life_used", "realloc", "pending", "uncorrect", "crc", "media_errors", "poh", "unsafe", "written")


class Store:
    def __init__(self, path=None):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path or os.path.join(DATA_DIR, "drivedoctor.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def get_settings(self):
        with self.lock:
            rows = {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM settings")}
        out = dict(DEFAULTS)
        if "interval_min" in rows:
            try:
                out["interval_min"] = int(rows["interval_min"])
            except ValueError:
                pass
        return out

    def set_settings(self, d):
        if "interval_min" in d:
            if d["interval_min"] not in INTERVALS or isinstance(d["interval_min"], bool):
                raise ValueError("interval_min must be one of %s" % (INTERVALS,))
            with self.lock:
                self.db.execute("INSERT INTO settings(key,value) VALUES('interval_min',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(d["interval_min"]),))
                self.db.commit()
        return self.get_settings()

    def record(self, drive, manual=False, ts=None):
        """Store one reading for a normalized drive dict. Returns True if stored."""
        ts = ts or time.time()
        with self.lock:
            last = self.db.execute("SELECT ts FROM readings WHERE drive=? ORDER BY ts DESC LIMIT 1", (drive["key"],)).fetchone()
            self.db.execute("INSERT INTO drives(key,model,serial,last_seen) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET model=excluded.model, serial=excluded.serial, last_seen=excluded.last_seen",
                            (drive["key"], drive.get("model"), drive.get("serial"), ts))
            if last and ts - last["ts"] < MIN_GAP and not manual:
                self.db.commit()
                return False
            if not any(drive.get(f) is not None for f in FIELDS):
                self.db.commit()
                return False                                     # nothing readable: do not pollute the history
            self.db.execute("INSERT INTO readings(ts,drive,model,level,temp,life_used,realloc,pending,uncorrect,crc,media_errors,poh,unsafe,written,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (ts, drive["key"], drive.get("model"), drive.get("level"), drive.get("temp"), drive.get("life_used"), drive.get("realloc"),
                             drive.get("pending"), drive.get("uncorrect"), drive.get("crc"), drive.get("media_errors"), drive.get("poh"),
                             drive.get("unsafe"), drive.get("written"), drive.get("source")))
            self.db.commit()
            return True

    def history(self, key, since=0, limit=20000):
        with self.lock:
            rows = self.db.execute("SELECT * FROM readings WHERE drive=? AND ts>=? ORDER BY ts ASC LIMIT ?", (key, since, limit)).fetchall()
        return [dict(r) for r in rows]

    def all_history(self, since=0):
        with self.lock:
            rows = self.db.execute("SELECT * FROM readings WHERE ts>=? ORDER BY drive, ts", (since,)).fetchall()
        return [dict(r) for r in rows]

    def clear(self):
        with self.lock:
            self.db.execute("DELETE FROM readings")
            self.db.commit()


def bundled_dir():
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "bundled", "smartmontools")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def extract_smartctl():
    """Copy the bundled smartmontools files to %APPDATA%\\DriveDoctor\\smartmontools (a real folder with the licence
    next to the binary). Returns the smartctl.exe path or None."""
    src = bundled_dir()
    if not os.path.isfile(os.path.join(src, "smartctl.exe")):
        return None
    dst = os.path.join(DATA_DIR, "smartmontools")
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if not os.path.isfile(d) or os.path.getsize(d) != os.path.getsize(s) or sha256(d) != sha256(s):
            shutil.copy2(s, d)
    return os.path.join(dst, "smartctl.exe")
