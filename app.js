// Item Checker: simple UI. One job: show which gallery photos are wrong.
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { products: [], results: {}, filter: "all", query: "", es: null, open: null };
  const LABEL = { valid: "Valid", invalid: "Invalid", review: "Review", error: "Error", unchecked: "Not checked" };

  const key = (handle, url, pos) => `${handle}|${pos}|${url}`;
  const verdictOf = (h, p) => (state.results[key(h, p.url, p.position)] || {}).verdict || "unchecked";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function init() {
    try {
      const data = await (await fetch("/api/products")).json();
      state.products = data.products || [];
      if (!data.api_key_set) notice("DASHSCOPE_API_KEY is missing. Add it to .env and restart the server.", "warn");
    } catch (e) {
      notice("Could not reach the server. Start it with: python qwen_server.py", "warn");
    }
    render();
  }

  function notice(msg, kind = "info") {
    const n = $("notice");
    n.textContent = msg; n.className = `notice ${kind}`; n.hidden = !msg;
  }

  // ---------- render ----------
  function counts() {
    const c = { all: 0, valid: 0, invalid: 0, review: 0, error: 0, unchecked: 0 };
    for (const p of state.products) for (const ph of p.photos) { c.all++; c[verdictOf(p.handle, ph)]++; }
    return c;
  }

  function render() {
    const c = counts();
    for (const k in c) { const el = $(`c-${k}`); if (el) el.textContent = c[k]; }
    const q = state.query.toLowerCase();
    const html = state.products
      .filter((p) => !q || p.title.toLowerCase().includes(q))
      .map((p, i) => productHtml(p, state.products.indexOf(p)))
      .filter(Boolean).join("");
    $("list").innerHTML = html || `<p class="empty">Nothing here for this filter.</p>`;
  }

  function productHtml(p, idx) {
    const photos = p.photos.map((ph, j) => ({ ph, j, v: verdictOf(p.handle, ph) }))
      .filter((x) => state.filter === "all" || x.v === state.filter);
    if (!photos.length) return "";
    const pc = { invalid: 0, review: 0 };
    p.photos.forEach((ph) => { const v = verdictOf(p.handle, ph); if (v in pc) pc[v]++; });
    const summary = [pc.invalid && `<span class="pill invalid">${pc.invalid} invalid</span>`,
                     pc.review && `<span class="pill review">${pc.review} review</span>`].filter(Boolean).join("");
    return `
      <section class="product">
        <div class="hero"><img src="${esc(p.hero)}" loading="lazy" alt=""><span>Hero</span></div>
        <div class="body">
          <div class="phead">
            <h2>${esc(p.title)}</h2>${summary}
            <button class="ghost small" data-run="${idx}">Check</button>
          </div>
          <div class="grid">${photos.map(({ ph, j, v }) => tileHtml(p, idx, ph, j, v)).join("")}</div>
        </div>
      </section>`;
  }

  function tileHtml(p, idx, ph, j, v) {
    const r = state.results[key(p.handle, ph.url, ph.position)] || {};
    const score = v === "unchecked" ? "" : ` ${Math.round(r.score || 0)}%`;
    const who = r.human_override ? " · you" : "";
    return `
      <div class="tile ${v}" data-open="${idx}:${j}" title="${esc(r.reason || "")}">
        <img src="${esc(ph.url)}" loading="lazy" alt="">
        <span class="badge">${LABEL[v]}${v === "valid" || v === "invalid" ? score : ""}${who}</span>
        <span class="pos">Pos ${esc(ph.position)}</span>
        ${r.reason ? `<p class="reason">${esc(r.reason)}</p>` : ""}
      </div>`;
  }

  // ---------- run ----------
  function run(handle) {
    if (state.es) return;
    const params = new URLSearchParams();
    if (handle) params.set("handle", handle);
    if ($("fresh").checked) params.set("fresh", "1");
    const es = new EventSource(`/api/stream_verify?${params}`);
    state.es = es;
    busy(true);
    notice("");
    progress(0, 0);

    es.addEventListener("start", (e) => { const d = JSON.parse(e.data); progress(0, d.total); });
    es.addEventListener("photo", (e) => {
      const d = JSON.parse(e.data), r = d.result;
      state.results[key(d.handle, r.url, r.position)] = r;
      progress(d.current, d.total);
      render();
    });
    es.addEventListener("complete", (e) => {
      const c = JSON.parse(e.data).counts;
      notice(`Done: ${c.invalid} invalid, ${c.review} to review, ${c.valid} valid, ${c.error} errors.`, c.invalid || c.review ? "info" : "good");
      stop();
    });
    es.addEventListener("fatal", (e) => { notice(JSON.parse(e.data).message, "warn"); stop(); });
    // EventSource auto-reconnects on close, which would restart the run. Never let it.
    es.onerror = () => { if (state.es) { notice("Connection closed.", "warn"); stop(); } };
  }

  function stop() {
    if (state.es) { state.es.close(); state.es = null; }
    busy(false);
    setTimeout(() => { $("progress").hidden = true; }, 1200);
  }

  function busy(on) {
    $("btnRun").disabled = on;
    $("btnRun").textContent = on ? "Checking…" : "Check all photos";
    $("btnStop").hidden = !on;
    document.querySelectorAll("[data-run]").forEach((b) => (b.disabled = on));
  }

  function progress(cur, total) {
    $("progress").hidden = false;
    $("progressFill").style.width = total ? `${(100 * cur) / total}%` : "0%";
    $("progressText").textContent = total ? `${cur} / ${total} photos` : "Starting…";
  }

  // ---------- viewer + operator decision ----------
  function openViewer(idx, j) {
    const p = state.products[idx], ph = p.photos[j];
    const r = state.results[key(p.handle, ph.url, ph.position)] || {};
    state.open = { idx, j };
    $("vHero").src = p.hero;
    $("vCand").src = ph.url;
    $("vCap").textContent = `Pos ${ph.position} · ${LABEL[r.verdict || "unchecked"]}`;
    $("vReason").textContent = r.reason || "Not checked yet.";
    $("viewer").hidden = false;
  }

  async function decide(isValid) {
    if (!state.open) return;
    const p = state.products[state.open.idx], ph = p.photos[state.open.j];
    try {
      const res = await fetch("/api/save_feedback", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle: p.handle, url: ph.url, position: ph.position, is_valid: isValid }),
      });
      if (!res.ok) throw new Error(await res.text());
      // same image can appear twice in a gallery: apply to every copy
      p.photos.forEach((x) => {
        if (x.url === ph.url) state.results[key(p.handle, x.url, x.position)] = {
          verdict: isValid ? "valid" : "invalid", score: 100, reason: "Operator decision", human_override: true,
        };
      });
      $("viewer").hidden = true; state.open = null;
      render();
    } catch (e) { notice(`Could not save: ${e.message}`, "warn"); }
  }

  // ---------- events ----------
  $("btnRun").onclick = () => run(null);
  $("btnStop").onclick = () => { notice("Stopped."); stop(); };
  $("search").oninput = (e) => { state.query = e.target.value; render(); };
  $("tabs").onclick = (e) => {
    const b = e.target.closest("button[data-filter]"); if (!b) return;
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
    state.filter = b.dataset.filter; render();
  };
  $("list").onclick = (e) => {
    const runBtn = e.target.closest("[data-run]");
    if (runBtn) return run(state.products[+runBtn.dataset.run].handle);
    const tile = e.target.closest("[data-open]");
    if (tile) { const [i, j] = tile.dataset.open.split(":").map(Number); openViewer(i, j); }
  };
  $("vValid").onclick = () => decide(true);
  $("vInvalid").onclick = () => decide(false);
  $("vClose").onclick = () => { $("viewer").hidden = true; state.open = null; };
  $("viewer").onclick = (e) => { if (e.target.id === "viewer") $("vClose").click(); };
  document.addEventListener("keydown", (e) => {
    if ($("viewer").hidden) return;
    if (e.key === "Escape") $("vClose").click();
    if (e.key === "v") decide(true);
    if (e.key === "x") decide(false);
  });

  init();
})();
