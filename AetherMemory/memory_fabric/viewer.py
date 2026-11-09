"""Viewer box: a read-only picture of one agent's mind.

Inputs:  an open MemoryEngine (open it with read_only=True), and choices:
         which future dates to show, whether to include proprietary
         memories and full bodies, and how many notes at most.
Outputs: snapshot(): a JSON-safe dict (the data);
         render_html(snapshot): one self-contained HTML page (no network,
         no external files) showing the fabric as a graph you can explore,
         a time slider (today, +1 week, +1 month, +3 months, +1 year),
         the memory table, the staff record and the dials.

The two outputs are separate on purpose: a different front end (the Mission
Control memory viewer, an Obsidian-style app) can take snapshot() as its
input and ignore render_html entirely.

Strength at future dates comes from the engine's own temporary views with a
moved clock, never from a second copy of the decay formula, so the picture
always matches what recall would do on that date. The viewer never writes:
open the cartridge read-only and the file is byte-for-byte unchanged.

Safety: every piece of text reaches the page as data and is inserted with
textContent, never as HTML. An avatar is shown only as an <img> data URI,
where an SVG can neither run code nor fetch anything.
"""
from __future__ import annotations

import base64
import html
import json
from typing import Optional, Sequence

from .clock import FixedClock, add_days

DEFAULT_HORIZONS = (0, 7, 30, 90, 365)
HORIZON_LABELS = {0: "Today", 7: "+1 week", 30: "+1 month", 90: "+3 months", 365: "+1 year"}
DEFAULT_MAX_NOTES = 2000


def _label(days: int) -> str:
    return HORIZON_LABELS.get(days, f"+{days} days")


def snapshot(engine, *, horizons: Sequence[int] = DEFAULT_HORIZONS,
             include_proprietary: bool = True, include_bodies: bool = True,
             max_notes: int = DEFAULT_MAX_NOTES) -> dict:
    cart = engine.cart
    conn = cart.conn
    archive = cart.archive_info()
    if archive:
        horizons = (0,)          # a frozen mind has no future to slide into
    horizons = tuple(sorted({int(h) for h in horizons} | {0}))
    original_clock = cart.clock
    now = original_clock.now()

    note_strength: dict = {}
    link_strength: dict = {}
    try:
        for index, days in enumerate(horizons):
            cart.clock = FixedClock(add_days(now, days)) if days else original_clock
            cart.sync_clock()
            for row in conn.execute("SELECT note_id, strength FROM temp.note_state"):
                note_strength.setdefault(row["note_id"], [0.0] * len(horizons))[index] = \
                    round(row["strength"], 4)
            for row in conn.execute("SELECT link_id, strength FROM temp.link_state"):
                link_strength.setdefault(row["link_id"], [0.0] * len(horizons))[index] = \
                    round(row["strength"], 4)
    finally:
        cart.clock = original_clock
        cart.sync_clock()

    tags: dict = {}
    for row in conn.execute("SELECT note_id, tag FROM tags ORDER BY tag"):
        tags.setdefault(row["note_id"], []).append(row["tag"])

    rows = conn.execute(
        "SELECT s.*, n.created_at, n.written_by, n.source_ref FROM temp.note_state s "
        "JOIN main.notes n ON n.note_id = s.note_id ORDER BY s.strength DESC, s.note_id").fetchall()
    total_notes = len(rows)
    hidden_proprietary = 0
    notes = []
    for row in rows:
        if row["proprietary"] and not include_proprietary:
            hidden_proprietary += 1
            continue
        if len(notes) >= max_notes:
            continue
        notes.append(dict(
            id=row["note_id"], title=row["title"],
            body=row["body"] if include_bodies else "",
            profile=row["profile"], category=row["category"], stratum=row["stratum"],
            importance=round(row["importance"], 4), proprietary=bool(row["proprietary"]),
            pulls=row["pull_count"], switch_at=row["switch_at"],
            refreshed_at=row["refreshed_at"], created_at=row["created_at"],
            written_by=row["written_by"], source=row["source_ref"],
            tags=tags.get(row["note_id"], []), strength=note_strength[row["note_id"]]))
    shown = {n["id"] for n in notes}
    links = [dict(id=row["link_id"], a=row["a"], b=row["b"], relation=row["relation"],
                  reinforced=row["reinforce_count"], strength=link_strength[row["link_id"]])
             for row in conn.execute("SELECT * FROM temp.link_state ORDER BY link_id")
             if row["a"] in shown and row["b"] in shown]

    card = engine.card()
    avatar = card.pop("avatar_svg", None)
    report = engine.open_report
    return dict(
        format="aether-memory-snapshot/1",
        generated_at=now,
        agent=dict(id=card["agent_id"], name=card["name"], scan_line=card["scan_line"],
                   avatar_description=card.get("avatar_description")),
        avatar_svg=avatar,
        seal=dict(sealed=report.sealed, tampered=report.tampered, status=report.status,
                  warning=report.warning, read_only=bool(getattr(cart, "read_only", False))),
        archive=archive,
        horizons=list(horizons),
        horizon_labels=([f"{archive['frozen_at'][:10]} (frozen)"] if archive
                        else [_label(h) for h in horizons]),
        settings=cart.settings(),
        profiles=[dict(p) for p in cart.profiles()],
        setting_changes=[dict(r) for r in conn.execute(
            "SELECT * FROM config_changes ORDER BY change_id DESC LIMIT 50")],
        card=card,
        reviews=engine.reviews(),
        notes=notes, links=links,
        counts=dict(total_notes=total_notes, shown_notes=len(notes),
                    hidden_proprietary=hidden_proprietary,
                    truncated=max(0, total_notes - hidden_proprietary - len(notes))),
    )


