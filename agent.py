"""
ProcureAI — Autonomous procurement agent for Apex Manufacturing.

Usage:
    python agent.py --scenario <scenario.sqlite>
    python agent.py --scenario <scenario.sqlite> --clean
    python agent.py --scenario <scenario.sqlite> --list-runs
"""

import logging
import sys

import click

from procureai.agents.graph import build_agent
from procureai.config import AgentConfig, get_chat_model
from procureai.constraints import extract_constraints
from procureai.pipeline import gap_analysis
from procureai.utils.db import (
    clean_run,
    finalize_run,
    list_runs,
    load_scenario,
    start_run,
    write_alerts,
    write_purchase_orders,
)


@click.command()
@click.option(
    "--scenario",
    required=True,
    type=click.Path(exists=True, readable=True),
    help="Path to the scenario SQLite database file.",
)
@click.option(
    "--model",
    default=None,
    help="Override the model name (e.g. claude-sonnet-4-6).",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose logging.",
)
@click.option(
    "--clean",
    "clean_flag",
    is_flag=True,
    default=False,
    help="Remove rows from the last agent run, then exit.",
)
@click.option(
    "--clean-run-id",
    default=None,
    help="Remove rows from a specific run ID, then exit.",
)
@click.option(
    "--list-runs",
    "list_runs_flag",
    is_flag=True,
    default=False,
    help="List all recorded agent runs for this scenario, then exit.",
)
def main(
    scenario: str,
    model: str | None,
    verbose: bool,
    clean_flag: bool,
    clean_run_id: str | None,
    list_runs_flag: bool,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # --- Utility modes (no agent run) ---

    if list_runs_flag:
        runs = list_runs(scenario)
        if runs.empty:
            click.echo("No recorded runs.")
        else:
            click.echo(runs.to_string(index=False))
        return

    if clean_flag or clean_run_id:
        result = clean_run(scenario, run_id=clean_run_id)
        if result["run_id"] is None:
            click.echo("No runs to clean.")
        else:
            click.echo(
                f"Cleaned run {result['run_id']}: "
                f"{result['orders_deleted']} orders, "
                f"{result['alerts_deleted']} alerts removed."
            )
        return

    # --- Agent run ---

    # 1. Load scenario
    click.echo(f"Loading scenario: {scenario}")
    scenario_data = load_scenario(scenario)
    click.echo(f"  Date: {scenario_data.current_date}")
    click.echo(f"  Description: {scenario_data.description}")

    # 2. Config + LLM
    config = AgentConfig(**({"model_name": model} if model else {}))
    llm = get_chat_model(config)
    click.echo(f"  Model: {config.model_provider}/{config.model_name}")

    # 3. Start run log
    run_id = start_run(scenario_data.db_path, model=config.model_name)
    click.echo(f"  Run ID: {run_id}")

    # 4. Extract constraints from PDFs
    click.echo("Extracting constraints from policy documents...")
    constraints = extract_constraints(config.policy_dir, config.memo_dir, llm)
    click.echo(f"  {len(constraints)} constraints loaded")
    if verbose:
        for c in constraints:
            click.echo(f"    [{c.type.value}] {c.description or c.params}")

    # 5. Gap analysis
    gap_df = gap_analysis(scenario_data)
    click.echo(f"  {len(gap_df)} component shortfalls identified")
    if verbose and not gap_df.empty:
        click.echo(gap_df.to_string(index=False))

    if gap_df.empty:
        click.echo("No shortfalls — nothing to procure.")
        finalize_run(scenario_data.db_path, run_id, 0, 0, 0.0)
        return

    # 6. Build and run agent
    click.echo("\nStarting procurement agent...")
    graph, ctx = build_agent(llm, scenario_data, constraints)

    invoke_config: dict = {}
    if verbose:
        from procureai.agents.callbacks import RichCallbackHandler

        invoke_config["callbacks"] = [RichCallbackHandler()]

    result = graph.invoke(
        {"messages": [("user", "Begin procurement planning.")]},
        config=invoke_config,
    )

    # 7. Write results to DB (tagged with run_id)
    n_orders = write_purchase_orders(scenario_data.db_path, ctx.placed_orders, run_id)
    n_alerts = write_alerts(scenario_data.db_path, ctx.placed_alerts, run_id)

    # 8. Finalize run log
    total_spend = sum(o["unit_price"] * o["quantity"] for o in ctx.placed_orders)
    finalize_run(scenario_data.db_path, run_id, n_orders, n_alerts, total_spend)

    # 9. Summary
    click.echo("\n" + "=" * 60)
    click.echo(f"PROCUREMENT COMPLETE  (run: {run_id})")
    click.echo("=" * 60)
    click.echo(f"  Purchase orders placed: {n_orders}")
    click.echo(f"  Total spend: ${total_spend:,.2f}")
    click.echo(f"  Alerts generated: {n_alerts}")
    click.echo(f"  Remaining gaps: {len(ctx.gap_df)}")

    if ctx.placed_orders:
        click.echo("\nOrders:")
        for o in ctx.placed_orders:
            click.echo(
                f"  {o['po_number']}: {o['component_id']} × {o['quantity']} "
                f"from {o['supplier_id']} — ${o['unit_price'] * o['quantity']:,.2f} "
                f"(delivery: {o['expected_delivery_date']})"
            )

    if ctx.placed_alerts:
        click.echo("\nAlerts:")
        for a in ctx.placed_alerts:
            click.echo(f"  ⚠ {a}")

    if ctx.gap_df.empty:
        click.echo("\n✓ All shortfalls resolved.")
    else:
        click.echo(f"\n✗ {len(ctx.gap_df)} shortfalls remain:")
        click.echo(ctx.gap_df.to_string(index=False))

    click.echo(f"\nTo undo this run: python agent.py --scenario {scenario} --clean")


if __name__ == "__main__":
    main()
