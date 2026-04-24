"""Deterministic plan executor — places orders and creates alerts.

Takes a finalized AllocationPlan and executes it by invoking the existing
place_order and create_alert tools. No LLM involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from procureai.agents.tools import ProcurementContext
from procureai.planner import AllocationPlan


@dataclass
class ExecutionResult:
    """Result of executing an allocation plan."""

    orders_placed: int = 0
    alerts_created: int = 0
    errors: list[dict] = field(default_factory=list)
    order_results: list[str] = field(default_factory=list)


def execute_plan(
    plan: AllocationPlan,
    ctx: ProcurementContext,
    tools: list,
) -> ExecutionResult:
    """Execute a finalized allocation plan deterministically.

    For each allocation, invokes place_order. For each alert, invokes create_alert.
    Errors are captured but do not halt execution.
    """
    tool_map = {t.name: t for t in tools}
    place_order = tool_map["place_order"]
    create_alert = tool_map["create_alert"]

    result = ExecutionResult()

    for alloc in plan.allocations:
        try:
            response = place_order.invoke(
                {
                    "component_id": alloc.component_id,
                    "supplier_id": alloc.supplier_id,
                    "quantity": alloc.quantity,
                    "rationale": alloc.rationale,
                }
            )
            if "ERROR" in response:
                result.errors.append(
                    {
                        "component_id": alloc.component_id,
                        "supplier_id": alloc.supplier_id,
                        "error": response,
                    }
                )
            else:
                result.orders_placed += 1
                result.order_results.append(response)
        except Exception as e:
            result.errors.append(
                {
                    "component_id": alloc.component_id,
                    "supplier_id": alloc.supplier_id,
                    "error": str(e),
                }
            )

    for alert in plan.alerts:
        desc = alert.description
        if alert.component_id and alert.component_id not in desc:
            desc = f"[{alert.component_id}] {desc}"
        create_alert.invoke({"description": desc})
        result.alerts_created += 1

    return result