def _avatar_uri(svg: Optional[str]) -> Optional[str]:
    if not svg:
        return None
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _safe_json(data) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026") \
               .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render_html(snap: dict) -> str:
    data = dict(snap)
    data["avatar_uri"] = _avatar_uri(data.pop("avatar_svg", None))
    title = html.escape(f"{snap['agent']['name']} · Memory Fabric")
    return _TEMPLATE.replace("__TITLE__", title).replace("__DATA__", _safe_json(data))


def write_html(engine, path: str, **options) -> dict:
    """Snapshot the engine and write the page. Returns the snapshot's counts."""
    snap = snapshot(engine, **options)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_html(snap))
    return snap["counts"]


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#f6f5f2;--panel:#ffffff;--ink:#1d1f24;--muted:#6a6f7a;--line:#e3e1dc;--soft:#efede8;
  --accent:#2f6f73;--warn:#b5462f;--ok:#2f7a4a;
  --c-Alert:#d0573c;--c-Information:#3b78c4;--c-Revelation:#8a5cc7;--c-Reflection:#2a9d8f;
  --c-Task:#d39b2a;--c-Thread:#7d8590;
  --shadow:0 1px 2px rgba(0,0,0,.05),0 6px 24px rgba(0,0,0,.05);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#121417;--panel:#1a1d22;--ink:#e8e6e1;--muted:#9aa0aa;--line:#2a2e35;--soft:#20242a;
  --accent:#6cc3c0;--warn:#ef8a6f;--ok:#7fcf9a;
  --c-Alert:#ef7a5d;--c-Information:#6aa5ec;--c-Revelation:#b48cf0;--c-Reflection:#4cc4b4;
  --c-Task:#eabf5a;--c-Thread:#9aa3ad;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 24px rgba(0,0,0,.25);
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:20px 16px 48px}
header{display:flex;gap:16px;align-items:center}
header > div:last-child{flex:1 1 auto;min-width:0}
.avatar{width:72px;height:72px;border-radius:18px;background:var(--soft);display:grid;
  place-items:center;overflow:hidden;box-shadow:var(--shadow);flex:none;font-size:28px;color:var(--muted)}
.avatar img{width:100%;height:100%;object-fit:contain}
h1{font-size:22px;margin:0;letter-spacing:-.01em}
.scan{color:var(--muted);margin-top:2px}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.badge{font-size:12px;padding:2px 8px;border-radius:99px;background:var(--soft);color:var(--muted)}
.badge.ok{color:var(--ok)}.badge.warn{color:var(--warn);font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:18px 0}
.tile{background:var(--panel);border-radius:14px;padding:12px 14px;box-shadow:var(--shadow)}
.tile b{display:block;font-size:22px;letter-spacing:-.02em}
.tile span{color:var(--muted);font-size:12px}
.main{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:14px}
@media (max-width:900px){.main{grid-template-columns:1fr}}
.card{background:var(--panel);border-radius:16px;box-shadow:var(--shadow);overflow:hidden}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:10px 12px;
  border-bottom:1px solid var(--line)}
input[type=search]{flex:1 1 180px;min-width:0;padding:7px 10px;border-radius:10px;
  border:1px solid var(--line);background:var(--bg);color:var(--ink);font:inherit}
