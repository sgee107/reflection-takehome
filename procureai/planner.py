"""Deterministic planner — data structures, option matrix, greedy allocator, conflict detection.

All functions in this module are pure deterministic code with no LLM dependency.
"""

from __future__ import annotations

import enum
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from procureai.constraints import Constraint, ConstraintType as CType
from procureai.utils.db import ScenarioData


# ---------------------------------------------------------------------------
# Conflict types
# ---------------------------------------------------------------------------


class ConflictType(enum.Enum):
    # Hard conflicts (arithmetic)
    INFEASIBLE_DEADLINE = "INFEASIBLE_DEADLINE"
    CONCENTRATION_SPLIT = "CONCENTRATION_SPLIT"
    BUDGET_THRESHOLD = "BUDGET_THRESHOLD"
    MOQ_OVERBUY = "MOQ_OVERBUY"
    NO_ELIGIBLE_SUPPLIER = "NO_ELIGIBLE_SUPPLIER"

    # Soft-preference tradeoffs
    DOMESTIC_VS_COST = "DOMESTIC_VS_COST"
    STRATEGIC_LOYALTY = "STRATEGIC_LOYALTY"
    SUSTAINABILITY_TRADEOFF = "SUSTAINABILITY_TRADEOFF"
    SHARED_SUPPLIER_LOAD = "SHARED_SUPPLIER_LOAD"


# ---------------------------------------------------------------------------
# Supplier option (pre-computed per component-supplier pair)
# ---------------------------------------------------------------------------


@dataclass
class SupplierOption:
    component_id: str
    supplier_id: str
    supplier_name: str
    unit_price: float
    lead_time_days: int
    delivery_date: str
    deadline: str
    days_late: int
    air_freight_delivery: str | None
    air_freight_days_late: int | None
    is_domestic: bool
    sustainability_rating: str
    relationship_tier: str
    certifications: list[str]
    moq: int
    gap_quantity: int
    max_concentration_qty: int | None
    fitness_score: float


# ---------------------------------------------------------------------------
# Option matrix
# ---------------------------------------------------------------------------


@dataclass
class OptionMatrix:
    options: dict[str, list[SupplierOption]]  # component_id → sorted by fitness
    shared_suppliers: dict[str, list[str]]  # supplier_id → [component_ids]
    component_priorities: list[str]  # sorted by deadline urgency


# ---------------------------------------------------------------------------
# Allocation plan
# ---------------------------------------------------------------------------


@dataclass
class Allocation:
    component_id: str
    supplier_id: str
    quantity: int
    rationale: str
    expedite: bool = False


@dataclass
class PlannedAlert:
    description: str
    component_id: str | None = None


