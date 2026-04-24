"""Post-run HTML report generator.

Produces a single-file HTML report summarizing everything the agent did and why.
Uses Plotly for charts and tables, no external dependencies beyond what's already installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from procureai.planner import (
    AllocationPlan,
    Conflict,
    DecisionLog,
    OptionMatrix,
)
from procureai.utils.db import ScenarioData

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

# ---------------------------------------------------------------------------
# Run data bundle — everything needed to generate the report
# ---------------------------------------------------------------------------


@dataclass
class RunData:
    """All data from a single agent run, collected for report generation."""

    scenario: ScenarioData
    run_id: str
    model_name: str
    gap_df: pd.DataFrame
    option_matrix: OptionMatrix
    plan: AllocationPlan
    conflicts: list[Conflict]
    decision_log: DecisionLog
    placed_orders: list[dict] = field(default_factory=list)
    placed_alerts: list[str] = field(default_factory=list)
    constraints: list = field(default_factory=list)
    plan_only: bool = False


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

COLORS = {
    "primary": "#2c3e50",
    "accent": "#3498db",
    "success": "#27ae60",
    "warning": "#f39c12",
    "danger": "#e74c3c",
    "muted": "#95a5a6",
    "bg": "#ecf0f1",
    "card": "#ffffff",
}


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------


def _section(title: str, content: str, icon: str = "") -> str:
    prefix = f"{icon} " if icon else ""
    return f'<div class="section"><h2>{prefix}{title}</h2>{content}</div>\n'


def _make_table_html(
    headers: list[str], rows: list[list], col_styles: dict | None = None
) -> str:
    """Build a simple HTML table without Plotly overhead."""
    html = '<table class="data-table"><thead><tr>'
    for h in headers:
        html += f"<th>{h}</th>"
    html += "</tr></thead><tbody>"
    for row in rows:
        html += "<tr>"
        for i, cell in enumerate(row):
            style = col_styles.get(i, "") if col_styles else ""
            html += f'<td style="{style}">{cell}</td>'
        html += "</tr>"
    html += "</tbody></table>"
    return html


def _scenario_overview(data: RunData) -> str:
    s = data.scenario
    n_products = len(s.products)
    n_components = len(s.components)
    n_suppliers = len(s.suppliers)
    n_orders = len(s.production_schedule)

    cards = [
        ("Scenario Date", s.current_date),
        ("Products", str(n_products)),
        ("Components", str(n_components)),
        ("Suppliers", str(n_suppliers)),
        ("Production Orders", str(n_orders)),
        ("Model", data.model_name),
        ("Run ID", data.run_id),
    ]

    html = '<div class="metrics-grid">'
    for label, value in cards:
        html += f'<div class="metric-card"><div class="metric-value">{value}</div><div class="metric-label">{label}</div></div>'
    html += "</div>"
    html += f'<p class="scenario-desc">{s.description}</p>'
    return _section("Scenario Overview", html)


def _gap_analysis(data: RunData) -> str:
    if data.gap_df.empty:
        return _section("Gap Analysis", "<p>No shortfalls identified.</p>")

    headers = ["Component", "Total Needed", "On Hand", "Incoming", "Gap", "Needed By"]
    rows = []
    for _, r in data.gap_df.iterrows():
        gap_class = "danger-text" if r["gap"] > 50 else "warning-text"
        rows.append(
            [
                f"<strong>{r['component_id']}</strong>",
                f"{int(r['total_needed']):,}",
                f"{int(r['on_hand']):,}",
                f"{int(r['incoming']):,}",
                f'<span class="{gap_class}">{int(r["gap"]):,}</span>',
                r["earliest_needed_by"],
            ]
        )

    html = _make_table_html(headers, rows)
    html += f'<p class="summary-note">{len(data.gap_df)} component shortfalls identified</p>'
    return _section("Gap Analysis", html)


def _option_matrix_section(data: RunData) -> str:
    sections_html = ""

    for comp_id in data.option_matrix.component_priorities:
        options = data.option_matrix.options.get(comp_id, [])
        if not options:
            sections_html += f'<div class="component-block"><h3>{comp_id}</h3><p class="muted">No eligible suppliers</p></div>'
            continue

        # Find which supplier(s) were allocated
        allocated_sids = {
            a.supplier_id for a in data.plan.allocations if a.component_id == comp_id
        }

        headers = [
            "Supplier",
            "Price",
            "Lead Time",
            "Delivery",
            "On Time?",
            "Fitness",
            "Domestic",
            "Tier",
            "Sustainability",
            "Selected",
        ]
        rows = []
        for opt in options:
            is_selected = opt.supplier_id in allocated_sids
            selected_badge = (
                '<span class="badge badge-selected">SELECTED</span>'
                if is_selected
                else ""
            )

            if opt.days_late == 0:
                time_badge = '<span class="badge badge-success">On Time</span>'
            else:
                time_badge = (
                    f'<span class="badge badge-danger">{opt.days_late}d late</span>'
                )

            domestic_flag = "Yes" if opt.is_domestic else "No"
            fitness_bar = f'<div class="fitness-bar"><div class="fitness-fill" style="width:{opt.fitness_score * 100:.0f}%"></div><span>{opt.fitness_score:.2f}</span></div>'

            rows.append(
                [
                    f'<strong>{opt.supplier_id}</strong><br><span class="muted">{opt.supplier_name}</span>',
                    f"${opt.unit_price:,.2f}",
                    f"{opt.lead_time_days}d",
                    opt.delivery_date,
                    time_badge,
                    fitness_bar,
                    domestic_flag,
                    opt.relationship_tier,
                    opt.sustainability_rating,
                    selected_badge,
                ]
            )

        # Gap info for this component
        gap_rows = data.gap_df[data.gap_df["component_id"] == comp_id]
        gap_info = ""
        if not gap_rows.empty:
            gr = gap_rows.iloc[0]
            gap_info = f" &mdash; Gap: {int(gr['gap']):,} units, needed by {gr['earliest_needed_by']}"

        table = _make_table_html(headers, rows)
        sections_html += (
            f'<div class="component-block"><h3>{comp_id}{gap_info}</h3>{table}</div>'
        )

    return _section("Supplier Options Considered", sections_html)


def _allocations_section(data: RunData) -> str:
    if not data.plan.allocations:
        return _section("Allocation Plan", "<p>No allocations made.</p>")

    # Summary metrics
    total_spend = 0.0
    supplier_spend: dict[str, float] = {}
    headers = [
        "Component",
        "Supplier",
        "Quantity",
        "Unit Price",
        "Total Cost",
        "Expedite",
        "Rationale",
    ]
    rows = []

    for alloc in data.plan.allocations:
        opts = data.option_matrix.options.get(alloc.component_id, [])
        opt = next((o for o in opts if o.supplier_id == alloc.supplier_id), None)
        unit_price = opt.unit_price if opt else 0.0
        cost = unit_price * alloc.quantity
        total_spend += cost
        supplier_spend[alloc.supplier_id] = (
            supplier_spend.get(alloc.supplier_id, 0) + cost
        )

        expedite = (
            '<span class="badge badge-warning">EXPEDITE</span>'
            if alloc.expedite
            else ""
        )
        rows.append(
            [
                f"<strong>{alloc.component_id}</strong>",
                alloc.supplier_id,
                f"{alloc.quantity:,}",
                f"${unit_price:,.2f}",
                f"${cost:,.2f}",
                expedite,
                f'<span class="rationale">{alloc.rationale}</span>',
            ]
        )

    table = _make_table_html(headers, rows)

    # Spend by supplier bar chart
    if supplier_spend:
        fig = go.Figure(
            data=[
                go.Bar(
                    x=list(supplier_spend.values()),
                    y=list(supplier_spend.keys()),
                    orientation="h",
                    marker_color=COLORS["accent"],
                    text=[f"${v:,.0f}" for v in supplier_spend.values()],
                    textposition="outside",
                )
            ]
        )
        fig.update_layout(
            title="Spend by Supplier",
            xaxis_title="Total Cost ($)",
            height=max(len(supplier_spend) * 40 + 120, 250),
            margin=dict(l=120, r=80, t=50, b=40),
            plot_bgcolor="white",
        )
        chart_html = fig.to_html(full_html=False, include_plotlyjs=False)
    else:
        chart_html = ""

    mode_label = "(plan-only mode)" if data.plan_only else ""
    summary = (
        f'<div class="metrics-grid">'
        f'<div class="metric-card"><div class="metric-value">{len(data.plan.allocations)}</div><div class="metric-label">Allocations {mode_label}</div></div>'
        f'<div class="metric-card"><div class="metric-value">${total_spend:,.2f}</div><div class="metric-label">Total Estimated Spend</div></div>'
        f'<div class="metric-card"><div class="metric-value">{len(supplier_spend)}</div><div class="metric-label">Suppliers Used</div></div>'
        f"</div>"
    )

    return _section("Allocation Plan", summary + table + chart_html)


def _conflicts_section(data: RunData) -> str:
    if not data.conflicts:
        return _section(
            "Conflicts & Resolutions",
            '<p class="muted">No conflicts detected — clean plan.</p>',
        )

    # Group by type
    type_counts: dict[str, int] = {}
    for cf in data.conflicts:
        t = cf.type.value
        type_counts[t] = type_counts.get(t, 0) + 1

    summary = '<div class="conflict-summary">'
    for t, count in sorted(type_counts.items()):
        badge_class = (
            "badge-danger"
            if "INFEASIBLE" in t or "NO_ELIGIBLE" in t
            else "badge-warning"
        )
        summary += f'<span class="badge {badge_class}">{t}: {count}</span> '
    summary += "</div>"

    # Find LLM resolutions from decision log
    resolutions: dict[str, str] = {}
    for d in data.decision_log.entries:
        if d.source == "llm_reviewer" and d.conflict_id:
            resolutions[d.conflict_id] = d.rationale

    headers = [
        "ID",
        "Type",
        "Component",
        "Description",
        "Greedy Default",
        "LLM Resolution",
    ]
    rows = []
    for i, cf in enumerate(data.conflicts):
        cf_id = f"CF-{i + 1:03d}"
        resolution = resolutions.get(
            cf_id, '<span class="muted">No LLM override</span>'
        )

        type_class = (
            "danger-text"
            if "INFEASIBLE" in cf.type.value or "NO_ELIGIBLE" in cf.type.value
            else "warning-text"
        )
        rows.append(
            [
                f"<strong>{cf_id}</strong>",
                f'<span class="{type_class}">{cf.type.value}</span>',
                cf.component_id,
                cf.description,
                cf.greedy_choice.get("action", "?") if cf.greedy_choice else "?",
                f'<span class="rationale">{resolution}</span>',
            ]
        )

    table = _make_table_html(headers, rows)
    return _section("Conflicts & Resolutions", summary + table)


def _alerts_section(data: RunData) -> str:
    alerts = (
        data.placed_alerts
        if data.placed_alerts
        else [a.description for a in data.plan.alerts]
    )
    if not alerts:
        return _section("Alerts", '<p class="muted">No alerts generated.</p>')

    html = '<ul class="alert-list">'
    for a in alerts:
        icon_class = (
            "alert-deadline"
            if "DEADLINE" in a.upper()
            else "alert-budget"
            if "BUDGET" in a.upper()
            else "alert-info"
        )
        html += f'<li class="{icon_class}">{a}</li>'
    html += "</ul>"
    return _section("Alerts", html)


def _decision_log_section(data: RunData) -> str:
    entries = data.decision_log.entries
    if not entries:
        return _section("Decision Log", '<p class="muted">No decisions recorded.</p>')

    # Summary
    greedy_count = sum(1 for d in entries if d.source == "greedy_algorithm")
    llm_count = sum(1 for d in entries if d.source == "llm_reviewer")

    summary = (
        f'<div class="metrics-grid">'
        f'<div class="metric-card"><div class="metric-value">{len(entries)}</div><div class="metric-label">Total Decisions</div></div>'
        f'<div class="metric-card"><div class="metric-value">{greedy_count}</div><div class="metric-label">Algorithmic</div></div>'
        f'<div class="metric-card"><div class="metric-value">{llm_count}</div><div class="metric-label">LLM Reviewer</div></div>'
        f"</div>"
    )

    headers = ["#", "Component", "Action", "Source", "Rationale", "Conflict", "Details"]
    rows = []
    for i, d in enumerate(entries, 1):
        source_badge = (
            '<span class="badge badge-algo">algorithm</span>'
            if d.source == "greedy_algorithm"
            else '<span class="badge badge-llm">llm</span>'
        )
        conflict_link = d.conflict_id or ""

        # Format details — show key fields, not raw JSON
        if isinstance(d.details, dict):
            detail_parts = []
            for k, v in d.details.items():
                if k in (
                    "supplier_id",
                    "quantity",
                    "unit_price",
                    "conflict_type",
                    "moq",
                    "gap",
                ):
                    detail_parts.append(f"{k}: {v}")
            details_str = ", ".join(detail_parts) if detail_parts else ""
        else:
            details_str = str(d.details) if d.details else ""

        rows.append(
            [
                str(i),
                f"<strong>{d.component_id}</strong>",
                d.action,
                source_badge,
                f'<span class="rationale">{d.rationale}</span>',
                conflict_link,
                f'<span class="muted">{details_str}</span>',
            ]
        )

    table = _make_table_html(headers, rows)
    return _section("Decision Log", summary + table)


# ---------------------------------------------------------------------------
# Full report assembly
# ---------------------------------------------------------------------------

_CSS = """
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    margin: 0; padding: 20px; background: #ecf0f1; color: #2c3e50;
}
.report-header {
    background: linear-gradient(135deg, #2c3e50, #34495e);
    color: white; padding: 30px 40px; border-radius: 12px; margin-bottom: 24px;
}
.report-header h1 { margin: 0 0 8px 0; font-size: 28px; }
.report-header p { margin: 0; opacity: 0.85; font-size: 14px; }
.section {
    background: white; border-radius: 10px; padding: 24px;
    margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.section h2 {
    margin: 0 0 16px 0; color: #2c3e50;
    border-bottom: 3px solid #3498db; padding-bottom: 10px; font-size: 20px;
}
.section h3 { margin: 20px 0 10px 0; color: #34495e; font-size: 16px; }
.metrics-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px; margin-bottom: 20px;
}
.metric-card {
    background: #f8f9fa; border-radius: 8px; padding: 16px; text-align: center;
    border: 1px solid #e9ecef;
}
.metric-value { font-size: 24px; font-weight: 700; color: #2c3e50; }
.metric-label { font-size: 12px; color: #7f8c8d; margin-top: 4px; text-transform: uppercase; }
.data-table {
    width: 100%; border-collapse: collapse; font-size: 13px; margin: 12px 0;
}
.data-table th {
    background: #34495e; color: white; padding: 10px 12px;
    text-align: left; font-weight: 600; font-size: 12px; text-transform: uppercase;
}
.data-table td { padding: 8px 12px; border-bottom: 1px solid #ecf0f1; vertical-align: top; }
.data-table tr:hover { background: #f8f9fa; }
.data-table tr:nth-child(even) { background: #fafbfc; }
.data-table tr:nth-child(even):hover { background: #f0f3f5; }
.badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 600; text-transform: uppercase;
}
.badge-success { background: #d5f5e3; color: #27ae60; }
.badge-danger { background: #fadbd8; color: #e74c3c; }
.badge-warning { background: #fdebd0; color: #e67e22; }
.badge-selected { background: #d4efdf; color: #1e8449; }
.badge-algo { background: #d6eaf8; color: #2471a3; }
.badge-llm { background: #e8daef; color: #7d3c98; }
.danger-text { color: #e74c3c; font-weight: 600; }
.warning-text { color: #e67e22; font-weight: 600; }
.muted { color: #95a5a6; font-size: 12px; }
.rationale { font-style: italic; color: #555; font-size: 12px; }
.scenario-desc { color: #7f8c8d; font-size: 14px; margin-top: 12px; }
.component-block {
    border: 1px solid #ecf0f1; border-radius: 8px; padding: 16px;
    margin-bottom: 16px; background: #fafbfc;
}
.component-block h3 { margin-top: 0; }
.fitness-bar {
    display: inline-flex; align-items: center; gap: 6px; min-width: 100px;
}
.fitness-fill {
    height: 8px; background: #3498db; border-radius: 4px;
    min-width: 4px; transition: width 0.3s;
}
.fitness-bar span { font-size: 12px; color: #555; }
.conflict-summary { margin-bottom: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
.alert-list { list-style: none; padding: 0; }
.alert-list li {
    padding: 10px 14px; margin-bottom: 8px; border-radius: 6px;
    border-left: 4px solid #f39c12; background: #fef9e7; font-size: 13px;
}
.alert-deadline { border-left-color: #e74c3c !important; background: #fdedec !important; }
.alert-budget { border-left-color: #f39c12 !important; background: #fef9e7 !important; }
.alert-info { border-left-color: #3498db !important; background: #ebf5fb !important; }
.summary-note { color: #7f8c8d; font-size: 13px; margin-top: 8px; }
"""


def generate_report(data: RunData) -> str:
    """Generate a full HTML report and return the file path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"report_{data.scenario.db_path.stem}_{data.run_id}.html"

    mode_note = " (plan-only)" if data.plan_only else ""
    header = (
        f'<div class="report-header">'
        f"<h1>ProcureAI Run Report{mode_note}</h1>"
        f"<p>{data.scenario.db_path.stem} &mdash; {data.scenario.current_date} &mdash; "
        f"Run {data.run_id} &mdash; Model: {data.model_name}</p>"
        f"</div>"
    )

    body = ""
    body += _scenario_overview(data)
    body += _gap_analysis(data)
    body += _option_matrix_section(data)
    body += _allocations_section(data)
    body += _conflicts_section(data)
    body += _alerts_section(data)
    body += _decision_log_section(data)

    html = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>ProcureAI Report - {data.run_id}</title>"
        f'<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>'
        f"<style>{_CSS}</style>"
        f"</head><body>"
        f"{header}{body}"
        f"</body></html>"
    )

    output_path.write_text(html)
    return str(output_path)