.chip{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:99px;
  padding:4px 10px;font:inherit;font-size:12px;cursor:pointer;display:inline-flex;gap:6px;align-items:center}
.chip[aria-pressed=false]{opacity:.45}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
label.toggle{font-size:12px;color:var(--muted);display:inline-flex;gap:6px;align-items:center;cursor:pointer}
.time{display:flex;gap:10px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line)}
.time input{flex:1}
.time output{min-width:84px;font-weight:600;color:var(--accent)}
#graph{display:block;width:100%;height:min(560px,65vh);cursor:grab;touch-action:none}
#graph.dragging{cursor:grabbing}
#graph circle:focus{outline:none}#graph circle:focus-visible{stroke:var(--accent);stroke-width:3}
input[type=range]{accent-color:var(--accent)}
.legend{padding:8px 12px;font-size:12px;color:var(--muted);border-top:1px solid var(--line)}
.side{padding:14px 16px;max-height:680px;overflow:auto;overflow-wrap:anywhere}
.side h2{font-size:16px;margin:0 0 4px}
.side .meta{color:var(--muted);font-size:12px}
.side .body{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.55;
  background:var(--soft);padding:10px 12px;border-radius:10px;margin:10px 0;max-height:340px;overflow:auto}
.bar{height:8px;border-radius:99px;background:var(--soft);overflow:hidden;margin:4px 0 10px}
.bar i{display:block;height:100%;background:var(--accent)}
.spark{display:flex;gap:4px;align-items:flex-end;height:44px;margin:6px 0 2px}
.spark i{flex:1;background:var(--accent);border-radius:3px 3px 0 0;opacity:.85}
.spark-l{display:flex;gap:4px;font-size:10px;color:var(--muted)}.spark-l span{flex:1;text-align:center}
.tags{display:flex;gap:4px;flex-wrap:wrap;margin:6px 0}
.tag{font-size:11px;padding:1px 7px;border-radius:99px;background:var(--soft);color:var(--muted);cursor:pointer}
.nbr{display:block;width:100%;text-align:left;border:0;background:transparent;color:var(--ink);
  padding:4px 0;font:inherit;cursor:pointer}
.nbr:hover{color:var(--accent)}
.tabs{display:flex;gap:4px;margin:22px 0 10px;flex-wrap:wrap}
.tabs button{border:0;background:transparent;color:var(--muted);font:inherit;font-weight:600;
  padding:6px 12px;border-radius:10px;cursor:pointer}
