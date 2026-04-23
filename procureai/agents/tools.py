"""Agent tools for the procurement ReAct loop.

Each tool is a closure over a shared ProcurementContext so that
state (placed orders, updated gaps) persists across tool calls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd
from langchain_core.tools import tool

from procureai.constraints import Constraint, ConstraintType
from procureai.utils.db import ScenarioData


@dataclass
class ProcurementContext:
    """Mutable shared state passed to every tool via closure."""

    scenario: ScenarioData
    constraints: list[Constraint]
    gap_df: pd.DataFrame  # updated in-place as orders are placed
    placed_orders: list[dict] = field(default_factory=list)
    placed_alerts: list[str] = field(default_factory=list)
    _order_seq: int = field(default=0)

    @property
    def current_date(self) -> str:
        return self.scenario.current_date

    def next_po_number(self) -> str:
        self._order_seq += 1
        return f"PO-AGENT-{self._order_seq:03d}"


# ---------------------------------------------------------------------------
# Helper: constraint lookups
# ---------------------------------------------------------------------------

def _constraints_of(ctx: ProcurementContext, ctype: ConstraintType) -> list[Constraint]:
    return [c for c in ctx.constraints if c.type == ctype]


def _is_supplier_blocked(ctx: ProcurementContext, supplier_id: str) -> bool:
    for c in _constraints_of(ctx, ConstraintType.SUPPLIER_BLOCKED):
        if c.params.get("supplier_id") == supplier_id:
            return True
    return False


def _required_cert(ctx: ProcurementContext, component_id: str) -> str | None:
    for c in _constraints_of(ctx, ConstraintType.CERT_REQUIRED):
        if c.params.get("component_id") == component_id:
            return c.params.get("cert_name")
    return None


def _is_pcb_restricted(ctx: ProcurementContext, component_id: str) -> bool:
    for c in _constraints_of(ctx, ConstraintType.PCB_QUALIFIED_ONLY):
        if c.params.get("component_id") == component_id:
            return True
    return False


def _air_freight_config(ctx: ProcurementContext) -> dict | None:
    constraints = _constraints_of(ctx, ConstraintType.AIR_FREIGHT_ALLOWED)
    return constraints[0].params if constraints else None


# ---------------------------------------------------------------------------
# Tool factory — returns bound tool functions
# ---------------------------------------------------------------------------

def build_tools(ctx: ProcurementContext) -> list:
    """Create all agent tools as closures over the given context."""

    @tool
    def get_shortfalls() -> str:
        """Return the current table of component shortfalls (gaps > 0)."""
        df = ctx.gap_df
        if df.empty:
            return "No remaining shortfalls — all components are covered."
        return df.to_string(index=False)

    @tool
    def get_eligible_suppliers(component_id: str) -> str:
        """Return eligible suppliers for a component, filtered by constraints.

        Checks: approved list, blocked suppliers, required certifications, PCB memo.
        """
        catalog = ctx.scenario.supplier_catalog
        suppliers = ctx.scenario.suppliers

        # Filter catalog to this component
        rows = catalog[catalog["component_id"] == component_id].copy()
        if rows.empty:
            return f"No suppliers found in catalog for {component_id}."

        # Join supplier info
        rows = rows.merge(suppliers, on="supplier_id", how="left")

        # Filter: must be on approved list
        if _constraints_of(ctx, ConstraintType.APPROVED_SUPPLIER_ONLY):
            rows = rows[rows["on_approved_list"] == 1]

        # Filter: not blocked
        rows = rows[~rows["supplier_id"].apply(lambda sid: _is_supplier_blocked(ctx, sid))]

        # Filter: cert requirements
        req_cert = _required_cert(ctx, component_id)
        if req_cert:
            rows = rows[rows["certifications"].str.contains(req_cert, case=False, na=False)]

        # Filter: PCB qualified only (same cert filter pattern)
        if _is_pcb_restricted(ctx, component_id):
            rows = rows[rows["certifications"].str.contains("ISO-9001", case=False, na=False)]

        if rows.empty:
            return f"No eligible suppliers for {component_id} after applying constraints."

        display_cols = [
            "supplier_id", "name", "unit_price", "lead_time_days",
            "minimum_order_qty", "is_domestic", "certifications",
            "sustainability_rating", "relationship_tier",
        ]
        display_cols = [c for c in display_cols if c in rows.columns]
        return rows[display_cols].to_string(index=False)

    @tool
    def place_order(
        component_id: str,
        supplier_id: str,
        quantity: int,
        rationale: str,
    ) -> str:
        """Place a purchase order for a component from a supplier.

        Validates MOQ, computes delivery date (with air freight if applicable),
        and flags budget thresholds. Updates gap table.
        """
        catalog = ctx.scenario.supplier_catalog
        suppliers = ctx.scenario.suppliers

        # Look up catalog entry
        entry = catalog[
            (catalog["component_id"] == component_id)
            & (catalog["supplier_id"] == supplier_id)
        ]
        if entry.empty:
            return f"ERROR: {supplier_id} does not supply {component_id}."

        entry = entry.iloc[0]
        unit_price = float(entry["unit_price"])
        lead_time = int(entry["lead_time_days"])
        moq = int(entry["minimum_order_qty"])

        # Supplier validation
        sup = suppliers[suppliers["supplier_id"] == supplier_id]
        if sup.empty:
            return f"ERROR: Supplier {supplier_id} not found."
        sup = sup.iloc[0]

        if _constraints_of(ctx, ConstraintType.APPROVED_SUPPLIER_ONLY) and sup["on_approved_list"] != 1:
            return f"ERROR: {supplier_id} is not on the approved supplier list."
        if _is_supplier_blocked(ctx, supplier_id):
            return f"ERROR: {supplier_id} is blocked."

        # Cert check
        req_cert = _required_cert(ctx, component_id)
        if req_cert and req_cert.lower() not in str(sup.get("certifications", "")).lower():
            return f"ERROR: {supplier_id} lacks required {req_cert} certification for {component_id}."

        # MOQ enforcement
        if _constraints_of(ctx, ConstraintType.MOQ_COMPLIANCE) and quantity < moq:
            quantity = moq
            ctx.placed_alerts.append(
                f"Quantity for {component_id} from {supplier_id} raised to MOQ ({moq})."
            )

        # Air freight adjustment (only for international suppliers within date window)
        af = _air_freight_config(ctx)
        if af and sup.get("is_domestic", 1) == 0:
            reduction = af.get("lead_time_reduction") or 14
            min_lt = af.get("min_lead_time") or 7
            # Check date eligibility if specified
            af_start = af.get("start_date")
            af_end = af.get("end_date")
            current_dt = datetime.strptime(ctx.current_date, "%Y-%m-%d")
            eligible = True
            if af_start:
                eligible = eligible and current_dt >= datetime.strptime(af_start, "%Y-%m-%d")
            if af_end:
                eligible = eligible and current_dt <= datetime.strptime(af_end, "%Y-%m-%d")
            if eligible:
                adjusted = max(lead_time - reduction, min_lt)
                if adjusted < lead_time:
                    lead_time = adjusted

        # Compute dates
        current = datetime.strptime(ctx.current_date, "%Y-%m-%d")
        order_date = current.strftime("%Y-%m-%d")
        delivery_date = (current + timedelta(days=lead_time)).strftime("%Y-%m-%d")

        total_cost = unit_price * quantity

        # Budget threshold alerts
        for bc in _constraints_of(ctx, ConstraintType.BUDGET_THRESHOLD):
            threshold = bc.params.get("amount", 0)
            approver = bc.params.get("approver", "management")
            if total_cost > threshold:
                ctx.placed_alerts.append(
                    f"BUDGET ALERT: Order for {component_id} from {supplier_id} "
                    f"totals ${total_cost:,.2f} (>{bc.params['amount']:,.0f}). "
                    f"Requires {approver} approval."
                )

        # Record the order
        po_number = ctx.next_po_number()
        order = {
            "po_number": po_number,
            "component_id": component_id,
            "supplier_id": supplier_id,
            "quantity": quantity,
            "unit_price": unit_price,
            "order_date": order_date,
            "expected_delivery_date": delivery_date,
            "rationale": rationale,
        }
        ctx.placed_orders.append(order)

        # Update gap table
        mask = ctx.gap_df["component_id"] == component_id
        if mask.any():
            ctx.gap_df.loc[mask, "gap"] -= quantity
            ctx.gap_df = ctx.gap_df[ctx.gap_df["gap"] > 0].reset_index(drop=True)

        return (
            f"Order placed: {po_number}\n"
            f"  {component_id} × {quantity} from {supplier_id}\n"
            f"  Unit price: ${unit_price:.2f} | Total: ${total_cost:,.2f}\n"
            f"  Expected delivery: {delivery_date} ({lead_time} days)\n"
            f"  Rationale: {rationale}"
        )

    @tool
    def create_alert(description: str) -> str:
        """Log a procurement alert (infeasible deadline, risk, escalation, etc.)."""
        ctx.placed_alerts.append(description)
        return f"Alert logged: {description}"

    @tool
    def check_concentration(component_id: str) -> str:
        """Show current supplier concentration for a component based on placed orders."""
        orders = [o for o in ctx.placed_orders if o["component_id"] == component_id]
        if not orders:
            return f"No orders placed yet for {component_id}."

        total_qty = sum(o["quantity"] for o in orders)
        lines = []
        supplier_qtys: dict[str, int] = {}
        for o in orders:
            supplier_qtys[o["supplier_id"]] = supplier_qtys.get(o["supplier_id"], 0) + o["quantity"]

        for sid, qty in sorted(supplier_qtys.items(), key=lambda x: -x[1]):
            pct = (qty / total_qty) * 100 if total_qty else 0
            lines.append(f"  {sid}: {qty} units ({pct:.0f}%)")

        return f"Concentration for {component_id} ({total_qty} total ordered):\n" + "\n".join(lines)

    @tool
    def get_order_status() -> str:
        """Return a summary of all orders placed so far and remaining gaps."""
        n_orders = len(ctx.placed_orders)
        n_alerts = len(ctx.placed_alerts)
        remaining = len(ctx.gap_df)
        total_spend = sum(o["unit_price"] * o["quantity"] for o in ctx.placed_orders)

        summary = (
            f"Orders placed: {n_orders}\n"
            f"Total spend: ${total_spend:,.2f}\n"
            f"Alerts: {n_alerts}\n"
            f"Remaining shortfalls: {remaining}"
        )
        if remaining > 0:
            summary += "\n\nRemaining gaps:\n" + ctx.gap_df[["component_id", "gap", "earliest_needed_by"]].to_string(index=False)
        return summary

    return [get_shortfalls, get_eligible_suppliers, place_order, create_alert, check_concentration, get_order_status]
