/* LocalLift front end (vanilla JS). All source text is escaped before rendering. */
"use strict";

const S = { boot: null, opps: null, brief: null, plans: [], selected: null, selDev: null, planId: null, phase: null,
            planTab: "tasks", tab: "briefing", map: null, markers: {}, line: null, tileErrors: 0, win: 7, showAllNews: false, mapMode: "top", projectionInputs: null, projectionResult: null, listLimit: 50 };
const TZ = "America/New_York";

// ---------------------------------------------------------------- utilities
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n, d = 2) => (n == null ? "—" : (n < 0 ? "−$" : "$") + Math.abs(n).toFixed(d));
const ic = (name, cls = "") => `<i data-lucide="${name}" class="icon ${cls}"></i>`;
const icons = () => { try { window.lucide && lucide.createIcons(); } catch { /* icons are decorative */ } };
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const fmtK = (n) => (n == null ? "—" : n >= 10000 ? Math.round(n / 1000) + "k" : n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k" : String(Math.round(n)));
function ring(score, cls = "") {
  const pct = Math.max(0, Math.min(100, score || 0)), r = 30, c = 2 * Math.PI * r;
  return `<div class="ring ${cls}" title="Fit for your café, out of 100"><svg viewBox="0 0 72 72"><circle class="bg" cx="36" cy="36" r="${r}" fill="none" stroke-width="7"/>
    <circle class="fg" cx="36" cy="36" r="${r}" fill="none" stroke-width="7" stroke-dasharray="${c}" stroke-dashoffset="${c}" data-off="${c * (1 - pct / 100)}"/></svg>
    <div class="num"><div><b class="count-up" data-to="${Math.round(pct)}">0</b><span>FIT</span></div></div></div>`;
}
let revealObs = null;
function fx(root = document) {
  icons();
  // staggered entrance
  const items = [...root.querySelectorAll(".reveal:not(.in)")];
  if (REDUCED || !("IntersectionObserver" in window)) items.forEach((el) => el.classList.add("in"));
  else {
    revealObs = revealObs || new IntersectionObserver((ents) => ents.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); revealObs.unobserve(e.target); } }), { threshold: .08 });
    items.forEach((el, i) => { el.style.setProperty("--d", `${Math.min(i, 8) * 70}ms`); revealObs.observe(el); });
    setTimeout(() => items.forEach((el) => el.classList.add("in")), 1600);  // never leave content hidden
  }
  // count-up numbers
  root.querySelectorAll(".count-up:not([data-done])").forEach((el) => {
    el.dataset.done = "1";
    const to = Number(el.dataset.to), money = el.dataset.money === "1", dur = 0, t0 = performance.now();
    const show = (v) => (el.textContent = money ? money_(v) : Math.round(v).toLocaleString("en-US"));
    if (!dur || !isFinite(to)) return show(to);
    const step = (t) => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 4); show(to * e); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
    setTimeout(() => show(to), dur + 400);  // final value even if frames are throttled (background tab)
  });
  // rings and bars
  const grow = () => {
    root.querySelectorAll(".ring .fg[data-off]").forEach((c) => (c.style.strokeDashoffset = c.dataset.off));
    root.querySelectorAll("[data-w]").forEach((b) => (b.style.width = b.dataset.w));
  };
  requestAnimationFrame(() => requestAnimationFrame(grow));
  setTimeout(grow, 300);
}
const money_ = (n) => (n < 0 ? "−$" : "$") + Math.abs(Math.round(n)).toLocaleString("en-US");
function moveSegPill() {
  const seg = $("#mainSeg"), pill = seg && seg.querySelector(".seg-pill"), on = seg && seg.querySelector('button[aria-selected="true"]');
  if (pill && on) { pill.style.left = on.offsetLeft + "px"; pill.style.width = on.offsetWidth + "px"; }
}
window.addEventListener("resize", moveSegPill);
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } } };

function fmtTime(iso, opts = {}) {
  if (!iso) return "Time not stated";
  return new Date(iso).toLocaleString("en-US", { timeZone: TZ, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", ...opts });
}
function fmtDay(iso) { return iso ? new Date(iso).toLocaleDateString("en-US", { timeZone: TZ, weekday: "short", month: "short", day: "numeric" }) : "—"; }
function fmtHour(iso) { return iso ? new Date(iso).toLocaleTimeString("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" }).replace(":00", "") : ""; }
function dateParts(iso) {
  const d = new Date(iso);
  const p = (o) => d.toLocaleString("en-US", { timeZone: TZ, ...o });
  return { m: p({ month: "short" }).toUpperCase(), d: p({ day: "numeric" }), t: fmtHour(iso), w: p({ weekday: "short" }) };
}
function ago(iso) {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now"; if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60); if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)} days ago`;
}
function until(iso) {
  if (!iso) return "";
  const h = (new Date(iso).getTime() - Date.now()) / 3600000;
  if (h < 0) return "now";
  if (h < 1) return "within the hour"; if (h < 24) return `in ${Math.round(h)}h`;
  const d = Math.round(h / 24); return d === 1 ? "tomorrow" : `in ${d} days`;
}
function link(url, text) {
  if (!url || !/^https?:\/\//i.test(url)) return esc(text || "");
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text || url)}</a>`;
}
function short(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s; }
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 4500);
}
async function api(path, opts = {}) {
  const o = { headers: { "Content-Type": "application/json" }, ...opts };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || `Request failed (${r.status})`);
  return data;
}
const TOOL_STEP = { discoverEvents: "Checked event listings", searchNews: "Searched the news", searchSocial: "Checked social buzz",
  fetchApprovedSource: "Read the official page", getEventHistory: "Looked at the event’s history", getDevelopmentHistory: "Looked at the story so far",
  getBusinessProfile: "Read the café profile", calculateDistance: "Measured the distance", rankOpportunities: "Scored opportunities",
  calculateDemandAndContribution: "Ran the numbers", recordClaim: "Recorded a claim", saveBriefingDraft: "Saved a briefing draft",
  savePlanDraft: "Drafted a plan", createWatch: "Set up a watch" };
function rankChip(e) {
  if (!e.rank) return "";
  const top = S.opps?.top?.find((t) => t.id === e.id);
  if (top) return `<span class="chip accent" title="One of the top picks for the next 7 days">#${top.rank} pick this week</span>`;
  const n = S.opps?.events?.filter((x) => x.rank).length;
  return `<span class="chip" title="Score rank among all eligible events in the next 14 days. Events further out score higher on lead time (more time to prepare), so a nearer event can rank lower overall while still being a top pick this week.">Ranked #${e.rank}${n ? ` of ${n}` : ""} · next 14 days</span>`;
}
// While the agent works, show the steps it has finished (polls the server's live tool log).
function agentProgress(el, eventId) {
  const t0 = Date.now(); let stop = false;
  const draw = (steps) => {
    if (stop) return;
    const secs = Math.round((Date.now() - t0) / 1000);
    el.innerHTML = `<div class="card" style="padding:14px 16px"><div style="display:flex;align-items:center;gap:8px;font-weight:700">${ic("loader-2", "icon-sm spin")}Agent at work · ${secs}s</div>
      <ol class="muted" style="margin:8px 0 0 18px;padding:0;font-size:13px;line-height:1.7">${steps.map((x) => `<li>${esc(TOOL_STEP[x.tool] || x.tool)}${x.ms ? ` <span style="opacity:.6">(${(x.ms / 1000).toFixed(1)}s)</span>` : ""}${x.status && x.status !== "ok" ? ` — ${esc(x.status)}` : ""}</li>`).join("")}
      <li style="opacity:.75">${steps.length ? "Reasoning about what it found…" : "Planning which sources to check…"}</li></ol></div>`;
    icons();
  };
  draw([]);
  const tick = async () => {
    if (stop) return;
    try { const r = await api(`/api/investigations/running${eventId ? `?event_id=${encodeURIComponent(eventId)}` : ""}`); draw(r.steps || []); } catch (_) {}
    if (!stop) setTimeout(tick, 1500);
  };
  setTimeout(tick, 800);
  return () => { stop = true; };
}
async function busy(btn, fn, label = "Working…") {
  const html = btn.innerHTML; btn.disabled = true; btn.innerHTML = `${ic("loader-2", "icon-sm spin")}${label}`; icons();
  try { return await fn(); } catch (e) { toast(e.message); } finally { btn.disabled = false; btn.innerHTML = html; icons(); }
}
const LABEL = { sourced_fact: ["fact", "Fact"], inference: ["inference", "Inference"], creative_suggestion: ["creative", "Idea"],
  owner_assumption: ["assumption", "Assumption"], unknown: ["unknown", "Unknown"], disputed: ["disputed", "Disputed"] };
const lab = (c) => { const [k, t] = LABEL[c] || ["", c || ""]; return `<span class="label ${k}">${esc(t)}</span>`; };

const DEV = {
  cancellation: { label: "Cancelled", icon: "x-circle" }, schedule_change: { label: "Time change", icon: "alarm-clock" },
  access_change: { label: "Getting around", icon: "train-front" }, gathering: { label: "Local gathering", icon: "party-popper" },
  event_news: { label: "Event news", icon: "newspaper" } };
const STYPE = { reporting: "News report", "official announcement": "Official", "event listing": "Event listing", "social observation": "Social post" };
const ACTION = { "Quick promo": ["promo", "Get free promo kit", "sparkles"], "Prepare": ["prepare", "Brief your staff", "users"],
  "Build a campaign": ["campaign", "Plan campaign", "megaphone"], "Review affected plan": ["review", "Review plan", "file-pen"],
  "Investigate": ["investigate", "Look into it", "search"], "Monitor": ["follow", "Keep an eye on it", "eye"], "No action": ["details", "See why", "info"] };
const FACTOR = { distance_access: ["Close to you", "map-pin"], timing_hours: ["Fits your hours", "clock"], menu_occasion: ["Fits your menu", "utensils"],
  demand_evidence: ["Proof of demand", "users"], lead_time: ["Time to prepare", "hourglass"], budget_capacity: ["Budget & capacity", "wallet"] };

function cleanHead(h) {
  let tag = null, t = h || "";
  const m = t.match(/^Permitted ([^:]+):\s*(.*)$/i);
  if (m) { tag = "City permit"; t = m[2]; }
  if (/^MTA:\s*/.test(t)) { tag = "MTA"; t = t.replace(/^MTA:\s*/, ""); }
  return { tag, text: t };
}