.tabs button[aria-selected=true]{background:var(--panel);color:var(--ink);box-shadow:var(--shadow)}
.tabpanel{display:none}.tabpanel.on{display:block}
.tablewrap{overflow:auto;max-height:560px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{position:sticky;top:0;background:var(--panel);cursor:pointer;user-select:none;font-size:12px;color:var(--muted)}
tbody tr{cursor:pointer}tbody tr:hover{background:var(--soft)}
td.num{font-variant-numeric:tabular-nums;text-align:right}
.empty{color:var(--muted);padding:16px}
.note-small{color:var(--muted);font-size:12px;margin-top:6px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.pad{padding:14px 16px}
.pad h3{margin:0 0 8px;font-size:14px}
.level{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line)}
.review{padding:8px 0;border-bottom:1px solid var(--line)}
.review .who{font-size:12px;color:var(--muted)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="avatar" id="avatar" role="img"></div>
    <div>
      <h1 id="name"></h1>
      <div class="scan" id="scan"></div>
      <div class="badges" id="badges"></div>
    </div>
  </header>
  <div class="tiles" id="tiles"></div>

  <div class="main">
    <div class="card">
      <div class="controls">
        <input type="search" id="q" placeholder="Find by title or tag…" aria-label="Find memories">
        <span id="chips"></span>
        <label class="toggle"><input type="checkbox" id="buried"> show buried</label>
      </div>
      <div class="time">
        <span class="meta">Time</span>
        <input type="range" id="t" min="0" step="1" value="0" aria-label="Look ahead in time">
        <output id="tl">Today</output>
      </div>
      <svg id="graph" role="img" aria-label="Memory fabric graph"></svg>
      <div class="legend">Size = importance · brightness = strength on the chosen date · ring = vital (seen through the fog) · dashed = buried. Drag to pan, scroll to zoom, double-click to fit, click a memory.</div>
    </div>
    <div class="card side" id="side">
      <div class="empty">Click a memory in the fabric or the table to see it here.</div>
    </div>
  </div>

  <div class="tabs" role="tablist">
    <button role="tab" aria-selected="true" data-tab="mem">Memories</button>
    <button role="tab" aria-selected="false" data-tab="staff">Staff record</button>
    <button role="tab" aria-selected="false" data-tab="dials">Dials</button>
  </div>
  <div class="tabpanel on card" id="tab-mem">
    <p class="muted pad">Recorded is when the memory was first written, shown in your local timezone. Current until is when it starts fading faster unless the agent uses it again; it is not a deletion date.</p>
    <div class="tablewrap"><table id="tbl"></table></div></div>
  <div class="tabpanel" id="tab-staff"><div class="grid2">
    <div class="card pad"><h3>Levels (earned, never go down)</h3><div id="levels"></div></div>
    <div class="card pad"><h3>Reviews (written by others, read-only)</h3><div id="reviews"></div></div>
  </div></div>
  <div class="tabpanel" id="tab-dials"><div class="grid2">
    <div class="card pad"><h3>Settings</h3><div class="tablewrap"><table id="settings"></table></div></div>
    <div class="card pad"><h3>Decay profiles</h3><div class="tablewrap"><table id="profiles"></table></div></div>
    <div class="card pad"><h3>Change log</h3><div class="tablewrap"><table id="changes"></table></div></div>
  </div></div>
  <div class="note-small" id="foot"></div>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const $ = (id) => document.getElementById(id);
const el = (tag, attrs, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") n.className = v; else if (k === "style") n.style.cssText = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) n.append(k.nodeType ? k : document.createTextNode(String(k)));
  return n;
};
const SVGNS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs) => { const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); return n; };
const css = getComputedStyle(document.documentElement);
const color = (cat) => css.getPropertyValue("--c-" + cat).trim() || "#888";
const floor = D.settings.floor ?? 0.1, fogMin = D.settings.fog_importance_min ?? 0.9;
const day = (s) => (s || "").slice(0, 10);
const recordedDay = (s) => {
  if (!s) return "";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
let T = 0;                       // index into horizons
let selected = null;

// ---- header -------------------------------------------------------------
$("name").textContent = D.agent.name;
$("scan").textContent = D.archive ? D.archive.note : (D.agent.scan_line || "No scan line yet.");
if (D.archive) { document.querySelector(".time").style.display = "none"; }
if (D.avatar_uri) {
  const img = el("img", {alt: D.agent.avatar_description || "avatar"}); img.src = D.avatar_uri;
  $("avatar").append(img);
} else { $("avatar").textContent = (D.agent.name || "?").slice(0, 1).toUpperCase(); }
const badges = $("badges");
if (D.archive) badges.append(el("span", {class: "badge warn"},
  "Archive · frozen " + day(D.archive.frozen_at) + " · read-only"));
else badges.append(el("span", {class: "badge"}, "as of " + day(D.generated_at)));
if (D.seal.sealed) badges.append(el("span", {class: "badge ok"}, "✓ seal intact"));
else if (D.seal.status === "tampered" || D.seal.status === "unsealed")
  badges.append(el("span", {class: "badge warn"}, "⚠ " + D.seal.warning));
else if (D.seal.warning) badges.append(el("span", {class: "badge"}, D.seal.warning));
if (D.seal.read_only) badges.append(el("span", {class: "badge"}, "read-only view"));
if (D.counts.hidden_proprietary) badges.append(el("span", {class: "badge"}, D.counts.hidden_proprietary + " proprietary hidden"));

// ---- data indexes --------------------------------------------------------
const notes = D.notes, byId = new Map(notes.map((n) => [n.id, n]));
const adj = new Map(notes.map((n) => [n.id, []]));
for (const l of D.links) { adj.get(l.a)?.push(l); adj.get(l.b)?.push(l); }
const cats = [...new Set(notes.map((n) => n.category))].sort();
const catOn = new Set(cats);
const isBuried = (n, t = T) => n.strength[t] <= floor + 0.005;
const isVital = (n) => n.importance >= fogMin;

function tiles() {
  const box = $("tiles"); box.replaceChildren();
  const thr = D.settings.surface_threshold ?? 0.3;
  const live = notes.filter((n) => n.strength[T] >= thr).length;
  const alerts = notes.filter((n) => n.category === "Alert" && n.strength[T] >= thr).length;
  const buried = notes.filter((n) => isBuried(n)).length;
  const top = (D.card.specialties || [])[0];
  const items = [
    [D.counts.total_notes, "memories (never deleted)"],
    [live, "above the surface on " + D.horizon_labels[T].toLowerCase()],
    [alerts, "live alerts"], [buried, "buried"], [D.links.length, "links"],
    [top ? "L" + top.level : "—", top ? "top level · " + top.specialty : "no verified work yet"],
  ];
  for (const [v, s] of items) box.append(el("div", {class: "tile"}, el("b", {}, v), el("span", {}, s)));
}

// ---- controls -------------------------------------------------------------
const chips = $("chips");
for (const c of cats) {
  const b = el("button", {class: "chip", "aria-pressed": "true", type: "button"},
    el("i", {class: "dot", style: "background:" + color(c)}), c);
  b.addEventListener("click", () => { catOn.has(c) ? catOn.delete(c) : catOn.add(c);
    b.setAttribute("aria-pressed", catOn.has(c)); draw(); table(); });
  chips.append(b);
}
const slider = $("t"); slider.max = D.horizons.length - 1;
slider.addEventListener("input", () => { T = +slider.value; $("tl").textContent = D.horizon_labels[T];
  draw(); tiles(); table(); if (selected) show(selected); });
$("q").addEventListener("input", () => { draw(); table(); });
$("buried").addEventListener("change", () => { draw(); table(); });
function visible(n) {
  if (!catOn.has(n.category)) return false;
  if (!$("buried").checked && isBuried(n)) return false;
  const q = $("q").value.trim().toLowerCase();
  if (!q) return true;
  return n.title.toLowerCase().includes(q) || n.tags.some((t) => t.includes(q)) || n.id === q;
}

// ---- layout (force-directed, computed once) ------------------------------
const W = 1000, H = 640, pos = new Map();
(function layout() {
  const N = notes.length; if (!N) return;
  const catIndex = new Map(cats.map((c, i) => [c, i]));
  notes.forEach((n, i) => {           // seed: one arc per category
    const a = (catIndex.get(n.category) / Math.max(cats.length, 1)) * Math.PI * 2 + (i % 17) * 0.05;
    const r = 120 + (i % 7) * 25;
    pos.set(n.id, {x: W / 2 + Math.cos(a) * r, y: H / 2 + Math.sin(a) * r, vx: 0, vy: 0});
  });
  const P = notes.map((n) => pos.get(n.id));
  const k = Math.sqrt((W * H) / Math.max(N, 1)) * 0.42;
  const iters = N > 800 ? 80 : N > 300 ? 160 : 260;
  for (let it = 0; it < iters; it++) {
    const temp = 18 * (1 - it / iters) + 0.5;
    for (let i = 0; i < N; i++) { P[i].vx = 0; P[i].vy = 0; }
    for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
      let dx = P[i].x - P[j].x, dy = P[i].y - P[j].y, d2 = dx * dx + dy * dy || 0.01;
      if (d2 > 90000) continue;
      const f = (k * k) / d2; P[i].vx += dx * f; P[i].vy += dy * f; P[j].vx -= dx * f; P[j].vy -= dy * f;
    }
    for (const l of D.links) {
      const p = pos.get(l.a), q = pos.get(l.b); if (!p || !q) continue;
      const dx = p.x - q.x, dy = p.y - q.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = ((d * d) / k) * (0.3 + l.strength[0]) * 0.5;
      p.vx -= (dx / d) * f; p.vy -= (dy / d) * f; q.vx += (dx / d) * f; q.vy += (dy / d) * f;
    }
    for (const p of P) {
      p.vx += (W / 2 - p.x) * 0.09; p.vy += (H / 2 - p.y) * 0.09;
      const v = Math.sqrt(p.vx * p.vx + p.vy * p.vy) || 1;
      p.x += (p.vx / v) * Math.min(v, temp); p.y += (p.vy / v) * Math.min(v, temp);
    }
  }
})();

