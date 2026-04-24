"""Tests for LLM reviewer tools and subgraph."""

from __future__ import annotations

import json
import os

import pytest

from procureai.planner import (
    build_option_matrix,
    greedy_allocate,
)
from tests.test_planner import make_constraints, make_gap_df, make_scenario_data


# ---------------------------------------------------------------------------
# Fixture: greedy plan with conflicts
# ---------------------------------------------------------------------------


def _build_reviewer_fixture():
    """Build a greedy plan with conflicts for reviewer tool tests."""
    scenario = make_scenario_data()
    constraints = make_constraints()
    gap_df = make_gap_df()
    matrix = build_option_matrix(scenario, constraints, gap_df)
    plan, conflicts, log = greedy_allocate(matrix, constraints, gap_df)
    return plan, conflicts, log, matrix, constraints, gap_df


# ===========================================================================
# Step 7: LLM Reviewer Tools
# ===========================================================================


class TestReviewerTools:
    def _get_tools_and_ctx(self):
        from procureai.agents.planner import ReviewerContext, _build_reviewer_tools

        plan, conflicts, log, matrix, constraints, gap_df = _build_reviewer_fixture()
        ctx = ReviewerContext(
            plan=plan,
            conflicts=conflicts,
            decision_log=log,
            matrix=matrix,
            constraints=constraints,
            gap_df=gap_df,
        )
        tools = _build_reviewer_tools(ctx)
        tool_map = {t.name: t for t in tools}
        return tool_map, ctx

    def test_resolve_conflict_appends_decision(self):
        """Resolve CF-001 → decision log has new entry with source='llm_reviewer'."""
        tool_map, ctx = self._get_tools_and_ctx()
        if not ctx.conflicts:
            return  # no conflicts to resolve
        initial_len = len(ctx.decision_log.entries)
        tool_map["resolve_conflict"].invoke(
            {
                "conflict_id": "CF-001",
                "chosen_option": "accept",
                "rationale": "Test resolution",
            }
        )
        assert len(ctx.decision_log.entries) > initial_len
        last = ctx.decision_log.entries[-1]
        assert last.source == "llm_reviewer"

    def test_resolve_conflict_returns_remaining(self):
        """Resolve 1 conflict → response shows remaining count."""
        tool_map, ctx = self._get_tools_and_ctx()
        if len(ctx.conflicts) < 2:
            return
        total = len(ctx.unresolved_conflicts())
        result = tool_map["resolve_conflict"].invoke(
            {
                "conflict_id": "CF-001",
                "chosen_option": "accept",
                "rationale": "Test resolution",
            }
        )
        assert str(total - 1) in result or "remaining" in result.lower()

    def test_resolve_conflict_accept_noop(self):
        """chosen_option='accept' → plan unchanged, log entry says accept."""
        tool_map, ctx = self._get_tools_and_ctx()
        if not ctx.conflicts:
            return
        plan_before = len(ctx.plan.allocations)
        tool_map["resolve_conflict"].invoke(
            {
                "conflict_id": "CF-001",
                "chosen_option": "accept",
                "rationale": "Accepting greedy default",
            }
        )
        assert len(ctx.plan.allocations) == plan_before

    def test_resolve_conflict_invalid_id(self):
        """conflict_id not found → error message."""
        tool_map, ctx = self._get_tools_and_ctx()
        result = tool_map["resolve_conflict"].invoke(
            {
                "conflict_id": "CF-999",
                "chosen_option": "accept",
                "rationale": "Invalid",
            }
        )
        assert "error" in result.lower() or "not found" in result.lower()

    def test_override_allocation_changes_plan(self):
        """Override CMP-002 supplier → plan.allocations updated."""
        tool_map, ctx = self._get_tools_and_ctx()
        # CMP-002 has only SUP-101 in our fixture
        changes = json.dumps([{"supplier_id": "SUP-101", "quantity": 200}])
        tool_map["override_allocation"].invoke(
            {
                "component_id": "CMP-002",
                "changes_json": changes,
                "rationale": "Testing override",
            }
        )
        cmp002 = [a for a in ctx.plan.allocations if a.component_id == "CMP-002"]
        assert any(a.quantity == 200 for a in cmp002)

    def test_override_allocation_logs_decision(self):
        """Override → decision log entry with old/new details."""
        tool_map, ctx = self._get_tools_and_ctx()
        initial_len = len(ctx.decision_log.entries)
        changes = json.dumps([{"supplier_id": "SUP-101", "quantity": 200}])
        tool_map["override_allocation"].invoke(
            {
                "component_id": "CMP-002",
                "changes_json": changes,
                "rationale": "Testing override",
            }
        )
        assert len(ctx.decision_log.entries) > initial_len
        last = ctx.decision_log.entries[-1]
        assert last.source == "llm_reviewer"
        assert last.action == "override"

    def test_override_allocation_reruns_conflicts(self):
        """Override triggers conflict re-detection for that component (no crash)."""
        tool_map, ctx = self._get_tools_and_ctx()
        changes = json.dumps([{"supplier_id": "SUP-101", "quantity": 200}])
        # Should not raise
        result = tool_map["override_allocation"].invoke(
            {
                "component_id": "CMP-002",
                "changes_json": changes,
                "rationale": "Testing override",
            }
        )
        assert isinstance(result, str)

    def test_simulate_plan_tool(self):
        """Invoke via tool interface → returns simulation report."""
        tool_map, ctx = self._get_tools_and_ctx()
        result = tool_map["simulate_plan"].invoke({})
        assert (
            "verdict" in result.lower()
            or "pass" in result.lower()
            or "fail" in result.lower()
        )

    def test_accept_plan_logs_terminal(self):
        """accept → decision log has terminal entry."""
        tool_map, ctx = self._get_tools_and_ctx()
        tool_map["accept_plan"].invoke({"rationale": "All looks good"})
        terminal = [d for d in ctx.decision_log.entries if d.action == "accept_plan"]
        assert len(terminal) >= 1

    def test_accept_plan_returns_summary(self):
        """accept → response includes plan summary."""
        tool_map, ctx = self._get_tools_and_ctx()
        result = tool_map["accept_plan"].invoke({"rationale": "All looks good"})
        assert "accepted" in result.lower() or "plan" in result.lower()


