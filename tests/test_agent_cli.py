"""Tests for agent.py CLI rewiring (Step 11)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
from click.testing import CliRunner

from agent import main


SCENARIO_PATH = "data/scenarios/scenario_06_simple.sqlite"


def _mock_gap_df():
    """A non-empty gap DataFrame for testing."""
    return pd.DataFrame(
        {
            "component_id": ["CMP-001", "CMP-002"],
            "total_needed": [200, 150],
            "on_hand": [100, 50],
            "incoming": [0, 0],
            "gap": [100, 100],
            "earliest_needed_by": ["2025-10-01", "2025-10-15"],
        }
    )


def _mock_reviewer():
    """Return a mock (graph, ctx) for the reviewer agent."""
    mock_ctx = MagicMock()
    # plan with no allocations after review (simplified)
    from procureai.planner import AllocationPlan, DecisionLog

    mock_ctx.plan = AllocationPlan(allocations=[], alerts=[])
    mock_ctx.decision_log = DecisionLog()
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {"messages": []}
    return mock_graph, mock_ctx


class TestAgentCLI:
    def test_plan_only_flag_no_execution(self):
        """--plan-only → plan printed, no POs written to DB."""
        runner = CliRunner()
        with (
            patch("agent.get_chat_model") as mock_llm_factory,
            patch("agent.extract_constraints", return_value=[]),
            patch("agent.gap_analysis", return_value=_mock_gap_df()),
            patch(
                "procureai.agents.planner.build_reviewer_agent",
                return_value=_mock_reviewer(),
            ),
            patch("agent.write_purchase_orders") as mock_write_po,
            patch("agent.write_alerts") as mock_write_alerts,
            patch("agent.start_run", return_value="test-run"),
            patch("agent.finalize_run"),
        ):
            mock_llm = MagicMock()
            mock_llm_factory.return_value = mock_llm

            result = runner.invoke(
                main,
                [
                    "--scenario",
                    SCENARIO_PATH,
                    "--plan-only",
                ],
            )

            assert result.exit_code == 0, (
                f"Exit code: {result.exit_code}\n{result.output}"
            )
            mock_write_po.assert_not_called()
            mock_write_alerts.assert_not_called()
            assert (
                "plan" in result.output.lower() or "allocation" in result.output.lower()
            )

    def test_full_pipeline_writes_results(self):
        """Default mode → POs and alerts written to DB."""
        runner = CliRunner()
        with (
            patch("agent.get_chat_model") as mock_llm_factory,
            patch("agent.extract_constraints", return_value=[]),
            patch("agent.gap_analysis", return_value=_mock_gap_df()),
            patch(
                "procureai.agents.planner.build_reviewer_agent",
                return_value=_mock_reviewer(),
            ),
            patch("agent.write_purchase_orders", return_value=3) as mock_write_po,
            patch("agent.write_alerts", return_value=1) as mock_write_alerts,
            patch("agent.start_run", return_value="test-run"),
            patch("agent.finalize_run"),
        ):
            mock_llm = MagicMock()
            mock_llm_factory.return_value = mock_llm

            result = runner.invoke(
                main,
                [
                    "--scenario",
                    SCENARIO_PATH,
                ],
            )

            assert result.exit_code == 0, (
                f"Exit code: {result.exit_code}\n{result.output}"
            )
            mock_write_po.assert_called_once()
            mock_write_alerts.assert_called_once()

    def test_decision_log_printed_verbose(self):
        """--verbose → decision log entries printed."""
        runner = CliRunner()
        with (
            patch("agent.get_chat_model") as mock_llm_factory,
            patch("agent.extract_constraints", return_value=[]),
            patch("agent.gap_analysis", return_value=_mock_gap_df()),
            patch(
                "procureai.agents.planner.build_reviewer_agent",
                return_value=_mock_reviewer(),
            ),
            patch("agent.write_purchase_orders", return_value=0),
            patch("agent.write_alerts", return_value=0),
            patch("agent.start_run", return_value="test-run"),
            patch("agent.finalize_run"),
        ):
            mock_llm = MagicMock()
            mock_llm_factory.return_value = mock_llm

            result = runner.invoke(
                main,
                [
                    "--scenario",
                    SCENARIO_PATH,
                    "--verbose",
                ],
            )

            assert result.exit_code == 0, (
                f"Exit code: {result.exit_code}\n{result.output}"
            )
            output_lower = result.output.lower()
            assert (
                "decision" in output_lower
                or "log" in output_lower
                or "allocat" in output_lower
            )
