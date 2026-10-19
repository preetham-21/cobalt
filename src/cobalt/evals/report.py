"""Static, self-contained HTML report for an eval (or for single runs).

No external assets: inline CSS with light/dark tokens, inline SVG charts with
hover tooltips, and a table under every chart (the accessible view).
Palette: the dataviz reference categorical order, validated light and dark.
"""

from __future__ import annotations

import html
from typing import Any

from cobalt.evals.metrics import TAXONOMY

DASH = chr(0x2013)  # en dash for missing values
SERIES_LIGHT = [
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
]
SERIES_DARK = [
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
]
STATUS = {
    "accepted": ("good", "✓"),
    "failed": ("critical", "✗"),
    "blocked": ("serious", "⊘"),
    "in_progress": ("warning", "…"),
    "pending": ("warning", "…"),
}

CSS = """
:root { --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --good:#0ca30c; --warning:#fab219; --serious:#ec835a;
  --critical:#d03b3b; --banner:#fff4d6; %(light)s }
@media (prefers-color-scheme: dark) { :root { --page:#0d0d0d; --surface:#1a1a19; --ink:#f0efec;
  --ink2:#c3c2b7; --muted:#898781; --grid:#2c2c2a; --axis:#383835; --banner:#3a2f12; %(dark)s } }
* { box-sizing:border-box } body { margin:0; background:var(--page); color:var(--ink);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif }
main { max-width:1120px; margin:0 auto; padding:24px 16px 64px }
h1 { font-size:24px; margin:0 0 4px } h2 { font-size:18px; margin:32px 0 8px }
.sub { color:var(--ink2); margin:0 0 16px }
.banner { background:var(--banner); border-radius:8px; padding:12px 14px; margin:16px 0 }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px }
.tile { background:var(--surface); border:1px solid var(--grid); border-radius:8px; padding:12px }
.tile .v { font-size:24px; font-weight:600 } .tile .k { color:var(--ink2); font-size:12px }
.card { background:var(--surface); border:1px solid var(--grid); border-radius:8px;
  padding:16px; margin:12px 0; overflow-x:auto }
table { border-collapse:collapse; width:100%%; font-size:13px }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--grid); white-space:nowrap }
th { color:var(--ink2); font-weight:600 } td.n, th.n { text-align:right;
  font-variant-numeric:tabular-nums }
.chip { display:inline-flex; gap:4px; align-items:center; padding:0 8px; border-radius:10px;
  border:1px solid currentColor; font-size:12px }
.chip.good { color:var(--good) } .chip.critical { color:var(--critical) }
.chip.serious { color:var(--serious) } .chip.warning { color:var(--ink2) }
svg text { fill:var(--ink2); font-size:12px } svg .val { fill:var(--ink); font-weight:600 }
svg .mark { transition:opacity .1s } svg .mark:hover { opacity:.75 }
.legend { display:flex; flex-wrap:wrap; gap:12px; margin:8px 0; color:var(--ink2); font-size:12px }
.legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px }
details summary { cursor:pointer; color:var(--ink2) } code { font-size:12px }
"""


def _esc(v: Any) -> str:
    return html.escape(str(v))


