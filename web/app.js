/* DriveDoctor UI. Text only (Kit.el), no innerHTML. */
(() => {
  "use strict";
  const { $, el, clear, toast, api, post, bytes, num, dt, ago, dur, initTheme, chart, css } = window.Kit;
  initTheme($("#theme"));
  const S = { status: null, drives: [], tools: {}, key: null, metric: "temp", view: "drives", hist: null };
  const TITLES = { drives: "Drives", settings: "Settings", about: "About & licenses" };
  const BADGE = { 0: ["Good", "ok"], 1: ["Watch", "warn"], 2: ["Replace soon", "danger"], "-1": ["Unknown", "unknown"] };
  const badge = (lvl, big) => el("span", { className: "badge " + BADGE[lvl][1] + (big ? " big" : ""), text: BADGE[lvl][0] });

  function show(v) {
    S.view = v;
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
    $("#view-title").textContent = TITLES[v];
    ["drives", "settings", "about"].forEach((x) => { $("#v-" + x).hidden = x !== v; });
    if (v === "settings" && S.status) $("#set-interval").value = String(S.status.settings.interval_min);
    if (v === "about") loadAbout();
  }
  document.querySelectorAll(".nav-btn").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

  // ---- polling
  let wasRefreshing = true;
  async function tick() {
    try {
      S.status = await api("/api/status");
      const r = await api("/api/drives");
      S.drives = r.drives; S.tools = r.tools || {};
      render();
      if (wasRefreshing && !S.status.refreshing && S.key) loadDetail();
      wasRefreshing = S.status.refreshing;
    } catch (e) { $("#status").textContent = "Cannot reach DriveDoctor"; $("#status").className = "status error"; }
    setTimeout(tick, S.status && S.status.refreshing ? 700 : 4000);
  }
  function render() {
    const st = S.status;
    $("#n-drives").textContent = S.drives.length ? String(S.drives.length) : "";
    $("#btn-refresh").disabled = st.refreshing;
    $("#btn-admin").hidden = !(st.windows && !st.admin);
    let s = st.refreshing ? "Reading drives…" : st.at ? "Last read " + ago(st.at, st.now) + (st.next ? " · next in " + dur(Math.max(0, st.next - st.now)) : "") : "Starting…";
    if (st.error) s = st.error;
    $("#status").textContent = s; $("#status").className = "status" + (st.error ? " error" : "");
    const b = $("#banner"), notes = [];
    if (S.tools && !st.refreshing && st.at) {
      if (st.windows && !st.admin) notes.push("DriveDoctor is not running as administrator, so Windows hides most SMART details (wear, error counts, sector counts). Press \"Restart as administrator\" for the full picture.");
      if (!S.tools.smartctl) notes.push("smartctl is not available, so only the basic Windows health data is shown.");
      (S.tools.notes || []).forEach((n) => notes.push(n));
    }
    b.hidden = !notes.length; b.textContent = notes.join(" ");
    renderCards();
  }
  function renderCards() {
    const box = clear($("#drive-cards"));
    if (!S.drives.length) {
      box.appendChild(el("p", { className: "empty", text: S.status.at ? "No drives were found. Try Refresh, or restart as administrator." : "Reading drives…" }));
      $("#detail").hidden = true; return;
    }
    if (!S.key || !S.drives.some((d) => d.key === S.key)) { S.key = S.drives[0].key; loadDetail(); }
    S.drives.forEach((d) => {
      const c = el("button", { type: "button", className: "dcard" + (d.key === S.key ? " active" : ""), "aria-pressed": d.key === S.key ? "true" : "false" },
        el("div", { className: "line" }, el("span", { className: "model", text: d.model || "Unknown drive", title: d.model || "" }), badge(d.level)),
        el("div", { className: "meta", text: [d.type, d.capacity ? bytes(d.capacity) : null, d.temp != null ? Math.round(d.temp) + " °C" : null].filter(Boolean).join(" · ") }),
        el("div", { className: "meta", text: d.headline }));
      c.addEventListener("click", () => { S.key = d.key; renderCards(); loadDetail(); });
      box.appendChild(c);
    });
    renderDetail();
  }

  // ---- detail
  let full = null;
  async function loadDetail() {
    if (!S.key) return;
    try {
      full = (await api("/api/drive?key=" + encodeURIComponent(S.key))).drive;
      S.hist = (await api("/api/history?key=" + encodeURIComponent(S.key) + "&range=" + $("#range").value));
      renderDetail();
    } catch (e) { /* drive vanished between calls */ }
  }
  function metric(label, value, unit, sub, lvl) {
    return el("div", { className: "card" }, el("span", { className: "label", text: label }),
      el("span", { className: "value" + (lvl ? " l" + lvl : "") }, value, unit ? el("small", { text: " " + unit }) : null), sub ? el("span", { className: "sub", text: sub }) : null);
  }
  function renderDetail() {
    const d = full && full.key === S.key ? full : S.drives.find((x) => x.key === S.key);
    if (!d) { $("#detail").hidden = true; return; }
    $("#detail").hidden = false;
    const hp = clear($("#head-panel"));
    hp.appendChild(el("div", { className: "verdict" }, badge(d.level, true), el("div", {},
      el("div", { className: "big", text: d.headline }),
      el("ul", { className: "reasons" }, (d.reasons || []).map((r) => el("li", { className: "l" + r.level, text: r.text }))),
      el("p", { className: "muted", text: d.advice || "" }))));
    const info = el("dl", { className: "deflist" });
    [["Model", d.model], ["Serial", d.serial], ["Firmware", d.firmware], ["Type", d.type + (d.interface ? " · " + d.interface : "")], ["Capacity", d.capacity ? bytes(d.capacity) : null],
     ["Data source", d.source === "smartctl" ? "SMART (smartctl)" : "Windows storage status (limited)"], ["Windows says", d.windows_health ? d.windows_health + (d.windows_operational ? " / " + d.windows_operational : "") : null]]
      .forEach(([k, v]) => { if (v) { info.appendChild(el("dt", { text: k })); info.appendChild(el("dd", { text: String(v) })); } });
    hp.appendChild(info);
    const m = clear($("#metrics"));
    const lv = (v, a, b) => (v == null ? 0 : v >= b ? 2 : v >= a ? 1 : 0);
    const hot = d.type === "HDD" ? [50, 60] : [70, 80];
    if (d.temp != null) m.appendChild(metric("Temperature", num(d.temp, 0), "°C", null, lv(d.temp, hot[0], hot[1])));
    if (d.life_used != null) {
      const left = Math.max(0, 100 - d.life_used);
      const card = metric("Life left", num(left, 0), "%", d.nvme ? "NVMe 'percentage used' " + num(d.life_used, 0) + "%" : d.life_attr || "Windows wear counter", left <= 10 ? 2 : left <= 30 ? 1 : 0);
      const bar = el("div", { className: "bar life-bar" }, el("span", {})); bar.firstChild.style.width = left + "%";
      card.appendChild(bar); m.appendChild(card);
    }
    if (d.spare != null) m.appendChild(metric("Spare capacity", num(d.spare, 0), "%", d.spare_threshold != null ? "warning at " + d.spare_threshold + "%" : null, d.spare <= (d.spare_threshold == null ? 10 : d.spare_threshold) ? 2 : 0));
    if (d.poh != null) m.appendChild(metric("Powered on", num(d.poh), "hours", "about " + (d.poh / 8766).toFixed(1) + " years"));
    if (d.written != null) m.appendChild(metric("Data written", bytes(d.written), "", "lifetime total"));
    if (d.realloc != null) m.appendChild(metric("Reallocated sectors", num(d.realloc), "", "bad spots the drive moved", d.realloc >= 50 ? 2 : d.realloc ? 1 : 0));
    if (d.pending != null) m.appendChild(metric("Pending sectors", num(d.pending), "", "waiting to be moved", d.pending >= 10 ? 2 : d.pending ? 1 : 0));
    if (d.offline_uncorrectable != null) m.appendChild(metric("Uncorrectable sectors", num(d.offline_uncorrectable), "", null, d.offline_uncorrectable >= 10 ? 2 : d.offline_uncorrectable ? 1 : 0));
    if (d.uncorrect != null) m.appendChild(metric("Uncorrectable errors", num(d.uncorrect), "", null, d.uncorrect >= 10 ? 2 : d.uncorrect ? 1 : 0));
    if (d.media_errors != null) m.appendChild(metric("Media errors", num(d.media_errors), "", d.error_log_entries != null ? d.error_log_entries + " entries in the error log" : null, d.media_errors >= 10 ? 2 : d.media_errors ? 1 : 0));
    if (d.crc != null) m.appendChild(metric("Cable (CRC) errors", num(d.crc), "", null, d.crc >= 10 ? 1 : 0));
    if (d.unsafe != null) m.appendChild(metric("Unsafe shutdowns", num(d.unsafe), "", "power loss / hard off"));
    if (d.power_cycles != null) m.appendChild(metric("Power cycles", num(d.power_cycles)));
    if (d.last_selftest) m.appendChild(metric("Last self-test", d.last_selftest.passed === false ? "Failed" : d.last_selftest.passed ? "Passed" : "—", "", (d.last_selftest.type || "") + " · " + (d.last_selftest.status || ""), d.last_selftest.passed === false ? 2 : 0));
    if (!m.children.length) m.appendChild(el("p", { className: "empty", text: "No numbers are available for this drive." }));
    renderActions(d);
    const rows = clear($("#raw-rows"));
    (d.attributes || []).forEach((a) => rows.appendChild(el("tr", { className: a.failing ? "bad" : "" }, el("td", { text: a.id }), el("td", { text: a.name }), el("td", { className: "num", text: a.value }), el("td", { className: "num", text: a.worst }), el("td", { className: "num", text: a.thresh }), el("td", { text: a.raw }))));
    $("#raw-box").hidden = !(d.attributes && d.attributes.length);
    drawChart();
  }
  function renderActions(d) {
    const p = $("#actions-panel"); clear(p);
    const can = d.source === "smartctl" && !d.unreadable && S.status.admin && S.tools.smartctl;
    p.hidden = !(d.source === "smartctl" && !d.unreadable);
    if (p.hidden) return;
    p.appendChild(el("h3", { text: "Short self-test (optional)" }));
    p.appendChild(el("p", { className: "muted small", text: "Asks the drive to run its own built-in quick check (about 2 minutes). It reads the drive and does not change your files. DriveDoctor never starts this on its own." }));
    const btn = el("button", { type: "button", text: "Run short self-test…", disabled: !can });
    if (!can) p.appendChild(el("p", { className: "muted small", text: "Needs administrator rights (use Restart as administrator)." }));
    btn.addEventListener("click", () => confirmSelfTest(d));
    p.appendChild(el("div", { className: "toolbar" }, btn));
  }
  function modal(title, text, okLabel, onOk, danger) {
    const ok = el("button", { type: "button", className: danger ? "danger" : "primary", text: okLabel }), no = el("button", { type: "button", text: "Cancel" });
    const back = el("div", { className: "modal-back", role: "presentation" }, el("div", { className: "modal", role: "dialog", "aria-modal": "true" }, el("h2", { text: title }), el("p", { text }), el("div", { className: "actions" }, no, ok)));
    $("#modal-root").appendChild(back);
    const close = () => back.remove();
    no.addEventListener("click", close); no.focus();
    ok.addEventListener("click", async () => { ok.disabled = true; try { await onOk(); } finally { close(); } });
  }
  function confirmSelfTest(d) {
    modal("Run a short self-test on " + d.model + "?", "The drive will check itself for about 2 minutes. You can keep using the PC. Press Refresh afterwards to see the result.", "Start the test", async () => {
      try { const r = await post("/api/selftest", { key: d.key, confirm: "short-test" }); toast(r.ok ? "Self-test started. " + r.note : "The drive did not start the test: " + (r.messages || []).join(" "), !r.ok); } catch (e) { toast(e.message, true); }
    });
  }

  // ---- chart
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
    S.metric = t.dataset.metric; document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t)); drawChart();
  }));
  $("#range").addEventListener("change", loadDetail);
  window.addEventListener("resize", drawChart);
  function drawChart() {
    const h = S.hist; if (!h || h.key !== S.key) return;
    const pts = (k, f) => h.points.filter((p) => p[k] != null).map((p) => [p.ts, f ? f(p[k]) : p[k]]);
    const accent = css("--accent"), warn = css("--warn"), danger = css("--danger"), ok = css("--ok");
    const d = S.drives.find((x) => x.key === S.key) || {};
    let series, legend, opts = { tMin: h.points.length ? Math.min(h.from, h.points[0].ts - 3600) : h.from, tMax: h.to, empty: "No saved readings yet. Each refresh adds one." };
    if (S.metric === "temp") { series = [{ color: accent, points: pts("temp") }]; legend = [["Temperature °C", accent]]; opts.yMin = 0; opts.hline = { v: d.type === "HDD" ? 50 : 70, label: "Warm", color: warn }; opts.fmtY = (v) => num(v, 0); }
    else if (S.metric === "wear") { series = [{ color: ok, points: pts("life_used", (v) => Math.max(0, 100 - v)) }]; legend = [["Life left %", ok]]; opts.yMin = 0; opts.yMax = 100; opts.fmtY = (v) => num(v, 0); }
    else { series = [{ color: warn, points: pts("realloc") }, { color: danger, points: pts("pending"), dashed: true }, { color: accent, points: pts("uncorrect").concat(pts("media_errors")).sort((a, b) => a[0] - b[0]) }];
      legend = [["Reallocated", warn], ["Pending", danger], ["Uncorrectable / media errors", accent]]; opts.fmtY = (v) => num(v, 0); opts.yMax = Math.max(5, ...series.flatMap((s) => s.points.map((p) => p[1]))) * 1.2; }
    const lg = clear($("#legend")); legend.forEach(([n]) => lg.appendChild(el("span", {}, el("i", {}), n)));
    lg.querySelectorAll("i").forEach((i, idx) => { i.style.background = legend[idx][1]; });
    chart($("#chart"), series, opts);
    $("#chart-note").textContent = h.points.length ? h.points.length + " saved readings in this range." : "";
  }

  // ---- buttons
  $("#btn-refresh").addEventListener("click", async () => { try { await post("/api/refresh", {}); tick(); } catch (e) { toast(e.message, true); } });
  $("#btn-admin").addEventListener("click", () => modal("Restart DriveDoctor as administrator?", "Windows will ask you to approve this. DriveDoctor closes and reopens with the extra permission it needs to read SMART data. If you decline the prompt, nothing changes.", "Restart", async () => {
    try { await post("/api/elevate", {}); toast("Waiting for Windows… DriveDoctor will reopen"); } catch (e) { toast(e.message, true); }
  }));
  $("#set-interval").addEventListener("change", async (e) => { try { const r = await post("/api/settings", { interval_min: Number(e.target.value) }); S.status.settings = r.settings; toast("Saved"); } catch (er) { toast(er.message, true); } });
  $("#btn-clear").addEventListener("click", () => modal("Delete all saved history?", "Trend charts start over. Your drives are not touched. This cannot be undone.", "Delete history", async () => {
    try { await post("/api/clear", { confirm: "clear" }); toast("History deleted"); loadDetail(); } catch (e) { toast(e.message, true); }
  }, true));
  async function loadAbout() { try { const a = await api("/api/about"); $("#license-text").textContent = a.smartmontools || "(license notice not found)"; $("#data-dir").textContent = a.data_dir; } catch (e) { toast(e.message, true); } }
  tick();
})();