// ---- drawing --------------------------------------------------------------
const svg = $("graph");
let view = {x: 0, y: 0, w: W, h: H};
function fit(ids) {               // frame the given memories (all, by default)
  const ps = (ids || notes.map((n) => n.id)).map((id) => pos.get(id)).filter(Boolean);
  if (!ps.length) return;
  let x0 = Math.min(...ps.map((p) => p.x)), x1 = Math.max(...ps.map((p) => p.x));
  let y0 = Math.min(...ps.map((p) => p.y)), y1 = Math.max(...ps.map((p) => p.y));
  const r = svg.getBoundingClientRect(), aspect = (r.width || W) / (r.height || H), pad = 60;
  let w = x1 - x0 + pad * 2, h = y1 - y0 + pad * 2;
  if (w / h < aspect) w = h * aspect; else h = w / aspect;
  view = {x: (x0 + x1) / 2 - w / 2, y: (y0 + y1) / 2 - h / 2, w, h};
}
fit(notes.filter(visible).map((n) => n.id));
const setView = () => {
  svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  const scale = view.w / (svg.getBoundingClientRect().width || W);   // keep labels ~11px on screen
  gLabels.setAttribute("font-size", (11 * scale).toFixed(2));
};
const gLinks = sv("g"), gNodes = sv("g"), gLabels = sv("g", {"font-size": "11", "pointer-events": "none"});
svg.append(gLinks, gNodes, gLabels);
setView();
window.addEventListener("resize", setView);
const radius = (n) => 4 + n.importance * 11;

