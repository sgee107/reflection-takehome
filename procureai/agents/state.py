"""LangGraph state schema for the procurement agent."""

from __future__ import annotations

from langgraph.graph import MessagesState


class ProcurementState(MessagesState):
    """State carried through the procurement agent graph.

    Inherits `messages` from MessagesState. Additional fields provide
    pre-computed context that tools and the LLM can reference.
    """

    shortfalls_summary: str = ""
    constraints_summary: str = ""
    scenario_summary: str = ""
    orders_placed: int = 0
    remaining_gaps: int = 0
