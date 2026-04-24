"""LLM reviewer subgraph — tools, graph wiring, system prompt.

The reviewer receives a greedy allocation plan + conflicts and uses
4 tools to triage conflicts, override allocations, simulate changes,
and accept the final plan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from langchain_core.tools import tool

from procureai.constraints import Constraint
from procureai.planner import (
    Allocation,
    AllocationPlan,
    Conflict,
    Decision,
    DecisionLog,
    OptionMatrix,
    simulate_plan,
)


@dataclass
class ReviewerContext:
    """Mutable shared state for the reviewer tools."""

    plan: AllocationPlan
    conflicts: list[Conflict]
    decision_log: DecisionLog
    matrix: OptionMatrix
    constraints: list[Constraint]
    gap_df: pd.DataFrame
    _resolved_ids: set[str] = field(default_factory=set)

    def unresolved_conflicts(self) -> list[Conflict]:
        return [
            c
            for i, c in enumerate(self.conflicts)
            if f"CF-{i + 1:03d}" not in self._resolved_ids
        ]


def _build_reviewer_tools(ctx: ReviewerContext) -> list:
    """Create the 4 reviewer tools as closures over the given context."""

    @tool
    def resolve_conflict(conflict_id: str, chosen_option: str, rationale: str) -> str:
        """Resolve a specific conflict by picking one of its options.

        Args:
            conflict_id: The conflict ID (e.g., 'CF-001').
            chosen_option: The action to take (e.g., 'accept').
            rationale: Why this resolution was chosen.
        """
        # Find the conflict
        cf = None
        for i, c in enumerate(ctx.conflicts):
            if f"CF-{i + 1:03d}" == conflict_id:
                cf = c
                break

        if cf is None:
            return f"Error: Conflict {conflict_id} not found."

        ctx._resolved_ids.add(conflict_id)
        ctx.decision_log.append(
            Decision(
                component_id=cf.component_id,
                action="resolve_conflict",
                details={
                    "conflict_id": conflict_id,
                    "conflict_type": cf.type.value,
                    "chosen_option": chosen_option,
                },
                rationale=rationale,
                source="llm_reviewer",
                timestamp=datetime.now().isoformat(),
                conflict_id=conflict_id,
            )
        )

        remaining = len(ctx.unresolved_conflicts())
        return (
            f"Resolved {conflict_id} ({cf.type.value}): {chosen_option}. "
            f"Rationale: {rationale}. "
            f"{remaining} conflicts remaining."
        )

    @tool
    def override_allocation(
        component_id: str, changes_json: str, rationale: str
    ) -> str:
        """Override allocations for a component.

        Args:
            component_id: The component to override.
            changes_json: JSON array of {supplier_id, quantity} dicts.
            rationale: Why this override was made.
        """
        try:
            changes = json.loads(changes_json)
        except json.JSONDecodeError:
            return "Error: Invalid JSON in changes_json."

        # Remove existing allocations for this component
        old_allocs = [a for a in ctx.plan.allocations if a.component_id == component_id]
        ctx.plan.allocations = [
            a for a in ctx.plan.allocations if a.component_id != component_id
        ]

        # Add new allocations
        for change in changes:
            ctx.plan.allocations.append(
                Allocation(
                    component_id=component_id,
                    supplier_id=change["supplier_id"],
                    quantity=change["quantity"],
                    rationale=rationale,
                    expedite=change.get("expedite", False),
                )
            )

        ctx.decision_log.append(
            Decision(
                component_id=component_id,
                action="override",
                details={
                    "old": [
                        {"supplier_id": a.supplier_id, "quantity": a.quantity}
                        for a in old_allocs
                    ],
                    "new": changes,
                },
                rationale=rationale,
                source="llm_reviewer",
                timestamp=datetime.now().isoformat(),
                conflict_id=None,
            )
        )

        new_allocs = [a for a in ctx.plan.allocations if a.component_id == component_id]
        return (
            f"Override applied for {component_id}: "
            f"{len(new_allocs)} allocation(s). "
            f"Rationale: {rationale}"
        )

    @tool
    def simulate_plan_tool() -> str:
        """Validate the current plan against all constraints. Returns pass/fail/warning report."""
        report = simulate_plan(ctx.plan, ctx.matrix, ctx.constraints, ctx.gap_df)
        return (
            f"Verdict: {report['verdict']}\n"
            f"Violations ({len(report['violations'])}):\n"
            + "\n".join(f"  - {v}" for v in report["violations"])
            + "\n"
            f"Warnings ({len(report['warnings'])}):\n"
            + "\n".join(f"  - {w}" for w in report["warnings"])
            + "\n"
            f"Summary: {report['summary']}"
        )

    @tool
    def accept_plan(rationale: str) -> str:
        """Accept the current plan as final.

        Args:
            rationale: Why the plan is being accepted.
        """
        ctx.decision_log.append(
            Decision(
                component_id="GLOBAL",
                action="accept_plan",
                details={
                    "allocations": len(ctx.plan.allocations),
                    "alerts": len(ctx.plan.alerts),
                },
                rationale=rationale,
                source="llm_reviewer",
                timestamp=datetime.now().isoformat(),
                conflict_id=None,
            )
        )

        total_spend = 0.0
        for alloc in ctx.plan.allocations:
            opts = ctx.matrix.options.get(alloc.component_id, [])
            opt = next((o for o in opts if o.supplier_id == alloc.supplier_id), None)
            if opt:
                total_spend += opt.unit_price * alloc.quantity

        return (
            f"Plan accepted. {len(ctx.plan.allocations)} allocations, "
            f"{len(ctx.plan.alerts)} alerts. "
            f"Estimated spend: ${total_spend:,.2f}. "
            f"Decision log: {len(ctx.decision_log.entries)} entries."
        )

    # Rename simulate_plan_tool to simulate_plan for the tool interface
    simulate_plan_tool.name = "simulate_plan"

    return [resolve_conflict, override_allocation, simulate_plan_tool, accept_plan]


# ---------------------------------------------------------------------------
# Reviewer prompt
# ---------------------------------------------------------------------------


def build_reviewer_prompt(
    plan: AllocationPlan,
    conflicts: list[Conflict],
    decision_log: DecisionLog,
    constraints: list[Constraint],
) -> str:
    """Build the system prompt for the reviewer agent."""
    # Serialize plan
    plan_lines = ["## Default Allocation Plan\n"]
    for alloc in plan.allocations:
        plan_lines.append(
            f"- {alloc.component_id}: {alloc.supplier_id} × {alloc.quantity} "
            f"({'expedite' if alloc.expedite else 'standard'})"
        )
    if plan.alerts:
        plan_lines.append("\nAlerts:")
        for alert in plan.alerts:
            plan_lines.append(f"- {alert.description}")

    # Serialize conflicts
    conflict_lines = ["\n## Conflicts Requiring Resolution\n"]
    for i, cf in enumerate(conflicts):
        cf_id = f"CF-{i + 1:03d}"
        conflict_lines.append(f"### {cf_id} — {cf.type.value} ({cf.component_id})")
        conflict_lines.append(f"  {cf.description}")
        conflict_lines.append("  Options:")
        for opt in cf.options:
            conflict_lines.append(
                f"    - {opt.get('action', '?')}: {opt.get('description', '')}"
            )
        if cf.greedy_choice:
            conflict_lines.append(
                f"  Greedy default: {cf.greedy_choice.get('action', '?')}"
            )
        conflict_lines.append("")

    # Serialize decision log
    log_lines = ["\n## Decision Log (algorithm decisions so far)\n"]
    log_lines.append(decision_log.summary())

    # Serialize constraints
    constraint_lines = ["\n## Active Constraints\n"]
    for c in constraints:
        constraint_lines.append(f"- [{c.type.value}] {c.description or str(c.params)}")

    prompt = (
        "You are a procurement plan reviewer. A greedy algorithm has produced a default "
        "allocation plan for all component shortfalls. Your job is to review it and resolve "
        "any conflicts the algorithm flagged.\n\n"
        + "\n".join(plan_lines)
        + "\n".join(conflict_lines)
        + "\n".join(log_lines)
        + "\n".join(constraint_lines)
        + "\n\n## Your Process\n"
        "1. Review the default plan and conflicts\n"
        "2. For each conflict, use `resolve_conflict` to pick a resolution and explain why\n"
        "3. If you want to change any allocation beyond the conflict resolutions, "
        "use `override_allocation`\n"
        "4. Use `simulate_plan` to verify the final plan has no violations\n"
        "5. Call `accept_plan` when satisfied\n\n"
        "## Principles\n"
        "- The greedy defaults are reasonable — only override with good reason\n"
        "- Every decision must include rationale (this is auditable)\n"
        "- Prefer the algorithm's choice unless the tradeoff is clearly wrong\n"
        "- Create alerts for genuinely infeasible situations rather than forcing bad orders\n"
    )
    return prompt


# ---------------------------------------------------------------------------
# Reviewer subgraph
# ---------------------------------------------------------------------------


def build_reviewer_agent(
    llm,
    plan: AllocationPlan,
    conflicts: list[Conflict],
    decision_log: DecisionLog,
    matrix: OptionMatrix,
    constraints: list[Constraint],
    gap_df: pd.DataFrame,
) -> tuple:
    """Build the reviewer LangGraph agent.

    Returns (compiled_graph, ReviewerContext).
    """
    from langgraph.graph import StateGraph
    from langgraph.prebuilt import ToolNode
    from procureai.agents.state import ReviewerState

    ctx = ReviewerContext(
        plan=plan,
        conflicts=conflicts,
        decision_log=decision_log,
        matrix=matrix,
        constraints=constraints,
        gap_df=gap_df,
    )

    tools = _build_reviewer_tools(ctx)
    system_prompt = build_reviewer_prompt(plan, conflicts, decision_log, constraints)

    llm_with_tools = llm.bind_tools(tools)

    def review_node(state):
        messages = state["messages"]
        from langchain_core.messages import SystemMessage

        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=system_prompt)] + list(messages)
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def should_continue(state):
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return "end"

    graph = StateGraph(ReviewerState)
    graph.add_node("review", review_node)
    graph.add_node("tools", ToolNode(tools))

    graph.set_entry_point("review")
    graph.add_conditional_edges(
        "review", should_continue, {"tools": "tools", "end": "__end__"}
    )
    graph.add_edge("tools", "review")

    compiled = graph.compile()
    return compiled, ctx