function draw() {
  gLinks.replaceChildren(); gNodes.replaceChildren(); gLabels.replaceChildren();
  const vis = new Set(notes.filter(visible).map((n) => n.id));
  const near = new Set();
  if (selected) { near.add(selected); for (const l of adj.get(selected) || []) { near.add(l.a); near.add(l.b); } }
  for (const l of D.links) {
    if (!vis.has(l.a) || !vis.has(l.b)) continue;
    const p = pos.get(l.a), q = pos.get(l.b), s = l.strength[T];
    const hot = selected && (l.a === selected || l.b === selected);
    gLinks.append(sv("line", {x1: p.x, y1: p.y, x2: q.x, y2: q.y,
      stroke: hot ? css.getPropertyValue("--accent") : css.getPropertyValue("--muted"),
      "stroke-opacity": (hot ? 0.35 + 0.65 * s : 0.08 + 0.45 * s).toFixed(3),
      "stroke-width": (0.6 + 2.2 * s).toFixed(2)}));
  }
  const ordered = notes.filter((n) => vis.has(n.id)).sort((a, b) => a.strength[T] - b.strength[T]);
  // Labels: strongest (and the selection's neighbours) first; skip any that would overlap.
  const scale = view.w / (svg.getBoundingClientRect().width || W), placed = [];
  const wantLabel = new Set();
  for (const n of [...ordered].reverse()) {
    if (!(near.has(n.id) || n.strength[T] >= 0.55)) continue;
    const p = pos.get(n.id), text = n.title.length > 34 ? n.title.slice(0, 33) + "…" : n.title;
    const box = {x: p.x + radius(n) + 3 * scale, y: p.y - 7 * scale,
                 w: text.length * 6.2 * scale, h: 13 * scale};
    const clash = placed.some((o) => box.x < o.x + o.w && o.x < box.x + box.w &&
                                     box.y < o.y + o.h && o.y < box.y + box.h);
    if (clash && !(selected && (n.id === selected))) continue;
    placed.push(box); wantLabel.add(n.id);
  }
  for (const n of ordered) {
    const p = pos.get(n.id), s = n.strength[T], dim = selected && !near.has(n.id);
    const c = sv("circle", {cx: p.x, cy: p.y, r: radius(n), fill: color(n.category),
      "fill-opacity": ((0.12 + 0.88 * s) * (dim ? 0.35 : 1)).toFixed(3),
      stroke: isVital(n) ? css.getPropertyValue("--ink") : color(n.category),
      "stroke-width": isVital(n) ? 2 : (n.id === selected ? 2.5 : 1),
      "stroke-dasharray": isBuried(n) ? "2 2" : "none", tabindex: 0});
    c.style.cursor = "pointer";
    const tip = sv("title"); tip.textContent = `${n.title} · strength ${s.toFixed(2)}`; c.append(tip);
    c.addEventListener("click", (e) => { e.stopPropagation(); if (dragged) { dragged = false; return; } select(n.id); });
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") select(n.id); });
    gNodes.append(c);
    if (wantLabel.has(n.id)) {
      const t = sv("text", {x: p.x + radius(n) + 3, y: p.y + 4, fill: css.getPropertyValue("--ink"),
        "fill-opacity": dim ? 0.3 : 0.85});
      t.textContent = n.title.length > 34 ? n.title.slice(0, 33) + "…" : n.title;
      gLabels.append(t);
    }
  }
}

// pan + zoom
let drag = null, dragged = false;
svg.addEventListener("pointerdown", (e) => { drag = {x: e.clientX, y: e.clientY, v: {...view}, id: e.pointerId};
  dragged = false; });