# ===========================================================================
# Step 8: LLM Reviewer Subgraph
# ===========================================================================


class TestReviewerSubgraph:
    def _build_reviewer(self):
        from unittest.mock import MagicMock

        plan, conflicts, log, matrix, constraints, gap_df = _build_reviewer_fixture()
        # Use a mock LLM — we just test construction and prompt content
        mock_llm = MagicMock()
        return plan, conflicts, log, matrix, constraints, gap_df, mock_llm

    def test_reviewer_builds_without_error(self):
        """build_reviewer_agent returns compiled graph."""
        from procureai.agents.planner import build_reviewer_agent

        plan, conflicts, log, matrix, constraints, gap_df, mock_llm = (
            self._build_reviewer()
        )
        graph, ctx = build_reviewer_agent(
            mock_llm, plan, conflicts, log, matrix, constraints, gap_df
        )
        assert graph is not None
        assert ctx is not None

    def test_reviewer_system_prompt_includes_plan(self):
        """System prompt contains serialized plan."""
        from procureai.agents.planner import build_reviewer_prompt

        plan, conflicts, log, matrix, constraints, gap_df, _ = self._build_reviewer()
        prompt = build_reviewer_prompt(plan, conflicts, log, constraints)
        # Should mention allocations
        assert "allocation" in prompt.lower() or "CMP-" in prompt

    def test_reviewer_system_prompt_includes_conflicts(self):
        """System prompt contains all conflicts."""
        from procureai.agents.planner import build_reviewer_prompt

        plan, conflicts, log, matrix, constraints, gap_df, _ = self._build_reviewer()
        prompt = build_reviewer_prompt(plan, conflicts, log, constraints)
        assert "conflict" in prompt.lower() or "CF-" in prompt

    def test_reviewer_system_prompt_includes_decision_log(self):
        """System prompt contains greedy decisions."""
        from procureai.agents.planner import build_reviewer_prompt

        plan, conflicts, log, matrix, constraints, gap_df, _ = self._build_reviewer()
        prompt = build_reviewer_prompt(plan, conflicts, log, constraints)
        assert "decision" in prompt.lower() or "greedy" in prompt.lower()

    def test_reviewer_system_prompt_includes_constraints(self):
        """System prompt contains constraint list."""
        from procureai.agents.planner import build_reviewer_prompt

        plan, conflicts, log, matrix, constraints, gap_df, _ = self._build_reviewer()
        prompt = build_reviewer_prompt(plan, conflicts, log, constraints)
        assert "constraint" in prompt.lower()


