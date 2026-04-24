"""LangGraph agent wiring — builds the ReAct procurement agent."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain.agents import create_agent

from procureai.agents.prompts import build_system_prompt
from procureai.agents.tools import ProcurementContext, build_tools
from procureai.constraints import Constraint
from procureai.pipeline import gap_analysis
from procureai.utils.db import ScenarioData


def build_agent(
    llm: BaseChatModel,
    scenario: ScenarioData,
    constraints: list[Constraint],
) -> tuple:
    """Build the procurement agent graph and its shared context.

    Returns:
        (compiled_graph, ProcurementContext)
    """
    gap_df = gap_analysis(scenario)

    ctx = ProcurementContext(
        scenario=scenario,
        constraints=constraints,
        gap_df=gap_df.copy(),
    )

    tools = build_tools(ctx)
    system_prompt = build_system_prompt(scenario, constraints, gap_df)

    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
    )

    return graph, ctx
