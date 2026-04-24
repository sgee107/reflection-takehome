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


def _concentration_limit(ctx: ProcurementContext, component_id: str) -> dict | None:
    """Return concentration limit params if a CONCENTRATION_LIMIT applies to this component."""
    for c in _constraints_of(ctx, ConstraintType.CONCENTRATION_LIMIT):
        cids = c.params.get("component_ids", [])
        if component_id in cids:
            return {
                "max_pct": c.params.get("max_pct", 0.5),
                "secondary_min_pct": c.params.get("secondary_min_pct", 0.2),
            }
    return None


# ---------------------------------------------------------------------------
# Helpers: enriched supplier annotations
# ---------------------------------------------------------------------------


def _annotate_supplier(
    ctx: ProcurementContext,
    component_id: str,
    supplier_row: pd.Series,
    gap_row: pd.Series | None,
) -> str:
    """Build an annotation block for one eligible supplier."""
    sid = supplier_row["supplier_id"]
    name = supplier_row.get("name", sid)
    price = supplier_row.get("unit_price", "?")
    lead_days = int(supplier_row.get("lead_time_days", 0))
    moq = int(supplier_row.get("minimum_order_qty", 0))
    is_domestic = supplier_row.get("is_domestic", 1)

    current_dt = datetime.strptime(ctx.current_date, "%Y-%m-%d")
    delivery_date = (current_dt + timedelta(days=lead_days)).strftime("%Y-%m-%d")

    # Delivery status
    if gap_row is not None and "earliest_needed_by" in gap_row.index:
        deadline = str(gap_row["earliest_needed_by"])
        deadline_dt = datetime.strptime(deadline, "%Y-%m-%d")
        delivery_dt = datetime.strptime(delivery_date, "%Y-%m-%d")
        if delivery_dt <= deadline_dt:
            delivery_status = "✓ ON TIME"
        else:
            days_late = (delivery_dt - deadline_dt).days
            delivery_status = f"⚠ LATE by {days_late} days"
    else:
        deadline = None
        delivery_status = ""

    line = (
        f"{sid} {name} | ${price} | {lead_days}d → {delivery_date} | {delivery_status}"
    )
    parts = [line]

    # Air freight annotation for international suppliers
    if is_domestic == 0:
        af = _air_freight_config(ctx)
        if af:
            af_start = af.get("start_date")
            af_end = af.get("end_date")
            eligible = True
            if af_start:
                eligible = eligible and current_dt >= datetime.strptime(
                    af_start, "%Y-%m-%d"
                )
            if af_end:
                eligible = eligible and current_dt <= datetime.strptime(
                    af_end, "%Y-%m-%d"
                )
            if eligible:
                reduction = af.get("lead_time_reduction") or 14
                min_lt = af.get("min_lead_time") or 7
                af_lead = max(lead_days - reduction, min_lt)
                af_delivery = (current_dt + timedelta(days=af_lead)).strftime(
                    "%Y-%m-%d"
                )
                af_status = ""
                if deadline:
                    af_dt = datetime.strptime(af_delivery, "%Y-%m-%d")
                    deadline_dt = datetime.strptime(deadline, "%Y-%m-%d")
                    if af_dt <= deadline_dt:
                        af_status = "✓ ON TIME"
                    else:
                        af_status = f"⚠ LATE by {(af_dt - deadline_dt).days} days"
                parts.append(
                    f"  [air freight: {af_lead}d → {af_delivery} | {af_status}]"
                )

    # Supplier attributes
    domestic_flag = "✓ domestic" if is_domestic == 1 else "✗ international"
    sustainability = supplier_row.get("sustainability_rating", "?")
    tier = supplier_row.get("relationship_tier", "?")
    parts.append(
        f"  {domestic_flag} | ✓ approved | sustainability: {sustainability} | tier: {tier}"
    )

    # Concentration info
    conc = _concentration_limit(ctx, component_id)
    if conc and gap_row is not None:
        total_needed = int(gap_row.get("total_needed", 0))
        already = sum(
            o["quantity"]
            for o in ctx.placed_orders
            if o["component_id"] == component_id and o["supplier_id"] == sid
        )
        current_pct = (already / total_needed * 100) if total_needed else 0
        max_pct = conc["max_pct"]
        max_qty = math.floor(max_pct * total_needed) - already
        parts.append(
            f"  concentration: {current_pct:.0f}% → limit: {max_pct:.0%}, "
            f"max qty: {max(max_qty, 0)}"
        )

    parts.append(f"  MOQ: {moq}")
    return "\n".join(parts)


