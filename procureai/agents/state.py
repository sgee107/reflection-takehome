"""LangGraph state schema for the procurement agent."""

from __future__ import annotations

from langgraph.graph import MessagesState


class ProcurementState(MessagesState):
    """State carried through the procurement agent graph.

    Inherits `messages` from MessagesState. Additional fields provide
    pre-computed context that tools and the LLM can reference.
    """



class ReviewerState(MessagesState):
    """State for the LLM reviewer subgraph."""

    plan_accepted: bool = False