@dataclass
class AllocationPlan:
    allocations: list[Allocation]
    alerts: list[PlannedAlert]

    def to_dict(self) -> dict:
        return {
            "allocations": [
                {
                    "component_id": a.component_id,
                    "supplier_id": a.supplier_id,
                    "quantity": a.quantity,
                    "rationale": a.rationale,
                    "expedite": a.expedite,
                }
                for a in self.allocations
            ],
            "alerts": [
                {"description": a.description, "component_id": a.component_id}
                for a in self.alerts
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> AllocationPlan:
        return cls(
            allocations=[Allocation(**a) for a in data.get("allocations", [])],
            alerts=[PlannedAlert(**a) for a in data.get("alerts", [])],
        )


# ---------------------------------------------------------------------------
# Conflict
# ---------------------------------------------------------------------------


@dataclass
class Conflict:
    type: ConflictType
    component_id: str
    description: str
    options: list[dict]
    greedy_choice: dict | None
    data: dict


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    component_id: str
    action: str
    details: dict
    rationale: str
    source: str  # "greedy_algorithm" or "llm_reviewer"
    timestamp: str
    conflict_id: str | None


# ---------------------------------------------------------------------------
# Fitness scoring
# ---------------------------------------------------------------------------

_SUSTAINABILITY_SCORES = {"A": 1.0, "B": 0.6, "C": 0.3}
_TIER_SCORES = {"strategic": 1.0, "preferred": 0.7, "standard": 0.4}


def compute_fitness(option: SupplierOption, price_range: tuple[float, float]) -> float:
    """Compute composite fitness score (0-1) for a supplier option.

    Args:
        option: The supplier option to score.
        price_range: (min_price, max_price) across all suppliers for this component.
    """
    min_price, max_price = price_range

    # Price score (0.25): lower is better — invert within range
    if max_price > min_price:
        price_score = 1.0 - (option.unit_price - min_price) / (max_price - min_price)
    else:
        price_score = 1.0

    # Delivery score (0.25): 1.0 if on time, gradient toward 0 as days_late increases
    if option.days_late <= 0:
        delivery_score = 1.0
    else:
        delivery_score = max(0.0, 1.0 - option.days_late / 60.0)

    # Cert score (0.15): always 1.0 (filtered pre-scoring)
    cert_score = 1.0

    # Tier score (0.10)
    tier_score = _TIER_SCORES.get(option.relationship_tier, 0.4)

    # Sustainability score (0.10)
    sustainability_score = _SUSTAINABILITY_SCORES.get(option.sustainability_rating, 0.3)

    # Domestic score (0.10)
    domestic_score = 1.0 if option.is_domestic else 0.5

    # MOQ fit score (0.05)
    if option.moq <= option.gap_quantity:
        moq_fit_score = 1.0
    else:
        moq_fit_score = option.gap_quantity / option.moq

    return (
        0.25 * price_score
        + 0.25 * delivery_score
        + 0.15 * cert_score
        + 0.10 * tier_score
        + 0.10 * sustainability_score
        + 0.10 * domestic_score
        + 0.05 * moq_fit_score
    )


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------


class DecisionLog:
    def __init__(self) -> None:
        self._entries: list[Decision] = []

    @property
    def entries(self) -> list[Decision]:
        return list(self._entries)

    def append(self, decision: Decision) -> None:
        self._entries.append(decision)

    def for_component(self, component_id: str) -> list[Decision]:
        return [d for d in self._entries if d.component_id == component_id]

    def summary(self) -> str:
        counts = Counter(d.source for d in self._entries)
        parts = [f"{len(self._entries)} total decisions"]
        for source, count in sorted(counts.items()):
            parts.append(f"  {source}: {count}")
        return "\n".join(parts)

    def to_dicts(self) -> list[dict]:
        """Serialize all decisions for persistence."""
        return [
            {
                "component_id": d.component_id,
                "action": d.action,
                "details": json.dumps(d.details)
                if isinstance(d.details, dict)
                else str(d.details),
                "rationale": d.rationale,
                "source": d.source,
                "timestamp": d.timestamp,
                "conflict_id": d.conflict_id,
            }
            for d in self._entries
        ]


# ---------------------------------------------------------------------------
# Constraint helpers
# ---------------------------------------------------------------------------


def _constraints_of(constraints: list[Constraint], ctype: CType) -> list[Constraint]:
    return [c for c in constraints if c.type == ctype]


def _is_supplier_blocked(constraints: list[Constraint], supplier_id: str) -> bool:
    for c in _constraints_of(constraints, CType.SUPPLIER_BLOCKED):
        if c.params.get("supplier_id") == supplier_id:
            return True
    return False


def _required_cert(constraints: list[Constraint], component_id: str) -> str | None:
    for c in _constraints_of(constraints, CType.CERT_REQUIRED):
        if c.params.get("component_id") == component_id:
            return c.params.get("cert_name")
    return None


def _concentration_limit(
    constraints: list[Constraint], component_id: str
) -> dict | None:
    for c in _constraints_of(constraints, CType.CONCENTRATION_LIMIT):
        cids = c.params.get("component_ids", [])
        if component_id in cids:
            return {
                "max_pct": c.params.get("max_pct", 0.5),
                "secondary_min_pct": c.params.get("secondary_min_pct", 0.2),
            }
    return None


def _air_freight_config(constraints: list[Constraint]) -> dict | None:
    afs = _constraints_of(constraints, CType.AIR_FREIGHT_ALLOWED)
    return afs[0].params if afs else None


# ---------------------------------------------------------------------------
# Option matrix builder
# ---------------------------------------------------------------------------


def build_option_matrix(
    scenario: ScenarioData,
    constraints: list[Constraint],
    gap_df: pd.DataFrame,
) -> OptionMatrix:
    """Pre-compute all eligible supplier options per component with fitness scores."""
    catalog = scenario.supplier_catalog
    suppliers = scenario.suppliers
    current_dt = datetime.strptime(scenario.current_date, "%Y-%m-%d")
    af_config = _air_freight_config(constraints)
    has_approved_only = bool(_constraints_of(constraints, CType.APPROVED_SUPPLIER_ONLY))

    options: dict[str, list[SupplierOption]] = {}
    all_supplier_components: dict[str, list[str]] = defaultdict(list)

    for _, gap_row in gap_df.iterrows():
        comp_id = gap_row["component_id"]
        gap_qty = int(gap_row["gap"])
        deadline = str(gap_row["earliest_needed_by"])
        deadline_dt = datetime.strptime(deadline, "%Y-%m-%d")

        # Filter catalog to this component
        rows = catalog[catalog["component_id"] == comp_id].copy()
        if rows.empty:
            options[comp_id] = []
            continue

        # Join supplier info
        rows = rows.merge(suppliers, on="supplier_id", how="left")

        # Apply hard gates
        if has_approved_only:
            rows = rows[rows["on_approved_list"] == 1]
        rows = rows[
            ~rows["supplier_id"].apply(
                lambda sid: _is_supplier_blocked(constraints, sid)
            )
        ]

        req_cert = _required_cert(constraints, comp_id)
        if req_cert:
            rows = rows[
                rows["certifications"].str.contains(req_cert, case=False, na=False)
            ]

        # Concentration limit for this component
        conc = _concentration_limit(constraints, comp_id)
        max_conc_qty = math.floor(conc["max_pct"] * gap_qty) if conc else None

        comp_options: list[SupplierOption] = []
        for _, row in rows.iterrows():
            sid = row["supplier_id"]
            lead_days = int(row["lead_time_days"])
            delivery_dt = current_dt + timedelta(days=lead_days)
            delivery_date = delivery_dt.strftime("%Y-%m-%d")
            days_late = max(0, (delivery_dt - deadline_dt).days)

            # Air freight for international suppliers
            af_delivery = None
            af_days_late = None
            is_domestic = bool(row.get("is_domestic", 1) == 1)
            if not is_domestic and af_config:
                af_start = af_config.get("start_date")
                af_end = af_config.get("end_date")
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
                    reduction = af_config.get("lead_time_reduction") or 14
                    min_lt = af_config.get("min_lead_time") or 7
                    af_lead = max(lead_days - reduction, min_lt)
                    af_dt = current_dt + timedelta(days=af_lead)
                    af_delivery = af_dt.strftime("%Y-%m-%d")
                    af_days_late = max(0, (af_dt - deadline_dt).days)

            certs_str = str(row.get("certifications", ""))
            certs = (
                [c.strip() for c in certs_str.split(",") if c.strip()]
                if certs_str
                else []
            )

            opt = SupplierOption(
                component_id=comp_id,
                supplier_id=sid,
                supplier_name=str(row.get("name", sid)),
                unit_price=float(row["unit_price"]),
                lead_time_days=lead_days,
                delivery_date=delivery_date,
                deadline=deadline,
                days_late=days_late,
                air_freight_delivery=af_delivery,
                air_freight_days_late=af_days_late,
                is_domestic=is_domestic,
                sustainability_rating=str(row.get("sustainability_rating", "C")),
                relationship_tier=str(row.get("relationship_tier", "standard")),
                certifications=certs,
                moq=int(row.get("minimum_order_qty", 0)),
                gap_quantity=gap_qty,
                max_concentration_qty=max_conc_qty,
                fitness_score=0.0,  # computed below
            )
            comp_options.append(opt)
            all_supplier_components[sid].append(comp_id)

        # Pre-filter: prefer on-time suppliers when available
        # If any supplier can deliver by the deadline, drop late ones before scoring.
        # If ALL are late, keep them all — INFEASIBLE_DEADLINE will be flagged downstream.
        if comp_options:
            on_time = [o for o in comp_options if o.days_late <= 0]
            if on_time:
                comp_options = on_time

        # Compute fitness scores
        if comp_options:
            prices = [o.unit_price for o in comp_options]
            price_range = (min(prices), max(prices))
            for opt in comp_options:
                opt.fitness_score = compute_fitness(opt, price_range)
            comp_options.sort(key=lambda o: o.fitness_score, reverse=True)

        options[comp_id] = comp_options

    # Shared suppliers: only those serving 2+ components
    shared_suppliers = {
        sid: comps for sid, comps in all_supplier_components.items() if len(comps) >= 2
    }

    # Priority ordering: earliest deadline first
    deadline_map = {
        row["component_id"]: row["earliest_needed_by"] for _, row in gap_df.iterrows()
    }
    component_priorities = sorted(
        options.keys(), key=lambda cid: deadline_map.get(cid, "9999-12-31")
    )

    return OptionMatrix(
        options=options,
        shared_suppliers=shared_suppliers,
        component_priorities=component_priorities,
    )


# ---------------------------------------------------------------------------
# Greedy allocator
# ---------------------------------------------------------------------------

_conflict_counter = 0


def _next_conflict_id() -> str:
    global _conflict_counter
    _conflict_counter += 1
    return f"CF-{_conflict_counter:03d}"


def greedy_allocate(
    matrix: OptionMatrix,
    constraints: list[Constraint],
    gap_df: pd.DataFrame,
) -> tuple[AllocationPlan, list[Conflict], DecisionLog]:
    """Produce a complete allocation plan covering all gaps.

    Returns (plan, conflicts, decision_log). The plan always covers every component
    in gap_df — either with allocations or with alerts for infeasible cases.
    """
    global _conflict_counter
    _conflict_counter = 0

    plan = AllocationPlan(allocations=[], alerts=[])
    conflicts: list[Conflict] = []
    log = DecisionLog()
    now = datetime.now().isoformat()

    for comp_id in matrix.component_priorities:
        options = matrix.options.get(comp_id, [])
        gap_rows = gap_df[gap_df["component_id"] == comp_id]
        if gap_rows.empty:
            continue
        gap_row = gap_rows.iloc[0]
        gap_qty = int(gap_row["gap"])

        # No eligible suppliers
        if not options:
            cf_id = _next_conflict_id()
            plan.alerts.append(
                PlannedAlert(
                    description=f"No eligible suppliers for {comp_id}. Manual procurement required.",
                    component_id=comp_id,
                )
            )
            conflicts.append(
                Conflict(
                    type=ConflictType.NO_ELIGIBLE_SUPPLIER,
                    component_id=comp_id,
                    description=f"All suppliers for {comp_id} were filtered out by constraints.",
                    options=[
                        {
                            "action": "alert_only",
                            "description": "Create alert for manual procurement",
                        }
                    ],
                    greedy_choice={"action": "alert_only"},
                    data={"gap": gap_qty},
                )
            )
            log.append(
                Decision(
                    component_id=comp_id,
                    action="alert",
                    details={"reason": "no_eligible_supplier"},
                    rationale=f"No eligible suppliers for {comp_id} after constraint filtering.",
                    source="greedy_algorithm",
                    timestamp=now,
                    conflict_id=cf_id,
                )
            )
            continue

        # Check concentration limit
        conc = _concentration_limit(constraints, comp_id)

        if conc:
            # Split across suppliers respecting concentration limit
            max_per_supplier = math.floor(conc["max_pct"] * gap_qty)
            remaining = gap_qty
            for opt in options:
                if remaining <= 0:
                    break
                alloc_qty = min(remaining, max_per_supplier)
                # MOQ roundup
                if alloc_qty < opt.moq:
                    alloc_qty = opt.moq
                plan.allocations.append(
                    Allocation(
                        component_id=comp_id,
                        supplier_id=opt.supplier_id,
                        quantity=alloc_qty,
                        rationale=f"Concentration split: max {conc['max_pct']:.0%}/supplier. "
                        f"Fitness {opt.fitness_score:.2f}.",
                    )
                )
                log.append(
                    Decision(
                        component_id=comp_id,
                        action="allocate",
                        details={
                            "supplier_id": opt.supplier_id,
                            "quantity": alloc_qty,
                            "unit_price": opt.unit_price,
                        },
                        rationale=f"Concentration split to {opt.supplier_id} ({opt.supplier_name}). "
                        f"Fitness {opt.fitness_score:.2f}.",
                        source="greedy_algorithm",
                        timestamp=now,
                        conflict_id=None,
                    )
                )
                remaining -= alloc_qty

            # If we couldn't cover the full gap, assign remainder to top supplier
            if remaining > 0:
                top = options[0]
                plan.allocations.append(
                    Allocation(
                        component_id=comp_id,
                        supplier_id=top.supplier_id,
                        quantity=remaining,
                        rationale=f"Remainder {remaining} units after concentration split. "
                        f"Assigned to top-fitness supplier (slight overshoot possible).",
                    )
                )
                log.append(
                    Decision(
                        component_id=comp_id,
                        action="allocate",
                        details={"supplier_id": top.supplier_id, "quantity": remaining},
                        rationale=f"Remainder allocation to {top.supplier_id}.",
                        source="greedy_algorithm",
                        timestamp=now,
                        conflict_id=None,
                    )
                )

            # Flag CONCENTRATION_SPLIT conflict
            cf_id = _next_conflict_id()
            conflicts.append(
                Conflict(
                    type=ConflictType.CONCENTRATION_SPLIT,
                    component_id=comp_id,
                    description=f"Concentration limit {conc['max_pct']:.0%} forced split across "
                    f"{min(len(options), math.ceil(1.0 / conc['max_pct']))} suppliers.",
                    options=[{"action": "accept", "description": "Keep split as-is"}],
                    greedy_choice={"action": "accept"},
                    data={"max_pct": conc["max_pct"], "suppliers": len(options)},
                )
            )
            log.append(
                Decision(
                    component_id=comp_id,
                    action="flag",
                    details={"conflict_type": "CONCENTRATION_SPLIT"},
                    rationale=f"Concentration limit forced split for {comp_id}.",
                    source="greedy_algorithm",
                    timestamp=now,
                    conflict_id=cf_id,
                )
            )
        else:
            # No concentration limit — allocate full gap to top fitness supplier
            top = options[0]
            alloc_qty = gap_qty
            # MOQ roundup
            if alloc_qty < top.moq:
                cf_id = _next_conflict_id()
                excess = top.moq - alloc_qty
                conflicts.append(
                    Conflict(
                        type=ConflictType.MOQ_OVERBUY,
                        component_id=comp_id,
                        description=f"Gap {alloc_qty} < MOQ {top.moq} for {top.supplier_id}. "
                        f"Rounding up to {top.moq} ({excess} excess).",
                        options=[
                            {
                                "action": "accept",
                                "description": f"Order {top.moq} (accept {excess} excess)",
                            },
                            {
                                "action": "alert_only",
                                "description": "Skip order, create alert",
                            },
                        ],
                        greedy_choice={"action": "accept"},
                        data={"gap": alloc_qty, "moq": top.moq, "excess": excess},
                    )
                )
                log.append(
                    Decision(
                        component_id=comp_id,
                        action="flag",
                        details={
                            "conflict_type": "MOQ_OVERBUY",
                            "moq": top.moq,
                            "gap": alloc_qty,
                        },
                        rationale=f"MOQ roundup: {alloc_qty} → {top.moq}.",
                        source="greedy_algorithm",
                        timestamp=now,
                        conflict_id=cf_id,
                    )
                )
                alloc_qty = top.moq

            plan.allocations.append(
                Allocation(
                    component_id=comp_id,
                    supplier_id=top.supplier_id,
                    quantity=alloc_qty,
                    rationale=f"Top fitness supplier ({top.fitness_score:.2f}). "
                    f"{top.supplier_name}, {top.delivery_date}.",
                )
            )
            log.append(
                Decision(
                    component_id=comp_id,
                    action="allocate",
                    details={
                        "supplier_id": top.supplier_id,
                        "quantity": alloc_qty,
                        "unit_price": top.unit_price,
                    },
                    rationale=f"Allocated to {top.supplier_id} ({top.supplier_name}). "
                    f"Fitness {top.fitness_score:.2f}.",
                    source="greedy_algorithm",
                    timestamp=now,
                    conflict_id=None,
                )
            )

        # Check for infeasible deadline (all suppliers late)
        all_late = all(o.days_late > 0 for o in options)
        if all_late:
            cf_id = _next_conflict_id()
            fastest = min(options, key=lambda o: o.days_late)
            conflicts.append(
                Conflict(
                    type=ConflictType.INFEASIBLE_DEADLINE,
                    component_id=comp_id,
                    description=f"No supplier delivers {comp_id} by {options[0].deadline}. "
                    f"Fastest: {fastest.supplier_id} ({fastest.days_late}d late).",
                    options=[
                        {
                            "action": "accept",
                            "description": "Keep plan, create deadline risk alert",
                        },
                        {
                            "action": "alert_only",
                            "description": "Skip order, alert for manual procurement",
                        },
                    ],
                    greedy_choice={"action": "accept"},
                    data={
                        "deadline": options[0].deadline,
                        "fastest_days_late": fastest.days_late,
                        "fastest_supplier": fastest.supplier_id,
                    },
                )
            )
            plan.alerts.append(
                PlannedAlert(
                    description=f"DEADLINE RISK: {comp_id} — all suppliers deliver late. "
                    f"Fastest: {fastest.supplier_id} ({fastest.days_late}d late).",
                    component_id=comp_id,
                )
            )
            log.append(
                Decision(
                    component_id=comp_id,
                    action="flag",
                    details={"conflict_type": "INFEASIBLE_DEADLINE"},
                    rationale=f"All suppliers late for {comp_id}. Fastest: {fastest.supplier_id}.",
                    source="greedy_algorithm",
                    timestamp=now,
                    conflict_id=cf_id,
                )
            )

        # Soft-preference conflicts
        # DOMESTIC_VS_COST: flagged if both domestic and international suppliers exist
        has_domestic = any(o.is_domestic for o in options)
        has_international = any(not o.is_domestic for o in options)
        if has_domestic and has_international:
            cf_id = _next_conflict_id()
            domestic_opts = [o for o in options if o.is_domestic]
            intl_opts = [o for o in options if not o.is_domestic]
            conflicts.append(
                Conflict(
                    type=ConflictType.DOMESTIC_VS_COST,
                    component_id=comp_id,
                    description=f"Both domestic and international suppliers available for {comp_id}.",
                    options=[{"action": "accept", "description": "Keep greedy choice"}],
                    greedy_choice={"action": "accept"},
                    data={
                        "domestic_cheapest": min(o.unit_price for o in domestic_opts),
                        "international_cheapest": min(o.unit_price for o in intl_opts),
                    },
                )
            )
            log.append(
                Decision(
                    component_id=comp_id,
                    action="flag",
                    details={"conflict_type": "DOMESTIC_VS_COST"},
                    rationale=f"Domestic and international options exist for {comp_id}.",
                    source="greedy_algorithm",
                    timestamp=now,
                    conflict_id=cf_id,
                )
            )

        # STRATEGIC_LOYALTY: if chosen supplier is not strategic but a strategic alternative exists
        comp_allocs = [a for a in plan.allocations if a.component_id == comp_id]
        if comp_allocs:
            chosen_ids = {a.supplier_id for a in comp_allocs}
            chosen_options = [o for o in options if o.supplier_id in chosen_ids]
            strategic_alternatives = [
                o
                for o in options
                if o.relationship_tier == "strategic"
                and o.supplier_id not in chosen_ids
            ]
            non_strategic_chosen = any(
                o.relationship_tier != "strategic" for o in chosen_options
            )
            if non_strategic_chosen and strategic_alternatives:
                cf_id = _next_conflict_id()
                conflicts.append(
                    Conflict(
                        type=ConflictType.STRATEGIC_LOYALTY,
                        component_id=comp_id,
                        description=f"Non-strategic supplier chosen over strategic alternative for {comp_id}.",
                        options=[
                            {"action": "accept", "description": "Keep greedy choice"}
                        ],
                        greedy_choice={"action": "accept"},
                        data={
                            "strategic_alternatives": [
                                o.supplier_id for o in strategic_alternatives
                            ]
                        },
                    )
                )
                log.append(
                    Decision(
                        component_id=comp_id,
                        action="flag",
                        details={"conflict_type": "STRATEGIC_LOYALTY"},
                        rationale=f"Non-strategic chosen over strategic for {comp_id}.",
                        source="greedy_algorithm",
                        timestamp=now,
                        conflict_id=cf_id,
                    )
                )

        # SUSTAINABILITY_TRADEOFF: if chosen has lower sustainability than alternative
        if comp_allocs:
            chosen_options = [o for o in options if o.supplier_id in chosen_ids]
            best_chosen_rating = min(
                (o.sustainability_rating for o in chosen_options),
                key=lambda r: {"A": 0, "B": 1, "C": 2}.get(r, 3),
            )
            better_sustainability = [
                o
                for o in options
                if o.supplier_id not in chosen_ids
                and {"A": 0, "B": 1, "C": 2}.get(o.sustainability_rating, 3)
                < {"A": 0, "B": 1, "C": 2}.get(best_chosen_rating, 3)
            ]
            if better_sustainability:
                cf_id = _next_conflict_id()
                conflicts.append(
                    Conflict(
                        type=ConflictType.SUSTAINABILITY_TRADEOFF,
                        component_id=comp_id,
                        description=f"Lower-sustainability supplier chosen over higher-rated for {comp_id}.",
                        options=[
                            {"action": "accept", "description": "Keep greedy choice"}
                        ],
                        greedy_choice={"action": "accept"},
                        data={
                            "chosen_rating": best_chosen_rating,
                            "better_alternatives": [
                                o.supplier_id for o in better_sustainability
                            ],
                        },
                    )
                )
                log.append(
                    Decision(
                        component_id=comp_id,
                        action="flag",
                        details={"conflict_type": "SUSTAINABILITY_TRADEOFF"},
                        rationale=f"Sustainability tradeoff for {comp_id}.",
                        source="greedy_algorithm",
                        timestamp=now,
                        conflict_id=cf_id,
                    )
                )

    return plan, conflicts, log


# ---------------------------------------------------------------------------
# Cross-component conflict detection
# ---------------------------------------------------------------------------


def detect_cross_component_conflicts(
    plan: AllocationPlan,
    conflicts: list[Conflict],
    log: DecisionLog,
    constraints: list[Constraint],
    matrix: OptionMatrix,
) -> None:
    """Detect plan-wide conflicts that the per-component loop can't see. Mutates in place."""
    now = datetime.now().isoformat()

    # Per-supplier aggregate spend
    supplier_spend: dict[str, float] = defaultdict(float)
    supplier_components: dict[str, set[str]] = defaultdict(set)
    for alloc in plan.allocations:
        # Look up unit price from matrix
        opts = matrix.options.get(alloc.component_id, [])
        price = next(
            (o.unit_price for o in opts if o.supplier_id == alloc.supplier_id), 0.0
        )
        supplier_spend[alloc.supplier_id] += price * alloc.quantity
        supplier_components[alloc.supplier_id].add(alloc.component_id)

    # Budget threshold
    for bc in _constraints_of(constraints, CType.BUDGET_THRESHOLD):
        threshold = bc.params.get("amount", 0)
        for sid, spend in supplier_spend.items():
            if spend > threshold:
                cf_id = _next_conflict_id()
                conflicts.append(
                    Conflict(
                        type=ConflictType.BUDGET_THRESHOLD,
                        component_id="GLOBAL",
                        description=f"Aggregate spend on {sid}: ${spend:,.2f} exceeds "
                        f"${threshold:,.0f} threshold.",
                        options=[
                            {
                                "action": "accept",
                                "description": "Acknowledge and flag for approval",
                            }
                        ],
                        greedy_choice={"action": "accept"},
                        data={
                            "supplier_id": sid,
                            "spend": spend,
                            "threshold": threshold,
                        },
                    )
                )
                log.append(
                    Decision(
                        component_id="GLOBAL",
                        action="flag",
                        details={
                            "conflict_type": "BUDGET_THRESHOLD",
                            "supplier_id": sid,
                            "spend": spend,
                        },
                        rationale=f"Budget threshold crossed for {sid}: ${spend:,.2f}.",
                        source="greedy_algorithm",
                        timestamp=now,
                        conflict_id=cf_id,
                    )
                )

    # Shared supplier load
    for sid, comps in supplier_components.items():
        if len(comps) >= 2:
            cf_id = _next_conflict_id()
            conflicts.append(
                Conflict(
                    type=ConflictType.SHARED_SUPPLIER_LOAD,
                    component_id="GLOBAL",
                    description=f"{sid} allocated across {len(comps)} components: {', '.join(sorted(comps))}. "
                    f"Aggregate spend: ${supplier_spend[sid]:,.2f}.",
                    options=[
                        {"action": "accept", "description": "Acknowledge consolidation"}
                    ],
                    greedy_choice={"action": "accept"},
                    data={
                        "supplier_id": sid,
                        "components": sorted(comps),
                        "spend": supplier_spend[sid],
                    },
                )
            )
            log.append(
                Decision(
                    component_id="GLOBAL",
                    action="flag",
                    details={
                        "conflict_type": "SHARED_SUPPLIER_LOAD",
                        "supplier_id": sid,
                    },
                    rationale=f"Shared supplier load: {sid} across {len(comps)} components.",
                    source="greedy_algorithm",
                    timestamp=now,
                    conflict_id=cf_id,
                )
            )


# ---------------------------------------------------------------------------
# Plan simulation
# ---------------------------------------------------------------------------


def simulate_plan(
    plan: AllocationPlan,
    matrix: OptionMatrix,
    constraints: list[Constraint],
    gap_df: pd.DataFrame,
) -> dict:
    """Validate a plan against all constraints. Returns structured report."""
    violations: list[str] = []
    warnings: list[str] = []

    # 1. Completeness: every component in gap_df should have allocations summing to >= gap
    #    (or have an alert)
    alerted_components = {a.component_id for a in plan.alerts if a.component_id}
    for _, gap_row in gap_df.iterrows():
        comp_id = gap_row["component_id"]
        gap_qty = int(gap_row["gap"])
        alloc_qty = sum(
            a.quantity for a in plan.allocations if a.component_id == comp_id
        )
        if alloc_qty < gap_qty and comp_id not in alerted_components:
            violations.append(
                f"Incomplete: {comp_id} needs {gap_qty}, only {alloc_qty} allocated."
            )

    # 2. Concentration: per-component per-supplier share vs limits
    for _, gap_row in gap_df.iterrows():
        comp_id = gap_row["component_id"]
        conc = _concentration_limit(constraints, comp_id)
        if not conc:
            continue
        gap_qty = int(gap_row["gap"])
        max_qty = math.floor(conc["max_pct"] * gap_qty)
        for alloc in plan.allocations:
            if alloc.component_id == comp_id and alloc.quantity > max_qty:
                violations.append(
                    f"Concentration violation: {alloc.supplier_id} has {alloc.quantity} of "
                    f"{comp_id} (max {max_qty}, limit {conc['max_pct']:.0%})."
                )

    # 3. MOQ: each allocation quantity >= supplier MOQ
    for alloc in plan.allocations:
        opts = matrix.options.get(alloc.component_id, [])
        opt = next((o for o in opts if o.supplier_id == alloc.supplier_id), None)
        if opt and alloc.quantity < opt.moq:
            violations.append(
                f"MOQ violation: {alloc.component_id} from {alloc.supplier_id}: "
                f"{alloc.quantity} < MOQ {opt.moq}."
            )

    # 4. Duplicates: no identical (component, supplier, qty) pairs
    seen: set[tuple[str, str, int]] = set()
    for alloc in plan.allocations:
        key = (alloc.component_id, alloc.supplier_id, alloc.quantity)
        if key in seen:
            violations.append(
                f"Duplicate allocation: {alloc.component_id} from {alloc.supplier_id} × {alloc.quantity}."
            )
        seen.add(key)

    # 5. Delivery: compare delivery dates to deadlines
    for alloc in plan.allocations:
        opts = matrix.options.get(alloc.component_id, [])
        opt = next((o for o in opts if o.supplier_id == alloc.supplier_id), None)
        if opt and opt.days_late > 0:
            warnings.append(
                f"Late delivery: {alloc.component_id} from {alloc.supplier_id} delivers "
                f"{opt.delivery_date}, {opt.days_late}d after deadline {opt.deadline}."
            )

    # 6. Budget: per-supplier aggregate vs thresholds
    supplier_spend: dict[str, float] = defaultdict(float)
    for alloc in plan.allocations:
        opts = matrix.options.get(alloc.component_id, [])
        opt = next((o for o in opts if o.supplier_id == alloc.supplier_id), None)
        price = opt.unit_price if opt else 0.0
        supplier_spend[alloc.supplier_id] += price * alloc.quantity

    for bc in _constraints_of(constraints, CType.BUDGET_THRESHOLD):
        threshold = bc.params.get("amount", 0)
        for sid, spend in supplier_spend.items():
            if spend > threshold:
                warnings.append(
                    f"Budget threshold: {sid} aggregate spend ${spend:,.2f} "
                    f"exceeds ${threshold:,.0f}."
                )

    # Determine verdict
    if violations:
        verdict = "FAIL"
    elif warnings:
        verdict = "PASS_WITH_WARNINGS"
    else:
        verdict = "PASS"

    total_spend = sum(supplier_spend.values())
    summary = (
        f"{verdict}: {len(plan.allocations)} allocations, {len(plan.alerts)} alerts. "
        f"Total spend: ${total_spend:,.2f}. "
        f"{len(violations)} violations, {len(warnings)} warnings."
    )

    return {
        "verdict": verdict,
        "violations": violations,
        "warnings": warnings,
        "summary": summary,
    }
