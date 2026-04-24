"""Unit tests for tool-level enforcement (enriched tools + hard constraints).

Tests are organized by feature:
- Delivery-vs-deadline warnings (Step 2)
- Concentration limit enforcement (Step 3)
- Duplicate order detection (Step 4)
- Enriched get_eligible_suppliers output (Step 5)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from procureai.agents.tools import ProcurementContext, build_tools
from procureai.constraints import Constraint, ConstraintType
from procureai.utils.db import ScenarioData


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_suppliers() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "supplier_id": "SUP-108",
                "name": "MagnetPro Inc.",
                "is_domestic": 1,
                "on_approved_list": 1,
                "certifications": "ISO-9001, ISO-14001",
                "sustainability_rating": "A",
                "relationship_tier": "preferred",
            },
            {
                "supplier_id": "SUP-107",
                "name": "Nanjing Rare Earth",
                "is_domestic": 0,
                "on_approved_list": 1,
                "certifications": "ISO-9001",
                "sustainability_rating": "B",
                "relationship_tier": "standard",
            },
            {
                "supplier_id": "SUP-101",
                "name": "Acme Parts",
                "is_domestic": 1,
                "on_approved_list": 1,
                "certifications": "ISO-9001",
                "sustainability_rating": "A",
                "relationship_tier": "preferred",
            },
        ]
    )


def _make_catalog() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "component_id": "CMP-003",
                "supplier_id": "SUP-108",
                "unit_price": 5.80,
                "lead_time_days": 14,
                "minimum_order_qty": 50,
            },
            {
                "component_id": "CMP-003",
                "supplier_id": "SUP-107",
                "unit_price": 3.25,
                "lead_time_days": 35,
                "minimum_order_qty": 100,
            },
            {
                "component_id": "CMP-001",
                "supplier_id": "SUP-101",
                "unit_price": 12.00,
                "lead_time_days": 7,
                "minimum_order_qty": 10,
            },
        ]
    )


def _make_gap_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "component_id": "CMP-003",
                "total_needed": 208,
                "on_hand": 0,
                "incoming": 0,
                "gap": 208,
                "earliest_needed_by": "2025-09-12",
            },
            {
                "component_id": "CMP-001",
                "total_needed": 100,
                "on_hand": 50,
                "incoming": 0,
                "gap": 50,
                "earliest_needed_by": "2025-10-01",
            },
        ]
    )


def _default_constraints() -> list[Constraint]:
    return [
        Constraint(
            type=ConstraintType.CONCENTRATION_LIMIT,
            params={
                "component_ids": ["CMP-003"],
                "max_pct": 0.5,
                "secondary_min_pct": 0.2,
            },
            source="MEMO-2025-041",
            description="Max 50% per supplier for magnets",
        ),
        Constraint(
            type=ConstraintType.APPROVED_SUPPLIER_ONLY,
            source="procurement_policy.pdf",
            description="Only approved suppliers",
        ),
        Constraint(
            type=ConstraintType.MOQ_COMPLIANCE,
            source="procurement_policy.pdf",
            description="Orders must meet MOQ",
        ),
    ]


def make_context(
    *,
    current_date: str = "2025-09-01",
    constraints: list[Constraint] | None = None,
    gap_df: pd.DataFrame | None = None,
) -> ProcurementContext:
    """Build a minimal ProcurementContext for unit testing (no DB needed)."""
    scenario = ScenarioData(
        current_date=current_date,
        description="Test scenario",
        db_path=Path("/tmp/fake.sqlite"),
        products=pd.DataFrame(),
        components=pd.DataFrame(),
        bom=pd.DataFrame(),
        suppliers=_make_suppliers(),
        supplier_catalog=_make_catalog(),
        inventory=pd.DataFrame(),
        production_schedule=pd.DataFrame(),
        purchase_orders=pd.DataFrame(),
        alerts=pd.DataFrame(),
    )
    return ProcurementContext(
        scenario=scenario,
        constraints=constraints if constraints is not None else _default_constraints(),
        gap_df=gap_df.copy() if gap_df is not None else _make_gap_df(),
    )


def invoke_tool(ctx: ProcurementContext, tool_name: str, **kwargs) -> str:
    """Build tools from context and invoke one by name."""
    tools = build_tools(ctx)
    tool_map = {t.name: t for t in tools}
    return tool_map[tool_name].invoke(kwargs)


# ===========================================================================
# Step 2: Delivery-vs-Deadline Warning
# ===========================================================================


class TestPlaceOrderDeliveryWarning:
    """place_order should warn when delivery is after the deadline."""

    def test_place_order_on_time(self):
        """Delivery before deadline → no warning in response."""
        # SUP-101 for CMP-001: 7d lead → delivers 2025-09-08, needed by 2025-10-01
        ctx = make_context()
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-001",
            supplier_id="SUP-101",
            quantity=50,
            rationale="Test on-time order",
        )
        assert "LATE" not in result
        assert "Order placed" in result

    def test_place_order_late(self):
        """Delivery after deadline → response contains LATE warning."""
        # SUP-107 for CMP-003: 35d lead → delivers 2025-10-06, needed by 2025-09-12
        ctx = make_context()
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-003",
            supplier_id="SUP-107",
            quantity=100,
            rationale="Test late order",
        )
        assert "LATE" in result
        assert "2025-09-12" in result  # deadline should be mentioned

    def test_place_order_no_gap_row(self):
        """Component not in gap_df → no crash, no warning."""
        ctx = make_context(
            gap_df=pd.DataFrame(
                columns=[
                    "component_id",
                    "total_needed",
                    "on_hand",
                    "incoming",
                    "gap",
                    "earliest_needed_by",
                ]
            )
        )
        # CMP-001 is in catalog but not in gap_df
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-001",
            supplier_id="SUP-101",
            quantity=50,
            rationale="Test no gap row",
        )
        assert "LATE" not in result
        assert "Order placed" in result


# ===========================================================================
# Step 3: Concentration Limit Enforcement
# ===========================================================================


class TestPlaceOrderConcentration:
    """place_order should enforce concentration limits."""

    def test_place_order_concentration_under_limit(self):
        """40% after order → order goes through."""
        ctx = make_context()
        # 80 out of 208 total = ~38% — under 50% limit
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-003",
            supplier_id="SUP-108",
            quantity=80,
            rationale="Under limit",
        )
        assert "Order placed" in result
        assert "Concentration limit" not in result

    def test_place_order_concentration_over_limit(self):
        """Would be 65% after order (limit 50%) → ERROR, order NOT placed."""
        ctx = make_context()
        # 135 out of 208 = ~65% — over 50% limit
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-003",
            supplier_id="SUP-108",
            quantity=135,
            rationale="Over limit",
        )
        assert "ERROR" in result or "Concentration limit" in result
        assert len(ctx.placed_orders) == 0  # order should NOT be placed

    def test_place_order_concentration_error_includes_max_qty(self):
        """Error message includes the max quantity this supplier can receive."""
        ctx = make_context()
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-003",
            supplier_id="SUP-108",
            quantity=135,
            rationale="Over limit",
        )
        # Max for 50% of 208 = 104
        assert "104" in result

    def test_place_order_concentration_no_constraint(self):
        """Component has no CONCENTRATION_LIMIT → no check, order goes through."""
        ctx = make_context()
        # CMP-001 has no concentration constraint
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-001",
            supplier_id="SUP-101",
            quantity=50,
            rationale="No concentration limit",
        )
        assert "Order placed" in result

    def test_place_order_concentration_accounts_for_prior_orders(self):
        """Two sequential orders to same supplier — second should count the first."""
        ctx = make_context()
        # First order: 100 out of 208 = ~48% — under 50%, should go through
        result1 = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-003",
            supplier_id="SUP-108",
            quantity=100,
            rationale="First order",
        )
        assert "Order placed" in result1

        # Second order: additional 10 → total 110/208 = ~53% — over 50%
        result2 = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-003",
            supplier_id="SUP-108",
            quantity=10,
            rationale="Second order",
        )
        assert "ERROR" in result2 or "Concentration limit" in result2
        assert len(ctx.placed_orders) == 1  # only first order placed


# ===========================================================================
# Step 4: Duplicate Order Detection
# ===========================================================================


class TestPlaceOrderDuplicate:
    """place_order should detect exact duplicate orders."""

    def test_place_order_no_duplicate(self):
        """First order for this combo → goes through."""
        ctx = make_context()
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-003",
            supplier_id="SUP-108",
            quantity=80,
            rationale="First order",
        )
        assert "Order placed" in result

    def test_place_order_exact_duplicate(self):
        """Same component, supplier, quantity already placed → ERROR."""
        ctx = make_context()
        invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-001",
            supplier_id="SUP-101",
            quantity=50,
            rationale="First order",
        )
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-001",
            supplier_id="SUP-101",
            quantity=50,
            rationale="Duplicate",
        )
        assert "ERROR" in result or "Duplicate" in result
        assert len(ctx.placed_orders) == 1

    def test_place_order_same_component_different_supplier(self):
        """Different supplier → goes through (not a duplicate)."""
        ctx = make_context()
        invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-003",
            supplier_id="SUP-108",
            quantity=80,
            rationale="First",
        )
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-003",
            supplier_id="SUP-107",
            quantity=100,
            rationale="Different supplier",
        )
        assert "Order placed" in result
        assert len(ctx.placed_orders) == 2

    def test_place_order_same_component_different_quantity(self):
        """Same supplier, different quantity → goes through (deliberate split)."""
        # Use CMP-001 which has no concentration limit
        ctx = make_context()
        invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-001",
            supplier_id="SUP-101",
            quantity=20,
            rationale="First split",
        )
        result = invoke_tool(
            ctx,
            "place_order",
            component_id="CMP-001",
            supplier_id="SUP-101",
            quantity=30,
            rationale="Second split",
        )
        assert "Order placed" in result


# ===========================================================================
# Step 5: Enriched get_eligible_suppliers
# ===========================================================================


class TestEligibleSuppliersEnriched:
    """get_eligible_suppliers should show delivery status, concentration, and constraints."""

    def test_eligible_suppliers_shows_delivery_status(self):
        """Output contains ON TIME or LATE per supplier."""
        ctx = make_context()
        result = invoke_tool(ctx, "get_eligible_suppliers", component_id="CMP-003")
        # SUP-108: 14d → 2025-09-15, needed by 2025-09-12 → LATE
        assert "LATE" in result or "ON TIME" in result

    def test_eligible_suppliers_shows_concentration(self):
        """Output contains concentration % info."""
        ctx = make_context()
        result = invoke_tool(ctx, "get_eligible_suppliers", component_id="CMP-003")
        assert "concentration" in result.lower() or "%" in result

    def test_eligible_suppliers_shows_constraint_notes(self):
        """Output includes applicable constraint summaries."""
        ctx = make_context()
        result = invoke_tool(ctx, "get_eligible_suppliers", component_id="CMP-003")
        assert "CONCENTRATION_LIMIT" in result or "concentration" in result.lower()

    def test_eligible_suppliers_shows_max_qty_per_supplier(self):
        """When concentration limit applies, shows max quantity each supplier can receive."""
        ctx = make_context()
        result = invoke_tool(ctx, "get_eligible_suppliers", component_id="CMP-003")
        # Max for 50% of 208 = 104
        assert "104" in result

    def test_eligible_suppliers_air_freight_annotation(self):
        """When air freight is active, shows both standard and air freight delivery dates."""
        constraints = _default_constraints()
        constraints.append(
            Constraint(
                type=ConstraintType.AIR_FREIGHT_ALLOWED,
                params={
                    "start_date": "2025-08-01",
                    "end_date": "2025-09-30",
                    "lead_time_reduction": 14,
                    "min_lead_time": 7,
                },
                source="memo",
                description="Air freight authorized",
            )
        )
        ctx = make_context(constraints=constraints)
        result = invoke_tool(ctx, "get_eligible_suppliers", component_id="CMP-003")
        # SUP-107 is international — should show air freight info
        assert "air freight" in result.lower() or "air" in result.lower()
