"""Write the computed ratings as JSON, CSV, Markdown and a static HTML page."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from jollyrating.models import ContestStandings
from jollyrating.rating import UserRating


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def build_payload(
    title: str,
    season_names: Sequence[str],
    contests: Sequence[tuple[str, ContestStandings]],
    ratings: Sequence[UserRating],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    contest_rows = []
    for season, c in sorted(contests, key=lambda t: (t[1].begin, t[1].id)):
        contest_rows.append(
            {
                "id": c.id,
                "title": c.title,
                "begin": _iso(c.begin),
                "season": season,
                "mode": c.mode,
                "participants": c.n_participants,
                "max_score": c.max_score,
                "problems": c.problems,
                "source": c.source,
                "url": c.url,
            }
        )
    users = []
    for rank, r in enumerate(ratings, start=1):
        seasons = []
        for s in r.seasons:
            seasons.append(
                {
                    "season": s.season,
                    "previous": s.previous_rating,
                    "base": s.base,
                    "rating": s.rating,
                    "gained": s.gained,
                    "actual": [
                        {
                            "contest_id": p.contest_id,
                            "title": p.title,
                            "begin": _iso(p.begin),
                            "rank": p.rank,
                            "score": p.score,
                            "penalty": p.penalty,
                            "participants": p.n_participants,
                            "max_score": p.max_score,
                            "perf": p.perf,
                            "url": p.url,
                        }
                        for p in s.actual
                    ],
                    "externals": [asdict(e) | {"at": _iso(e.at)} for e in s.externals],
                    "weighted": [asdict(w) for w in s.weighted],
                }
            )
        users.append(
            {
                "rank": rank,
                "user": r.user,
                "display": r.display,
                "rating": r.rating,
                "contests": r.contests,
                "best_perf": r.best_perf,
                "seasons": seasons,
                "history": [
                    {
                        "at": _iso(h.at),
                        "season": h.season,
                        "rating": h.rating,
                        "label": h.label,
                        "contest_id": h.contest_id,
                        "delta": h.delta,
                    }
                    for h in r.history
                ],
            }
        )
    return {
        "title": title,
        "generated_at": generated_at.isoformat(),
        "seasons": list(season_names),
        "contests": contest_rows,
        "users": users,
    }


def write_json(payload: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(payload: dict[str, Any], path: Path) -> None:
    seasons = payload["seasons"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "user", "display", "rating", "contests", "best_perf"] + [f"rating_{s}" for s in seasons])
        for u in payload["users"]:
            per_season = {s["season"]: s["rating"] for s in u["seasons"]}
            w.writerow(
                [u["rank"], u["user"], u["display"], f"{u['rating']:.2f}", u["contests"], f"{u['best_perf']:.2f}"]
                + [f"{per_season.get(s, 0.0):.2f}" for s in seasons]
            )


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def write_markdown(payload: dict[str, Any], path: Path) -> None:
    lines = [f"# {payload['title']}", ""]
    lines.append(f"Updated {payload['generated_at'][:16].replace('T', ' ')} UTC · "
                 f"{len(payload['contests'])} contests · {len(payload['users'])} rated users")
    lines.append("")
    lines.append("| # | User | Rating | Contests | Best perf | Last change |")
    lines.append("|--:|:-----|-------:|---------:|----------:|------------:|")
    for u in payload["users"]:
        name = _md_escape(u["display"])
        if u["display"] != u["user"]:
            name += f" (`{_md_escape(u['user'])}`)"
        last = u["history"][-1]["delta"] if u["history"] else 0.0
        lines.append(
            f"| {u['rank']} | {name} | {u['rating']:.0f} | {u['contests']} | {u['best_perf']:.0f} | {last:+.0f} |"
        )
    lines.append("")
    if payload["contests"]:
        lines.append("## Contests")
        lines.append("")
        lines.append("| Date | Season | Contest | Participants | Top score |")
        lines.append("|:-----|:-------|:--------|-------------:|----------:|")
        for c in payload["contests"]:
            title = _md_escape(c["title"])
            if c.get("url"):
                title = f"[{title}]({c['url']})"
            lines.append(f"| {(c['begin'] or '')[:10]} | {_md_escape(c['season'])} | {title} | {c['participants']} | {c['max_score']:g} |")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_html(payload: dict[str, Any], path: Path) -> None:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = _HTML_TEMPLATE.replace("__TITLE__", _html_escape(payload["title"])).replace("__DATA__", data)
    path.write_text(html, encoding="utf-8")


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def write_all(payload: dict[str, Any], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "ratings.json": write_json,
        "ratings.csv": write_csv,
        "ratings.md": write_markdown,
        "index.html": write_html,
    }
    written = []
    for name, fn in targets.items():
        p = out_dir / name
        fn(payload, p)
        written.append(p)
    return written


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --hover: rgba(11,11,11,0.04);
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --good: #006300; --bad: #d03b3b;
  --gold: #eda100; --silver: #898781; --bronze: #c98500;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --hover: rgba(255,255,255,0.05);
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --good: #0ca30c; --bad: #e66767;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --hover: rgba(255,255,255,0.05);
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --good: #0ca30c; --bad: #e66767;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 16px; }
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 24px; margin: 8px 0 2px; }
h2 { font-size: 16px; margin: 20px 0 8px; }
h3 { font-size: 14px; margin: 16px 0 6px; color: var(--ink-2); }
.sub { color: var(--ink-2); margin-bottom: 16px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-bottom: 16px; }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 12px; }
.controls label { color: var(--ink-2); }
input[type=search], select { font: inherit; padding: 6px 10px; border: 1px solid var(--axis); border-radius: 6px; background: var(--surface); color: var(--ink); }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 7px 8px; text-align: left; border-bottom: 1px solid var(--grid); vertical-align: top; }
th { color: var(--ink-2); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
td.title { min-width: 170px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
@media (max-width: 600px) { .opt { display: none; } }
tbody tr.row { cursor: pointer; }
tbody tr.row:hover, tbody tr.row:focus-visible { background: var(--hover); outline: none; }
tbody tr.row.selected { background: var(--hover); box-shadow: inset 3px 0 0 var(--s1); }
.handle { color: var(--muted); font-size: 12px; }
.medal { display: inline-block; width: 22px; height: 22px; line-height: 22px; text-align: center; border-radius: 50%; font-weight: 700; font-size: 12px; color: #0b0b0b; }
.medal.g { background: var(--gold); } .medal.s { background: #c3c2b7; } .medal.b { background: var(--bronze); }
.delta { font-variant-numeric: tabular-nums; }
.up { color: var(--good); } .down { color: var(--bad); }
.pill { display: inline-block; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--axis); color: var(--ink-2); font-size: 12px; }
button { font: inherit; font-size: 12px; padding: 3px 8px; border-radius: 6px; border: 1px solid var(--axis); background: var(--surface); color: var(--ink); cursor: pointer; }
button:hover { background: var(--hover); }
button[disabled] { opacity: .5; cursor: default; }
.wrap { overflow-x: auto; }
#detail:empty { display: none; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 4px 0 8px; color: var(--ink-2); font-size: 13px; }
.legend .key { display: inline-block; width: 18px; height: 0; border-top: 2px solid; vertical-align: middle; margin-right: 6px; border-radius: 2px; }
.chart { position: relative; width: 100%; }
.chart svg { display: block; width: 100%; height: auto; }
.tip { position: absolute; pointer-events: none; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 13px; box-shadow: 0 4px 14px rgba(0,0,0,.12); min-width: 150px; display: none; z-index: 2; }
.tip .when { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
.tip .row { display: flex; align-items: center; gap: 8px; }
.tip .row b { margin-left: auto; font-variant-numeric: tabular-nums; }
.grid line { stroke: var(--grid); stroke-width: 1; }
.axis text { fill: var(--muted); font-size: 11px; }
.axis line, .axis path { stroke: var(--axis); stroke-width: 1; }
.season-mark line { stroke: var(--axis); stroke-width: 1; }
.season-mark text { fill: var(--muted); font-size: 11px; }
.series path { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.series circle { stroke: var(--surface); stroke-width: 2; }
.crosshair { stroke: var(--axis); stroke-width: 1; display: none; }
.small { color: var(--muted); font-size: 12px; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 720px) { .two { grid-template-columns: 1fr; } }
details summary { cursor: pointer; color: var(--ink-2); }
.empty { color: var(--muted); padding: 24px; text-align: center; }
</style>
</head>
<body>
<main>
  <h1 id="title"></h1>
  <div class="sub" id="subtitle"></div>

  <div class="card">
    <div class="controls">
      <input type="search" id="search" placeholder="Search user…" aria-label="Search user">
      <label>Rating as of
        <select id="season"></select>
      </label>
      <span class="small" id="count"></span>
      <span class="small" id="compare-hint"></span>
    </div>
    <div class="wrap">
      <table id="board">
        <thead><tr>
          <th class="num">#</th><th>User</th><th class="num">Rating</th><th class="num opt">Contests</th>
          <th class="num opt">Best perf</th><th class="num">Last change</th><th></th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div id="detail"></div>

  <div class="card">
    <h2 style="margin-top:0">Contests</h2>
    <div class="wrap">
      <table id="contests">
        <thead><tr><th>Date</th><th>Season</th><th>Contest</th><th class="num">Participants</th><th class="num">Top score</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <details class="card">
    <summary>How the rating works</summary>
    <p>Each contest gives every participant a <b>performance</b> in [0, 4000]:
      <code>perf = 4000 × (N − rank + 241) / (N + 240) × score / max score</code>,
      where N is the number of participants. The best participant always gets 4000.</p>
    <p>A season's performances are sorted from best to worst and weighted with
      <code>W<sub>i</sub> = max(0.01, 0.1 × 0.95<sup>i−1</sup></code>). The i-th <b>external participation</b>
      (a contest missed for another training or competition) counts as the <code>i(i+1)/2</code>-th best actual performance.</p>
    <p><code>rating<sub>s</sub> = seasonReset(rating<sub>s−1</sub>) + Σ W<sub>i</sub> × P<sub>i</sub></code> with
      <code>seasonReset(x) = x<sup>log<sub>4000</sub> 400</sup></code> (so 4000 carries over as 400). A rating never decreases within a season.</p>
  </details>
</main>

<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  "use strict";
  const DATA = JSON.parse(document.getElementById("data").textContent);
  const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)"];
  const fmt0 = n => Math.round(n).toLocaleString("en-US");
  const fmt1 = n => (Math.round(n * 10) / 10).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fmt3 = n => n.toFixed(3);
  const el = (tag, attrs, ...kids) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const kid of kids) node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return node;
  };
  const svgEl = (tag, attrs) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    return node;
  };
  const dateFmt = iso => iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : "";
  const penaltyFmt = s => {
    s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  };

  document.getElementById("title").textContent = DATA.title;
  document.title = DATA.title;
  document.getElementById("subtitle").textContent =
    `Updated ${new Date(DATA.generated_at).toLocaleString("en-US")} · ${DATA.contests.length} contests · ${DATA.users.length} rated users`;

  // ----- season selector -------------------------------------------------
  const seasonSel = document.getElementById("season");
  seasonSel.append(el("option", { value: "" }, "latest"));
  for (const s of DATA.seasons) seasonSel.append(el("option", { value: s }, `end of ${s}`));

  const state = { season: "", query: "", selected: null, compare: [] };
  const ratingAt = (u, season) => {
    if (!season) return u.rating;
    const s = u.seasons.find(x => x.season === season);
    return s ? s.rating : 0;
  };

  // ----- leaderboard ------------------------------------------------------
  const tbody = document.querySelector("#board tbody");
  function renderBoard() {
    tbody.textContent = "";
    const q = state.query.trim().toLowerCase();
    let rows = DATA.users.map(u => ({ u, rating: ratingAt(u, state.season) }));
    rows.sort((a, b) => b.rating - a.rating || a.u.user.localeCompare(b.u.user));
    let shown = 0;
    rows.forEach((row, i) => {
      const u = row.u;
      if (q && !u.user.toLowerCase().includes(q) && !u.display.toLowerCase().includes(q)) return;
      shown++;
      const rank = i + 1;
      const medal = rank <= 3 ? el("span", { class: "medal " + ["g", "s", "b"][rank - 1] }, rank) : rank;
      const last = u.history.length ? u.history[u.history.length - 1].delta : 0;
      const name = el("div", {}, el("div", {}, u.display));
      if (u.display !== u.user) name.append(el("div", { class: "handle" }, u.user));
      const inCompare = state.compare.includes(u.user);
      const btn = el("button", {
        type: "button",
        title: inCompare ? "Remove from chart" : "Add to chart (up to 3)",
        onclick: ev => { ev.stopPropagation(); toggleCompare(u.user); },
      }, inCompare ? "− chart" : "+ chart");
      if (!inCompare && state.compare.length >= 3) btn.disabled = true;
      const tr = el("tr", {
        class: "row" + (state.selected === u.user ? " selected" : ""), tabindex: "0", role: "button",
        onclick: () => select(u.user),
        onkeydown: ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(u.user); } },
      },
        el("td", { class: "num" }, medal),
        el("td", {}, name),
        el("td", { class: "num" }, el("b", {}, fmt0(row.rating))),
        el("td", { class: "num opt" }, u.contests),
        el("td", { class: "num opt" }, fmt0(u.best_perf)),
        el("td", { class: "num delta " + (last > 0 ? "up" : last < 0 ? "down" : "") }, (last > 0 ? "+" : "") + fmt0(last)),
        el("td", {}, btn));
      tbody.append(tr);
    });
    if (!shown) tbody.append(el("tr", {}, el("td", { colspan: 7, class: "empty" }, "No users match.")));
    document.getElementById("count").textContent = `${shown} of ${DATA.users.length} users`;
    document.getElementById("compare-hint").textContent = state.compare.length ? `Chart: ${state.compare.join(", ")}` : "";
  }
  document.getElementById("search").addEventListener("input", e => { state.query = e.target.value; renderBoard(); });
  seasonSel.addEventListener("change", e => { state.season = e.target.value; renderBoard(); });

  function toggleCompare(user) {
    const i = state.compare.indexOf(user);
    if (i >= 0) state.compare.splice(i, 1);
    else if (state.compare.length < 3) state.compare.push(user);
    if (!state.selected) state.selected = state.compare[0] || null;
    renderBoard(); renderDetail();
  }
  function select(user) {
    state.selected = user;
    if (!state.compare.includes(user)) {
      if (state.compare.length >= 3) state.compare.pop();
      state.compare.unshift(user);
    }
    renderBoard(); renderDetail();
    document.getElementById("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ----- detail panel -----------------------------------------------------
  const detail = document.getElementById("detail");
  function renderDetail() {
    detail.textContent = "";
    const users = state.compare.map(name => DATA.users.find(u => u.user === name)).filter(Boolean);
    if (!users.length) return;
    const card = el("div", { class: "card" });
    card.append(el("h2", { style: "margin-top:0" }, users.length > 1 ? "Rating history" : `${users[0].display} — rating history`));
    if (users.length > 1) {
      const legend = el("div", { class: "legend" });
      users.forEach((u, i) => legend.append(el("span", {}, el("span", { class: "key", style: `border-color:${SERIES[i]}` }), u.display)));
      card.append(legend);
    }
    card.append(renderChart(users));
    detail.append(card);

    const u = DATA.users.find(x => x.user === state.selected) || users[0];
    const bd = el("div", { class: "card" });
    bd.append(el("h2", { style: "margin-top:0" }, `${u.display} — breakdown`),
      el("div", { class: "sub" }, `Rating ${fmt1(u.rating)} · ${u.contests} contests · best performance ${fmt1(u.best_perf)}`));
    for (const s of [...u.seasons].reverse()) {
      if (!s.actual.length && !s.externals.length && s.base === 0) continue;
      bd.append(el("h3", {}, `${s.season}: ${fmt1(s.base)} carried over + ${fmt1(s.gained)} gained = ${fmt1(s.rating)}`));
      if (s.previous > 0) bd.append(el("div", { class: "small" }, `season reset: ${fmt1(s.previous)}^0.7224 = ${fmt1(s.base)}`));
      const grid = el("div", { class: "two" });
      const t1 = el("table", {}, el("thead", {}, el("tr", {},
        el("th", {}, "Contest"), el("th", { class: "num" }, "Rank"), el("th", { class: "num" }, "Score"),
        el("th", { class: "num" }, "Penalty"), el("th", { class: "num" }, "Perf"))));
      const b1 = el("tbody");
      for (const p of [...s.actual].sort((a, b) => a.begin.localeCompare(b.begin))) {
        const title = p.url ? el("a", { href: p.url, target: "_blank", rel: "noopener" }, p.title) : el("span", {}, p.title);
        b1.append(el("tr", {},
          el("td", { class: "title" }, title, el("div", { class: "small" }, dateFmt(p.begin))),
          el("td", { class: "num" }, `${p.rank} / ${p.participants}`),
          el("td", { class: "num" }, `${+p.score.toFixed(2)} / ${+p.max_score.toFixed(2)}`),
          el("td", { class: "num" }, p.penalty ? penaltyFmt(p.penalty) : "—"),
          el("td", { class: "num" }, el("b", {}, fmt1(p.perf)))));
      }
      for (const e of s.externals) {
        b1.append(el("tr", {},
          el("td", { class: "title" }, el("span", { class: "pill" }, "external"), " ", e.note || `external participation #${e.index}`,
            el("div", { class: "small" }, e.at ? dateFmt(e.at) : "")),
          el("td", { class: "num" }, "—"), el("td", { class: "num" }, "—"),
          el("td", { class: "num" }, `= P${e.source_index}`),
          el("td", { class: "num" }, el("b", {}, fmt1(e.perf)))));
      }
      if (!b1.children.length) b1.append(el("tr", {}, el("td", { colspan: 5, class: "small" }, "No contests this season.")));
      t1.append(b1);
      const t2 = el("table", {}, el("thead", {}, el("tr", {},
        el("th", { class: "num" }, "i"), el("th", {}, "Performance"), el("th", { class: "num" }, "Weight"), el("th", { class: "num" }, "Adds"))));
      const b2 = el("tbody");
      for (const w of s.weighted) {
        b2.append(el("tr", {},
          el("td", { class: "num" }, w.position),
          el("td", {}, w.kind === "external" ? el("span", { class: "pill" }, "external") : "", w.kind === "external" ? " " : "", w.kind === "external" ? w.ref : (s.actual.find(p => p.contest_id === w.ref) || { title: w.ref }).title, el("span", { class: "small" }, ` ${fmt1(w.perf)}`)),
          el("td", { class: "num" }, fmt3(w.weight)),
          el("td", { class: "num" }, "+" + fmt1(w.contribution))));
      }
      if (b2.children.length) {
        b2.append(el("tr", {}, el("td", {}), el("td", {}, el("b", {}, "Total gained")), el("td", {}), el("td", { class: "num" }, el("b", {}, "+" + fmt1(s.gained)))));
      }
      t2.append(b2);
      grid.append(el("div", { class: "wrap" }, t1), el("div", { class: "wrap" }, t2));
      bd.append(grid);
    }
    detail.append(bd);
  }

  // ----- chart --------------------------------------------------------------
  function renderChart(users) {
    const W = Math.min(900, Math.max(340, document.documentElement.clientWidth - 70));
    const H = W < 600 ? 260 : 320, m = { top: 24, right: W < 600 ? 16 : 24, bottom: 36, left: 52 };
    const wrap = el("div", { class: "chart" });
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Rating over time" });
    const series = users.map((u, i) => ({
      user: u, color: SERIES[i],
      pts: u.history.map(h => ({ t: new Date(h.at).getTime(), y: h.rating, h })).sort((a, b) => a.t - b.t),
    }));
    const all = series.flatMap(s => s.pts);
    if (!all.length) { wrap.append(el("div", { class: "empty" }, "No rated contests yet.")); return wrap; }
    let t0 = Math.min(...all.map(p => p.t)), t1 = Math.max(...all.map(p => p.t));
    if (t1 === t0) { t0 -= 86400e3 * 7; t1 += 86400e3 * 7; }
    const pad = (t1 - t0) * 0.03; t0 -= pad; t1 += pad;
    const yMaxRaw = Math.max(...all.map(p => p.y), 100);
    const step = niceStep(yMaxRaw / 5);
    const yMax = Math.ceil(yMaxRaw / step) * step;
    const x = t => m.left + (t - t0) / (t1 - t0) * (W - m.left - m.right);
    const y = v => H - m.bottom - v / yMax * (H - m.top - m.bottom);

    const grid = svgEl("g", { class: "grid" });
    const axis = svgEl("g", { class: "axis" });
    for (let v = 0; v <= yMax + 1e-9; v += step) {
      grid.append(svgEl("line", { x1: m.left, x2: W - m.right, y1: y(v), y2: y(v) }));
      const t = svgEl("text", { x: m.left - 8, y: y(v) + 4, "text-anchor": "end" }); t.textContent = fmt0(v); axis.append(t);
    }
    const ticks = timeTicks(t0, t1, W < 600 ? 4 : 6);
    for (const tk of ticks) {
      const t = svgEl("text", { x: x(tk), y: H - m.bottom + 18, "text-anchor": "middle" });
      t.textContent = new Date(tk).toLocaleDateString("en-US", { month: "short", year: "2-digit" }); axis.append(t);
    }
    axis.append(svgEl("line", { x1: m.left, x2: W - m.right, y1: y(0), y2: y(0) }));
    svg.append(grid, axis);

    // season boundaries: first contest of each season after the first
    const seasonStarts = new Map();
    for (const c of DATA.contests) { const t = new Date(c.begin).getTime(); if (!seasonStarts.has(c.season) || t < seasonStarts.get(c.season)) seasonStarts.set(c.season, t); }
    const marks = svgEl("g", { class: "season-mark" });
    let first = true;
    for (const [name, t] of [...seasonStarts.entries()].sort((a, b) => a[1] - b[1])) {
      if (t < t0 || t > t1) continue;
      if (!first) marks.append(svgEl("line", { x1: x(t), x2: x(t), y1: m.top, y2: H - m.bottom }));
      const nearRight = x(t) > W - m.right - 110;
      const lbl = svgEl("text", { x: nearRight ? x(t) - 4 : x(t) + 4, y: m.top - 8, "text-anchor": nearRight ? "end" : "start" });
      lbl.textContent = name; marks.append(lbl);
      first = false;
    }
    svg.append(marks);

    const cross = svgEl("line", { class: "crosshair", y1: m.top, y2: H - m.bottom });
    svg.append(cross);
    const layer = svgEl("g", { class: "series" });
    for (const s of series) {
      if (!s.pts.length) continue;
      let d = "";
      s.pts.forEach((p, i) => {
        if (i && p.h.label === "season reset") d += ` L${x(p.t).toFixed(1)} ${y(s.pts[i - 1].y).toFixed(1)}`;
        d += `${i ? " L" : "M"}${x(p.t).toFixed(1)} ${y(p.y).toFixed(1)}`;
      });
      layer.append(svgEl("path", { d, stroke: s.color }));
      for (const p of s.pts) layer.append(svgEl("circle", { cx: x(p.t), cy: y(p.y), r: 4, fill: s.color }));
    }
    svg.append(layer);
    if (series.length === 1 && series[0].pts.length) {
      const last = series[0].pts[series[0].pts.length - 1];
      const tt = svgEl("text", { x: Math.min(x(last.t) + 8, W - m.right - 30), y: y(last.y) - 8 });
      tt.setAttribute("style", "font-size:12px;font-weight:600;fill:var(--ink)");
      tt.textContent = fmt0(last.y); svg.append(tt);
    }

    const tip = el("div", { class: "tip" });
    wrap.append(svg, tip);
    const times = [...new Set(all.map(p => p.t))].sort((a, b) => a - b);
    const hitArea = svgEl("rect", { x: m.left, y: m.top, width: W - m.left - m.right, height: H - m.top - m.bottom, fill: "transparent" });
    svg.append(hitArea);
    const move = ev => {
      const rect = svg.getBoundingClientRect();
      const px = (ev.clientX - rect.left) / rect.width * W;
      const tAt = t0 + (px - m.left) / (W - m.left - m.right) * (t1 - t0);
      let best = times[0];
      for (const t of times) if (Math.abs(t - tAt) < Math.abs(best - tAt)) best = t;
      cross.setAttribute("x1", x(best)); cross.setAttribute("x2", x(best)); cross.style.display = "block";
      tip.textContent = "";
      tip.append(el("div", { class: "when" }, dateFmt(new Date(best).toISOString())));
      for (const s of series) {
        const p = s.pts.filter(q => q.t <= best).pop();
        if (!p) continue;
        const row = el("div", { class: "row" }, el("span", { class: "key", style: `display:inline-block;width:14px;border-top:2px solid ${s.color}` }),
          el("span", {}, s.user.display), el("b", {}, fmt0(p.y)));
        tip.append(row);
        if (p.t === best) tip.append(el("div", { class: "small" }, `${p.h.label} (${p.h.delta >= 0 ? "+" : ""}${fmt0(p.h.delta)})`));
      }
      tip.style.display = "block";
      const left = (ev.clientX - rect.left) + 14, top = (ev.clientY - rect.top) - 10;
      tip.style.left = Math.min(left, rect.width - tip.offsetWidth - 8) + "px";
      tip.style.top = Math.max(0, top) + "px";
    };
    hitArea.addEventListener("pointermove", move);
    hitArea.addEventListener("pointerleave", () => { tip.style.display = "none"; cross.style.display = "none"; });
    return wrap;
  }
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const k of [1, 2, 2.5, 5, 10]) if (raw <= k * p) return k * p;
    return 10 * p;
  }
  function timeTicks(t0, t1, n) {
    const span = t1 - t0, out = [];
    const months = span / (30.44 * 86400e3);
    const stepMonths = months <= n ? 1 : Math.ceil(months / n);
    const d = new Date(t0); d.setUTCDate(1); d.setUTCHours(0, 0, 0, 0); d.setUTCMonth(d.getUTCMonth() + 1);
    while (d.getTime() <= t1) { out.push(d.getTime()); d.setUTCMonth(d.getUTCMonth() + stepMonths); }
    return out;
  }

  // ----- contests table ------------------------------------------------------
  const ctb = document.querySelector("#contests tbody");
  for (const c of [...DATA.contests].reverse()) {
    const title = c.url ? el("a", { href: c.url, target: "_blank", rel: "noopener" }, c.title) : el("span", {}, c.title);
    ctb.append(el("tr", {}, el("td", {}, dateFmt(c.begin)), el("td", {}, c.season), el("td", {}, title, " ", el("span", { class: "pill" }, c.mode.toUpperCase())),
      el("td", { class: "num" }, c.participants), el("td", { class: "num" }, +c.max_score.toFixed(2))));
  }
  if (!DATA.contests.length) ctb.append(el("tr", {}, el("td", { colspan: 5, class: "empty" }, "No contests yet.")));

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.compare.length) renderDetail(); }, 150);
  });

  renderBoard();
})();
</script>
</body>
</html>
"""
