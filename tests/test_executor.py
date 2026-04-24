"""Tests for the deterministic plan executor."""

from __future__ import annotations


from procureai.planner import Allocation, AllocationPlan, PlannedAlert
from tests.test_planner import make_constraints, make_gap_df, make_scenario_data


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ctx():
    """Build a ProcurementContext with test scenario data."""
    from procureai.agents.tools import ProcurementContext, build_tools

    scenario = make_scenario_data()
    constraints = make_constraints()
    gap_df = make_gap_df()
    ctx = ProcurementContext(
        scenario=scenario, constraints=constraints, gap_df=gap_df.copy()
    )
    tools = build_tools(ctx)
    return ctx, tools


def _make_plan_with_allocations():
    """Build an AllocationPlan with 3 allocations and 2 alerts."""
    plan = AllocationPlan(
        allocations=[
            Allocation(
                component_id="CMP-001",
                supplier_id="SUP-101",
                quantity=100,
                rationale="Top fitness supplier for CMP-001",
                expedite=False,
            ),
            Allocation(
                component_id="CMP-002",
                supplier_id="SUP-101",
                quantity=150,
                rationale="Only eligible supplier for CMP-002",
                expedite=False,
            ),
            Allocation(
                component_id="CMP-003",
                supplier_id="SUP-108",
                quantity=80,
                rationale="Domestic preferred for CMP-003",
                expedite=False,
            ),
        ],
        alerts=[
            PlannedAlert(
                description="INFEASIBLE DEADLINE: CMP-005 cannot be delivered on time",
                component_id="CMP-005",
            ),
            PlannedAlert(
                description="NO ELIGIBLE SUPPLIER: CMP-EMPTY has no suppliers",
                component_id="CMP-EMPTY",
            ),
        ],
    )
    return plan


# ===========================================================================
# Step 10: Deterministic Executor Tests
# ===========================================================================


class TestExecutor:
    def test_executor_places_all_orders(self):
        """3 allocations → 3 place_order calls, all recorded in results."""
        from procureai.agents.executor import execute_plan

        ctx, tools = _make_ctx()
        plan = _make_plan_with_allocations()
        result = execute_plan(plan, ctx, tools)
        assert result.orders_placed == 3
        assert len(ctx.placed_orders) == 3

    def test_executor_creates_all_alerts(self):
        """2 planned alerts → 2 create_alert calls."""
        from procureai.agents.executor import execute_plan

        ctx, tools = _make_ctx()
        plan = _make_plan_with_allocations()
        result = execute_plan(plan, ctx, tools)
        assert result.alerts_created == 2
        # Alerts should be in ctx.placed_alerts
        assert any("CMP-005" in a for a in ctx.placed_alerts)
        assert any("CMP-EMPTY" in a for a in ctx.placed_alerts)

    def test_executor_records_errors(self):
        """If place_order returns 'ERROR: ...', captured in errors list."""
        from procureai.agents.executor import execute_plan

        ctx, tools = _make_ctx()
        # Create a plan with an invalid supplier
        plan = AllocationPlan(
            allocations=[
                Allocation(
                    component_id="CMP-001",
                    supplier_id="SUP-NONEXISTENT",
                    quantity=100,
                    rationale="Bad supplier",
                    expedite=False,
                ),
            ],
            alerts=[],
        )
        result = execute_plan(plan, ctx, tools)
        assert len(result.errors) >= 1
        # Execution should continue (not crash)
        assert result.orders_placed == 0

    def test_executor_returns_summary(self):
        """ExecutionResult has correct orders_placed, alerts_created, errors counts."""
        from procureai.agents.executor import ExecutionResult, execute_plan

        ctx, tools = _make_ctx()
        plan = _make_plan_with_allocations()
        result = execute_plan(plan, ctx, tools)
        assert isinstance(result, ExecutionResult)
        assert result.orders_placed == 3
        assert result.alerts_created == 2
        assert isinstance(result.errors, list)

    def test_executor_preserves_rationale(self):
        """Rationale from allocation passed through to place_order."""
        from procureai.agents.executor import execute_plan

        ctx, tools = _make_ctx()
        plan = AllocationPlan(
            allocations=[
                Allocation(
                    component_id="CMP-001",
                    supplier_id="SUP-101",
                    quantity=100,
                    rationale="Greedy top-fitness pick",
                    expedite=False,
                ),
            ],
            alerts=[],
        )
        execute_plan(plan, ctx, tools)
        assert len(ctx.placed_orders) == 1
        assert ctx.placed_orders[0]["rationale"] == "Greedy top-fitness pick"

    def test_executor_handles_empty_plan(self):
        """No allocations, no alerts → returns zero counts, no errors."""
        from procureai.agents.executor import execute_plan

        ctx, tools = _make_ctx()
        plan = AllocationPlan(allocations=[], alerts=[])
        result = execute_plan(plan, ctx, tools)
        assert result.orders_placed == 0
        assert result.alerts_created == 0
        assert len(result.errors) == 0

    def test_executor_alert_includes_component_id(self):
        """Alert description includes component context when component_id is set."""
        from procureai.agents.executor import execute_plan

        ctx, tools = _make_ctx()
        plan = AllocationPlan(
            allocations=[],
            alerts=[
                PlannedAlert(
                    description="INFEASIBLE DEADLINE: cannot deliver on time",
                    component_id="CMP-005",
                ),
            ],
        )
        result = execute_plan(plan, ctx, tools)
        assert result.alerts_created == 1
        # The alert in ctx should contain the component ID
        assert any("CMP-005" in a for a in ctx.placed_alerts)

    def test_executor_continues_after_error(self):
        """After a failed order, executor continues processing remaining allocations."""
        from procureai.agents.executor import execute_plan

        ctx, tools = _make_ctx()
        plan = AllocationPlan(
            allocations=[
                # This will fail (nonexistent supplier)
                Allocation(
                    component_id="CMP-001",
                    supplier_id="SUP-NONEXISTENT",
                    quantity=100,
                    rationale="Bad supplier",
                    expedite=False,
                ),
                # This should succeed
                Allocation(
                    component_id="CMP-001",
                    supplier_id="SUP-101",
                    quantity=100,
                    rationale="Good supplier",
                    expedite=False,
                ),
            ],
            alerts=[],
        )
        result = execute_plan(plan, ctx, tools)
        assert len(result.errors) >= 1
        assert result.orders_placed >= 1