svg.addEventListener("pointermove", (e) => { if (!drag) return;
  if (!dragged) {                  // only a real drag captures the pointer, so clicks still reach memories
    if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 4) return;
    dragged = true; svg.classList.add("dragging"); svg.setPointerCapture(drag.id);
  }
  const r = svg.getBoundingClientRect(), sx = view.w / r.width, sy = view.h / r.height;
  view.x = drag.v.x - (e.clientX - drag.x) * sx; view.y = drag.v.y - (e.clientY - drag.y) * sy; setView(); });
const endDrag = () => { drag = null; svg.classList.remove("dragging"); };
svg.addEventListener("pointerup", endDrag); svg.addEventListener("pointercancel", endDrag);
svg.addEventListener("wheel", (e) => { e.preventDefault();
  const r = svg.getBoundingClientRect(), f = e.deltaY > 0 ? 1.12 : 1 / 1.12;
  const mx = view.x + ((e.clientX - r.left) / r.width) * view.w, my = view.y + ((e.clientY - r.top) / r.height) * view.h;
  view = {x: mx - (mx - view.x) * f, y: my - (my - view.y) * f, w: view.w * f, h: view.h * f}; setView();
  clearTimeout(zoomTimer); zoomTimer = setTimeout(draw, 150);     // re-place labels for the new zoom
}, {passive: false});
let zoomTimer = null;
svg.addEventListener("click", () => { if (dragged) { dragged = false; return; }
  if (selected) { selected = null; draw(); clearSide(); } });
svg.addEventListener("dblclick", () => { fit(notes.filter(visible).map((n) => n.id)); setView(); });

// ---- side panel -----------------------------------------------------------
function clearSide() { $("side").replaceChildren(el("div", {class: "empty"},
  "Click a memory in the fabric or the table to see it here.")); }
function select(id) { selected = id; draw(); show(id); }
function show(id) {
  const n = byId.get(id); if (!n) return;
  const s = n.strength[T], side = $("side"); side.replaceChildren();
  side.append(el("h2", {}, n.title));
  side.append(el("div", {class: "meta"}, `${n.category} · ${n.profile.replace(/_/g, " ")}` +
    (n.stratum ? ` · ${n.stratum}` : "") + (n.proprietary ? " · proprietary" : "") +
    (isVital(n) ? " · vital" : "")));
  side.append(el("div", {class: "meta", style: "margin-top:10px"},
    `Strength ${s.toFixed(2)} · depth ${(1 - s).toFixed(2)} on ${D.horizon_labels[T].toLowerCase()}`));
  side.append(el("div", {class: "bar"}, el("i", {style: `width:${(s * 100).toFixed(1)}%`})));
  const spark = el("div", {class: "spark"}), lab = el("div", {class: "spark-l"});
  n.strength.forEach((v, i) => { spark.append(el("i", {style: `height:${Math.max(4, v * 44)}px;` +
    (i === T ? "" : "opacity:.4")})); lab.append(el("span", {}, D.horizon_labels[i].replace("+", ""))); });
  side.append(spark, lab);
  if (n.body) side.append(el("div", {class: "body"}, n.body));
  const tg = el("div", {class: "tags"});
  for (const t of n.tags) tg.append(el("span", {class: "tag", onclick: () => { $("q").value = t; draw(); table(); }}, t));
  side.append(tg);
  side.append(el("div", {class: "meta"},
    `Importance ${n.importance.toFixed(2)} · pulled up ${n.pulls}× · current until ${day(n.switch_at)}`));
  side.append(el("div", {class: "meta"}, `Written ${day(n.created_at)} by ${n.written_by}` +
    ` · last refreshed ${day(n.refreshed_at)}` + (n.source ? ` · source ${n.source}` : "")));
  side.append(el("div", {class: "meta"}, `id ${n.id}`));
  const ls = adj.get(id) || [];
  if (ls.length) {
    side.append(el("h2", {style: "font-size:13px;margin-top:14px"}, "Linked memories"));
    for (const l of [...ls].sort((a, b) => b.strength[T] - a.strength[T])) {
      const other = byId.get(l.a === id ? l.b : l.a); if (!other) continue;
      side.append(el("button", {class: "nbr", type: "button", onclick: () => select(other.id)},
        `${l.relation} · ${l.strength[T].toFixed(2)} → ${other.title}`));
    }
  }
}

// ---- table ----------------------------------------------------------------
let sortKey = "strength", sortDir = -1;
const cols = [["title", "Memory"], ["category", "Category"], ["profile", "Kind"],
  ["created_at", "Recorded"], ["strength", "Strength"], ["importance", "Importance"], ["pulls", "Pulls"], ["switch_at", "Current until"]];
