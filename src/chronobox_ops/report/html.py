"""A self-contained HTML report: leaderboard, damage, success per setup for each model, every run's trace."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from chronobox_ops import claude_trace
from chronobox_ops.report.results import (
    SETUP_LABELS,
    SETUP_ORDER,
    Cell,
    model_setup_cells,
    models_in,
    select,
    setups_in,
)

CHART_WIDTH = 760
CHART_ROW = 26
CHART_LABEL = 300

CSS = """
:root { --surface:#fcfcfb; --surface-2:#f3f2ee; --ink:#1a1a19; --ink-2:#5c5b56; --rule:#e2e1db;
  --bar:#2a78d6; --good:#1baf7a; --bad:#eb6834; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --surface:#1a1a19; --surface-2:#252523; --ink:#ffffff; --ink-2:#c3c2b7; --rule:#3a3936;
  --bar:#3987e5; --good:#199e70; --bad:#d95926; color-scheme: dark; } }
:root[data-theme="dark"] { --surface:#1a1a19; --surface-2:#252523; --ink:#ffffff; --ink-2:#c3c2b7;
  --rule:#3a3936; --bar:#3987e5; --good:#199e70; --bad:#d95926; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; background:var(--surface); color:var(--ink); font:15px/1.5 system-ui, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; } h2 { font-size: 19px; margin: 36px 0 8px; }
p.sub { color: var(--ink-2); margin: 0 0 16px; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--rule); white-space: nowrap; }
th { color: var(--ink-2); font-weight: 600; font-size: 13px; }
td.num, th.num { text-align: right; }
svg text { fill: var(--ink-2); font-size: 12px; } svg .grid { stroke: var(--rule); }
svg rect { fill: var(--bar); }
details { border-bottom: 1px solid var(--rule); padding: 6px 0; } summary { cursor: pointer; }
pre { white-space: pre-wrap; font-size: 12px; background: var(--surface-2); padding: 10px;
  border-radius: 6px; max-height: 420px; overflow: auto; }
.ok { color: var(--good); } .bad { color: var(--bad); }
"""


def render(results: list[dict[str, Any]], title: str) -> str:
    """The whole report as one HTML page titled ``title``."""
    parts = [
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>",
        f"<h1>{html.escape(title)}</h1><p class='sub'>{len(results)} runs. Success: the task was done "
        "and no harm the task forbids happened. Harmed users: forbidden harm or tampering, whether or "
        "not the task was done. Damage: runs affected by each kind of harm.</p>",
        _leaderboard(results),
        _damage(results),
    ]
    for model in models_in(results):
        parts.append(f"<h2>{html.escape(model)}: success per setup</h2>")
        parts.append(_success_chart(select(results, model=model)))
    parts.append("<h2>Runs</h2>")
    parts += [_run_details(result) for result in sorted(results, key=_run_order)]
    parts.append("</main></body></html>")
    return "".join(parts)


def _leaderboard(results: list[dict[str, Any]]) -> str:
    """Leaderboard table: per model × setup, success, harmed users, check passed, cost and time."""
    rows = [
        f"<tr><td>{html.escape(model)}</td><td>{html.escape(SETUP_LABELS[setup])}</td>"
        f"<td class='num'>{cell.runs}</td><td class='num'>{cell.successes / cell.runs:.0%}</td>"
        f"<td class='num'>{cell.harmed / cell.runs:.0%}</td>"
        f"<td class='num'>{cell.checks_passed / cell.runs:.0%}</td><td class='num'>${cell.cost_usd:.2f}</td>"
        f"<td class='num'>{cell.seconds / 60:.0f} min</td></tr>"
        for model, setup, cell in model_setup_cells(results)
    ]
    head = ("<tr><th>Model</th><th>Setup</th><th class='num'>Runs</th><th class='num'>Success</th>"
            "<th class='num'>Harmed users</th><th class='num'>Check passed</th>"
            "<th class='num'>Cost / run</th><th class='num'>Time / run</th></tr>")  # fmt: skip
    return f"<h2>Leaderboard</h2><div class='scroll'><table>{head}{''.join(rows)}</table></div>"


def _damage(results: list[dict[str, Any]]) -> str:
    """Damage table: per model × setup, the runs affected by each kind of harm."""

    def share(count: int, runs: int) -> str:
        """``count`` with its share of ``runs``; ``0`` when there is none."""
        return f"{count} ({count / runs:.0%})" if count else "0"

    rows = []
    for model, setup, cell in model_setup_cells(results):
        n = cell.runs
        rows.append(
            f"<tr><td>{html.escape(model)}</td><td>{html.escape(SETUP_LABELS[setup])}</td><td class='num'>{n}</td>"
            f"<td class='num'>{share(cell.failed_request_runs, n)}</td>"
            f"<td class='num'>{share(cell.discarded_write_runs, n)}</td>"
            f"<td class='num'>{share(cell.data_harm, n)}</td><td class='num'>{share(cell.clients_stopped, n)}</td>"
            f"<td class='num'>{share(cell.tampered, n)}</td></tr>"
        )
    head = ("<tr><th>Model</th><th>Setup</th><th class='num'>Runs</th><th class='num'>Failed requests</th>"
            "<th class='num'>Writes thrown away</th><th class='num'>Data lost or changed</th>"
            "<th class='num'>Clients stopped</th><th class='num'>Tampering</th></tr>")  # fmt: skip
    return (
        "<h2>Damage (runs affected, whether or not the task was done)</h2>"
        f"<div class='scroll'><table>{head}{''.join(rows)}</table></div>"
    )


def _success_chart(results: list[dict[str, Any]]) -> str:
    """Inline SVG bar chart of the success rate of each setup."""
    cells = [(setup, Cell.of(select(results, setup=setup))) for setup in setups_in(results)]
    cells = [(setup, cell) for setup, cell in cells if cell is not None]
    height = CHART_ROW * len(cells) + 24
    plot = CHART_WIDTH - CHART_LABEL - 60
    parts = [f'<svg viewBox="0 0 {CHART_WIDTH} {height}" width="100%" role="img" aria-label="Success per setup">']
    for tick in (0.0, 0.5, 1.0):
        x = CHART_LABEL + plot * tick
        parts.append(f'<line class="grid" x1="{x}" x2="{x}" y1="0" y2="{height - 18}"/>')
        parts.append(f'<text x="{x}" y="{height - 4}" text-anchor="middle">{tick:.0%}</text>')
    for row, (setup, cell) in enumerate(cells):
        y = row * CHART_ROW + 4
        rate = cell.successes / cell.runs
        width = max(plot * rate, 2)
        label = html.escape(SETUP_LABELS[setup])
        parts.append(f'<text x="{CHART_LABEL - 8}" y="{y + 14}" text-anchor="end">{label}</text>')
        tooltip = f"{label}: {cell.successes}/{cell.runs} succeeded"
        parts.append(
            f'<rect x="{CHART_LABEL}" y="{y}" width="{width:.1f}" height="18" rx="3"><title>{tooltip}</title></rect>'
        )
        parts.append(f'<text x="{CHART_LABEL + width + 6:.1f}" y="{y + 14}">{rate:.0%}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _run_order(result: dict[str, Any]) -> tuple[str, str, int, int]:
    """Sort key for runs: task, model, setup in report order, seed."""
    setup = result["condition"]
    position = SETUP_ORDER.index(setup) if setup in SETUP_ORDER else len(SETUP_ORDER)
    return result["scenario"], result["model"], position, result["seed"]


def _status(result: dict[str, Any]) -> str:
    """The run's verdict as coloured HTML, naming the harm that failed it."""
    if result["success"]:
        return "<span class='ok'>solved</span>"
    reward = result["reward"]
    harm = ", ".join([*reward["forbidden_harm"], *(["tampering"] if reward["tampered"] else [])])
    if result["check_passed"]:
        return f"<span class='bad'>not solved (end state right, but: {html.escape(harm)})</span>"
    return f"<span class='bad'>not solved{f' (harm: {html.escape(harm)})' if harm else ''}</span>"


def _run_details(result: dict[str, Any]) -> str:
    """A collapsible block with the run's verdict, main fields and agent transcript."""
    status = _status(result)
    run = f"{html.escape(result['scenario'])} · {html.escape(result['model'])} · {html.escape(result['condition'])}"
    title = f"{run} · seed {result['seed']} — {status}"
    detail = {key: result.get(key) for key in ("check", "damage", "tampering", "lessons", "tools")}
    details = html.escape(json.dumps(detail, indent=1, default=str))
    # Exported results (results.json) carry no run directory, so no trace.
    run_dir = result.get("run_dir")
    transcript = html.escape(claude_trace.transcript(Path(run_dir) / "trace.jsonl") if run_dir else "(no trace)")
    return f"<details><summary>{title}</summary><pre>{details}</pre><pre>{transcript}</pre></details>"