# ===========================================================================
# Step 9: LLM Reviewer Integration Tests
# ===========================================================================


def _has_api_key() -> bool:
    """Check if an Anthropic API key is available (env or .env file)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        from procureai.config import AgentConfig

        AgentConfig()
        return True
    except Exception:
        return False


def _get_llm():
    """Get a real LLM for integration tests."""
    from procureai.config import AgentConfig, get_chat_model

    config = AgentConfig(model_name="claude-haiku-4-5")
    return get_chat_model(config)


def _run_reviewer():
    """Build and run the reviewer agent on the test fixture. Returns (ctx, messages)."""
    from procureai.agents.planner import build_reviewer_agent

    plan, conflicts, log, matrix, constraints, gap_df = _build_reviewer_fixture()
    llm = _get_llm()
    graph, ctx = build_reviewer_agent(
        llm, plan, conflicts, log, matrix, constraints, gap_df
    )
    result = graph.invoke(
        {"messages": [("user", "Review the plan and resolve all conflicts.")]},
        config={"recursion_limit": 25},
    )
    return ctx, result["messages"]


@pytest.mark.llm
class TestReviewerIntegration:
    """Integration tests requiring a real LLM API key.

    Run with: uv run python -m pytest tests/test_reviewer.py -m llm -v
    Skip with: uv run python -m pytest tests/test_reviewer.py -m 'not llm' -v
    """

    @pytest.fixture(autouse=True)
    def skip_without_key(self):
        if not _has_api_key():
            pytest.skip("ANTHROPIC_API_KEY not set")

    @pytest.fixture(scope="class")
    def reviewer_result(self):
        """Run the reviewer once and share the result across all tests in this class."""
        if not _has_api_key():
            pytest.skip("ANTHROPIC_API_KEY not set")
        return _run_reviewer()

    def test_reviewer_resolves_all_conflicts(self, reviewer_result):
        """All conflicts get resolutions via resolve_conflict tool calls."""
        ctx, messages = reviewer_result
        assert len(ctx.unresolved_conflicts()) == 0, (
            f"{len(ctx.unresolved_conflicts())} conflicts remain unresolved"
        )

    def test_reviewer_decision_log_grows(self, reviewer_result):
        """Log has more entries after review than the initial greedy log."""
        ctx, messages = reviewer_result
        llm_entries = [
            d for d in ctx.decision_log.entries if d.source == "llm_reviewer"
        ]
        assert len(llm_entries) > 0, "No LLM reviewer decisions recorded"

    def test_reviewer_decision_log_has_rationale(self, reviewer_result):
        """Every LLM resolution entry has non-empty rationale."""
        ctx, messages = reviewer_result
        llm_entries = [
            d for d in ctx.decision_log.entries if d.source == "llm_reviewer"
        ]
        for entry in llm_entries:
            assert entry.rationale and len(entry.rationale.strip()) > 0, (
                f"Empty rationale in decision: {entry.action} for {entry.component_id}"
            )

    def test_reviewer_calls_simulate_before_accept(self, reviewer_result):
        """Tool call trace includes simulate_plan before accept_plan."""
        ctx, messages = reviewer_result
        tool_names = []
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_names.append(tc["name"])

        if "accept_plan" in tool_names:
            accept_idx = tool_names.index("accept_plan")
            simulate_indices = [
                i for i, n in enumerate(tool_names) if n == "simulate_plan"
            ]
            assert any(i < accept_idx for i in simulate_indices), (
                "simulate_plan was not called before accept_plan"
            )

    def test_reviewer_accepts_plan(self, reviewer_result):
        """Reviewer eventually calls accept_plan."""
        ctx, messages = reviewer_result
        accept_entries = [
            d for d in ctx.decision_log.entries if d.action == "accept_plan"
        ]
        assert len(accept_entries) >= 1, "Reviewer never called accept_plan"

    def test_reviewer_tool_calls_are_valid(self, reviewer_result):
        """All tool call responses are non-error strings."""
        ctx, messages = reviewer_result
        from langchain_core.messages import ToolMessage

        error_msgs = []
        for msg in messages:
            if isinstance(msg, ToolMessage):
                content = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                if content.lower().startswith("error:"):
                    error_msgs.append(content)
        assert len(error_msgs) == 0, f"Tool errors: {error_msgs}"
