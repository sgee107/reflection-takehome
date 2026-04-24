"""Layer 2: Constraint extraction agent subgraph.

Provides a LangGraph subgraph that processes policy/memo markdown documents,
uses DB grounding tools to resolve IDs, and builds a constraint working set
that handles document supersession (memos overriding policies).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool

from procureai.constraints import Constraint, ConstraintType
from procureai.utils.db import ScenarioData

logger = logging.getLogger(__name__)


class ConstraintWorkingSet:
    """Mutable constraint set with audit trail.

    The LLM agent uses submit/update/supersede to build up constraints.
    Maintains an append-only edit log for observability.
    """

    def __init__(self) -> None:
        self._constraints: list[dict] = []
        self._edit_log: list[dict] = []
        self._seq: int = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"C-{self._seq:03d}"

    def _find(self, constraint_id: str) -> dict | None:
        for c in self._constraints:
            if c["id"] == constraint_id:
                return c
        return None

    def _summary(self) -> str:
        lines = [f"Working set ({len(self._constraints)} constraints):"]
        for c in self._constraints:
            lines.append(
                f"  {c['id']}: {c['type']} — {c['description']} | params={json.dumps(c['params'])}"
            )
        return "\n".join(lines)

    def submit(
        self,
        type: str,
        params: dict | None = None,
        source: str = "",
        description: str = "",
        effective_date: str | None = None,
        expiry_date: str | None = None,
    ) -> str:
        """Add a new constraint to the working set. Returns ID + summary."""
        cid = self._next_id()
        entry = {
            "id": cid,
            "type": type,
            "params": params or {},
            "source": source,
            "description": description,
            "effective_date": effective_date,
            "expiry_date": expiry_date,
        }
        self._constraints.append(entry)
        self._edit_log.append(
            {
                "action": "submit",
                "constraint_id": cid,
                "type": type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": f"Extracted from {source}",
            }
        )
        return f"Submitted {cid} ({type}): {description}\n\n{self._summary()}"

    def update(
        self,
        constraint_id: str,
        params: dict | None = None,
        description: str | None = None,
        reason: str = "",
    ) -> str:
        """Update an existing constraint. Merges params, replaces description."""
        c = self._find(constraint_id)
        if c is None:
            return f"Error: constraint {constraint_id} not found in working set."

        old_params = dict(c["params"])
        old_desc = c["description"]

        if params:
            c["params"].update(params)
        if description is not None:
            c["description"] = description

        self._edit_log.append(
            {
                "action": "update",
                "constraint_id": constraint_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "old_params": old_params,
                "new_params": dict(c["params"]),
                "old_description": old_desc,
                "new_description": c["description"],
            }
        )
        return f"Updated {constraint_id}: {c['description']}\n\n{self._summary()}"

    def supersede(self, constraint_id: str, reason: str = "") -> str:
        """Remove a constraint from the working set."""
        c = self._find(constraint_id)
        if c is None:
            return f"Error: constraint {constraint_id} not found in working set."

        self._constraints = [x for x in self._constraints if x["id"] != constraint_id]
        self._edit_log.append(
            {
                "action": "supersede",
                "constraint_id": constraint_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "removed_constraint": c,
            }
        )
        return (
            f"Superseded {constraint_id} ({c['type']}): {reason}\n\n{self._summary()}"
        )

    def get_all(self) -> str:
        """Return formatted working set."""
        return self._summary()

    def to_constraints(self) -> list[Constraint]:
        """Convert working set entries to Constraint model objects."""
        result = []
        for c in self._constraints:
            try:
                ct = ConstraintType(c["type"])
            except ValueError:
                ct = ConstraintType.OTHER
            result.append(
                Constraint(
                    type=ct,
                    params=c["params"],
                    source=c["source"],
                    description=c["description"],
                    effective_date=c.get("effective_date"),
                    expiry_date=c.get("expiry_date"),
                )
            )
        return result

    @property
    def edit_log(self) -> list[dict]:
        return list(self._edit_log)


# ---------------------------------------------------------------------------
# Read tools (DB grounding)
# ---------------------------------------------------------------------------


def _build_read_tools(scenario: ScenarioData) -> list:
    """Build read-only tools that query the scenario DB for grounding."""

    @tool
    def lookup_component(search: str) -> str:
        """Look up a component by name, component_id, or raw_material_code."""
        df = scenario.components
        search_lower = search.lower()

        mask = df.apply(
            lambda row: any(search_lower in str(v).lower() for v in row.values),
            axis=1,
        )
        matches = df[mask]

        if matches.empty:
            return f"No matching components found for '{search}'."

        lines = []
        for _, row in matches.iterrows():
            lines.append(" | ".join(f"{col}: {row[col]}" for col in df.columns))
        return "\n".join(lines)

    @tool
    def lookup_supplier(search: str) -> str:
        """Look up a supplier by name or supplier_id. Shows approval status and certifications."""
        df = scenario.suppliers
        search_lower = search.lower()

        mask = df.apply(
            lambda row: any(search_lower in str(v).lower() for v in row.values),
            axis=1,
        )
        matches = df[mask]

        if matches.empty:
            return f"No matching suppliers found for '{search}'."

        lines = []
        for _, row in matches.iterrows():
            lines.append(" | ".join(f"{col}: {row[col]}" for col in df.columns))
        return "\n".join(lines)

    @tool
    def search_catalog(component_id: str = "", supplier_id: str = "") -> str:
        """Search the supplier catalog by component_id and/or supplier_id."""
        df = scenario.supplier_catalog
        if component_id:
            df = df[df["component_id"] == component_id]
        if supplier_id:
            df = df[df["supplier_id"] == supplier_id]

        if df.empty:
            return "No matching catalog entries found."

        lines = []
        for _, row in df.iterrows():
            lines.append(
                " | ".join(
                    f"{col}: {row[col]}" for col in scenario.supplier_catalog.columns
                )
            )
        return "\n".join(lines)

    return [lookup_component, lookup_supplier, search_catalog]


# ---------------------------------------------------------------------------
# Write tools (working set mutations)
# ---------------------------------------------------------------------------


def _build_write_tools(working_set: ConstraintWorkingSet) -> list:
    """Build tools that mutate the constraint working set."""

    @tool
    def submit_constraint(
        type: str,
        params: str = "{}",
        source: str = "",
        description: str = "",
        effective_date: str = "",
        expiry_date: str = "",
    ) -> str:
        """Submit a new constraint to the working set. params is a JSON string."""
        parsed_params = json.loads(params) if isinstance(params, str) else params
        return working_set.submit(
            type=type,
            params=parsed_params,
            source=source,
            description=description,
            effective_date=effective_date or None,
            expiry_date=expiry_date or None,
        )

    @tool
    def update_constraint(
        constraint_id: str,
        params: str = "{}",
        description: str = "",
        reason: str = "",
    ) -> str:
        """Update an existing constraint. params is a JSON string to merge."""
        parsed_params = (
            json.loads(params) if isinstance(params, str) and params else None
        )
        return working_set.update(
            constraint_id=constraint_id,
            params=parsed_params,
            description=description or None,
            reason=reason,
        )

    @tool
    def supersede_constraint(constraint_id: str, reason: str = "") -> str:
        """Remove a constraint from the working set (superseded by a memo)."""
        return working_set.supersede(constraint_id=constraint_id, reason=reason)

    @tool
    def get_working_set() -> str:
        """Return the current constraint working set."""
        return working_set.get_all()

    return [submit_constraint, update_constraint, supersede_constraint, get_working_set]


# ---------------------------------------------------------------------------
# Constraint extraction subgraph
# ---------------------------------------------------------------------------

CONSTRAINT_SYSTEM_PROMPT = """\
You are a procurement policy analyst extracting structured constraints from documents.

