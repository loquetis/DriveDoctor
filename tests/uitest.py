"""API + UI test on the box using canned drive data. Run: /workspace/_testenv/bin/python tests/uitest.py"""
import csv, io, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, ROOT)
PORT = 18770; URL = f"http://127.0.0.1:{PORT}/"
tmp = tempfile.mkdtemp(prefix="dd-"); fx = os.path.join(tmp, "fx"); subprocess.check_call([sys.executable, os.path.join(HERE, "make_fixtures.py"), fx])
os.environ["DRIVEDOCTOR_DATA"] = os.path.join(tmp, "data")
import storage, drives
os.environ["DRIVEDOCTOR_FIXTURES"] = fx; os.environ["DRIVEDOCTOR_FAKE_ADMIN"] = "0"
# seed 20 days of worsening history for the WD drive so the trend warning and charts have something to show
st = storage.Store(); hd_key = drives.norm_serial("WD-WCC7K1234567"); now = time.time()
for i in range(40, -1, -1):
    st.record({"key": hd_key, "model": "WDC WD40EZRZ-00GXCB0", "level": 1, "temp": 45 + i % 5, "realloc": 10 if i > 20 else 24 - i // 3, "pending": 0 if i > 10 else 8, "uncorrect": 0, "poh": 60000 + (40 - i) * 12}, ts=now - i * 12 * 3600, manual=True)
st.db.close()
def start(admin):
    env = dict(os.environ, DRIVEDOCTOR_FAKE_ADMIN="1" if admin else "0")
    return subprocess.Popen([sys.executable, os.path.join(ROOT, "launcher.py"), "--no-window", "--port", str(PORT)], env=env, cwd=ROOT)
srv = start(False)
results = {}
def ok(name, cond, detail=""):
    results[name] = bool(cond); print(("PASS " if cond else "FAIL ") + name, detail if not cond else "", flush=True)
def req(path, method="GET", body=None, headers=None):
    r = urllib.request.Request(URL.rstrip("/") + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp: return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e: return e.code, e.read(), dict(e.headers)
def wait_up():
    for _ in range(60):
        try: req("/api/status"); return
        except Exception: time.sleep(0.2)
try:
    wait_up(); time.sleep(1.5)
    ok("bad host rejected", req("/api/status", headers={"Host": "evil.example:%d" % PORT})[0] == 403)
    ok("post without token rejected", req("/api/refresh", "POST", b"{}", {"Content-Type": "application/json"})[0] == 403)
    ok("traversal blocked", req("/../server.py")[0] == 404 and req("/%2e%2e/drives.py")[0] == 404 and req("/bundled/smartmontools/smartctl.exe")[0] == 404)
    s, body, h = req("/"); ok("csp header", "script-src 'self'" in h.get("Content-Security-Policy", ""))
    token = re.search(rb'name="app-token" content="([^"]+)"', body).group(1).decode(); ok("token injected", token != "__APP_TOKEN__" and len(token) > 20)
    J = {"Content-Type": "application/json", "X-App-Token": token}
    ok("bad interval rejected", req("/api/settings", "POST", b'{"interval_min": 5}', J)[0] == 400)
    ok("self-test needs confirmation", req("/api/selftest", "POST", b'{"key": "x"}', J)[0] == 400)
    ok("self-test refused when not admin", req("/api/selftest", "POST", json.dumps({"key": "x", "confirm": "short-test"}).encode(), J)[0] == 400)
    ok("elevate refused off Windows", req("/api/elevate", "POST", b"{}", J)[0] == 400)
    ok("clear needs confirmation", req("/api/clear", "POST", b"{}", J)[0] == 400)
    ok("unknown drive 404", req("/api/drive?key=nope")[0] == 404 and req("/api/history?key=nope")[0] == 404)
    dl = json.loads(req("/api/drives")[1]); ok("5 drives, worst first", len(dl["drives"]) == 5 and dl["drives"][0]["level"] == 2, [d["level"] for d in dl["drives"]])
    ok("attributes not in list payload", all("attributes" not in d for d in dl["drives"]))
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1320, "height": 860}, accept_downloads=True)
        pg.on("pageerror", lambda e: errors.append(str(e))); pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.goto(URL); pg.wait_for_selector(".dcard"); pg.wait_for_timeout(800)
        ok("window title", pg.title() == "DriveDoctor")
        ok("5 drive cards", pg.locator(".dcard").count() == 5)
        ok("worst drive first and selected", "ST2000" in pg.inner_text(".dcard.active") and "Replace soon" in pg.inner_text(".dcard.active"))
        ok("verdict text shown", "FAILING" in pg.inner_text("#head-panel"))
        ok("non-admin banner + restart button (Windows only, so hidden here)", "smartctl could not open" in pg.inner_text("#banner"))
        pg.locator(".dcard", has_text="WD40EZRZ").click(); pg.wait_for_timeout(700)
        ok("HDD detail: watch + trend warning from history", "getting worse" in pg.inner_text("#head-panel"), pg.inner_text("#head-panel")[:300])
        ok("metric cards", "reallocated sectors" in pg.inner_text("#metrics").lower() and "pending sectors" in pg.inner_text("#metrics").lower())
        def painted(): return pg.evaluate("(() => { const c = document.getElementById('chart'); const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data; let n=0; for (let i=3;i<d.length;i+=4) if (d[i]>0) n++; return n; })()")
        ok("temperature chart painted", painted() > 1500, painted())
        pg.click('[data-metric="errors"]'); pg.wait_for_timeout(200); ok("errors chart painted", painted() > 1000)
        pg.click('[data-metric="wear"]'); pg.wait_for_timeout(200); ok("wear chart: empty-state text for a drive with no wear data", painted() > 50)
        pg.select_option("#range", "week"); pg.wait_for_timeout(600); ok("range switch", "saved readings" in pg.inner_text("#chart-note"))
        pg.locator(".dcard", has_text="980 PRO").click(); pg.wait_for_timeout(600)
        ok("NVMe detail Good", "Healthy" in pg.inner_text("#head-panel") and "life left" in pg.inner_text("#metrics").lower() and "unsafe shutdowns" in pg.inner_text("#metrics").lower())
        ok("self-test button disabled when not admin", pg.locator("#actions-panel button").is_disabled() if not pg.locator("#actions-panel").is_hidden() else True)
        pg.locator(".dcard", has_text="MX500").click(); pg.wait_for_timeout(600); pg.click("#raw-box summary"); ok("raw attribute table", pg.locator("#raw-rows tr").count() >= 5)
        ok("USB/unreadable drive handled", pg.locator(".dcard", has_text="Generic USB").count() == 1)
        pg.click("#btn-refresh"); pg.wait_for_timeout(1500); ok("refresh works", pg.locator(".dcard").count() == 5)
        with pg.expect_download() as dlw: pg.click('#v-drives a.button')
        rows = list(csv.reader(io.StringIO(open(dlw.value.path(), encoding="utf-8-sig").read()))); ok("csv export has history", rows[0][0] == "time" and len(rows) > 40, len(rows))
        pg.click('[data-view="settings"]'); pg.select_option("#set-interval", "360"); pg.wait_for_timeout(500)
        ok("interval saved", json.loads(req("/api/status")[1])["settings"]["interval_min"] == 360)
        pg.click('[data-view="about"]'); pg.wait_for_timeout(500); ok("license page shows GPL notice", "General Public License" in pg.inner_text("#license-text") or "GNU General Public License" in pg.inner_text("#license-text"))
        pg.click('[data-view="settings"]'); pg.click("#btn-clear"); pg.wait_for_selector(".modal"); pg.click(".modal >> text=Cancel"); pg.wait_for_timeout(200)
        pg.click("#btn-clear"); pg.click(".modal .danger"); pg.wait_for_timeout(600)
        ok("history cleared after confirm", len(json.loads(req("/api/history?key=%s&range=all" % hd_key)[1])["points"]) == 0)
        pg.select_option("#theme", "amber"); ok("theme", pg.evaluate("document.documentElement.dataset.theme") == "amber")
        ok("no console/page errors", not errors, str(errors[:3]))
        b.close()
    # admin mode: self-test path (canned data only: smartctl is never actually run in fixture mode)
    srv.terminate(); srv.wait(10); srv = start(True); wait_up(); time.sleep(1.5)
    s, body, _ = req("/"); token = re.search(rb'name="app-token" content="([^"]+)"', body).group(1).decode(); J = {"Content-Type": "application/json", "X-App-Token": token}
    drs = json.loads(req("/api/drives")[1])["drives"]; wd = [d for d in drs if "WD40" in d["model"]][0]; usb = [d for d in drs if "USB" in (d["model"] or "")][0]
    ok("admin: no admin banner data", json.loads(req("/api/status")[1])["admin"] is True)
    ok("self-test refused for non-smartctl drive", req("/api/selftest", "POST", json.dumps({"key": usb["key"], "confirm": "short-test"}).encode(), J)[0] == 400)
    ok("self-test only with confirm", req("/api/selftest", "POST", json.dumps({"key": wd["key"]}).encode(), J)[0] == 400)
    ok("quit endpoint ends the app", req("/api/quit", "POST", b"{}", J)[0] == 200 and srv.wait(15) is not None)
finally:
    if srv.poll() is None: srv.terminate(); srv.wait(10)
    shutil.rmtree(tmp, ignore_errors=True)
bad = [k for k, v in results.items() if not v]
print(f"{len(results) - len(bad)}/{len(results)} passed"); sys.exit(1 if bad else 0)