// ---------------------------------------------------------------- tabs
function setTab(tab) {
  if (document.startViewTransition && !REDUCED && S.tab !== tab && S.booted) return document.startViewTransition(() => setTabNow(tab));
  setTabNow(tab);
}
function setTabNow(tab) {
  S.tab = tab; store.set("ll_tab", tab);
  document.querySelectorAll(".topbar .seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${tab}`));
  moveSegPill();
  if (tab === "briefing" && S.map) setTimeout(() => S.map.invalidateSize(), 60);
  if (tab === "campaigns") loadPlans();
  if (tab === "results") loadResults();
  window.scrollTo({ top: 0 });
}
document.querySelectorAll(".topbar .seg button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

// ---------------------------------------------------------------- boot
async function boot() {
  icons();
  S.boot = await api("/api/bootstrap");
  $("#bizName").textContent = S.boot.profile.name + " · fictional café, real data" + (S.boot.env === "test" ? " · TEST MODE" : "");
  const ap = $("#agentPill");
  ap.querySelector(".pulse").className = "pulse " + (S.boot.agent.configured ? "on" : "off");
  ap.querySelector(".lbl").textContent = S.boot.agent.configured ? "AI agent on" : "AI agent off";
  ap.title = S.boot.agent.configured ? `Model: ${S.boot.agent.provider}` : "No AI model configured — live data still works.";
  $("#today").textContent = new Date().toLocaleDateString("en-US", { timeZone: TZ, weekday: "long", month: "long", day: "numeric" });
  setBell(S.boot.unread);
  initMap();
  await Promise.all([loadOpportunities(), loadPlansQuiet()]);
  await loadBriefing();
  loadRadar(); loadQuestions();
  const saved = store.get("ll_tab");
  setTab(["briefing", "campaigns", "results"].includes(saved) ? saved : "briefing");
  S.booted = true; requestAnimationFrame(moveSegPill); setTimeout(moveSegPill, 400);
  setInterval(() => { loadBriefing(); refreshBell(); }, 120000);
}
function setBell(n) { const b = $("#bellCount"); b.textContent = n; b.hidden = !n; }
async function refreshBell() { try { setBell((await api("/api/bootstrap")).unread); } catch { /* offline */ } }
async function loadPlansQuiet() { try { S.plans = (await api("/api/plans")).filter((p) => p.state !== "archived"); } catch { S.plans = []; } }

$("#btnRefresh").onclick = (e) => busy(e.currentTarget, async () => {
  const r = await api("/api/refresh", { method: "POST" });
  const failed = [];
  Object.entries(r).forEach(([k, v]) => {
    if (k === "news") Object.entries(v).forEach(([kk, vv]) => vv && vv.ok === false && failed.push(kk));
    else if (v && v.ok === false) failed.push(k);
  });
  toast(failed.length ? `Refreshed. Some sources didn’t respond: ${failed.join(", ")}` : "Everything is up to date.");
  await loadOpportunities(); await loadBriefing(); loadRadar();
}, "Refreshing…");

// ---------------------------------------------------------------- briefing
async function loadBriefing() {
  let b;
  try { b = await api("/api/briefing"); } catch (e) { $("#devCards").innerHTML = `<div class="empty">Couldn’t load the briefing: ${esc(e.message)}</div>`; return; }
  S.brief = b;
  const ok = b.sources.filter((s) => ["healthy", "connected", "no_relevant_results"].includes(s.status)).length;
  $("#updated").innerHTML = `${ic("check-circle", "icon-sm")}Updated ${ago(b.last_news_success)} · ${ok}/${b.sources.length} sources OK`;
  renderStats(); renderUrgent(); renderNews(); renderTicker(); fx();
}

function renderStats() {
  const o = S.opps || { counts: {}, top: [] }, b = S.brief || { open_developments: 0, items: [] };
  const top = o.top[0];
  const act = (S.plans || []).filter((p) => !["archived", "completed"].includes(p.state));
  const tasksLeft = act.reduce((a, p) => a + (p.tasks_total - p.tasks_done), 0);
  const tile = (cls, icon, label, value, sub, attrs = "", small = false) =>
    `<div class="stat glass lift reveal ${cls} ${attrs ? "click" : ""}" ${attrs}><div class="stat-icon">${ic(icon)}</div><div class="stat-body">
      <div class="stat-label">${label}</div><div class="stat-value ${small ? "sm" : ""}">${value}</div><div class="stat-sub">${sub}</div></div></div>`;
  const num = (n) => `<span class="count-up" data-to="${n || 0}">0</span>`;
  const topCrowd = top?.crowd?.expected ? ` · ~${fmtK(top.crowd.expected)} people` : "";
  $("#stats").innerHTML =
    tile("a", "calendar-days", "Events nearby", num(o.counts.eligible), `within ${S.boot.profile.discovery.radius_miles} mi · next 2 weeks`) +
    tile("b", "radio", "News & alerts", num(b.open_developments), `${b.items.filter((i) => ["Quick promo", "Prepare", "Build a campaign", "Review affected plan"].includes(i.action)).length} need a move from you`) +
    tile("c", "trophy", "Top pick", top ? esc(short(top.title, 46)) : "—", top ? `${fmtDay(top.start_utc)} · ${top.distance ? top.distance.miles + " mi" : "?"}${topCrowd}` : "No ranked event yet",
      top ? `data-ev="${esc(top.id)}" tabindex="0" role="button"` : "", true) +
    tile("d", "megaphone", "Campaigns", num(act.length), act.length ? `${tasksLeft} task${tasksLeft === 1 ? "" : "s"} to do` : "Build one from a top pick", `data-go="campaigns" tabindex="0" role="button"`);
  $("#stats").querySelectorAll("[data-ev]").forEach((el) => el.onclick = () => openEvent(el.dataset.ev));
  $("#stats").querySelectorAll("[data-go]").forEach((el) => el.onclick = () => setTab(el.dataset.go));
}

function renderTicker() {
  const items = [];
  (S.opps?.top || []).forEach((t) => items.push({ ev: t.id, icon: "trophy", text: `<b>${esc(short(t.title, 48))}</b> · ${fmtDay(t.start_utc)}${t.crowd?.expected ? ` · ~${fmtK(t.crowd.expected)} people` : ""}` }));
  (S.brief?.items || []).slice(0, 6).forEach((d) => items.push({ dev: d.development_id, icon: (DEV[d.dev_type] || DEV.event_news).icon,
    text: `<b>${esc(short(cleanHead(d.headline).text, 60))}</b>${d.implication?.when && d.implication.when !== "Not stated" ? " · " + esc(d.implication.when) : ""}` }));
  if (!items.length) { $("#ticker").hidden = true; return; }
  const html = items.map((x) => `<span class="ticker-item" ${x.ev ? `data-ev="${esc(x.ev)}"` : `data-dev="${esc(x.dev)}"`}>${ic(x.icon, "icon-sm")}${x.text}</span>`).join("");
  $("#tickerMove").innerHTML = html + html;  // duplicated for a seamless loop
  $("#ticker").hidden = false;
  $("#tickerMove").querySelectorAll("[data-ev]").forEach((el) => el.onclick = () => openEvent(el.dataset.ev));
  $("#tickerMove").querySelectorAll("[data-dev]").forEach((el) => el.onclick = () => openDev(el.dataset.dev));
}

function renderUrgent() {
  const u = (S.brief?.urgent || []);
  $("#urgent").innerHTML = u.map((x) => `<div class="banner reveal ${x.kind === "cancellation" ? "" : "warn"}">
      <div class="b-icon">${ic(x.kind === "cancellation" ? "alert-octagon" : "alert-triangle")}</div>
      <div class="b-text"><div class="b-title">${esc(x.title)}: ${x.kind === "cancellation" ? "event cancelled" : "details changed"}</div>
        <div class="b-sub">We drafted an update to your campaign. Review it before anything changes.</div></div>
      <button class="btn primary sm" data-plan="${esc(x.plan_id)}">Review update</button></div>`).join("");
  $("#urgent").querySelectorAll("[data-plan]").forEach((b) => b.onclick = () => { S.planId = b.dataset.plan; store.set("ll_plan", S.planId); setTab("campaigns"); });
}

function renderNews() {
  const b = S.brief, el = $("#devCards");
  $("#newsCount").textContent = b.open_developments;
  if (!b.items.length) {
    const failed = b.news_failures.map((f) => f.provider).join(", ");
    el.innerHTML = `<div class="empty" style="grid-column:1/-1">${ic("coffee", "icon-lg")}<div><b>All quiet nearby.</b></div>
      <div class="small">Nothing in the latest news affects your foot traffic or access${failed ? ` (some sources didn’t respond: ${esc(failed)})` : ""}.</div></div>`;
    $("#moreNews").innerHTML = ""; return;
  }
  const items = S.showAllNews ? b.items : b.items.slice(0, 4);
  el.innerHTML = items.map(newsCard).join("");
  $("#moreNews").innerHTML = b.items.length > 4 ? `<button class="btn ghost" id="moreNewsBtn">${S.showAllNews ? "Show less" : `Show ${b.items.length - 4} more`}</button>` : "";
  if ($("#moreNewsBtn")) $("#moreNewsBtn").onclick = () => { S.showAllNews = !S.showAllNews; renderNews(); fx(); };
  el.querySelectorAll(".ncard").forEach((c) => {
    const id = c.dataset.dev;
    c.onclick = (e) => { if (!e.target.closest("button")) openDev(id); };
    c.onkeydown = (e) => { if (e.key === "Enter" && e.target === c) openDev(id); };
    c.querySelectorAll("button[data-act]").forEach((bt) => bt.onclick = (e) => { e.stopPropagation(); devAction(id, bt.dataset.act, bt); });
  });
}

function newsCard(d) {
  const t = DEV[d.dev_type] || DEV.event_news;
  const h = cleanHead(d.headline);
  const draft = d.agent_draft;
  const why = (draft && draft.implication) || d.implication?.text || "";
  const action = (draft && draft.action) || d.action;
  const [act, actLabel, actIcon] = ACTION[action] || ACTION.Investigate;
  const isNew = !d.seen || d.seen < d.version;
  const when = d.implication?.when && d.implication.when !== "Not stated" ? d.implication.when : null;
  const where = d.location_text ? short(d.location_text.split(",")[0], 34) : null;
  const src = h.tag || STYPE[d.source_types[0]] || "Source";
  return `<article class="ncard glass lift glow-border reveal t-${esc(d.dev_type)}" data-dev="${esc(d.development_id)}" tabindex="0" aria-current="${S.selDev === d.development_id}">
    <div class="ncard-top"><div class="type-icon t-${esc(d.dev_type)}">${ic(t.icon)}</div>
      <div><div class="ncard-type">${esc(t.label)}</div><div class="small muted">${esc(src)}${draft ? " · AI summary" : ""}</div></div>
      <div class="ncard-time">${isNew ? '<span class="new-dot" title="New"></span>' : ""}${when ? esc(when) : ago(d.last_seen)}</div></div>
    <h3>${esc(h.text)}</h3>
    <div class="why">${esc(why)}</div>
    <div class="ncard-foot">${where ? `<span class="chip">${ic("map-pin", "icon-sm")}${esc(where)}</span>` : ""}<span class="spacer"></span>
      <button class="action-pill" data-act="${act}">${ic(actIcon, "icon-sm")}${actLabel}</button></div>
  </article>`;
}

function highlightDev(id) {
  S.selDev = id;
  document.querySelectorAll(".ncard").forEach((c) => c.setAttribute("aria-current", String(c.dataset.dev === id)));
  const d = S.brief?.items.find((x) => x.development_id === id);
  const evs = new Set(d?.implication?.affected?.events || []);
  document.querySelectorAll(".ocard").forEach((c) => c.classList.toggle("related", evs.has(c.dataset.id)));
  restyleMarkers(evs);
}

async function devAction(id, act, btn) {
  const d = S.brief.items.find((x) => x.development_id === id);
  const evs = d?.implication?.affected?.events || [];
  if (act === "campaign" && evs[0]) return busy(btn, () => buildPlan(evs[0]));
  if (act === "promo" || act === "prepare" || act === "details") return openDev(id);
  if (act === "review") { const pl = d.implication.affected.plans[0]; S.planId = pl; store.set("ll_plan", pl); closeDrawer(); return setTab("campaigns"); }
  if (act === "follow") return busy(btn, async () => {
    await api(`/api/developments/${id}/follow`, { method: "POST", body: { on: !d.followed } });
    toast(d.followed ? "Stopped following." : "Following — you’ll get an alert if this changes."); await loadBriefing();
  });
  if (act === "accept" || act === "reject") return busy(btn, async () => {
    await api(`/api/developments/${id}/review`, { method: "POST", body: { accept: act === "accept" } }); await loadBriefing(); openDev(id);
  });
  if (act === "investigate") return busy(btn, async () => {
    const r = await api(`/api/developments/${id}/investigate`, { method: "POST" });
    toast(r.investigation.status === "agent_setup_required" ? "Checked the official source. (AI agent is off.)" : `AI agent finished: ${r.investigation.status}.`);
    await loadOpportunities(); await loadBriefing(); openDev(id, "history");
  }, "Looking into it…");
}

// ---------------------------------------------------------------- rush radar
const RAD_COLORS = [[255, 138, 76], [255, 61, 139], [139, 92, 246]];
function heat(extra, cap) {
  const k = Math.min(1, extra / (cap * 0.9));
  if (k < 0.02) return "rgba(255,255,255,.04)";
  const t = k < .5 ? k * 2 : (k - .5) * 2, [a, b] = k < .5 ? [RAD_COLORS[0], RAD_COLORS[1]] : [RAD_COLORS[1], RAD_COLORS[2]];
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgba(${c[0]},${c[1]},${c[2]},${(0.25 + 0.75 * k).toFixed(2)})`;
}
const hrLabel = (h) => `${h % 12 || 12}${h < 12 ? "am" : "pm"}`;
async function loadRadar() {
  let r;
  try { r = await api("/api/radar"); } catch (e) { $("#radar").innerHTML = `<div class="empty">Rush Radar unavailable: ${esc(e.message)}</div>`; return; }
  S.radar = r;
  if (!S.radarDay || !r.days.some((d) => d.date === S.radarDay)) S.radarDay = (r.days.slice().sort((a, b) => b.extra_total - a.extra_total)[0] || r.days[0]).date;
  renderRadar();
}
function renderRadar() {
  const r = S.radar, hours = r.days[0].hours.map((c) => c.hour);
  const dayLabel = (d) => { const dt = new Date(d.date + "T16:00:00Z"); return dt.toLocaleDateString("en-US", { timeZone: TZ, weekday: "short" }); };
  const best = r.days.slice().sort((a, b) => b.extra_total - a.extra_total)[0];
  $("#radar").innerHTML = `
    <div class="radar-head"><div><div class="overline">Next 7 days · 7am–10pm</div>
      <div style="font-family:'Plus Jakarta Sans';font-size:20px;font-weight:800">Busiest: ${dayLabel(best)} ${best.peak ? hrLabel(best.peak.hour) : ""} · ~${Math.round(best.peak?.total || 0)} orders/h <span class="muted" style="font-size:14px;font-weight:600">(limit ${r.capacity})</span></div></div>
      <span class="chip">${ic("info", "icon-sm")}Estimate from crowd × distance × 25% conversion</span></div>
    <div class="radar-grid" role="grid" aria-label="Expected extra walk-ins by day and hour">
      <div></div>${hours.map((h) => `<div class="hr">${hrLabel(h)}</div>`).join("")}
      ${r.days.map((d) => `<div class="radar-day ${d.date === S.radarDay ? "sel" : ""}" data-day="${d.date}" tabindex="0">${dayLabel(d)}<small>${new Date(d.date + "T16:00:00Z").toLocaleDateString("en-US", { timeZone: TZ, month: "short", day: "numeric" })}</small></div>
        ${d.hours.map((c) => `<div class="cell" data-day="${d.date}" style="background:${heat(c.extra, r.capacity)}${d.date === S.radarDay ? ";outline:1px solid rgba(255,255,255,.18)" : ""}"
          title="${hrLabel(c.hour)}: ~${Math.round(c.total)} orders (normal ~${Math.round(c.baseline)}, +${Math.round(c.extra)} from events)${c.over_capacity ? " — over capacity" : ""}">${c.over_capacity ? '<span class="star">!</span>' : ""}</div>`).join("")}`).join("")}
    </div>
    <div class="radar-legend"><span>Fewer</span><span class="scale"></span><span>More extra walk-ins</span><span class="chip bad">! over your ${r.capacity}/h limit</span></div>
    <div class="radar-detail" id="radarDetail"></div>`;
  $("#radar").querySelectorAll("[data-day]").forEach((el) => el.onclick = () => { S.radarDay = el.dataset.day; renderRadar(); });
  renderRadarDay();
  icons();
}
function renderRadarDay() {
  const r = S.radar, d = r.days.find((x) => x.date === S.radarDay);
  const name = new Date(d.date + "T16:00:00Z").toLocaleDateString("en-US", { timeZone: TZ, weekday: "long", month: "short", day: "numeric" });
  const busy = d.hours.filter((c) => c.extra >= 1);
  const max = Math.max(r.capacity, ...busy.map((c) => c.total));
  const walk = {};
  d.hours.forEach((c) => c.events.forEach((e) => (walk[e.id] = (walk[e.id] || 0) + e.walkins)));
  const evs = d.events.filter((e) => (walk[e.id] || 0) >= 1).sort((a, b) => walk[b.id] - walk[a.id]).slice(0, 5);
  $("#radarDetail").innerHTML = `
    <div><h4>${name}</h4>
      ${busy.length ? busy.map((c) => `<div class="peak"><span class="ph">${hrLabel(c.hour)}</span><span class="pbar"><span style="width:${c.total / max * 100}%"></span></span>
        <span class="pv">${Math.round(c.total)} / ${r.capacity} ${c.over_capacity ? '<span class="chip bad">over</span>' : "orders"}</span></div>`).join("")
        : `<p class="muted">No event-driven rush expected — a normal day.</p>`}
      ${d.staff_hours ? `<div class="staff-note">${ic("users", "icon-sm")} About <b>${d.staff_hours} extra staff hour${d.staff_hours > 1 ? "s" : ""}</b> would cover the peak (≈$${d.staff_hours * r.assumptions.labor_cost_per_hour}). Your call — or switch to grab-and-go only at peak.</div>` : ""}</div>
    <div><h4>Who’s coming</h4>
      ${evs.length ? evs.map((e) => `<div class="item"><a href="#" class="item-title" data-ev="${esc(e.id)}">${esc(short(e.title, 46))}</a>
        <div class="item-sub">${fmtHour(e.start)} · ~${fmtK(e.crowd)} attending · ~${Math.round(walk[e.id])} likely walk-ins</div></div>`).join("") : `<p class="muted">Nothing close enough to matter.</p>`}
      <div class="row" style="margin-top:12px"><button class="btn primary sm" id="copyBrief">${ic("clipboard-copy", "icon-sm")}Copy staff briefing</button>
        ${evs[0] ? `<button class="btn sm" data-plan-ev="${esc(evs[0].id)}">${ic("megaphone", "icon-sm")}Plan campaign</button>` : ""}</div></div>`;
  $("#radarDetail").querySelectorAll("[data-ev]").forEach((a) => a.onclick = (e) => { e.preventDefault(); openEvent(a.dataset.ev); });
  $("#radarDetail").querySelectorAll("[data-plan-ev]").forEach((b) => b.onclick = () => busy(b, () => buildPlan(b.dataset.planEv), "Building…"));
  $("#copyBrief").onclick = (e) => busy(e.currentTarget, async () => {
    const txt = await api(`/api/radar/${d.date}/briefing`);
    try { await navigator.clipboard.writeText(txt); toast("Staff briefing copied — paste it into your team chat."); }
    catch { openDrawer({ title: "Staff briefing", body: `<div class="answer">${esc(txt)}</div>` }); }
  });
  icons();
}

// ---------------------------------------------------------------- follow a question
function watchCard(q, open = false) {
  const st = !S.boot.agent.configured ? ["warn", "AI agent off"] : q.verdict === "MET" ? ["ok", "It happened"] : q.verdict === "NOT MET" ? ["", "Watching · not yet"]
    : q.verdict === "UNCLEAR" ? ["warn", "Unclear · re-checking"] : ["", "First check pending"];
  return `<details class="watch-item ${q.verdict === "MET" ? "met" : ""}" ${open ? "open" : ""}>
    <summary><span class="wi-icon">${ic(q.verdict === "MET" ? "bell-ring" : "radar", "icon-sm")}</span>
      <span class="wi-main"><span class="wi-q">${esc(q.question)}</span>
        <span class="wi-sub"><span class="chip ${st[0]}">${st[1]}</span>${q.event ? ` <span class="chip">${ic("calendar", "icon-sm")}${esc(short(q.event.title, 34))}</span>` : ""} · checked ${q.last_checked ? ago(q.last_checked) : "not yet"}</span></span>
      <span class="wi-chev">${ic("chevron-down", "icon-sm")}</span></summary>
    <div class="wi-body">${q.last_answer ? `<div class="answer">${esc(q.last_answer)}</div>` : `<p class="muted small">No answer yet.</p>`}
      <div class="row" style="margin-top:10px"><button class="btn sm" data-qcheck="${esc(q.id)}">${ic("refresh-cw", "icon-sm")}Check now</button>
        ${q.event ? `<button class="btn sm" data-qev="${esc(q.event.id)}">${ic("eye", "icon-sm")}Open event</button>` : ""}
        <button class="btn ghost sm" data-qstop="${esc(q.id)}">${ic("x-circle", "icon-sm")}Stop following</button>
        <span class="small muted">Re-checked every 6 h · ${q.checks || 0} check${q.checks === 1 ? "" : "s"} so far</span></div></div></details>`;
}
function bindWatchCards(root, after) {
  root.querySelectorAll("[data-qcheck]").forEach((b) => b.onclick = () => busy(b, async () => { await api(`/api/questions/${b.dataset.qcheck}/check`, { method: "POST" }); await after(); refreshBell(); }, "Researching…"));
  root.querySelectorAll("[data-qstop]").forEach((b) => b.onclick = () => busy(b, async () => { await api(`/api/questions/${b.dataset.qstop}`, { method: "DELETE" }); await after(); }));
  root.querySelectorAll("[data-qev]").forEach((b) => b.onclick = () => openEvent(b.dataset.qev, "ask"));
}
async function renderEventWatches(eventId) {
  const box = $("#eventWatches"); if (!box) return;
  const qs = await api(`/api/questions?event_id=${encodeURIComponent(eventId)}`).catch(() => []);
  box.innerHTML = qs.length ? `<h4 class="muted small" style="margin:18px 0 8px;text-transform:uppercase;letter-spacing:.06em">Following for this event (${qs.length})</h4>
    <div class="watch-scroll">${qs.map((q) => watchCard(q)).join("")}</div>` : "";
  bindWatchCards(box, async () => { await renderEventWatches(eventId); loadQuestions(); });
  icons();
}
async function loadQuestions() {
  let qs = [];
  try { qs = await api("/api/questions"); } catch { /* optional */ }
  const met = qs.filter((q) => q.verdict === "MET").length;
  $("#watchList").innerHTML = qs.length ? `<details class="watch-group" ${S.watchOpen ? "open" : ""}>
      <summary><span>${ic("list-checks", "icon-sm")} Questions you’re following <span class="count">${qs.length}</span>${met ? ` <span class="chip ok">${met} happened</span>` : ""}</span>
        <span class="wi-chev">${ic("chevron-down", "icon-sm")}</span></summary>
      <div class="watch-scroll">${qs.map((q) => watchCard(q)).join("")}</div></details>`
    : `<p class="small muted" style="margin:0">You’re not following any questions yet.</p>`;
  const grp = $("#watchList").querySelector(".watch-group");
  if (grp) grp.ontoggle = () => (S.watchOpen = grp.open);
  bindWatchCards($("#watchList"), loadQuestions);
  icons();
}
$("#watchForm").onsubmit = (e) => {
  e.preventDefault();
  const q = $("#watchQ").value.trim();
  if (q.length < 8) return toast("Write a full question, e.g. “Tell me if …”.");
  busy(e.submitter || $("#watchForm button"), async () => {
    await api("/api/questions", { method: "POST", body: { question: q } });
    $("#watchQ").value = ""; S.watchOpen = true; await loadQuestions(); refreshBell();
    toast("Following — the agent checked it once and will re-check every 6 hours.");
  }, "Researching (~1 min)…");
};

// ---------------------------------------------------------------- opportunities
async function loadOpportunities() {
  S.opps = await api("/api/opportunities");
  renderTop(); renderAll(); renderMarkers(); if (S.brief) { renderStats(); renderTicker(); } fx();
}

function fitScore(c) { return c.score == null ? null : Math.round(c.score); }
function oppCard(c) {
  const dp = dateParts(c.start_utc);
  const cr = c.crowd || {};
  const crowdV = cr.expected ? `~${fmtK(cr.expected)}` : esc(cr.band || "Unknown");
  const crowdS = !cr.expected ? "no estimate" : cr.basis === "Venue scale proxy" ? `${esc(cr.band)} · from venue size`
    : cr.basis === "Published estimate" ? `${esc(cr.band)} · published` : `${fmtK(cr.low)}–${fmtK(cr.high)} · typical for type`;
  const posts = c.social?.posts;
  const trend = c.social?.trends ? Object.values(c.social.trends)[0] : null;
  const buzzV = posts == null ? "—" : posts === 0 ? "0 sampled" : `${posts}`;
  const bp = c.social?.by_provider || {}, un = c.social?.unavailable || [];
  const split = [bp.x != null ? `X ${bp.x}` : null, bp.bluesky != null ? `Bluesky ${bp.bluesky}` : un.includes("bluesky") ? "Bluesky n/a" : null].filter(Boolean).join(" · ");
  const buzzS = posts == null ? "not checked yet" : split || (posts === 0 ? "no posts found" : "posts");
  const fit = fitScore(c);
  const reason = (c.rationale?.reasons || [])[0];
  const reasonText = reason ? reason.split(": ").slice(1).join(": ") || reason : null;
  return `<article class="ocard glass lift glow-border reveal" data-id="${esc(c.id)}" tabindex="0" aria-current="${S.selected === c.id}">
    <div class="datebox"><div class="m">${dp.m}</div><div class="d">${dp.d}</div><div class="t">${esc(dp.t)}</div></div>
    <div style="min-width:0">
      ${c.rank ? `<span class="rank-badge">#${c.shortlistRank || c.rank}</span>` : ""}
      <h3>${esc(c.title)}</h3>
      <div class="where">${ic("map-pin", "icon-sm")}${esc(short(c.venue || "Venue unknown", 40))}${c.distance ? ` · <b>${c.distance.miles} mi</b>` : ""} · ${until(c.start_utc)}</div>
      <div class="metrics">
        <div class="metric" title="${esc(cr.method || cr.basis || "")}"><div class="k">${ic("users", "icon-sm")}Crowd</div><div class="v">${crowdV}${cr.estimated ? '<span class="est">EST</span>' : ""}</div><div class="s">${crowdS}</div></div>
        <div class="metric" title="${esc(c.social?.detail || "Sampled posts about the artist or team; not verified attendance")}"><div class="k">${ic("message-circle", "icon-sm")}Buzz</div><div class="v">${buzzV}</div><div class="s">${buzzS}</div></div>
        ${ring(fit)}
      </div>
      ${reasonText ? `<div class="reason">${ic("check", "icon-sm")}<span>${esc(short(reasonText, 90))}</span></div>` : ""}
      <div class="ocard-foot">
        <button class="btn primary sm" data-act="plan">${ic("megaphone", "icon-sm")}Plan campaign</button>
        <button class="btn sm" data-act="open">${ic("eye", "icon-sm")}Details</button>
        <button class="btn ghost sm" data-act="follow" title="${c.followed ? "Stop following" : "Follow for changes"}">${ic(c.followed ? "bookmark-check" : "bookmark", "icon-sm")}${c.followed ? "Following" : "Follow"}</button>
      </div>
    </div></article>`;
}
function renderTop() {
  const el = $("#topCards");
  if (!S.opps.top.length) {
    el.innerHTML = `<div class="empty">${ic("calendar-search", "icon-lg")}<div><b>No ranked events yet.</b></div><div class="small">Press Refresh, or check Data sources (top right).</div></div>`;
    return;
  }
  el.innerHTML = S.opps.top.map((c, i) => oppCard({...c, shortlistRank:i+1})).join("");
  el.querySelectorAll(".ocard").forEach((card) => {
    const id = card.dataset.id;
    card.onclick = (e) => { if (!e.target.closest("button")) openEvent(id); };
    card.onkeydown = (e) => { if (e.key === "Enter" && e.target === card) openEvent(id); };
    card.querySelectorAll("button[data-act]").forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      if (b.dataset.act === "plan") return busy(b, () => buildPlan(id), "Building…");
      if (b.dataset.act === "open") return openEvent(id);
      if (b.dataset.act === "follow") return busy(b, () => toggleFollow(id));
    });
  });
}
async function toggleFollow(id) {
  const c = S.opps.events.find((x) => x.id === id);
  await api(`/api/events/${encodeURIComponent(id)}/follow`, { method: "POST", body: { on: !c.followed } });
  toast(c.followed ? "Stopped following." : "Following — you’ll be alerted to cancellations or time changes.");
  await loadOpportunities();
}

$("#toggleAll").onclick = () => {
  const box = $("#allBox"); box.hidden = !box.hidden;
  $("#toggleAll span").textContent = box.hidden ? "Show all nearby events" : "Hide the full list";
};
$("#winSeg").querySelectorAll("button").forEach((b) => b.onclick = () => {
  S.win = Number(b.dataset.win); S.listLimit = 50;
  $("#winSeg").querySelectorAll("button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  renderAll();
});
$("#eventSearch").oninput = () => { S.listLimit = 50; renderAll(); };
function renderAll() {
  const limit = Date.now() + S.win * 86400000;
  const query = $("#eventSearch").value.trim().toLowerCase();
  const list = S.opps.events.filter((e) => e.eligible && !e.historical && `${e.title} ${e.venue || ""}`.toLowerCase().includes(query) && (!e.start_utc || new Date(e.start_utc).getTime() <= limit))
    .sort((a, b) => (b.score || 0) - (a.score || 0));
  $("#allCount").textContent = `${list.length} events · showing ${Math.min(S.listLimit, list.length)}`;
  $("#allEvents").innerHTML = list.slice(0, S.listLimit).map((e) => `<div class="list-item" data-id="${esc(e.id)}" tabindex="0">
      <div class="li-main"><div class="li-title">${esc(e.title)}</div><div class="li-sub">${fmtTime(e.start_utc)} · ${esc(e.venue || "")}</div></div>
      <div class="li-right">${e.distance ? e.distance.miles + " mi" : "no map pin"}<br><b>${fitScore(e) ?? "—"}</b> fit</div></div>`).join("")
    || `<div class="empty" style="border:0">No events in this window.</div>`;
  if (list.length > S.listLimit) {
    const more = document.createElement("button"); more.className = "btn ghost list-more";
    more.textContent = "Show more events";
    more.onclick = () => { S.listLimit += 50; renderAll(); };
    $("#allEvents").appendChild(more);
  }
  $("#allEvents").querySelectorAll(".list-item").forEach((li) => {
    li.onclick = () => openEvent(li.dataset.id);
    li.onkeydown = (e) => { if (e.key === "Enter") openEvent(li.dataset.id); };
  });
}

// ---------------------------------------------------------------- map
function initMap() {
  if (!window.L) { $("#mapFallback").hidden = false; $("#map").hidden = true; return; }
  const p = S.boot.profile.location;
  const m = L.map("map", { scrollWheelZoom: false, zoomControl: true }).setView([p.lat, p.lon], 15);
  const tiles = L.tileLayer(S.boot.map.tile_url, { maxZoom: 19, attribution: S.boot.map.attribution });
  tiles.on("tileerror", () => { if (++S.tileErrors > 6) $("#mapFallback").hidden = false; });
  tiles.on("load", () => { S.tileErrors = 0; $("#mapFallback").hidden = true; });
  tiles.addTo(m);
  const cup = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M17 8h1a4 4 0 1 1 0 8h-1"/><path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"/></svg>';
  L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "", html: `<div class="cafe-marker">${cup}</div>`, iconSize: [34, 34], iconAnchor: [17, 17] }),
    zIndexOffset: 1000, title: "Opening Act Café" }).addTo(m)
    .bindTooltip("<b>Opening Act Café</b><br>Fictional café · scenario location", { direction: "top", offset: [0, -14] });
  L.circle([p.lat, p.lon], { radius: S.boot.profile.discovery.radius_miles * 1609.344, color: "#ff3d8b", weight: 1.2, opacity: .5, fillColor: "#ff3d8b", fillOpacity: .035, dashArray: "4 6", interactive: false }).addTo(m);
  S.map = m;
}
const BAND_R = { Unknown: 6, Low: 6, Medium: 9, High: 13 };
function renderMarkers() {
  if (!S.map) return;
  new Set(Object.values(S.markers)).forEach((mk) => mk.remove()); S.markers = {};
  const top = new Set(S.opps.top.map((t) => t.id)), groups = new Map();
  const events = S.opps.events.filter((e) => e.lat != null && e.eligible && !e.historical && (S.mapMode === "all" || top.has(e.id)));
  events.forEach((e) => { const key = `${e.lat.toFixed(4)},${e.lon.toFixed(4)}`; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(e); });
  groups.forEach((group) => {
    group.sort((a,b) => (a.rank || 9999) - (b.rank || 9999));
    const e = group[0], isTop = group.some((x) => top.has(x.id));
    const mk = L.marker([e.lat, e.lon], {title: `${e.venue}: ${group.length} events`, icon: L.divIcon({className: "venue-pin", html: `<span class="venue-dot ${isTop ? "top" : ""}">${group.length > 1 ? group.length : isTop ? "★" : "•"}</span>`, iconSize:[32,32], iconAnchor:[16,16]})}).addTo(S.map);
    mk.bindTooltip(`<b>${esc(e.venue || e.title)}</b><br>${group.length} event${group.length === 1 ? "" : "s"} · click to explore`, {direction:"top", offset:[0,-12]});
    mk.on("click", () => {
      if (group.length === 1) return openEvent(e.id);
      openDrawer({title: e.venue || "Events here", body: `<p class="small muted">${group.length} upcoming events at this location. Select one to inspect its evidence.</p>${group.map((x) => `<button class="venue-event" data-venue-event="${esc(x.id)}"><span><b>${esc(x.title)}</b><small>${fmtTime(x.start_utc)}</small></span>${ic("arrow-up-right", "icon-sm")}</button>`).join("")}`});
      $("#drawerBody").querySelectorAll("[data-venue-event]").forEach((button) => button.onclick = () => openEvent(button.dataset.venueEvent)); icons();
    });
    group.forEach((x) => S.markers[x.id] = mk);
  });
  $("#mapCount").textContent = `${groups.size} venue${groups.size === 1 ? "" : "s"} · ${events.length} events`;
}
function restyleMarkers(related = new Set()) {
  new Set(Object.values(S.markers)).forEach((mk) => {
    const ids = Object.keys(S.markers).filter((id) => S.markers[id] === mk);
    mk.getElement()?.classList.toggle("selected", ids.includes(S.selected) || ids.some((id) => related.has(id)));
  });
}
$("#mapReset").onclick = () => {
  if (!S.map) return;
  if (S.line) { S.line.remove(); S.line = null; }
  const p = S.boot.profile.location;
  const coords = [[p.lat, p.lon], ...S.opps.top.filter((e) => e.lat != null).map((e) => [e.lat, e.lon])];
  S.map.fitBounds(L.latLngBounds(coords).pad(.35), {maxZoom:15});
};
$(".map-switch").querySelectorAll("button").forEach((button) => button.onclick = () => {
  S.mapMode = button.dataset.mapMode;
  $(".map-switch").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
  renderMarkers();
});
$("#btnResearchLead").onclick = () => {
  if (S.opps?.top[0]) openEvent(S.opps.top[0].id, "ask");
  else toast("Refresh events to find an opportunity to investigate.");
};
function focusOnMap(e) {
  if (!S.map) return;
  if (S.line) { S.line.remove(); S.line = null; }
  restyleMarkers();
  if (!e || e.lat == null) return;
  const p = S.boot.profile.location;
  S.line = L.polyline([[p.lat, p.lon], [e.lat, e.lon]], { color: "#ff8a4c", weight: 3, dashArray: "6 8", className: "dist-line" })
    .bindTooltip(`${e.distance?.miles} mi as the crow flies`, { sticky: true }).addTo(S.map);
  S.markers[e.id]?.setZIndexOffset(1000);
  S.map.fitBounds(L.latLngBounds([[p.lat, p.lon], [e.lat, e.lon]]).pad(0.6), { maxZoom: 16 });
}

// ---------------------------------------------------------------- drawer
function openDrawer({ title, head = "", tabs = null, body = "", initial = null, onRender = null }) {
  $("#drawerTitle").textContent = title;
  $("#overlay").hidden = false; $("#drawer").hidden = false;
  const render = (tabId) => {
    if (tabs) {
      $("#drawerHeadExtra").innerHTML = head + `<div class="drawer-tabs" role="tablist">${tabs.map((t) =>
        `<button role="tab" data-t="${t.id}" aria-selected="${t.id === tabId}">${ic(t.icon, "icon-sm")}${esc(t.label)}</button>`).join("")}</div>`;
      $("#drawerHeadExtra").querySelectorAll("[data-t]").forEach((b) => b.onclick = () => render(b.dataset.t));
      $("#drawerBody").innerHTML = tabs.find((t) => t.id === tabId).render();
    } else {
      $("#drawerHeadExtra").innerHTML = head + '<div class="drawer-head-pad"></div>';
      $("#drawerBody").innerHTML = body;
    }
    $("#drawerBody").scrollTop = 0;
    onRender && onRender(tabId);
    fx($("#drawer"));
  };
  render(tabs ? (initial || tabs[0].id) : null);
  $("#drawerClose").focus();
}
function closeDrawer() { $("#drawer").hidden = true; $("#overlay").hidden = true; }
$("#drawerClose").onclick = closeDrawer;
$("#overlay").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer(); });

// ---------------------------------------------------------------- event detail
async function openEvent(id, initial = "overview") {
  S.selected = id;
  document.querySelectorAll(".ocard, .list-item").forEach((el) => el.setAttribute("aria-current", String(el.dataset.id === id)));
  const card = S.opps?.events.find((x) => x.id === id);
  focusOnMap(card);
  openDrawer({ title: card ? card.title : "Loading…", body: `<div class="skeleton"></div>` });
  let D;
  try { D = await api(`/api/events/${encodeURIComponent(id)}/detail`); } catch (e) { $("#drawerBody").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const e = D.event, plan = D.plans[0];
  const head = `<div class="sub">
      <span class="chip">${ic("calendar", "icon-sm")}${fmtTime(e.start_utc)}</span>
      <span class="chip">${ic("map-pin", "icon-sm")}${esc(short(e.venue || "Unknown venue", 36))}${e.distance ? ` · ${e.distance.miles} mi` : ""}</span>
      ${e.status === "cancelled" ? '<span class="chip bad">Cancelled</span>' : e.historical ? '<span class="chip">Past</span>' : ""}
      ${rankChip(e)}</div>
    <div class="drawer-actions">
      <button class="btn primary" id="dPlan">${ic("megaphone", "icon-sm")}${plan ? "Open campaign" : "Plan campaign"}</button>
      <button class="btn" id="dFollow">${ic(e.followed ? "bookmark-check" : "bookmark", "icon-sm")}${e.followed ? "Following" : "Follow"}</button>
      <button class="btn" id="dResearch">${ic("refresh-cw", "icon-sm")}Check news &amp; buzz</button>
      <button class="btn ghost" id="dDismiss" title="Hide from rankings">${ic("eye-off", "icon-sm")}Not interested</button></div>`;
  const tabs = [
    { id: "overview", label: "Overview", icon: "layout-grid", render: () => eventOverview(D) },
    { id: "news", label: `News (${D.news.filter((n) => !n.retrospective).length})`, icon: "newspaper", render: () => eventNews(D) },
    { id: "buzz", label: "Buzz", icon: "message-circle", render: () => eventBuzz(D) },
    { id: "ask", label: "Ask AI", icon: "sparkles", render: () => eventAsk(D) },
    { id: "sources", label: "Sources", icon: "library", render: () => eventSources(D) },
  ];
  openDrawer({ title: e.title, head, tabs, initial, onRender: (t) => bindEvent(D, t) });
}

function eventOverview(D) {
  const e = D.event, f = D.factors || {}, fac = f.factors || {};
  const fit = fitScore(e), cov = Math.round((e.coverage || 0) * 100);
  const bars = Object.keys(FACTOR).map((k) => {
    const v = fac[k];
    return `<div class="factor" title="${esc((f.notes || {})[k] || "")}"><span>${ic(FACTOR[k][1], "icon-sm")} ${FACTOR[k][0]}</span>
      <div class="bar"><span data-w="${v == null ? 0 : v * 20}%"></span></div>
      <span class="val ${v == null ? "unk" : ""}">${v == null ? "?" : v + "/5"}</span></div>`;
  }).join("");
  const crowd = e.crowd || {};
  const buzzPosts = e.social?.posts;
  const devs = (D.developments || []).map((x) => `<div class="item"><a href="#" data-dev="${esc(x.id)}" class="item-title">${esc(cleanHead(x.headline).text)}</a>
      <div class="item-sub">${esc((DEV[x.dev_type] || {}).label || x.dev_type)} · ${esc(x.verification_state)}</div></div>`).join("");
  return `
    <div class="block"><h4>${ic("target", "icon-sm")}Fit for your café ${cov < 100 ? `<span class="chip warn" style="margin-left:auto">${cov}% of data known</span>` : '<span class="chip ok" style="margin-left:auto">Complete data</span>'}</h4>
      <div class="fit-wrap">${ring(fit, "lg")}<div>${bars}</div></div></div>
    ${e.uncertainty ? `<div class="block"><h4>${ic("help-circle", "icon-sm")}Biggest unknown</h4><p>${esc(e.uncertainty)}</p></div>` : ""}
    <div class="two-col">
      <div class="block"><h4>${ic("users", "icon-sm")}Expected crowd</h4>
        <div class="big-num">${crowd.expected ? `<span class="count-up" data-to="${crowd.expected}">0</span>` : esc(crowd.band || "Unknown")}${crowd.estimated ? '<span class="est">EST</span>' : ""}</div>
        <div class="small muted" style="margin-top:6px">${crowd.low && crowd.high ? `Range ${fmtK(crowd.low)}–${fmtK(crowd.high)} · ${esc(crowd.band)}<br>` : ""}${esc(crowd.method || crowd.basis || "")}</div></div>
      <div class="block"><h4>${ic("message-circle", "icon-sm")}Buzz</h4><div class="big-num">${buzzPosts == null ? "Not checked" : buzzPosts === 0 ? "Quiet" : `<span class="count-up" data-to="${buzzPosts}">0</span> posts`}</div>
        <div class="small muted">${esc(e.social?.trends ? Object.entries(e.social.trends).map(([p, t]) => `${p === "x" ? "X" : "Bluesky"}: ${t}`).join(" · ") : "Press “Check news & buzz”")}</div></div>
    </div>
    <div class="block"><h4>${ic("info", "icon-sm")}Event details</h4><dl class="kv">
      <dt>Status</dt><dd>${esc(e.status)}${D.sales_note ? `<div class="small muted">${esc(D.sales_note)}</div>` : ""}</dd>
      <dt>When</dt><dd>${fmtTime(e.start_utc)}</dd>
      <dt>Type</dt><dd>${esc(e.category || "—")}</dd>
      <dt>Distance</dt><dd>${e.distance ? `${e.distance.miles} mi as the crow flies` : "Unknown (no location published)"}</dd>
      <dt>Listing</dt><dd>${link(e.url, "Open official listing")} <span class="muted small">· checked ${ago(e.last_success)}</span></dd></dl></div>
    ${devs ? `<div class="block"><h4>${ic("radio", "icon-sm")}Related news &amp; alerts</h4>${devs}</div>` : ""}`;
}
function eventNews(D) {
  const news = D.news.filter((n) => !n.retrospective), retro = D.news.filter((n) => n.retrospective);
  if (!news.length && !retro.length) return `<div class="empty">${ic("newspaper", "icon-lg")}<div><b>No coverage found yet.</b></div><div class="small">Coverage may not have been retrieved yet. Press “Check news &amp; buzz” to search again.</div></div>`;
  return `<p class="small muted">Event, residency and team coverage. Background articles may describe other dates.</p><div class="block">${news.map((n) => `<div class="item"><div class="item-title">${link(n.url, n.title)}</div>
      <div class="item-sub">${esc(n.domain)} · published ${ago(n.published_at)}${n.copies > 1 ? ` · ${n.copies} outlets ran this` : ""}</div></div>`).join("")}</div>
    ${retro.length ? `<div class="block"><h4>Older coverage (background)</h4>${retro.map((n) => `<div class="item">${link(n.url, n.title)}</div>`).join("")}</div>` : ""}`;
}
function eventBuzz(D) {
  const xs = D.x_status;
  const obs = D.social.filter((s, i, all) => s.status === "ok" && !all.slice(0, i).some((p) => p.provider === s.provider && p.status === "ok"));
  const failed = D.social.filter((s) => s.status !== "ok" && !obs.some((o) => o.provider === s.provider && o.collected_at >= s.collected_at));
  const card = (s) => {
    const m = s.metrics || {}, tr = { ...(m.trend || {}) };
    if (tr.label === "Trend unavailable") {
      tr.label = s.provider === "x" ? "Comparison not collected" : s.truncated || m.prior_window?.truncated ? "Partial sample" : "Not enough posts";
      if (s.provider === "x" && m.counts_error === "counts not requested for this call") tr.reason = "Post search is working. Daily comparison counts are disabled to limit paid API usage.";
    }
    const name = s.provider === "x" ? "X (Twitter)" : "Bluesky";
    const needs = Object.entries(s.needs || {});
    return `<div class="block"><div class="row" style="justify-content:space-between"><h4 style="margin:0">${ic(s.provider === "x" ? "twitter" : "cloud", "icon-sm")}${name}</h4>
        <span class="chip ${tr.label === "Rising" ? "ok" : tr.label === "Falling" ? "warn" : ""}">${esc(tr.label || "Trend unavailable")}</span></div>
      <div class="buzz-nums">
        ${s.provider === "x" && m.counts ? `<div><b>${Number(m.counts.current_24h).toLocaleString()}</b><span>total posts on X, last 24h</span></div>` : ""}
        ${s.provider === "bluesky" && tr.method && tr.method.includes("rate") ? `<div><b>${tr.current}</b><span>posts / hour now</span></div>` : ""}
        <div><b>${s.unique_posts ?? 0}</b><span>sampled posts</span></div>
        <div><b>${s.unique_authors ?? 0}</b><span>people</span></div>
        <div><b>${m.geo_specific_posts ?? "—"}</b><span>mention NYC</span></div></div>
      <div class="small muted" style="margin:8px 0">Search: ${esc(s.query)} · ${s.provider === "bluesky" ? "artist/team discussion; NYC mentions counted separately" : "venue/city-filtered discussion"}</div><div class="trend-explanation"><b>${tr.change_pct != null ? `${esc(tr.label)} · ${tr.change_pct > 0 ? "+" : ""}${tr.change_pct}% vs the previous 24 hours` : tr.current != null ? `${tr.current} now · ${tr.prior} before` : "Why this status?"}</b>
        <div>${tr.method ? `<span class="muted">${esc(tr.method)}.</span> ` : ""}${esc(tr.reason || "A comparable earlier sample is needed.")}</div></div>
      ${needs.length ? `<div class="row" style="margin-bottom:8px">${needs.map(([k, v]) => `<span class="chip accent">${esc(k)} · ${v}</span>`).join("")}</div>` : ""}
      ${(s.excerpts || []).slice(0, 3).map((x) => `<div class="quote">“${esc(x.text)}” ${link(x.url, "view")}</div>`).join("")}
      <div class="small muted">Window: ${fmtTime(s.window_start)} → ${fmtTime(s.window_end)} · checked ${ago(s.collected_at)}${s.truncated ? " · sample only" : ""}</div></div>`;
  };
  return `${obs.map(card).join("") || `<div class="empty">${ic("message-circle", "icon-lg")}<div><b>No buzz checked yet.</b></div><div class="small">Press “Check news &amp; buzz”.</div></div>`}
    ${failed.map((s) => `<p class="small muted">${esc(s.provider)}: ${esc(s.detail || "The latest request did not succeed.")}</p>`).join("")}
    ${xs.status !== "connected" ? `<p class="small muted">${ic("twitter", "icon-sm")} X (Twitter): ${esc(xs.status.replace("_", " "))} — paid reads are off.</p>` : ""}
    <div class="fineprint">${ic("info", "icon-sm")}Posts show online attention, not ticket sales. No posts doesn’t mean no demand.</div>`;
}
function eventAsk(D) {
  const on = S.boot.agent.configured;
  return `${on ? "" : `<div class="banner warn" style="margin:0 0 14px">${ic("bot")}<div class="b-text"><div class="b-title">AI agent is off</div><div class="b-sub">Add a model key in .env to ask questions.</div></div></div>`}
    <div class="quick-qs"><button class="btn sm" data-q="Why this event?">Why this event?</button>
      <button class="btn sm" data-q="What would make you change this recommendation?">What would change your mind?</button>
      <button class="btn sm" id="dInvestigate">${ic("search", "icon-sm")}Full investigation</button></div>
    <form class="ask-row" id="askForm"><input id="askInput" maxlength="500" placeholder="Ask, or say “let me know if this gets called off”…" aria-label="Question"><button class="btn primary">${ic("send", "icon-sm")}Ask</button>
      <button type="button" class="btn" id="askFollow" title="Keep watching this question every 6 hours">${ic("radar", "icon-sm")}Follow this</button></form>
    <div class="fineprint" style="margin-top:8px">${ic("info", "icon-sm")}<span><b>Ask</b> answers once. <b>Follow this</b> (or phrases like “keep a watch”, “let me know if”) makes the agent re-check every 6 hours and alert you when it happens.</span></div>
    <div id="askOut" style="margin-top:14px"></div>
    <div id="eventWatches"></div>
    ${D.investigations.length ? `<h4 class="muted small" style="margin:18px 0 8px;text-transform:uppercase;letter-spacing:.06em">Earlier answers</h4>${D.investigations.map((r) => invHtml(r)).join("")}` : ""}`;
}
function invHtml(r, open = false) {
  if (r.status === "agent_setup_required") return `<div class="answer">The AI agent is off, so nothing was generated.</div>`;
  const log = (r.tool_log || []).map((l) => `<div>${l.step}. ${esc(l.tool)} → ${esc(l.status)}</div>`).join("");
  return `<details class="block" ${open ? "open" : ""} style="padding:12px 16px"><summary style="cursor:pointer;font-weight:600">${esc(r.question || "Investigation")}
      <span class="muted small" style="font-weight:400"> · ${ago(r.started_at)} · ${esc(r.status)}</span></summary>
    ${r.answer ? `<div class="answer" style="margin-top:10px">${esc(r.answer)}</div>` : ""}
    ${(r.errors || []).length ? `<p class="small" style="color:var(--bad)">${esc(r.errors.join("; "))}</p>` : ""}
    ${log ? `<details style="margin-top:8px"><summary class="small muted" style="cursor:pointer">Steps the agent took (${r.tool_log.length})</summary><div class="log">${log}</div></details>` : ""}</details>`;
}
function eventSources(D) {
  return `<div class="block"><h4>Where this comes from</h4>
    ${D.sources.map((s) => `<div class="item"><div class="item-title">${link(s.url, s.title || s.id)}</div><div class="item-sub">${esc(s.provider)} · retrieved ${ago(s.retrieved_at)}</div></div>`).join("")}
    ${D.news.map((n) => `<div class="item"><div class="item-title">${link(n.url, n.title)}</div><div class="item-sub">${esc(n.domain)} · news</div></div>`).join("")}</div>
    ${D.claims.length ? `<div class="block"><h4>Claims we checked</h4>${D.claims.map((c) => `<div class="item">${lab(c.classification)} ${esc(c.statement)}</div>`).join("")}</div>` : ""}
    ${D.history.length ? `<div class="block"><h4>Changes to this listing</h4>${D.history.map((h) => `<div class="item"><b>${esc(h.change_kind)}</b> <span class="muted small">${ago(h.changed_at)}</span></div>`).join("")}</div>` : ""}`;
}
function bindEvent(D, tab) {
  const e = D.event;
  $("#dPlan").onclick = (ev) => busy(ev.currentTarget, () => buildPlan(e.id), "Building…");
  $("#dFollow").onclick = (ev) => busy(ev.currentTarget, async () => { await toggleFollow(e.id); openEvent(e.id, tab); });
  $("#dDismiss").onclick = (ev) => busy(ev.currentTarget, async () => {
    await api(`/api/events/${encodeURIComponent(e.id)}/dismiss`, { method: "POST", body: { on: true } });
    closeDrawer(); toast("Hidden from your rankings."); await loadOpportunities();
  });
  $("#dResearch").onclick = (ev) => busy(ev.currentTarget, async () => {
    const r = await api(`/api/events/${encodeURIComponent(e.id)}/research`, { method: "POST", body: {} });
    const bad = []; if (!r.news?.ok) bad.push("news");
    Object.entries(r.social || {}).forEach(([k, v]) => { if (!v.ok) bad.push(k === "x" ? "X" : k); });
    toast(bad.length ? `Done. Not available right now: ${bad.join(", ")}.` : "Updated news and buzz.");
    await loadOpportunities(); openEvent(e.id, "buzz");
  }, "Checking…");
  $("#drawerBody").querySelectorAll("a[data-dev]").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); openDev(a.dataset.dev); });
  if (tab === "ask") {
    const ask = (q, btn, follow = false) => busy(btn, async () => {
      const done = agentProgress($("#askOut"), e.id);
      let r; try { r = await api(`/api/events/${encodeURIComponent(e.id)}/ask`, { method: "POST", body: { question: q, follow } }); } finally { done(); }
      if (r.followed) {
        $("#askOut").innerHTML = `<div class="banner" style="margin:0 0 12px;background:linear-gradient(120deg,rgba(52,211,153,.14),rgba(6,182,212,.08));border-color:rgba(52,211,153,.35)">
          <div class="b-icon" style="color:#6ee7b7">${ic("radar")}</div><div class="b-text"><div class="b-title">Now following this</div>
          <div class="b-sub">The agent checked it once (below) and will re-check every 6 hours. You’ll get an alert in the bell when it happens. It’s also listed under “Your research agent” on the Briefing page.</div></div></div>`;
        $("#askInput").value = "";
        renderEventWatches(e.id); loadQuestions(); refreshBell();
      } else $("#askOut").innerHTML = invHtml(r, true);
      fx($("#askOut"));
    }, follow ? "Setting up watch…" : "Thinking…");
    $("#drawerBody").querySelectorAll("button[data-q]").forEach((b) => b.onclick = () => ask(b.dataset.q, b));
    $("#askForm").onsubmit = (ev) => { ev.preventDefault(); const q = $("#askInput").value.trim(); if (q) ask(q, $("#askForm button")); };
    $("#askFollow").onclick = (ev) => { const q = $("#askInput").value.trim() || `Let me know if ${e.title} is cancelled, postponed or changes time`; ask(q, ev.currentTarget, true); };
    renderEventWatches(e.id);
    $("#dInvestigate").onclick = (ev) => busy(ev.currentTarget, async () => {
      const done = agentProgress($("#askOut"), e.id);
      let r; try { r = await api(`/api/events/${encodeURIComponent(e.id)}/investigate`, { method: "POST" }); } finally { done(); }
      $("#askOut").innerHTML = invHtml(r, true); fx($("#askOut")); loadOpportunities();
    }, "Investigating…");
  }
}

// ---------------------------------------------------------------- development detail
async function openDev(id, initial = "overview") {
  highlightDev(id);
  const d0 = S.brief?.items.find((x) => x.development_id === id);
  openDrawer({ title: d0 ? cleanHead(d0.headline).text : "Loading…", body: `<div class="skeleton"></div>` });
  let h;
  try { h = await api(`/api/developments/${id}`); } catch (e) { $("#drawerBody").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (d0 && (!d0.seen || d0.seen < d0.version)) api(`/api/developments/${id}/seen`, { method: "POST" }).then(() => { d0.seen = d0.version; });
  const d = h.development, b = h.briefing_item || {}, imp = b.implication || {}, cert = b.certainty || {};
  const t = DEV[d.dev_type] || DEV.event_news;
  const ch = cleanHead(d.headline);
  const draft = b.agent_draft;
  const action = (draft && draft.action) || b.action || "Investigate";
  const [act, actLabel, actIcon] = ACTION[action] || ACTION.Investigate;
  const head = `<div class="sub"><span class="chip">${ic(t.icon, "icon-sm")}${t.label}</span>
      ${(d.source_types || []).map((s) => `<span class="chip outline">${esc(ch.tag && s === "official announcement" ? ch.tag : STYPE[s] || s)}</span>`).join("")}
      <span class="chip ${/verified|official/.test(d.verification_state) ? "ok" : /disputed/.test(d.verification_state) ? "bad" : ""}">${esc(d.verification_state)}</span></div>
    <div class="drawer-actions"><button class="btn primary" data-act="${act}">${ic(actIcon, "icon-sm")}${actLabel}</button>
      ${act !== "investigate" ? `<button class="btn" data-act="investigate">${ic("search", "icon-sm")}Look into it</button>` : ""}
      ${act !== "follow" ? `<button class="btn" data-act="follow">${ic(d.followed ? "bookmark-check" : "bookmark", "icon-sm")}${d.followed ? "Following" : "Follow"}</button>` : ""}
      ${draft && b.review_state === "agent_draft_pending_review" ? `<button class="btn" data-act="accept">${ic("check", "icon-sm")}Accept AI summary</button>` : ""}</div>`;
  const overview = () => {
    const why = (draft && draft.implication) || imp.text;
    const evs = h.events.map((e) => `<div class="item"><a href="#" data-ev="${esc(e.id)}" class="item-title">${esc(e.title)}</a><div class="item-sub">${fmtTime(e.start_utc)} · ${esc(e.status)}</div></div>`).join("");
    return `
      <div class="block"><h4>${ic("lightbulb", "icon-sm")}Why it matters to you</h4><p class="big-why">${esc(why)}</p>
        <div class="small muted" style="margin-top:8px">${draft ? `AI summary (${esc(draft.model)})${b.review_state === "agent_draft_pending_review" ? " — waiting for your review" : ""}` : "Automatic rule-based read — AI agent hasn’t looked yet"}</div></div>
      <div class="block"><h4>${ic("list-checks", "icon-sm")}What to do</h4><p><b>${esc(action)}.</b> ${esc((draft && draft.action_rationale) || b.action_rationale || "")}</p></div>
      ${action === "Quick promo" ? promoKit(ch.text, d, imp) : ""}
      ${action === "Prepare" ? `<div class="block"><h4>${ic("users", "icon-sm")}Staff heads-up (copy & send)</h4><p class="quote" id="kit-staff">Heads up: ${esc(ch.text)} — ${esc(imp.when || "")}. Expect fewer walk-ins from that direction; keep grab-and-go stocked.</p>
        <button class="btn sm" data-copykit="kit-staff">${ic("copy", "icon-sm")}Copy</button></div>` : ""}
      <div class="block"><h4>${ic("map-pin", "icon-sm")}When &amp; where</h4><dl class="kv">
        <dt>When</dt><dd>${esc(imp.when || "Not stated")}</dd>
        <dt>Where</dt><dd>${esc(d.location_text || "Not stated")}${d.lat == null ? '<div class="small muted">No exact location published, so it’s not on the map</div>' : ""}</dd></dl></div>
      <div class="block"><h4>${ic("file-text", "icon-sm")}What happened</h4><p>${esc((draft && draft.summary) || d.summary || d.headline)}</p></div>
      ${evs ? `<div class="block"><h4>${ic("calendar", "icon-sm")}Linked events</h4>${evs}</div>` : ""}`;
  };
  const evidence = () => `
    <div class="block"><h4>${ic("shield-check", "icon-sm")}How sure are we?</h4><dl class="kv">
      <dt>Status</dt><dd>${esc(d.verification_state)}</dd><dt>Sources</dt><dd>${cert.sources ?? h.sources.length}</dd>
      ${(cert.limitations || []).length ? `<dt>Gaps</dt><dd>${cert.limitations.map((l) => `<span class="chip" style="margin:0 4px 4px 0">${esc(l)}</span>`).join("")}</dd>` : ""}
      ${cert.would_change ? `<dt>Would change if</dt><dd>${esc((draft && draft.would_change) || cert.would_change)}</dd>` : ""}</dl></div>
    <div class="block"><h4>${ic("library", "icon-sm")}Sources</h4>${h.sources.map((s) => `<div class="item"><div class="item-title">${link(s.url, s.title || s.id)}</div>
      <div class="item-sub">${esc(STYPE[s.source_type] || s.source_type)}${s.published_at ? ` · published ${fmtDay(s.published_at)}` : ""}</div></div>`).join("")}</div>`;
  const history = () => `
    <div class="block"><h4>${ic("history", "icon-sm")}What changed</h4>${h.versions.map((v) => `<div class="item"><div class="item-title">${esc(v.what_changed)}</div><div class="item-sub">${fmtTime(v.at)}</div></div>`).join("")}</div>
    <div class="block"><h4>${ic("git-compare", "icon-sm")}Decisions</h4>${h.decisions.length ? h.decisions.map((x) => `<div class="item"><div class="item-title">${esc(x.decision)}</div>
      <div class="item-sub">${esc(x.source_change)} · ${ago(x.at)}</div>${baHtml({ before: x.before, after: x.after })}</div>`).join("") : '<p class="muted">No decisions yet — press “Look into it”.</p>'}</div>`;
  openDrawer({ title: ch.text, head, initial, tabs: [
    { id: "overview", label: "Overview", icon: "layout-grid", render: overview },
    { id: "evidence", label: "Evidence", icon: "shield-check", render: evidence },
    { id: "history", label: "History", icon: "history", render: history }],
    onRender: () => {
      $("#drawerHeadExtra").querySelectorAll("button[data-act]").forEach((bt) => bt.onclick = () => devAction(id, bt.dataset.act, bt));
      $("#drawerBody").querySelectorAll("a[data-ev]").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); openEvent(a.dataset.ev); });
      $("#drawerBody").querySelectorAll("[data-copykit]").forEach((b) => b.onclick = async () => {
        try { await navigator.clipboard.writeText($("#" + b.dataset.copykit).textContent); toast("Copied — paste it wherever you like."); }
        catch { toast("Couldn’t access the clipboard."); }
      });
    } });
}
function promoKit(name, d, imp) {
  const where = d.location_text ? d.location_text.split(",")[0] : "nearby";
  const when = imp.when && imp.when !== "Not stated" ? imp.when : "today";
  const sign = `Going to ${name}? Coffee & a pastry to go — ready in 2 minutes. Opening Act Café, Seventh Ave & W 30th.`;
  const story = `${name} is happening ${when} at ${where} — we’re right around the corner. Swing by for an iced coffee and something warm to go ☕️`;
  return `<div class="block" style="border-color:rgba(255,61,139,.35);background:linear-gradient(120deg,rgba(255,138,76,.10),rgba(139,92,246,.08))">
    <h4>${ic("sparkles", "icon-sm")}Free promo kit — $0, takes 5 minutes</h4>
    <div class="small muted" style="margin-bottom:6px">1 · Chalk this on your sidewalk sign</div><p class="quote" id="kit-sign">${esc(sign)}</p>
    <button class="btn sm" data-copykit="kit-sign">${ic("copy", "icon-sm")}Copy sign text</button>
    <div class="small muted" style="margin:14px 0 6px">2 · Post this as an Instagram story the morning of</div><p class="quote" id="kit-story">${esc(story)}</p>
    <button class="btn sm" data-copykit="kit-story">${ic("copy", "icon-sm")}Copy story text</button>
    <div class="fineprint">${ic("info", "icon-sm")}Not affiliated with the organizer. Nothing is posted for you.</div></div>`;
}

function baHtml(ba) {
  if (!ba || !ba.before) return "";
  const flat = (x) => (x && x.facts ? { ...x.facts, recommendation: x.recommendation } : x) || {};
  const fmt = (x) => Object.entries(flat(x)).filter(([, v]) => v != null && typeof v !== "object")
    .map(([k, v]) => `<div>${esc(k.replace(/_utc$/, "").replace(/_/g, " "))}: <b>${esc(/_utc$|start$/.test(k) ? fmtTime(v) : v)}</b></div>`).join("") || "—";
  return `<div class="ba"><div><b>Before</b>${fmt(ba.before)}</div><div><b>After</b>${fmt(ba.after)}</div></div>`;
}

// ---------------------------------------------------------------- campaigns
async function buildPlan(eventId) {
  const p = await api("/api/plans", { method: "POST", body: { event_id: eventId } });
  S.planId = p.id; S.phase = null; S.planTab = "tasks"; store.set("ll_plan", p.id);
  closeDrawer(); setTab("campaigns"); toast("Campaign plan ready — review it below.");
}
async function loadPlans() {
  S.plans = (await api("/api/plans")).filter((p) => p.state !== "archived");
  if (!S.planId) S.planId = store.get("ll_plan");
  if (!S.plans.some((p) => p.id === S.planId)) S.planId = S.plans[0]?.id || null;
  $("#planList").innerHTML = S.plans.map((p) => `<div class="ccard glass lift glow-border reveal" data-plan="${esc(p.id)}" tabindex="0" aria-current="${p.id === S.planId}">
      <div class="ct">${esc(short(p.event_title, 48))}</div>
      <div class="cs">${fmtDay(p.event_start)} ${stateChip(p.state)}${p.pending_revision ? '<span class="chip warn">update</span>' : ""}${p.event_status === "cancelled" ? '<span class="chip bad">cancelled</span>' : ""}</div>
      <div class="progress"><span style="width:0" data-w="${p.tasks_total ? (p.tasks_done / p.tasks_total) * 100 : 0}%"></span></div>
      <div class="small muted" style="margin-top:6px">${p.tasks_done} of ${p.tasks_total} tasks done</div></div>`).join("")
    || `<div class="empty">${ic("megaphone", "icon-lg")}<div><b>No campaigns yet</b></div><div class="small">Open Briefing and press “Plan campaign” on a top pick.</div></div>`;
  $("#planList").querySelectorAll("[data-plan]").forEach((el) => {
    const go = () => { S.planId = el.dataset.plan; S.phase = null; store.set("ll_plan", S.planId); loadPlans(); };
    el.onclick = go; el.onkeydown = (e) => e.key === "Enter" && go();
  });
  if (S.planId) renderPlan(await api(`/api/plans/${S.planId}`));
  else $("#planEditor").innerHTML = "";
  fx();
}
function stateChip(s) {
  const m = { draft: ["", "Draft"], ready: ["info", "Ready"], active: ["ok", "Active"], completed: ["ok", "Done"], archived: ["", "Archived"] }[s] || ["", s];
  return `<span class="chip ${m[0]}">${m[1]}</span>`;
}

function renderPlan(plan) {
  const d = plan.data, o = d.offer, b = d.budget, base = (d.projection_summary || {}).base || {};
  const steps = ["draft", "ready", "active", "completed"];
  const cur = steps.indexOf(plan.state);
  const next = { draft: ["ready", "Mark as ready", "check"], ready: ["active", "Start campaign", "play"], active: ["completed", "Mark as done", "flag"] }[plan.state];
  const tasks = d.phases.flatMap((p) => p.tasks);
  if (!S.phase || !d.phases.some((p) => p.key === S.phase)) S.phase = (d.phases.find((p) => p.tasks.some((t) => !t.done)) || d.phases[0]).key;
  const rev = plan.pending_revision;
  const ai = (d.authored_by || "").startsWith("agent");
  const contrib = base.incremental_contribution;
  $("#planEditor").innerHTML = `
    ${rev ? `<div class="banner warn" style="margin:0 0 16px">${ic("refresh-ccw")}<div class="b-text"><div class="b-title">Suggested update: ${esc(rev.reason.replace(/^Source change v\d+:\s*/, ""))}</div>
      <div class="b-sub">Your own edits are kept either way.</div>${baHtml(rev.before_after)}</div>
      <div class="row"><button class="btn primary sm" id="revAccept">Apply update</button><button class="btn sm" id="revReject">Keep current</button></div></div>` : ""}
    ${plan.event_status === "cancelled" ? `<div class="banner" style="margin:0 0 16px"><div class="b-icon">${ic("alert-octagon")}</div><div class="b-text"><div class="b-title">This event was cancelled</div><div class="b-sub">Apply the suggested update to stand the campaign down.</div></div></div>` : ""}
    <div class="plan-head glass reveal">
      <div class="plan-head-top"><div><h2>${esc(d.event_title)}</h2>
        <div class="plan-meta"><span class="chip">${ic("calendar", "icon-sm")}${fmtTime(d.event_start)}</span><span class="chip">${ic("map-pin", "icon-sm")}${esc(short(d.venue || "", 30))}</span>
          <span class="chip ${ai ? "violet" : ""}">${ic(ai ? "sparkles" : "file-text", "icon-sm")}${ai ? "Written by AI" : "Template copy"}</span></div></div>
        <div class="row">${next ? `<button class="btn primary" id="pNext">${ic(next[2], "icon-sm")}${next[1]}</button>` : ""}
          <button class="btn" id="pCopy">${ic("copy", "icon-sm")}Copy plan</button>
          <a class="btn" href="/api/plans/${esc(plan.id)}/export" download="campaign-${esc(plan.id)}.md">${ic("download", "icon-sm")}Download</a></div></div>
      <div class="state-steps">${steps.map((s, i) => `<span class="state-step ${i < cur ? "done" : i === cur ? "current" : ""}">${i < cur ? ic("check", "icon-sm") : ""}${{ draft: "Draft", ready: "Ready", active: "Running", completed: "Done" }[s]}</span>`).join(`<span class="muted">›</span>`)}
        <span class="small muted" style="margin-left:6px">Nothing is posted or sent automatically.</span></div>
    </div>
    <div class="kpis">
      <div class="kpi glass lift reveal"><div class="k">Offer</div><div class="v">${money(o.price)}</div><div class="s">save ${money(o.regular_price - o.price)} · ${esc(short(o.name.split(":").pop().trim(), 34))}</div></div>
      <div class="kpi glass lift reveal"><div class="k">You keep per order</div><div class="v">${money(o.contribution_per_order)}</div><div class="s">after food &amp; card fees</div></div>
      <div class="kpi glass lift reveal"><div class="k">Budget</div><div class="v">${money(b.total, 0)}</div><div class="s">of ${money(b.cap_event, 0)} limit · no paid ads</div></div>
      <div class="kpi glass lift reveal"><div class="k">Expected extra contribution</div><div class="v ${contrib < 0 ? "neg" : "pos"}"><span class="count-up" data-money="1" data-to="${contrib || 0}">$0</span></div>
        <div class="s">${base.achievable_incremental_orders ?? 0} extra orders${base.break_even_incremental_orders != null ? ` · break-even at ${base.break_even_incremental_orders}` : ""}</div></div>
    </div>
    <div class="tabs-line" role="tablist">${[["tasks", "Tasks", "list-checks"], ["strategy", "Strategy", "compass"], ["content", "Posts & copy", "pen-line"],
      ["budget", "Budget", "wallet"], ["risks", "What if…", "shield-alert"]].map(([k, l, i]) =>
      `<button role="tab" data-ptab="${k}" aria-selected="${S.planTab === k}">${ic(i, "icon-sm")}${l}</button>`).join("")}</div>
    <div id="planTabBody">${planTab(plan)}</div>`;
  bindPlan(plan, tasks);
  fx($("#planEditor"));
}

function editable(plan, path, label, value, rows = 2) {
  const edited = path in plan.owner_edits;
  return `<div class="copy-field"><label for="f-${esc(path)}">${esc(label)}${edited ? '<span class="edited">your edit</span>' : ""}
      <button type="button" class="btn ghost sm" data-copy="${esc(path)}" style="margin-left:auto">${ic("copy", "icon-sm")}Copy</button></label>
    <textarea id="f-${esc(path)}" data-path="${esc(path)}" rows="${rows}" maxlength="4000">${esc(Array.isArray(value) ? value.join("\n") : value)}</textarea></div>`;
}

function planTab(plan) {
  const d = plan.data, o = d.offer, b = d.budget;
  const ph = d.phases.find((p) => p.key === S.phase) || d.phases[0];
  if (S.planTab === "tasks") return `
    <div class="timeline">${d.phases.map((p, i) => {
      const done = p.tasks.filter((t) => t.done).length;
      return `<button class="phase glass lift glow-border" data-phase="${esc(p.key)}" aria-current="${p.key === S.phase}"><div class="pn">STEP ${i + 1}</div><div class="pt">${esc(p.name)}</div>
        <div class="pd">${fmtDay(p.start)}</div><div class="progress"><span style="width:0" data-w="${p.tasks.length ? done / p.tasks.length * 100 : 0}%"></span></div></button>`;
    }).join("")}</div>

    <div class="block" style="display:flex;gap:12px;align-items:flex-start">${ic("flag", "icon-lg")}<div><div style="font-weight:650">${esc(ph.name)} · ${fmtDay(ph.start)} → ${fmtDay(ph.end)}</div>
      <div class="small muted">${esc(ph.objective)}</div></div></div>
    ${ph.tasks.map((t) => `<label class="task ${t.done ? "done" : ""}"><input type="checkbox" data-task="${esc(t.id)}" ${t.done ? "checked" : ""}>
      <span class="tt">${esc(t.title)}</span><span class="td">${fmtTime(t.due)} · ${esc(t.owner)}</span></label>`).join("")}
    <div class="fineprint">${ic("target", "icon-sm")}Goal for this step: ${esc(ph.success_measure)}</div>`;
  if (S.planTab === "strategy") return `
    <div class="offer-card"><div style="flex:1;min-width:240px"><div class="small muted">${o.theme ? "Event special" : "The offer"}</div><div class="oname">${esc(o.name.split(":").pop().trim())}</div>
      ${o.theme ? `<div class="small" style="margin-top:4px">${esc(o.theme.story)}</div>
        <div class="row" style="margin-top:10px"><span class="chip accent">${esc(o.theme.components)}</span><span class="chip violet">Fan-inspired · unofficial</span>
        ${o.theme.addon ? `<span class="chip warn">Needs your OK: ${esc(o.theme.addon.name)} (~$${o.theme.addon.cost.toFixed(2)}/serving)</span>` : ""}
        ${o.theme.source && o.theme.source.startsWith("ai") ? `<span class="chip">${ic("sparkles", "icon-sm")}idea by AI</span>` : ""}</div>` : ""}
      <div class="small muted" style="margin-top:8px">${esc(o.redemption)} · ${esc(o.quantity_limit)}</div></div><div style="margin-left:auto;text-align:right"><div class="price">${money(o.price)}</div><div class="was">${money(o.regular_price)}</div></div></div>
    <div class="two-col"><div>${editable(plan, "objective", "Goal", d.objective)}${editable(plan, "target_occasion", "Who it’s for", d.target_occasion)}</div>
      <div>${editable(plan, "positioning", "The pitch", d.positioning)}${editable(plan, "how_customers_learn", "How people will find out", d.how_customers_learn, 3)}</div></div>
    <div class="block"><h4>${ic("radio-tower", "icon-sm")}Channels</h4>${d.channels.map((c) => `<div class="channel"><div class="ch-icon">${ic({ "In-store signage": "sign", }[c.channel] || (c.channel.startsWith("Instagram") ? "instagram" : c.channel.startsWith("Email") ? "mail" : "handshake"), "icon-sm")}</div>
      <div><div style="font-weight:600">${esc(c.channel)} ${c.type === "available" ? '<span class="chip ok">ready</span>' : '<span class="chip warn">needs permission</span>'}${c.included === false ? ' <span class="chip">skipped</span>' : ""}</div>
      <div class="small muted">${esc(c.why)}</div></div></div>`).join("")}</div>
    <div class="block"><h4>${ic("lightbulb", "icon-sm")}Why this event</h4><p>${esc(d.rationale.text)}</p>${d.rationale.largest_uncertainty ? `<p class="small muted" style="margin-top:6px">Biggest unknown: ${esc(d.rationale.largest_uncertainty)}</p>` : ""}</div>`;
  if (S.planTab === "content") return `
    ${editable(plan, "copy.instagram_caption", "Instagram caption", d.copy.instagram_caption, 3)}
    ${editable(plan, "copy.story_sequence", "Instagram stories (one per line)", d.copy.story_sequence, 3)}
    <div class="two-col">${editable(plan, "copy.email_subject", "Email subject", d.copy.email_subject, 1)}${editable(plan, "copy.sign_copy", "Window sign", d.copy.sign_copy, 2)}</div>
    ${editable(plan, "copy.email_body", "Email to your list", d.copy.email_body, 4)}
    ${editable(plan, "copy.staff_script", "What staff say", d.copy.staff_script, 2)}
    ${editable(plan, "copy.organizer_outreach", "Message to the organizer (draft — not sent)", d.copy.organizer_outreach, 4)}
    ${editable(plan, "copy.visual_direction", "Look & feel", d.copy.visual_direction, 2)}`;
  if (S.planTab === "budget") return `
    <div class="block"><h4>${ic("wallet", "icon-sm")}Spend</h4>${b.items.map((i) => `<div class="budget-row"><span>${esc(i.item)}</span><span>${money(i.amount)}</span></div>`).join("")}
      <div class="budget-row"><span>Total</span><span>${money(b.total)}</span></div></div>
    <div class="two-col"><div class="kpi glass"><div class="k">This campaign</div><div class="v">${money(b.total, 0)} <span class="muted" style="font-size:14px;font-weight:500">/ ${money(b.cap_event, 0)}</span></div><div class="progress" style="margin-top:8px"><span style="width:${Math.min(100, b.total / b.cap_event * 100)}%"></span></div></div>
      <div class="kpi glass"><div class="k">This week, all campaigns</div><div class="v">${money(b.week_total, 0)} <span class="muted" style="font-size:14px;font-weight:500">/ ${money(b.cap_week, 0)}</span></div><div class="progress" style="margin-top:8px"><span style="width:${Math.min(100, b.week_total / b.cap_week * 100)}%"></span></div></div></div>
    <div class="fineprint">${ic("info", "icon-sm")}Optional extra: ${b.optional_paid_amplification.map((x) => `${esc(x.item.split(" (")[0])} ${money(x.amount, 0)}`).join(", ")} — not included, never bought automatically.</div>`;
  return `
    <div class="block"><h4>${ic("list-checks", "icon-sm")}Before you go ahead</h4>${d.go_no_go.map((g) => `<div class="item">${ic("check", "icon-sm")} ${esc(g)}</div>`).join("")}</div>
    ${editable(plan, "contingencies.cancellation", "If the event is cancelled", d.contingencies.cancellation)}
    ${editable(plan, "contingencies.low_response", "If few people show up", d.contingencies.low_response)}
    ${editable(plan, "contingencies.stockout", "If you run out", d.contingencies.stockout)}
    ${editable(plan, "contingencies.overload", "If it gets too busy", d.contingencies.overload)}
    <div class="block"><h4>${ic("shuffle", "icon-sm")}Other options</h4>${d.alternatives.map((a) => `<div class="item"><div class="item-title">${esc(a.name)}</div><div class="item-sub">${esc(a.summary)}</div></div>`).join("")}</div>
    ${plan.versions.length ? `<div class="block"><h4>${ic("history", "icon-sm")}Earlier versions</h4>${plan.versions.map((v) => `<div class="item">v${v.version} · ${ago(v.archived_at)} — ${esc(v.reason)}</div>`).join("")}</div>` : ""}`;
}

function bindPlan(plan, tasks) {
  const ed = $("#planEditor");
  ed.querySelectorAll("[data-phase]").forEach((b) => b.onclick = () => { S.phase = b.dataset.phase; renderPlan(plan); });
  ed.querySelectorAll("[data-ptab]").forEach((b) => b.onclick = () => { S.planTab = b.dataset.ptab; renderPlan(plan); });
  ed.querySelectorAll("textarea[data-path]").forEach((t) => t.addEventListener("change", async () => {
    const path = t.dataset.path;
    const value = path === "copy.story_sequence" ? t.value.split("\n").filter(Boolean) : t.value;
    try { await api(`/api/plans/${plan.id}`, { method: "PATCH", body: { path, value } }); toast("Saved."); renderPlan(await api(`/api/plans/${plan.id}`)); }
    catch (e) { toast(e.message); }
  }));
  ed.querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => {
    const v = $(`textarea[data-path="${b.dataset.copy}"]`).value;
    try { await navigator.clipboard.writeText(v); toast("Copied."); } catch { toast("Couldn’t access the clipboard."); }
  });
  ed.querySelectorAll("input[data-task]").forEach((c) => c.addEventListener("change", async () => {
    await api(`/api/plans/${plan.id}/tasks/${encodeURIComponent(c.dataset.task)}`, { method: "POST", body: { done: c.checked } });
    const t = tasks.find((x) => x.id === c.dataset.task); if (t) t.done = c.checked;
    renderPlan(plan); loadPlansQuiet().then(() => { const p = S.plans.find((x) => x.id === plan.id); const el = $(`.ccard[data-plan="${plan.id}"]`);
      if (p && el) { el.querySelector(".progress span").style.width = `${p.tasks_done / p.tasks_total * 100}%`; el.querySelector(".small").textContent = `${p.tasks_done} of ${p.tasks_total} tasks done`; } });
  }));
  if ($("#pNext")) $("#pNext").onclick = (e) => busy(e.currentTarget, async () => {
    const to = { draft: "ready", ready: "active", active: "completed" }[plan.state];
    await api(`/api/plans/${plan.id}/state`, { method: "POST", body: { state: to } }); await loadPlans();
  });
  $("#pCopy").onclick = async () => {
    const md = await api(`/api/plans/${plan.id}/export`);
    try { await navigator.clipboard.writeText(md); toast("Whole plan copied."); } catch { toast("Clipboard unavailable — use Download."); }
  };
  if ($("#revAccept")) {
    $("#revAccept").onclick = (e) => busy(e.currentTarget, async () => { await api(`/api/plans/${plan.id}/revision`, { method: "POST", body: { accept: true } }); toast("Update applied — your edits were kept."); await loadPlans(); });
    $("#revReject").onclick = (e) => busy(e.currentTarget, async () => { await api(`/api/plans/${plan.id}/revision`, { method: "POST", body: { accept: false } }); await loadPlans(); });
  }
}

// ---------------------------------------------------------------- results
async function loadResults() {
  S.plans = (await api("/api/plans")).filter((p) => p.state !== "archived");
  const opts = S.plans.map((p) => `<option value="${esc(p.id)}">${esc(short(p.event_title, 44))} · ${fmtDay(p.event_start)}</option>`).join("");
  $("#resPlan").innerHTML = opts || `<option value="">No campaigns yet</option>`;
  $("#outPlan").innerHTML = opts || `<option value="">No campaigns yet</option>`;
  if (S.planId && S.plans.some((p) => p.id === S.planId)) { $("#resPlan").value = S.planId; $("#outPlan").value = S.planId; }
  await loadProjection(); await loadOutcomes(); icons();
}
$("#resPlan").addEventListener("change", (e) => { S.planId = e.target.value; store.set("ll_plan", S.planId); loadProjection(); });

async function loadProjection() {
  const pid = $("#resPlan").value;
  if (!pid) { $("#scen").innerHTML = `<div class="empty" style="grid-column:1/-1">${ic("line-chart", "icon-lg")}<div><b>No campaign yet</b></div><div class="small">Plan a campaign from the Briefing page to see projections.</div></div>`; $("#capacity").innerHTML = ""; $("#projForm").innerHTML = ""; icons(); return; }
  const { inputs } = await api(`/api/projection/${pid}`);
  const sc = (k, d) => { const v = inputs[k] ?? d; return typeof v === "object" ? v : { low: v, base: v, high: v }; };
  const tri = (name, label, v, step = "0.01") => `<div class="tri">${label}<div class="tri-row">${["low", "base", "high"].map((s) =>
    `<label>${{ low: "Low", base: "Expected", high: "High" }[s]}<input type="number" step="${step}" min="0" name="${name}.${s}" value="${v[s]}"></label>`).join("")}</div></div>`;
  const isAtt = inputs.method === "attendance";
  $("#projForm").innerHTML = `<div class="assump">
    <label class="field">Event date<input name="event_date" type="date" value="${esc(inputs.event_date)}" required></label>
    <label class="field">Busy hours (24h, e.g. 18,19)<input name="affected_hours" value="${esc((inputs.affected_hours || []).join(","))}" pattern="^\\s*\\d{1,2}(\\s*,\\s*\\d{1,2})*\\s*$" required></label>
    <label class="field">Campaign spend ($)<input name="campaign_spend" type="number" min="0" max="75" step="0.01" value="${inputs.campaign_spend ?? 0}"></label>
    <label class="field">Extra staff hours<input name="extra_labor_hours" type="number" min="0" step="0.5" value="${inputs.extra_labor_hours ?? 0}"></label>
    <input type="hidden" name="method" value="${esc(inputs.method)}">
    ${isAtt ? tri("reachable_share", "Share of attendees who pass by", sc("reachable_share", { low: 0.005, base: 0.01, high: 0.02 }), "0.001") + tri("conversion", "Share of those who buy", sc("conversion", { low: 0.1, base: 0.2, high: 0.3 }))
            : tri("uplift_pct", "Extra orders vs. a normal day (0.10 = +10%)", sc("uplift_pct", { low: 0.05, base: 0.1, high: 0.2 }))}
    ${tri("incremental_share", "Share that are truly new customers", sc("incremental_share", { low: 0.6, base: 0.75, high: 0.85 }))}
    ${tri("offer_take_rate", "Share who pick the offer", sc("offer_take_rate", 0.6))}
    ${tri("regular_offer_redemptions", "Regulars who’d use the discount anyway", sc("regular_offer_redemptions", 3), "1")}
    <div style="grid-column:1/-1" class="row"><button class="btn primary">${ic("calculator", "icon-sm")}Recalculate</button><span class="form-msg" id="projMsg" role="status"></span></div></div>`;
  $("#projForm").dataset.base = JSON.stringify(inputs);
  $("#projForm").onsubmit = (e) => { e.preventDefault(); runProjection(true); };
  await runProjection(false);
}
async function runProjection(save) {
  const form = $("#projForm");
  if (!form.reportValidity()) return;
  const base = JSON.parse(form.dataset.base);
  const body = { ...base, plan_id: $("#resPlan").value, save };
  for (const [k, v] of new FormData(form).entries()) {
    if (k.includes(".")) { const [a, c] = k.split("."); body[a] = { ...(typeof body[a] === "object" ? body[a] : {}), [c]: Number(v) }; }
    else if (k === "affected_hours") body[k] = v.split(",").map((x) => Number(x.trim())).filter((x) => !Number.isNaN(x));
    else if (["campaign_spend", "extra_labor_hours"].includes(k)) body[k] = Number(v);
    else body[k] = v;
  }
  const plan = S.plans.find((p) => p.id === body.plan_id);
  if (plan) body.event_id = plan.event_id;
  try {
    const r = await api("/api/projection", { method: "POST", body });
    if (save) { $("#projMsg").textContent = "Updated."; $("#projMsg").className = "form-msg ok"; }
    S.projectionInputs = body; S.projectionResult = r;
    $("#whatIfOut").innerHTML = "";
    renderProjection(r);
  } catch (e) { $("#projMsg").textContent = e.message; $("#projMsg").className = "form-msg err"; }
}
$("#whatIfForm").onsubmit = async (event) => {
  event.preventDefault();
  if (!S.projectionInputs || !S.projectionResult) return;
  const inputs = {...S.projectionInputs, save: false, capacity_orders_per_hour: Number($("#whatIfCapacity").value),
    extra_labor_hours: Number(S.projectionInputs.extra_labor_hours || 0) + Number($("#whatIfLabor").value)};
  await busy($("#whatIfForm button"), async () => {
    const result = await api("/api/projection", {method:"POST", body:inputs});
    $("#whatIfOut").innerHTML = `<div class="whatif-grid">${["base", "high"].map((key) => {
      const before = S.projectionResult.scenarios[key], after = result.scenarios[key], delta = after.incremental_contribution - before.incremental_contribution;
      return `<div><span class="small muted">${key === "base" ? "Expected" : "Busy"}</span><strong>${money(after.incremental_contribution,0)}</strong><span class="small ${delta >= 0 ? "positive" : "negative"}">${delta >= 0 ? "+" : "−"}${money(Math.abs(delta),0)} vs. current setup</span><p class="small muted">${after.achievable_incremental_orders - before.achievable_incremental_orders} additional orders served · ${money(after.labor - before.labor,0)} extra labor</p></div>`;
    }).join("")}</div><p class="small muted">Planning comparison only. A faster setup must actually deliver the capacity you entered.</p>`;
  }, "Calculating…");
};
function renderProjection(r) {
  const tile = (k, name) => {
    const s = r.scenarios[k], v = s.incremental_contribution;
    return `<div class="scen-tile glass lift reveal ${k === "base" ? "base" : ""}"><div class="sn"><span>${name}</span>${k === "base" ? '<span class="chip accent">base case</span>' : ""}</div>
      <div class="sv ${v < 0 ? "neg" : "pos"}"><span class="count-up" data-money="1" data-to="${v}">$0</span></div>
      <div class="ss">${s.achievable_incremental_orders} extra orders · ${money(s.net_sales, 0)} in sales</div>
      ${s.unmet_potential_demand > 0 ? `<div class="ss muted">Capacity limited · ${Math.round(s.unmet_potential_demand)} potential orders unserved. More demand needs more capacity to increase earnings.</div>` : ''}
      <div class="ss muted" style="margin-top:4px">${s.break_even_incremental_orders == null ? "Can’t break even with these numbers" : `Break-even: ${s.break_even_incremental_orders} orders`}</div></div>`;
  };
  const att = r.method === "attendance" ? JSON.parse($("#projForm").dataset.base || "{}").attendance : null;
  const basis = r.method === "attendance"
    ? `Based on ~${fmtK(att?.base)} expected attendees (${att?.provenance || "estimate"}) × the share who pass your café and stop in. Capped by your ${r.capacity_orders_per_hour} orders/hour.`
    : `Based on a normal day’s orders in those hours × your assumed boost (expected: +${Math.round((JSON.parse($("#projForm").dataset.base || "{}").uplift_pct?.base ?? 0.1) * 100)}%). Attendance isn’t published, so this starts cautious — raise it under “Adjust assumptions” if you expect more.`;
  const b = r.scenarios.base, h = r.scenarios.high;
  const capped = b.unmet_potential_demand > 0 && Math.round(b.incremental_contribution) === Math.round(h.incremental_contribution);
  const capNote = capped ? `<div class="banner warn" style="grid-column:1/-1;margin-top:0"><div class="b-icon">${ic("gauge", "")}</div><div class="b-text">
      <div class="b-title">Expected and busy are the same because the café is full</div>
      <div class="b-sub">Demand (~${Math.round(b.incremental_demand_orders)} extra orders) is above what you can serve (${b.achievable_incremental_orders}). Your ${r.capacity_orders_per_hour} orders/hour limit — not the crowd — sets the number. Try “What if you could serve more?” below.</div></div></div>` : "";
  $("#scen").innerHTML = tile("low", "If it’s slow") + tile("base", "Expected") + tile("high", "If it’s busy") + capNote +
    `<div class="fineprint" style="grid-column:1/-1;margin-top:0">${ic("info", "icon-sm")}<span>${esc(basis)}</span></div>`;
  const hours = r.scenarios.base.hours;
  const max = Math.max(r.capacity_orders_per_hour, ...hours.map((h) => h.total_orders));
  const hr = (h) => { const x = h % 12 || 12; return `${x}${h < 12 ? "am" : "pm"}`; };
  $("#capacity").innerHTML = hours.length ? `<div class="bars">${hours.map((h) => `<div class="hbar"><span><b>${hr(h.hour)}</b></span><span class="track">
      <span class="b" style="width:0" data-w="${(h.baseline_orders + h.other_event_orders) / max * 100}%"></span>
      <span class="i" style="left:${(h.baseline_orders + h.other_event_orders) / max * 100}%;width:0" data-w="${h.incremental_orders / max * 100}%"></span>
      <span class="cap" style="left:${h.capacity / max * 100}%"></span></span>
      <span>${Math.round(h.total_orders)} / ${h.capacity} ${h.at_capacity ? '<span class="chip bad">full</span>' : "orders"}</span></div>`).join("")}</div>
    <div class="legend-row"><span><span class="sw" style="background:rgba(255,255,255,.25)"></span>Normal day</span><span><span class="sw" style="background:var(--grad)"></span>Extra from campaign</span>
      <span><span class="sw" style="background:#fff;width:3px"></span>Your limit</span><span>Seats used on average: ${Math.max(...hours.map((h) => h.occupied_seats_avg))} of ${r.seats}</span>
      ${r.scenarios.base.unmet_potential_demand > 0 ? `<span class="chip warn">${Math.round(r.scenarios.base.unmet_potential_demand)} more orders than you can serve</span>` : ""}</div>`
    : `<p class="muted">${esc(r.warnings.join(" "))}</p>`;
  fx($("#view-results"));
}
async function loadOutcomes() {
  const rows = await api("/api/outcomes");
  const p = S.boot.profile;
  $("#outcomes").innerHTML = rows.length ? `<h4 class="muted small" style="margin:18px 0 4px;text-transform:uppercase;letter-spacing:.06em">Recorded</h4>` + rows.map((o) => {
    const plan = S.plans.find((x) => x.id === o.plan_id);
    const est = o.sales * (1 - p.variable_cost_rate - p.payment_fee_rate) - o.actual_costs;
    return `<div class="outcome"><div class="row" style="justify-content:space-between"><b>${esc(plan ? short(plan.event_title, 40) : "Campaign")}</b>
        <button class="btn ghost sm" data-del="${esc(o.id)}" title="Delete">${ic("trash-2", "icon-sm")}</button></div>
      <div class="row small" style="margin-top:4px"><span class="chip">${o.redemptions} redemptions</span><span class="chip">${money(o.sales, 0)} sales</span>
        <span class="chip ${est < 0 ? "bad" : "ok"}">≈ ${money(est, 0)} kept</span></div>
      ${o.notes ? `<div class="small" style="margin-top:6px">${esc(o.notes)}</div>` : ""}</div>`;
  }).join("") : "";
  $("#outcomes").querySelectorAll("[data-del]").forEach((b) => b.onclick = () => busy(b, async () => {
    if (!confirm("Delete this outcome?")) return;
    await api(`/api/outcomes/${b.dataset.del}`, { method: "DELETE" }); loadOutcomes();
  }));
  icons();
}
$("#outcomeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, msg = $("#outMsg");
  if (!f.reportValidity()) return;
  try {
    await api("/api/outcomes", { method: "POST", body: Object.fromEntries(new FormData(f).entries()) });
    msg.textContent = "Saved."; msg.className = "form-msg ok"; f.reset(); loadOutcomes(); loadPlansQuiet();
  } catch (err) { msg.textContent = err.message; msg.className = "form-msg err"; }
});

// ---------------------------------------------------------------- secondary drawers
const SRC = { ticketmaster: "Ticketmaster", nycparks: "NYC Parks", gothamist: "Gothamist", gdelt: "GDELT news", nyc_permits: "NYC street permits",
  mta: "MTA subway alerts", bluesky: "Bluesky", x: "X (Twitter)" };
$("#btnSources").onclick = async () => {
  const s = await api("/api/sources");
  const dot = (st) => (["healthy", "connected", "no_relevant_results"].includes(st) ? "ok" : st === "unavailable" ? "bad" : "warn");
  const txt = { healthy: "Working", connected: "Connected", no_relevant_results: "Working · nothing new", setup_required: "Needs setup", rate_limited: "Busy, retrying",
    access_limited: "Limited", unavailable: "Down", not_checked: "Not checked yet", stale: "Out of date" };
  const groups = { events: "Events", news: "News & official alerts", social: "Social buzz" };
  openDrawer({ title: "Data sources", body: Object.entries(groups).map(([cat, name]) => `<div class="block"><h4>${name}</h4>
      ${s.statuses.filter((x) => x.category === cat).map((x) => `<div class="status-row"><span class="sdot ${dot(x.status)}"></span>
        <div style="flex:1;min-width:0"><div style="font-weight:600">${esc(SRC[x.provider] || x.provider)}</div><div class="small muted">${esc(short(x.detail, 140))}</div>
        ${x.next_step && dot(x.status) !== "ok" ? `<div class="small" style="margin-top:3px">${ic("arrow-right", "icon-sm")} ${esc(x.next_step)}</div>` : ""}</div>
        <div class="small" style="text-align:right;white-space:nowrap">${esc(txt[x.status] || x.status)}<div class="muted">${ago(x.last_success)}</div></div></div>`).join("")}</div>`).join("") +
    `<div class="block"><h4>Background updates</h4><div class="status-row"><span class="sdot ${s.worker.worker_alive ? "ok" : "warn"}"></span>
      <div style="flex:1">${s.worker.worker_alive ? "Running — updates even when this page is closed" : "Not running — start <code>worker.py</code>"}</div><div class="small muted">${ago(s.worker.worker_heartbeat)}</div></div></div>
     <div class="block"><h4>AI agent</h4><div class="status-row"><span class="sdot ${s.agent.configured ? "ok" : "warn"}"></span><div style="flex:1">${esc(s.agent.provider)}</div>
       <div class="small muted">limit $${s.agent.daily_cap_usd}/day</div></div></div>` });
};

$("#btnSettings").onclick = async () => {
  const p = await api("/api/business");
  openDrawer({ title: "Café profile", head: `<div class="sub"><span class="chip warn">${ic("info", "icon-sm")}Fictional café for this demo</span></div>`, body: `
    <div class="block"><h4>${ic("store", "icon-sm")}${esc(p.name)}</h4><p>${esc(p.description)}</p>
      <p class="small muted" style="margin-top:8px">${esc(p.location.label)} — a scenario location, not a real storefront.</p></div>
    <form id="profForm" class="block"><h4>${ic("settings-2", "icon-sm")}Settings</h4><div class="form-grid">
      <label class="field">Search radius (miles)<input name="discovery.radius_miles" type="number" min="0.5" max="5" step="0.5" value="${p.discovery.radius_miles}" required></label>
      <label class="field">Days ahead<input name="discovery.days_ahead" type="number" min="1" max="14" value="${p.discovery.days_ahead}" required></label>
      <label class="field">Opens (hour)<input name="hours.open" type="number" min="0" max="23" value="${p.hours.open}" required></label>
      <label class="field">Closes (hour)<input name="hours.close" type="number" min="1" max="24" value="${p.hours.close}" required></label>
      <label class="field">Budget per campaign ($)<input name="budget.per_event" type="number" min="0" value="${p.budget.per_event}" required></label>
      <label class="field">Budget per week ($)<input name="budget.per_week" type="number" min="0" value="${p.budget.per_week}" required></label>
      <label class="field">Max orders per hour<input name="capacity_orders_per_hour" type="number" min="1" value="${p.capacity_orders_per_hour}" required></label>
      <label class="field">Staff cost ($/hour)<input name="labor_cost_per_hour" type="number" min="0" step="0.5" value="${p.labor_cost_per_hour}" required></label>
    </div><div class="row" style="margin-top:14px"><button class="btn primary">${ic("save", "icon-sm")}Save</button><span class="form-msg" id="profMsg" role="status"></span></div></form>
    <div class="block"><h4>${ic("utensils", "icon-sm")}Menu</h4>${p.menu.map((m) => `<div class="budget-row"><span>${esc(m.name)}</span><span>${money(m.price)} <span class="muted small">· costs ${money(m.unit_cost)}</span></span></div>`).join("")}</div>
    <div class="block"><h4>${ic("bar-chart-3", "icon-sm")}A normal day</h4><dl class="kv">
      <dt>Weekday</dt><dd>${p.typical_weekday.orders} orders · ${money(p.typical_weekday.sales, 0)}</dd>
      <dt>Weekend</dt><dd>${p.typical_weekend.orders} orders · ${money(p.typical_weekend.sales, 0)}</dd>
      <dt>Seats</dt><dd>${p.seating.seats}</dd><dt>Audience</dt><dd>${p.channels.instagram_followers} Instagram · ${p.channels.email_optins} email</dd></dl>
      <div class="fineprint">${ic("info", "icon-sm")}Sales history is simulated for this demo; events, news and posts are real.</div></div>`,
    onRender: () => {
      $("#profForm").onsubmit = async (e) => {
        e.preventDefault(); if (!e.target.reportValidity()) return;
        const body = {};
        for (const [k, v] of new FormData(e.target).entries()) { const [a, c] = k.split("."); if (c) (body[a] = body[a] || {})[c] = Number(v); else body[a] = Number(v); }
        try { await api("/api/business", { method: "PATCH", body }); $("#profMsg").textContent = "Saved — rankings updated."; $("#profMsg").className = "form-msg ok";
          S.boot = await api("/api/bootstrap"); await loadOpportunities(); }
        catch (err) { $("#profMsg").textContent = err.message; $("#profMsg").className = "form-msg err"; }
      };
    } });
};

$("#btnBell").onclick = async () => {
  const n = await api("/api/notifications");
  const icn = { cancellation: "alert-octagon", rescheduling: "alarm-clock", relocation: "map-pin", top_recommendation: "trophy", evidence_change: "newspaper" };
  openDrawer({ title: "Alerts", body: n.length ? `<div class="block">${n.map((x) => `<div class="item"><div class="row" style="align-items:flex-start;flex-wrap:nowrap">
        <div class="type-icon ${x.kind === "cancellation" ? "t-cancellation" : "t-schedule_change"}" style="width:32px;height:32px">${ic(icn[x.kind] || "bell", "icon-sm")}</div>
        <div style="flex:1;min-width:0"><div class="item-title">${x.read ? "" : '<span class="new-dot"></span> '}${esc(x.message)}</div>
        <div class="item-sub">${ago(x.created_at)}${(x.affected_plans || []).length ? ` · affects ${x.affected_plans.length} campaign(s)` : ""}</div>
        ${x.event_id ? `<button class="btn sm" style="margin-top:8px" data-ev="${esc(x.event_id)}">Open</button>` : ""}</div></div></div>`).join("")}</div>`
    : `<div class="empty">${ic("bell-off", "icon-lg")}<div><b>No alerts</b></div><div class="small">Follow an event and we’ll tell you if it’s cancelled or moved.</div></div>`,
    onRender: () => $("#drawerBody").querySelectorAll("[data-ev]").forEach((b) => b.onclick = () => openEvent(b.dataset.ev)) });
  await api("/api/notifications/read", { method: "POST" }); setBell(0);
};

boot().catch((e) => { $("#devCards").innerHTML = `<div class="empty">Couldn’t load LocalLift: ${esc(e.message)}. Is the server running?</div>`; });