def _money(v: float | None) -> str:
    return DASH if v is None else f"${v:,.3f}"


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def bar_chart(
    rows: list[tuple[str, float, str]],
    *,
    max_value: float | None = None,
    fmt: Any = str,
    width: int = 640,
) -> str:
    """Single-series horizontal bars: (label, value, tooltip)."""
    if not rows:
        return "<p class='sub'>(no data)</p>"
    if all(v == 0 for _, v, _ in rows):
        return "<p class='sub'>All values are zero (no model spend in these runs).</p>"
    top = max_value or max((v for _, v, _ in rows), default=1) or 1
    label_w, bar_h, gap = 120, 22, 12
    plot_w = width - label_w - 80
    height = len(rows) * (bar_h + gap) + 8
    out = [f"<svg viewBox='0 0 {width} {height}' width='100%' role='img'>"]
    out.append(
        f"<line x1='{label_w}' y1='0' x2='{label_w}' y2='{height}' "
        "stroke='var(--axis)' stroke-width='1'/>"
    )
    for i, (label, value, tip) in enumerate(rows):
        y = i * (bar_h + gap) + 4
        w = max(2.0, plot_w * (value / top))
        out.append(f"<text x='{label_w - 8}' y='{y + 15}' text-anchor='end'>{_esc(label)}</text>")
        out.append(
            f"<rect class='mark' x='{label_w}' y='{y}' width='{w:.1f}' height='{bar_h}' "
            f"rx='4' fill='var(--s1)'><title>{_esc(tip)}</title></rect>"
        )
        out.append(
            f"<text class='val' x='{label_w + w + 6:.1f}' y='{y + 15}'>{_esc(fmt(value))}</text>"
        )
    out.append("</svg>")
    return "".join(out)


def stacked_chart(
    rows: list[tuple[str, dict[str, int]]], categories: list[str], width: int = 640
) -> str:
    """Stacked horizontal bars of counts per category (2px surface gaps)."""
    total = max((sum(c.values()) for _, c in rows), default=0)
    if total == 0:
        return "<p class='sub'>No failures in any variant.</p>"
    label_w, bar_h, gap = 120, 22, 12
    plot_w = width - label_w - 40
    height = len(rows) * (bar_h + gap) + 8
    out = [f"<svg viewBox='0 0 {width} {height}' width='100%' role='img'>"]
    for i, (label, counts) in enumerate(rows):
        y = i * (bar_h + gap) + 4
        out.append(f"<text x='{label_w - 8}' y='{y + 15}' text-anchor='end'>{_esc(label)}</text>")
        x = float(label_w)
        for j, cat in enumerate(categories):
            n = counts.get(cat, 0)
            if not n:
                continue
            w = plot_w * n / total
            out.append(
                f"<rect class='mark' x='{x:.1f}' y='{y}' width='{w:.1f}' height='{bar_h}' "
                f"rx='4' fill='var(--s{j + 1})' stroke='var(--surface)' stroke-width='2'>"
                f"<title>{_esc(label)}: {_esc(cat)} = {n}</title></rect>"
            )
            x += w
        out.append(f"<text class='val' x='{x + 6:.1f}' y='{y + 15}'>{sum(counts.values())}</text>")
    out.append("</svg>")
    legend = "".join(
        f"<span><i style='background:var(--s{j + 1})'></i>{_esc(c)}</span>"
        for j, c in enumerate(categories)
    )
    return f"<div class='legend'>{legend}</div>" + "".join(out)


def _chip(status: str) -> str:
    kind, icon = STATUS.get(status, ("warning", "?"))
    return f"<span class='chip {kind}'>{icon} {_esc(status)}</span>"


def module_table(run: dict[str, Any]) -> str:
    head = (
        "<tr><th>module</th><th>status</th><th>failure</th><th class='n'>attempts</th>"
        "<th class='n'>cost</th><th class='n'>input tok</th><th class='n'>cache read</th>"
        "<th class='n'>output tok</th><th class='n'>LLM calls</th><th class='n'>tool calls"
        "</th><th class='n'>tool errors</th><th class='n'>wall s</th><th>verdict</th></tr>"
    )
    rows = []
    for m in run["modules"]:
        rows.append(
            f"<tr><td>{_esc(m['program'])}</td><td>{_chip(m['status'])}</td>"
            f"<td>{_esc(m['failure'] or '')}</td><td class='n'>{m['attempts']}</td>"
            f"<td class='n'>{_money(m['cost_usd'])}</td><td class='n'>{m['input_tokens']:,}</td>"
            f"<td class='n'>{m['cache_read_tokens']:,}</td><td class='n'>{m['output_tokens']:,}"
            f"</td><td class='n'>{m['llm_calls']}</td><td class='n'>{m['tool_calls']}</td>"
            f"<td class='n'>{m['tool_errors']}</td><td class='n'>{m['wall_s']:.0f}</td>"
            f"<td>{_esc(m['verdict'][:80])}</td></tr>"
        )
    return f"<table>{head}{''.join(rows)}</table>"


