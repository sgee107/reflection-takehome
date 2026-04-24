"""
ProcureAI — Autonomous procurement agent for Apex Manufacturing.

Usage:
    python agent.py --scenario <scenario.sqlite>
    python agent.py --scenario <scenario.sqlite> --plan-only
    python agent.py --scenario <scenario.sqlite> --clean
    python agent.py --scenario <scenario.sqlite> --list-runs
"""

import logging
import webbrowser

import click

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
    write_decision_log,
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
@click.option(
    "--plan-only",
    "plan_only",
    is_flag=True,
    default=False,
    help="Run greedy planner + LLM review, print plan, but do not execute orders.",
)
def main(
    scenario: str,
    model: str | None,
    verbose: bool,
    clean_flag: bool,
    clean_run_id: str | None,
    list_runs_flag: bool,
    plan_only: bool,
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
    constraints = extract_constraints(
        config.policy_dir, config.memo_dir, llm, scenario_data
    )
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

    # --- Run planner-executor pipeline ---

    _run_planner_executor(
        llm,
        config.model_name,
        scenario_data,
        constraints,
        gap_df,
        run_id,
        scenario,
        verbose,
        plan_only,
    )


def _run_planner_executor(
    llm,
    model_name,
    scenario_data,
    constraints,
    gap_df,
    run_id,
    scenario,
    verbose,
    plan_only,
):
    """Greedy planner → LLM reviewer → deterministic executor."""
    from procureai.agents.executor import execute_plan
    from procureai.agents.planner import build_reviewer_agent
    from procureai.agents.tools import ProcurementContext, build_tools
    from procureai.planner import (
        build_option_matrix,
        detect_cross_component_conflicts,
        greedy_allocate,
    )
    from procureai.report import RunData, generate_report

    # Phase 1: Greedy planner
    click.echo("\n--- Phase 1: Greedy Planner ---")
    click.echo("  Building option matrix...")
    option_matrix = build_option_matrix(scenario_data, constraints, gap_df)

    # Report on options found
    total_options = sum(len(opts) for opts in option_matrix.options.values())
    no_options = [cid for cid, opts in option_matrix.options.items() if not opts]
    click.echo(
        f"  {total_options} supplier options across {len(option_matrix.options)} components"
    )
    if no_options:
        click.echo(f"  Warning: No eligible suppliers for: {', '.join(no_options)}")

    click.echo("  Running greedy allocation...")
    plan, conflicts, decision_log = greedy_allocate(option_matrix, constraints, gap_df)
    detect_cross_component_conflicts(
        plan, conflicts, decision_log, constraints, option_matrix
    )

    # Report allocations
    total_est_spend = 0.0
    for alloc in plan.allocations:
        opts = option_matrix.options.get(alloc.component_id, [])
        opt = next((o for o in opts if o.supplier_id == alloc.supplier_id), None)
        if opt:
            total_est_spend += opt.unit_price * alloc.quantity

    click.echo(
        f"  Allocations: {len(plan.allocations)}  |  Est. spend: ${total_est_spend:,.2f}"
    )
    click.echo(f"  Alerts: {len(plan.alerts)}  |  Conflicts: {len(conflicts)}")

    if conflicts:
        # Show conflict breakdown
        from collections import Counter

        type_counts = Counter(cf.type.value for cf in conflicts)
        for ctype, count in sorted(type_counts.items()):
            click.echo(f"    {ctype}: {count}")

    if verbose:
        click.echo(f"\n  Decision log (greedy):\n  {decision_log.summary()}")
        for alloc in plan.allocations:
            opts = option_matrix.options.get(alloc.component_id, [])
            opt = next((o for o in opts if o.supplier_id == alloc.supplier_id), None)
            price = opt.unit_price if opt else 0
            click.echo(
                f"    {alloc.component_id}: {alloc.supplier_id} x{alloc.quantity} "
                f"${price * alloc.quantity:,.2f}"
            )

    # Phase 2: LLM reviewer (if conflicts exist)
    click.echo("\n--- Phase 2: LLM Reviewer ---")
    if conflicts:
        click.echo(f"  Resolving {len(conflicts)} conflicts...")
        reviewer, reviewer_ctx = build_reviewer_agent(
            llm, plan, conflicts, decision_log, option_matrix, constraints, gap_df
        )
        reviewer.invoke(
            {"messages": [("user", "Review the plan and resolve all conflicts.")]},
            config={"recursion_limit": 25},
        )
        plan = reviewer_ctx.plan
        decision_log = reviewer_ctx.decision_log

        llm_decisions = [d for d in decision_log.entries if d.source == "llm_reviewer"]
        overrides = [d for d in llm_decisions if d.action == "override"]
        click.echo(
            f"  Reviewer complete: {len(llm_decisions)} decisions, {len(overrides)} overrides"
        )
    else:
        click.echo("  No conflicts — skipping LLM review.")

    if verbose:
        click.echo(f"\n  Decision log (final):\n  {decision_log.summary()}")

    # Persist decision log
    n_decisions = write_decision_log(
        scenario_data.db_path, decision_log.to_dicts(), run_id
    )
    click.echo(f"  Decision log: {n_decisions} entries persisted")

    # Plan-only mode: print plan, generate report, and exit
    if plan_only:
        _print_plan(plan, decision_log, option_matrix)
        finalize_run(scenario_data.db_path, run_id, 0, 0, 0.0)
        run_data = RunData(
            scenario=scenario_data,
            run_id=run_id,
            model_name=model_name,
            gap_df=gap_df,
            option_matrix=option_matrix,
            plan=plan,
            conflicts=conflicts,
            decision_log=decision_log,
            constraints=constraints,
            plan_only=True,
        )
        report_path = generate_report(run_data)
        click.echo(f"\nReport: {report_path}")
        webbrowser.open(f"file://{report_path}")
        return

    # Phase 3: Deterministic executor
    click.echo("\n--- Phase 3: Executor ---")
    ctx = ProcurementContext(
        scenario=scenario_data, constraints=constraints, gap_df=gap_df.copy()
    )
    tools = build_tools(ctx)
    exec_result = execute_plan(plan, ctx, tools)

    click.echo(f"  Orders placed: {exec_result.orders_placed}")
    click.echo(f"  Alerts created: {exec_result.alerts_created}")
    if exec_result.errors:
        click.echo(f"  Errors: {len(exec_result.errors)}")
        for err in exec_result.errors:
            click.echo(
                f"    {err['component_id']}/{err['supplier_id']}: {err['error']}"
            )

    # Write results to DB
    n_orders = write_purchase_orders(scenario_data.db_path, ctx.placed_orders, run_id)
    n_alerts = write_alerts(scenario_data.db_path, ctx.placed_alerts, run_id)
    total_spend = sum(o["unit_price"] * o["quantity"] for o in ctx.placed_orders)
    finalize_run(scenario_data.db_path, run_id, n_orders, n_alerts, total_spend)

    _print_summary(run_id, ctx.placed_orders, ctx.placed_alerts, ctx.gap_df, scenario)

    # Generate HTML report
    run_data = RunData(
        scenario=scenario_data,
        run_id=run_id,
        model_name=model_name,
        gap_df=gap_df,
        option_matrix=option_matrix,
        plan=plan,
        conflicts=conflicts,
        decision_log=decision_log,
        placed_orders=ctx.placed_orders,
        placed_alerts=ctx.placed_alerts,
        constraints=constraints,
    )
    report_path = generate_report(run_data)
    click.echo(f"\nReport: {report_path}")
    webbrowser.open(f"file://{report_path}")


def _print_plan(plan, decision_log, option_matrix):
    """Print the allocation plan without executing."""
    click.echo("\n" + "=" * 60)
    click.echo("ALLOCATION PLAN (plan-only mode — no orders placed)")
    click.echo("=" * 60)

    if plan.allocations:
        click.echo("\nAllocations:")
        total_spend = 0.0
        for alloc in plan.allocations:
            opts = option_matrix.options.get(alloc.component_id, [])
            opt = next((o for o in opts if o.supplier_id == alloc.supplier_id), None)
            unit_price = opt.unit_price if opt else 0.0
            cost = unit_price * alloc.quantity
            total_spend += cost
            expedite_flag = " [EXPEDITE]" if alloc.expedite else ""
            click.echo(
                f"  {alloc.component_id}: {alloc.supplier_id} × {alloc.quantity}"
                f" — ${cost:,.2f}{expedite_flag}"
            )
            click.echo(f"    Rationale: {alloc.rationale}")
        click.echo(f"\n  Total estimated spend: ${total_spend:,.2f}")

    if plan.alerts:
        click.echo("\nAlerts:")
        for alert in plan.alerts:
            click.echo(f"  ⚠ {alert.description}")

    click.echo(f"\nDecision log: {decision_log.summary()}")


def _print_summary(run_id, placed_orders, placed_alerts, gap_df, scenario):
    """Print final summary after execution."""
    total_spend = sum(o["unit_price"] * o["quantity"] for o in placed_orders)

    click.echo("\n" + "=" * 60)
    click.echo(f"PROCUREMENT COMPLETE  (run: {run_id})")
    click.echo("=" * 60)
    click.echo(f"  Purchase orders placed: {len(placed_orders)}")
    click.echo(f"  Total spend: ${total_spend:,.2f}")
    click.echo(f"  Alerts generated: {len(placed_alerts)}")
    click.echo(f"  Remaining gaps: {len(gap_df)}")

    if placed_orders:
        click.echo("\nOrders:")
        for o in placed_orders:
            click.echo(
                f"  {o['po_number']}: {o['component_id']} × {o['quantity']} "
                f"from {o['supplier_id']} — ${o['unit_price'] * o['quantity']:,.2f} "
                f"(delivery: {o['expected_delivery_date']})"
            )

    if placed_alerts:
        click.echo("\nAlerts:")
        for a in placed_alerts:
            click.echo(f"  ⚠ {a}")

    if gap_df.empty:
        click.echo("\n✓ All shortfalls resolved.")
    else:
        click.echo(f"\n✗ {len(gap_df)} shortfalls remain:")
        click.echo(gap_df.to_string(index=False))

    click.echo(f"\nTo undo this run: python agent.py --scenario {scenario} --clean")


if __name__ == "__main__":
    main()