function table() {
  const t = $("tbl"); t.replaceChildren();
  const head = el("tr");
  for (const [k, label] of cols) head.append(el("th", {onclick: () => {
    sortDir = sortKey === k ? -sortDir : -1; sortKey = k; table(); }},
    label + (sortKey === k ? (sortDir < 0 ? " ↓" : " ↑") : "")));
  t.append(el("thead", {}, head));
  const val = (n, k) => k === "strength" ? n.strength[T] : k === "created_at" ? (Date.parse(n[k]) || 0) : n[k];
  const rows = notes.filter(visible).sort((a, b) => {
    const x = val(a, sortKey), y = val(b, sortKey); return (x > y ? 1 : x < y ? -1 : 0) * sortDir; });
  const body = el("tbody");
  for (const n of rows.slice(0, 1000)) {
    body.append(el("tr", {onclick: () => { select(n.id); window.scrollTo({top: 0, behavior: "smooth"}); }},
      el("td", {}, n.title), el("td", {}, el("i", {class: "dot", style: "background:" + color(n.category)}), " ", n.category),
      el("td", {}, n.profile.replace(/_/g, " ")), el("td", {title: n.created_at || ""}, recordedDay(n.created_at)),
      el("td", {class: "num"}, n.strength[T].toFixed(2)),
      el("td", {class: "num"}, n.importance.toFixed(2)), el("td", {class: "num"}, n.pulls),
      el("td", {}, day(n.switch_at))));
  }
  t.append(body);
  if (!rows.length) t.append(el("caption", {class: "empty"}, "No memories match."));
}

// ---- staff + dials ----------------------------------------------------------
(function staff() {
  const lv = $("levels"), sp = D.card.specialties || [];
  if (!sp.length) lv.append(el("div", {class: "empty"}, "No verified outcomes yet."));
  for (const s of sp) lv.append(el("div", {class: "level"},
    el("span", {}, s.specialty), el("span", {}, `Level ${s.level} · ${s.points} pts` +
      (s.next_level_at ? ` (next at ${s.next_level_at})` : "") + (s.last_active ? ` · active ${day(s.last_active)}` : ""))));
  const rv = $("reviews");
  if (!D.reviews.length) rv.append(el("div", {class: "empty"}, "No reviews yet."));
  for (const r of D.reviews) rv.append(el("div", {class: "review"},
    el("div", {class: "who"}, `${r.reviewer} (${r.reviewer_role})` + (r.rating != null ? ` · ${r.rating}/5` : "") +
      (r.project ? ` · ${r.project}` : "") + ` · ${day(r.created_at)}`), el("div", {}, r.text)));
})();
function kv(tableId, headers, rows) {
  const t = $(tableId); t.append(el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", {}, h)))));
  const b = el("tbody"); for (const r of rows) b.append(el("tr", {}, ...r.map((c) => el("td", {}, c ?? "")))); t.append(b);
  if (!rows.length) t.append(el("caption", {class: "empty"}, "Nothing yet."));
}
kv("settings", ["Setting", "Value"], Object.entries(D.settings).map(([k, v]) => [k.replace(/_/g, " "), v]));
kv("profiles", ["Kind", "Importance", "Slow/day", "Fast/day", "Switch after", "Refresh adds"],
  D.profiles.map((p) => [p.profile.replace(/_/g, " "), p.importance, p.slow_rate, p.fast_rate,
    p.horizon_days + " d", p.refresh_extends_days + " d"]));
kv("changes", ["When", "What", "From → to", "By", "Why"], D.setting_changes.map((c) =>
  [day(c.at), c.key, `${c.old_value} → ${c.new_value}`, c.changed_by, c.reason]));

// ---- tabs + boot ------------------------------------------------------------
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => {
  for (const o of document.querySelectorAll(".tabs button")) o.setAttribute("aria-selected", o === b);
  for (const p of document.querySelectorAll(".tabpanel")) p.classList.toggle("on", p.id === "tab-" + b.dataset.tab);
});
$("foot").textContent = `Snapshot ${D.format} · ${D.counts.shown_notes} of ${D.counts.total_notes} memories shown` +
  (D.counts.truncated ? ` (${D.counts.truncated} weakest left out)` : "") +
  ". Generated by the Aether memory engine; this page is a read-only picture and changes nothing.";
tiles(); draw(); table();
</script>
</body>
</html>
"""