## Available constraint types and their param schemas:
{type_schemas}

## Instructions:
1. Process each document in order (policies first, then memos).
2. For each constraint you find, use `lookup_component` or `lookup_supplier` to resolve \
names to real IDs (e.g. "neodymium magnets" → CMP-003, "Jiangsu Electronics" → SUP-113).
3. Use `search_catalog` to verify component-supplier relationships when needed.
4. Use `submit_constraint` to add each constraint to the working set.
5. When a memo references or overrides a policy section, use `update_constraint` or \
`supersede_constraint` — do NOT create duplicates.
6. After processing all documents, use `get_working_set` to review the final set.

## Critical extraction rules:
- If a supplier is described as "removed from" or "not on" the approved list, create a \
SUPPLIER_BLOCKED constraint with their supplier_id. Use `lookup_supplier` to resolve the name.
- If a document says a component requires specific certification, that is a CERT_REQUIRED constraint.
- If a component is listed as hazardous/hazmat, create HAZMAT_HANDLING with the component_id.
- Pay close attention to appendix tables, supplier lists, and footnotes — they often contain \
specific blocked suppliers or special handling requirements.
- Use fractional values for percentages: 70% → 0.70, 50% → 0.50, 85% → 0.85.

## Documents to process:
{documents}

Begin by reading the first document and extracting constraints. Use the lookup tools \
to resolve any component or supplier names to their actual IDs before submitting.
"""

_TYPE_SCHEMAS = """
- APPROVED_SUPPLIER_ONLY: {{}} (no params — applies globally)
- SUPPLIER_BLOCKED: {{"supplier_id": str, "reason": str}}
- CERT_REQUIRED: {{"component_id": str, "cert_name": str}}
- DOMESTIC_PREFERENCE: {{"max_premium_pct": float, "critical_max_premium_pct": float}}
- CONCENTRATION_LIMIT: {{"component_ids": list[str], "max_pct": float, "secondary_min_pct": float}}
- CRITICAL_COMPONENT: {{"component_ids": list[str]}}
- MOQ_COMPLIANCE: {{}} (global)
- HAZMAT_HANDLING: {{"component_ids": list[str]}}
- BUDGET_THRESHOLD: {{"amount": float, "approver": str}}
- SUSTAINABILITY_PREFERENCE: {{"price_tolerance_pct": float, "lead_time_tolerance_days": int, "min_rating": str}}
- STRATEGIC_SUPPLIER_PROTECTION: {{"min_savings_pct": float}}
- AIR_FREIGHT_ALLOWED: {{"start_date": str, "end_date": str, "lead_time_reduction": int, "min_lead_time": int, "max_cost": float}}
- PCB_QUALIFIED_ONLY: {{"component_id": str, "note": str}}
- OTHER: {{"rule": str}}
"""


def build_constraint_agent(
    llm: BaseChatModel,
    scenario: ScenarioData,
    documents: list[str],
) -> tuple:
    """Build the constraint extraction agent.

    Args:
        llm: Chat model to use for extraction.
        scenario: Scenario data for DB grounding tools.
        documents: List of markdown document texts to process.

    Returns:
        (compiled_graph, ConstraintWorkingSet)
    """
    from langgraph.graph import END, StateGraph
    from langgraph.prebuilt import ToolNode

    from procureai.agents.state import ProcurementState

    working_set = ConstraintWorkingSet()

    read_tools = _build_read_tools(scenario)
    write_tools = _build_write_tools(working_set)
    all_tools = read_tools + write_tools

    # Format documents for the prompt
    doc_texts = []
    for i, doc in enumerate(documents, 1):
        doc_texts.append(f"### Document {i}\n{doc}")
    docs_formatted = "\n\n---\n\n".join(doc_texts)

    system_prompt = CONSTRAINT_SYSTEM_PROMPT.format(
        type_schemas=_TYPE_SCHEMAS,
        documents=docs_formatted,
    )

    model_with_tools = llm.bind_tools(all_tools)

    def extract_node(state):
        messages = state["messages"]
        response = model_with_tools.invoke(
            [{"role": "system", "content": system_prompt}] + messages
        )
        return {"messages": [response]}

    def should_continue(state):
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    tool_node = ToolNode(all_tools)

    graph = StateGraph(ProcurementState)
    graph.add_node("extract", extract_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("extract")
    graph.add_conditional_edges(
        "extract", should_continue, {"tools": "tools", END: END}
    )
    graph.add_edge("tools", "extract")

    compiled = graph.compile()
    return compiled, working_set