def _constraint_notes(
    ctx: ProcurementContext,
    component_id: str,
    gap_row: pd.Series | None,
) -> str:
    """Summarize applicable constraints for a component."""
    notes: list[str] = []
    conc = _concentration_limit(ctx, component_id)
    if conc and gap_row is not None:
        total_needed = int(gap_row.get("total_needed", 0))
        max_qty = math.floor(conc["max_pct"] * total_needed)
        notes.append(
            f"CONCENTRATION_LIMIT: max {conc['max_pct']:.0%}/supplier, "
            f"min {conc['secondary_min_pct']:.0%} secondary. "
            f"Recommended max per supplier: {max_qty} units"
        )

    for c in _constraints_of(ctx, ConstraintType.CRITICAL_COMPONENT):
        cids = c.params.get("component_ids", [])
        if component_id in cids:
            notes.append(f"CRITICAL_COMPONENT: {c.description}")

    for c in _constraints_of(ctx, ConstraintType.HAZMAT_HANDLING):
        cids = c.params.get("component_ids", [])
        if component_id in cids:
            notes.append(f"HAZMAT: {c.description}")

    req_cert = _required_cert(ctx, component_id)
    if req_cert:
        notes.append(f"CERT_REQUIRED: {req_cert}")

    if not notes:
        return ""
    return (
        "CONSTRAINTS for " + component_id + ":\n" + "\n".join(f"  - {n}" for n in notes)
    )


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
        """Return eligible suppliers for a component with delivery, concentration, and constraint annotations.

        Checks: approved list, blocked suppliers, required certifications, PCB memo.
        Shows: delivery feasibility, concentration impact, max quantities, constraint notes.
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
        rows = rows[
            ~rows["supplier_id"].apply(lambda sid: _is_supplier_blocked(ctx, sid))
        ]

        # Filter: cert requirements
        req_cert = _required_cert(ctx, component_id)
        if req_cert:
            rows = rows[
                rows["certifications"].str.contains(req_cert, case=False, na=False)
            ]

        # Filter: PCB qualified only (same cert filter pattern)
        if _is_pcb_restricted(ctx, component_id):
            rows = rows[
                rows["certifications"].str.contains("ISO-9001", case=False, na=False)
            ]

        if rows.empty:
            return (
                f"No eligible suppliers for {component_id} after applying constraints."
            )

        # Look up gap row for this component
        gap_rows = ctx.gap_df[ctx.gap_df["component_id"] == component_id]
        gap_row = gap_rows.iloc[0] if not gap_rows.empty else None

        # Header
        parts: list[str] = []
        if gap_row is not None:
            gap_qty = int(gap_row["gap"])
            deadline = str(gap_row["earliest_needed_by"])
            parts.append(f"{component_id} (gap: {gap_qty}, needed by: {deadline})")
        else:
            parts.append(f"{component_id}")
        parts.append("")

        # Annotate each supplier
        for _, row in rows.iterrows():
            parts.append(_annotate_supplier(ctx, component_id, row, gap_row))
            parts.append("")

        # Constraint notes
        notes = _constraint_notes(ctx, component_id, gap_row)
        if notes:
            parts.append(notes)

        return "\n".join(parts)

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

        if (
            _constraints_of(ctx, ConstraintType.APPROVED_SUPPLIER_ONLY)
            and sup["on_approved_list"] != 1
        ):
            return f"ERROR: {supplier_id} is not on the approved supplier list."
        if _is_supplier_blocked(ctx, supplier_id):
            return f"ERROR: {supplier_id} is blocked."

        # Cert check
        req_cert = _required_cert(ctx, component_id)
        if (
            req_cert
            and req_cert.lower() not in str(sup.get("certifications", "")).lower()
        ):
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
                eligible = eligible and current_dt >= datetime.strptime(
                    af_start, "%Y-%m-%d"
                )
            if af_end:
                eligible = eligible and current_dt <= datetime.strptime(
                    af_end, "%Y-%m-%d"
                )
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

        # --- Duplicate order detection ---
        for existing in ctx.placed_orders:
            if (
                existing["component_id"] == component_id
                and existing["supplier_id"] == supplier_id
                and existing["quantity"] == quantity
            ):
                return (
                    f"ERROR: Duplicate order. {existing['po_number']} already placed "
                    f"{component_id} × {quantity} from {supplier_id}. "
                    f"Adjust quantity or choose a different supplier."
                )

        # --- Concentration limit enforcement ---
        conc = _concentration_limit(ctx, component_id)
        if conc:
            max_pct = conc["max_pct"]
            # Total needed from gap_df (use original total, not remaining gap)
            gap_row = ctx.gap_df[ctx.gap_df["component_id"] == component_id]
            if not gap_row.empty:
                total_needed = int(gap_row.iloc[0]["total_needed"])
            else:
                # Fall back: sum all placed orders for this component + quantity
                total_needed = (
                    sum(
                        o["quantity"]
                        for o in ctx.placed_orders
                        if o["component_id"] == component_id
                    )
                    + quantity
                )
            # Existing orders from this supplier
            already_from_supplier = sum(
                o["quantity"]
                for o in ctx.placed_orders
                if o["component_id"] == component_id and o["supplier_id"] == supplier_id
            )
            projected_share = (
                (already_from_supplier + quantity) / total_needed
                if total_needed
                else 1.0
            )
            if projected_share > max_pct:
                max_allowed = math.floor(max_pct * total_needed) - already_from_supplier
                return (
                    f"ERROR: Concentration limit — {supplier_id} would have "
                    f"{projected_share:.0%} of {component_id} (limit: {max_pct:.0%}). "
                    f"Max additional quantity: {max(max_allowed, 0)}. Split across suppliers."
                )

        # Capture deadline before gap update (row may be removed)
        _gap_row = ctx.gap_df[ctx.gap_df["component_id"] == component_id]
        deadline_for_component = (
            str(_gap_row.iloc[0]["earliest_needed_by"]) if not _gap_row.empty else None
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

        # --- Delivery-vs-deadline warning ---
        response = (
            f"Order placed: {po_number}\n"
            f"  {component_id} × {quantity} from {supplier_id}\n"
            f"  Unit price: ${unit_price:.2f} | Total: ${total_cost:,.2f}\n"
            f"  Expected delivery: {delivery_date} ({lead_time} days)\n"
            f"  Rationale: {rationale}"
        )
        if deadline_for_component:
            delivery_dt = datetime.strptime(delivery_date, "%Y-%m-%d")
            deadline_dt = datetime.strptime(deadline_for_component, "%Y-%m-%d")
            if delivery_dt > deadline_dt:
                days_late = (delivery_dt - deadline_dt).days
                response += (
                    f"\n  ⚠ LATE: delivers {delivery_date} but needed by "
                    f"{deadline_for_component} ({days_late} days late)"
                )
                ctx.placed_alerts.append(
                    f"DEADLINE RISK: {component_id} from {supplier_id} delivers "
                    f"{delivery_date}, needed by {deadline_for_component} ({days_late} days late)"
                )
        return response

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
            supplier_qtys[o["supplier_id"]] = (
                supplier_qtys.get(o["supplier_id"], 0) + o["quantity"]
            )

        for sid, qty in sorted(supplier_qtys.items(), key=lambda x: -x[1]):
            pct = (qty / total_qty) * 100 if total_qty else 0
            lines.append(f"  {sid}: {qty} units ({pct:.0f}%)")

        return (
            f"Concentration for {component_id} ({total_qty} total ordered):\n"
            + "\n".join(lines)
        )

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
            summary += "\n\nRemaining gaps:\n" + ctx.gap_df[
                ["component_id", "gap", "earliest_needed_by"]
            ].to_string(index=False)
        return summary

    return [
        get_shortfalls,
        get_eligible_suppliers,
        place_order,
        create_alert,
        check_concentration,
        get_order_status,
    ]