def _first(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return runs[0]


def render_eval(result: dict[str, Any]) -> str:
    """HTML for an eval results dict (see :mod:`cobalt.evals.suite`)."""
    cfg = result["config"]
    variants = list(result["runs"])
    agg: dict[str, dict[str, Any]] = {}
    for v, runs in result["runs"].items():
        n = len(runs)
        agg[v] = {
            "success": sum(r["success_rate"] for r in runs) / n,
            "accepted": sum(r["accepted"] for r in runs) / n,
            "total": runs[0]["modules_total"],
            "cost": sum(r["cost_usd"] for r in runs) / n,
            "cpa": _mean([r["cost_per_accepted"] for r in runs]),
            "attempts": _mean([r["mean_attempts_to_pass"] for r in runs]),
            "tokens": sum(r["tokens"] for r in runs) / n,
            "wall": sum(r["wall_s"] for r in runs) / n,
            "failures": _sum_counts([r["failures"] for r in runs], n),
            "roles": _sum_counts([r["role_costs"] for r in runs], n),
        }
    full = agg.get("full")
    scripted = cfg.get("provider") != "anthropic"

    parts = [
        f"<h1>cobalt eval {_esc(result['eval_id'])}</h1>",
        f"<p class='sub'>corpus <b>{_esc(cfg['corpus'])}</b> · provider "
        f"<b>{_esc(cfg['provider'])}</b> · model <b>{_esc(cfg['model'])}</b> · "
        f"{cfg['repeats']} repeat(s) per variant · started {_esc(result['started'])}</p>",
    ]
    if scripted:
        parts.append(
            "<div class='banner'><b>Scripted provider - no language model ran.</b> These runs "
            "replay reference migrations through the real harness (sandbox, equivalence, "
            "budgets, traces). They validate the pipeline; they are <b>not</b> a measure of "
            "model capability, and differences between variants reflect pipeline structure "
            "only (e.g. migration order), not agent behaviour.</div>"
        )

    if full:
        parts.append("<div class='tiles'>")
        for k, v in (
            ("modules accepted (full)", f"{full['accepted']:.0f}/{full['total']}"),
            ("success rate (full)", _pct(full["success"])),
            ("cost (full)", _money(full["cost"])),
            ("cost per accepted module", _money(full["cpa"])),
            (
                "mean attempts to pass",
                DASH if full["attempts"] is None else f"{full['attempts']:.2f}",
            ),
        ):
            parts.append(
                f"<div class='tile'><div class='v'>{v}</div><div class='k'>{k}</div></div>"
            )
        parts.append("</div>")

    parts.append("<h2>Ablations</h2><div class='card'>")
    parts.append(
        bar_chart(
            [(v, agg[v]["success"], f"{v}: {_pct(agg[v]['success'])} accepted") for v in variants],
            max_value=1.0,
            fmt=_pct,
        )
    )
    head = (
        "<tr><th>variant</th><th>graph</th><th>folding</th><th class='n'>accepted</th>"
        "<th class='n'>success</th><th class='n'>Δ vs full</th><th class='n'>attempts</th>"
        "<th class='n'>cost</th><th class='n'>cost / accepted</th><th class='n'>tokens</th>"
        "<th class='n'>wall s</th></tr>"
    )
    rows = []
    for v in variants:
        a = agg[v]
        run_cfg = _first(result["runs"][v])["config"]
        delta = (
            DASH if not full or v == "full" else f"{(a['success'] - full['success']) * 100:+.0f} pt"
        )
        attempts = DASH if a["attempts"] is None else f"{a['attempts']:.2f}"
        rows.append(
            f"<tr><td>{_esc(v)}</td><td>{'on' if run_cfg.get('use_graph') else 'off'}</td>"
            f"<td>{'on' if run_cfg.get('use_folding') else 'off'}</td>"
            f"<td class='n'>{a['accepted']:.0f}/{a['total']}</td>"
            f"<td class='n'>{_pct(a['success'])}</td><td class='n'>{delta}</td>"
            f"<td class='n'>{attempts}</td>"
            f"<td class='n'>{_money(a['cost'])}</td><td class='n'>{_money(a['cpa'])}</td>"
            f"<td class='n'>{a['tokens']:,.0f}</td><td class='n'>{a['wall']:.0f}</td></tr>"
        )
    parts.append(f"<table>{head}{''.join(rows)}</table></div>")

    parts.append("<h2>Cost per accepted module</h2><div class='card'>")
    parts.append(
        bar_chart(
            [(v, agg[v]["cpa"] or 0.0, f"{v}: {_money(agg[v]['cpa'])}") for v in variants],
            fmt=_money,
        )
    )
    parts.append("</div>")

    cats = [c for c in TAXONOMY if any(agg[v]["failures"].get(c) for v in variants)]
    parts.append("<h2>Failure taxonomy</h2><div class='card'>")
    parts.append(stacked_chart([(v, agg[v]["failures"]) for v in variants], cats[:8]))
    if cats:
        head = "<tr><th>variant</th>" + "".join(f"<th class='n'>{_esc(c)}</th>" for c in cats)
        body = "".join(
            "<tr><td>"
            + _esc(v)
            + "</td>"
            + "".join(f"<td class='n'>{agg[v]['failures'].get(c, 0):g}</td>" for c in cats)
            + "</tr>"
            for v in variants
        )
        parts.append(f"<table>{head}</tr>{body}</table>")
    parts.append("</div>")

    parts.append(
        "<h2>Spend by role</h2><div class='card'><table><tr><th>variant</th>"
        + "".join(
            f"<th class='n'>{r}</th>" for r in ("analyst", "test_writer", "translator", "reviewer")
        )
        + "</tr>"
    )
    for v in variants:
        parts.append(
            "<tr><td>"
            + _esc(v)
            + "</td>"
            + "".join(
                f"<td class='n'>{_money(agg[v]['roles'].get(r, 0.0))}</td>"
                for r in ("analyst", "test_writer", "translator", "reviewer")
            )
            + "</tr>"
        )
    parts.append("</table></div>")

    parts.append("<h2>Modules</h2>")
    for v in variants:
        for run in result["runs"][v]:
            parts.append(
                f"<div class='card'><b>{_esc(v)}</b> · run "
                f"<code>{_esc(run['run_id'])}</code>{module_table(run)}</div>"
            )
    return _page(f"cobalt eval {result['eval_id']}", "".join(parts))


def render_runs(runs: list[dict[str, Any]]) -> str:
    """HTML for one or more plain runs (``cobalt report RUN...``)."""
    parts = ["<h1>cobalt runs</h1>"]
    for run in runs:
        parts.append(
            f"<h2>{_esc(run['run_id'])}</h2><p class='sub'>corpus {_esc(run['corpus'])} · provider"
            f" {_esc(run['config'].get('provider'))} · accepted {run['accepted']}/"
            f"{run['modules_total']} · cost {_money(run['cost_usd'])}</p>"
            f"<div class='card'>{module_table(run)}</div>"
        )
    return _page("cobalt runs", "".join(parts))


def _page(title: str, body: str) -> str:
    light = " ".join(f"--s{i + 1}:{c};" for i, c in enumerate(SERIES_LIGHT))
    dark = " ".join(f"--s{i + 1}:{c};" for i, c in enumerate(SERIES_DARK))
    css = CSS % {"light": light, "dark": dark}
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>{css}</style></head><body><main>{body}"
        "</main></body></html>\n"
    )


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _sum_counts(dicts: list[dict[str, float]], n: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v / n
    return out
