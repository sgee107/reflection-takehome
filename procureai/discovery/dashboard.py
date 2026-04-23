"""Generate a standalone Plotly HTML dashboard for viewing scenario data."""

import webbrowser
from pathlib import Path

import click
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from procureai.utils.db import ScenarioData, load_scenario

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"


def make_table(df, title: str) -> go.Figure:
    """Create a Plotly table figure from a DataFrame."""
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No data", xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False, font=dict(size=16))
        fig.update_layout(title=title, height=150)
        return fig

    fig = go.Figure(
        data=[
            go.Table(
                header=dict(
                    values=[f"<b>{col}</b>" for col in df.columns],
                    fill_color="#2c3e50",
                    font=dict(color="white", size=12),
                    align="left",
                ),
                cells=dict(
                    values=[df[col].tolist() for col in df.columns],
                    fill_color=[["#f8f9fa", "white"] * ((len(df) + 1) // 2)],
                    font=dict(size=11),
                    align="left",
                    height=28,
                ),
            )
        ]
    )
    row_height = max(28 * len(df) + 80, 200)
    fig.update_layout(title=title, height=min(row_height, 800), margin=dict(l=20, r=20, t=50, b=20))
    return fig


def make_inventory_bar(scenario: ScenarioData) -> go.Figure:
    """Bar chart of inventory levels by component."""
    inv = scenario.inventory.merge(scenario.components[["component_id", "name"]], on="component_id")
    inv = inv.sort_values("quantity_on_hand", ascending=True)

    colors = ["#e74c3c" if qty < 20 else "#f39c12" if qty < 50 else "#27ae60" for qty in inv["quantity_on_hand"]]

    fig = go.Figure(
        data=[
            go.Bar(
                x=inv["quantity_on_hand"],
                y=inv["name"],
                orientation="h",
                marker_color=colors,
                text=inv["quantity_on_hand"],
                textposition="outside",
            )
        ]
    )
    fig.update_layout(
        title="Inventory Levels by Component",
        xaxis_title="Quantity on Hand",
        height=max(len(inv) * 35 + 100, 400),
        margin=dict(l=200, r=40, t=50, b=40),
    )
    return fig


def make_schedule_timeline(scenario: ScenarioData) -> go.Figure:
    """Gantt-style chart showing production schedule deadlines relative to current date."""
    sched = scenario.production_schedule.merge(
        scenario.products[["product_id", "name"]], on="product_id"
    )
    if sched.empty:
        fig = go.Figure()
        fig.add_annotation(text="No production orders", xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
        fig.update_layout(title="Production Schedule", height=200)
        return fig

    labels = [f"{row['order_id']}: {row['name']} (x{row['quantity']})" for _, row in sched.iterrows()]
    deadlines = sched["materials_needed_by"].tolist()
    current = scenario.current_date

    fig = go.Figure()
    for i, (label, deadline) in enumerate(zip(labels, deadlines)):
        fig.add_trace(
            go.Scatter(
                x=[current, deadline],
                y=[label, label],
                mode="lines+markers",
                marker=dict(size=10, symbol=["circle", "diamond"]),
                line=dict(width=3),
                name=label,
                showlegend=False,
            )
        )

    fig.add_shape(type="line", x0=current, x1=current, y0=-0.5, y1=len(sched) - 0.5, line=dict(dash="dash", color="blue", width=2))
    fig.add_annotation(x=current, y=len(sched) - 0.5, text=f"Today ({current})", showarrow=False, yshift=15, font=dict(color="blue"))
    fig.update_layout(
        title="Production Schedule — Material Deadlines",
        xaxis_title="Date",
        height=max(len(sched) * 60 + 150, 300),
        margin=dict(l=300, r=40, t=50, b=40),
    )
    return fig


def build_dashboard(scenario: ScenarioData) -> str:
    """Build a combined HTML dashboard and return the file path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"dashboard_{scenario.db_path.stem}.html"

    header = f"""
    <html><head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 20px; background: #ecf0f1; }}
        .header {{ background: #2c3e50; color: white; padding: 20px 30px; border-radius: 8px; margin-bottom: 20px; }}
        .header h1 {{ margin: 0 0 8px 0; }}
        .header p {{ margin: 0; opacity: 0.8; }}
        .section {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .section h2 {{ margin-top: 0; color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 8px; }}
    </style>
    </head><body>
    <div class="header">
        <h1>{scenario.db_path.stem}</h1>
        <p>Current Date: {scenario.current_date} &mdash; {scenario.description}</p>
    </div>
    """

    sections = []

    # Components table
    components_fig = make_table(scenario.components, "Components")
    sections.append(("Components", components_fig.to_html(full_html=False, include_plotlyjs=False)))

    # Inventory bar chart
    inv_fig = make_inventory_bar(scenario)
    sections.append(("Inventory", inv_fig.to_html(full_html=False, include_plotlyjs=False)))

    # Inventory table
    inv_table = scenario.inventory.merge(scenario.components[["component_id", "name"]], on="component_id")
    inv_table = inv_table[["component_id", "name", "quantity_on_hand", "warehouse_location"]]
    inv_table_fig = make_table(inv_table, "Inventory Details")
    sections.append(("Inventory Details", inv_table_fig.to_html(full_html=False, include_plotlyjs=False)))

    # Bill of Materials — join product and component names for readability
    bom_display = (
        scenario.bom
        .merge(scenario.products[["product_id", "name"]], on="product_id")
        .rename(columns={"name": "product"})
        .merge(scenario.components[["component_id", "name"]], on="component_id")
        .rename(columns={"name": "component"})
        [["product_id", "product", "component_id", "component", "quantity_per"]]
        .sort_values(["product_id", "component_id"])
    )
    bom_fig = make_table(bom_display, "Bill of Materials")
    sections.append(("Bill of Materials", bom_fig.to_html(full_html=False, include_plotlyjs=False)))

    # Production schedule timeline
    sched_fig = make_schedule_timeline(scenario)
    sections.append(("Production Schedule Timeline", sched_fig.to_html(full_html=False, include_plotlyjs=False)))

    # Production schedule table
    sched_table_fig = make_table(scenario.production_schedule, "Production Schedule")
    sections.append(("Production Schedule", sched_table_fig.to_html(full_html=False, include_plotlyjs=False)))

    # Purchase orders
    po_fig = make_table(scenario.purchase_orders, "Purchase Orders")
    sections.append(("Purchase Orders", po_fig.to_html(full_html=False, include_plotlyjs=False)))

    body = ""
    for title, html in sections:
        body += f'<div class="section"><h2>{title}</h2>{html}</div>\n'

    full_html = f"""
    {header}
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    {body}
    </body></html>
    """

    output_path.write_text(full_html)
    return str(output_path)


@click.command()
@click.option("--scenario", type=click.Path(exists=True), required=True, help="Path to a scenario .sqlite file")
@click.option("--open/--no-open", default=True, help="Open in browser after generating")
def main(scenario: str, open: bool) -> None:
    """Generate a Plotly HTML dashboard for a scenario database."""
    s = load_scenario(scenario)
    path = build_dashboard(s)
    click.echo(f"Dashboard written to: {path}")
    if open:
        webbrowser.open(f"file://{path}")


if __name__ == "__main__":
    main()
