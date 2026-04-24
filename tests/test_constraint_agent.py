"""Tests for the constraint extraction agent: working set, tools, and subgraph."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from procureai.constraints import ConstraintType
from procureai.utils.db import ScenarioData


# ===========================================================================
# Step 3: ConstraintWorkingSet
# ===========================================================================


class TestWorkingSetSubmit:
    """submit() assigns sequential IDs and returns summaries."""

    def test_submit_assigns_sequential_ids(self):
        from procureai.agents.constraint_graph import ConstraintWorkingSet

        ws = ConstraintWorkingSet()
        id1 = ws.submit(
            type="SUPPLIER_BLOCKED",
            params={"supplier_id": "SUP-113"},
            source="policy.pdf",
            description="Blocked supplier",
        )
        id2 = ws.submit(
            type="CERT_REQUIRED",
            params={"component_id": "CMP-005", "cert_name": "ISO-9001"},
            source="policy.pdf",
            description="Cert required",
        )
        id3 = ws.submit(
            type="HAZMAT_HANDLING",
            params={"component_ids": ["CMP-010"]},
            source="policy.pdf",
            description="Hazmat",
        )
        assert "C-001" in id1
        assert "C-002" in id2
        assert "C-003" in id3

    def test_submit_returns_summary(self):
        from procureai.agents.constraint_graph import ConstraintWorkingSet

        ws = ConstraintWorkingSet()
        result = ws.submit(
            type="SUPPLIER_BLOCKED",
            params={"supplier_id": "SUP-113"},
            source="policy.pdf",
            description="Blocked supplier",
        )
        assert "C-001" in result
        assert "SUPPLIER_BLOCKED" in result


class TestWorkingSetUpdate:
    """update() merges params and replaces description."""

    def test_update_merges_params(self):
        from procureai.agents.constraint_graph import ConstraintWorkingSet

        ws = ConstraintWorkingSet()
        ws.submit(
            type="CONCENTRATION_LIMIT",
            params={"max_pct": 0.7},
            source="policy.pdf",
            description="Concentration limit",
        )
        ws.update("C-001", params={"max_pct": 0.5, "secondary_min_pct": 0.2})
        constraints = ws.get_all()
        assert "0.5" in constraints
        assert "0.2" in constraints

    def test_update_replaces_description(self):
        from procureai.agents.constraint_graph import ConstraintWorkingSet

        ws = ConstraintWorkingSet()
        ws.submit(
            type="CONCENTRATION_LIMIT",
            params={"max_pct": 0.7},
            source="policy.pdf",
            description="Original description",
        )
        ws.update("C-001", description="Updated description")
        constraints = ws.get_all()
        assert "Updated description" in constraints
        assert "Original description" not in constraints

    def test_update_logs_reason(self):
        from procureai.agents.constraint_graph import ConstraintWorkingSet

        ws = ConstraintWorkingSet()
        ws.submit(
            type="CONCENTRATION_LIMIT",
            params={"max_pct": 0.7},
            source="policy.pdf",
            description="Concentration limit",
        )
        ws.update(
            "C-001",
            params={"max_pct": 0.5},
            reason="MEMO-2025-041 overrides policy",
        )
        log = ws.edit_log
        assert len(log) >= 1
        update_entry = [e for e in log if e["action"] == "update"]
        assert len(update_entry) == 1
        assert "MEMO-2025-041" in update_entry[0]["reason"]

    def test_update_invalid_id_errors(self):
        from procureai.agents.constraint_graph import ConstraintWorkingSet

        ws = ConstraintWorkingSet()
        result = ws.update("C-999", params={"max_pct": 0.5})
        assert "error" in result.lower() or "not found" in result.lower()


class TestWorkingSetSupersede:
    """supersede() removes a constraint from the working set."""

    def test_supersede_removes_constraint(self):
        from procureai.agents.constraint_graph import ConstraintWorkingSet

        ws = ConstraintWorkingSet()
        ws.submit(type="A", params={}, source="s", description="first")
        ws.submit(type="B", params={}, source="s", description="second")
        ws.submit(type="C", params={}, source="s", description="third")
        ws.supersede("C-002", reason="No longer applicable")
        all_set = ws.get_all()
        assert "C-001" in all_set
        assert "C-002" not in all_set
        assert "C-003" in all_set

    def test_supersede_logs_reason(self):
        from procureai.agents.constraint_graph import ConstraintWorkingSet

        ws = ConstraintWorkingSet()
        ws.submit(type="A", params={}, source="s", description="first")
        ws.supersede("C-001", reason="Replaced by memo")
        log = ws.edit_log
        supersede_entries = [e for e in log if e["action"] == "supersede"]
        assert len(supersede_entries) == 1
        assert "Replaced by memo" in supersede_entries[0]["reason"]


class TestWorkingSetGetAll:
    """get_all() returns all constraints with IDs, types, and params."""

    def test_get_working_set_returns_all(self):
        from procureai.agents.constraint_graph import ConstraintWorkingSet

        ws = ConstraintWorkingSet()
        ws.submit(
            type="SUPPLIER_BLOCKED",
            params={"supplier_id": "SUP-113"},
            source="policy.pdf",
            description="Blocked",
        )
        ws.submit(
            type="CERT_REQUIRED",
            params={"component_id": "CMP-005"},
            source="policy.pdf",
            description="Cert",
        )
        ws.submit(
            type="HAZMAT_HANDLING",
            params={"component_ids": ["CMP-010"]},
            source="policy.pdf",
            description="Hazmat",
        )
        result = ws.get_all()
        assert "C-001" in result
        assert "C-002" in result
        assert "C-003" in result
        assert "SUPPLIER_BLOCKED" in result
        assert "SUP-113" in result


# ===========================================================================
# Step 4: Read Tools (DB Grounding)
# ===========================================================================


def _make_scenario() -> ScenarioData:
    """Build a minimal ScenarioData for read tool tests."""
    components = pd.DataFrame(
        [
            {
                "component_id": "CMP-003",
                "name": "Neodymium Magnets",
                "raw_material_code": "RM-3003",
                "category": "magnets",
            },
            {
                "component_id": "CMP-005",
                "name": "PCB Assembly",
                "raw_material_code": "RM-5005",
                "category": "electronics",
            },
        ]
    )
    suppliers = pd.DataFrame(
        [
            {
                "supplier_id": "SUP-108",
                "name": "MagnetPro Inc.",
                "is_domestic": 1,
                "on_approved_list": 1,
                "certifications": "ISO-9001, ISO-14001",
            },
            {
                "supplier_id": "SUP-113",
                "name": "Jiangsu Electronics",
                "is_domestic": 0,
                "on_approved_list": 0,
                "certifications": "ISO-9001",
            },
        ]
    )
    supplier_catalog = pd.DataFrame(
        [
            {
                "component_id": "CMP-003",
                "supplier_id": "SUP-108",
                "unit_price": 5.80,
                "lead_time_days": 14,
                "minimum_order_qty": 50,
            },
            {
                "component_id": "CMP-005",
                "supplier_id": "SUP-113",
                "unit_price": 22.00,
                "lead_time_days": 21,
                "minimum_order_qty": 25,
            },
            {
                "component_id": "CMP-005",
                "supplier_id": "SUP-108",
                "unit_price": 30.00,
                "lead_time_days": 10,
                "minimum_order_qty": 10,
            },
        ]
    )
    return ScenarioData(
        current_date="2025-09-01",
        description="Test scenario",
        db_path=Path("/tmp/fake.sqlite"),
        products=pd.DataFrame(),
        components=components,
        bom=pd.DataFrame(),
        suppliers=suppliers,
        supplier_catalog=supplier_catalog,
        inventory=pd.DataFrame(),
        production_schedule=pd.DataFrame(),
        purchase_orders=pd.DataFrame(),
        alerts=pd.DataFrame(),
    )


class TestLookupComponent:
    """lookup_component resolves by name or raw material code."""

    def test_lookup_component_by_name(self):
        from procureai.agents.constraint_graph import _build_read_tools

        scenario = _make_scenario()
        tools = {t.name: t for t in _build_read_tools(scenario)}
        result = tools["lookup_component"].invoke({"search": "neodymium magnets"})
        assert "CMP-003" in result

    def test_lookup_component_by_raw_material(self):
        from procureai.agents.constraint_graph import _build_read_tools

        scenario = _make_scenario()
        tools = {t.name: t for t in _build_read_tools(scenario)}
        result = tools["lookup_component"].invoke({"search": "RM-3003"})
        assert "CMP-003" in result

    def test_lookup_component_not_found(self):
        from procureai.agents.constraint_graph import _build_read_tools

        scenario = _make_scenario()
        tools = {t.name: t for t in _build_read_tools(scenario)}
        result = tools["lookup_component"].invoke({"search": "nonexistent"})
        assert "no matching" in result.lower()


class TestLookupSupplier:
    """lookup_supplier resolves by name or ID."""

    def test_lookup_supplier_by_name(self):
        from procureai.agents.constraint_graph import _build_read_tools

        scenario = _make_scenario()
        tools = {t.name: t for t in _build_read_tools(scenario)}
        result = tools["lookup_supplier"].invoke({"search": "MagnetPro"})
        assert "SUP-108" in result

    def test_lookup_supplier_by_id(self):
        from procureai.agents.constraint_graph import _build_read_tools

        scenario = _make_scenario()
        tools = {t.name: t for t in _build_read_tools(scenario)}
        result = tools["lookup_supplier"].invoke({"search": "SUP-113"})
        assert "SUP-113" in result
        # Should show approval status
        assert "approved" in result.lower() or "on_approved_list" in result.lower()

    def test_lookup_supplier_not_found(self):
        from procureai.agents.constraint_graph import _build_read_tools

        scenario = _make_scenario()
        tools = {t.name: t for t in _build_read_tools(scenario)}
        result = tools["lookup_supplier"].invoke({"search": "nonexistent"})
        assert "no matching" in result.lower()


class TestSearchCatalog:
    """search_catalog filters by component and/or supplier."""

    def test_search_catalog_by_component(self):
        from procureai.agents.constraint_graph import _build_read_tools

        scenario = _make_scenario()
        tools = {t.name: t for t in _build_read_tools(scenario)}
        result = tools["search_catalog"].invoke({"component_id": "CMP-003"})
        assert "SUP-108" in result

    def test_search_catalog_by_supplier(self):
        from procureai.agents.constraint_graph import _build_read_tools

        scenario = _make_scenario()
        tools = {t.name: t for t in _build_read_tools(scenario)}
        result = tools["search_catalog"].invoke({"supplier_id": "SUP-108"})
        assert "CMP-003" in result
        assert "CMP-005" in result

    def test_search_catalog_both(self):
        from procureai.agents.constraint_graph import _build_read_tools

        scenario = _make_scenario()
        tools = {t.name: t for t in _build_read_tools(scenario)}
        result = tools["search_catalog"].invoke(
            {"component_id": "CMP-005", "supplier_id": "SUP-108"}
        )
        assert "CMP-005" in result
        assert "SUP-108" in result
        # Should NOT contain SUP-113's entry
        assert "SUP-113" not in result


# ===========================================================================
# Step 5: Write Tools (Working Set)
# ===========================================================================


class TestWriteTools:
    """Write tools wrap ConstraintWorkingSet as LangChain tools."""

    def test_submit_constraint_tool(self):
        from procureai.agents.constraint_graph import (
            ConstraintWorkingSet,
            _build_write_tools,
        )

        ws = ConstraintWorkingSet()
        tools = {t.name: t for t in _build_write_tools(ws)}
        result = tools["submit_constraint"].invoke(
            {
                "type": "SUPPLIER_BLOCKED",
                "params": '{"supplier_id": "SUP-113"}',
                "source": "policy.pdf",
                "description": "Blocked supplier",
            }
        )
        assert "C-001" in result
        assert "SUPPLIER_BLOCKED" in result

    def test_update_constraint_tool(self):
        from procureai.agents.constraint_graph import (
            ConstraintWorkingSet,
            _build_write_tools,
        )

        ws = ConstraintWorkingSet()
        ws.submit(
            type="CONCENTRATION_LIMIT",
            params={"max_pct": 0.7},
            source="policy.pdf",
            description="Concentration limit",
        )
        tools = {t.name: t for t in _build_write_tools(ws)}
        result = tools["update_constraint"].invoke(
            {
                "constraint_id": "C-001",
                "params": '{"max_pct": 0.5}',
                "reason": "Memo override",
            }
        )
        assert "0.5" in result
        # edit_log should have an entry
        log = ws.edit_log
        update_entries = [e for e in log if e["action"] == "update"]
        assert len(update_entries) == 1

    def test_supersede_constraint_tool(self):
        from procureai.agents.constraint_graph import (
            ConstraintWorkingSet,
            _build_write_tools,
        )

        ws = ConstraintWorkingSet()
        ws.submit(type="A", params={}, source="s", description="first")
        tools = {t.name: t for t in _build_write_tools(ws)}
        result = tools["supersede_constraint"].invoke(
            {"constraint_id": "C-001", "reason": "Replaced"}
        )
        assert "Superseded" in result
        assert "C-001" not in ws.get_all() or "0 constraints" in ws.get_all()

    def test_get_working_set_tool(self):
        from procureai.agents.constraint_graph import (
            ConstraintWorkingSet,
            _build_write_tools,
        )

        ws = ConstraintWorkingSet()
        ws.submit(
            type="SUPPLIER_BLOCKED",
            params={"supplier_id": "SUP-113"},
            source="policy.pdf",
            description="Blocked",
        )
        tools = {t.name: t for t in _build_write_tools(ws)}
        result = tools["get_working_set"].invoke({})
        assert "C-001" in result
        assert "SUPPLIER_BLOCKED" in result

    def test_write_tools_share_working_set(self):
        from procureai.agents.constraint_graph import (
            ConstraintWorkingSet,
            _build_write_tools,
        )

        ws = ConstraintWorkingSet()
        tools = {t.name: t for t in _build_write_tools(ws)}
        # Submit via tool
        tools["submit_constraint"].invoke(
            {
                "type": "CERT_REQUIRED",
                "params": '{"component_id": "CMP-005"}',
                "source": "policy.pdf",
                "description": "Cert needed",
            }
        )
        # Read via different tool
        result = tools["get_working_set"].invoke({})
        assert "CMP-005" in result
        assert "C-001" in result


# ===========================================================================
# Step 6: Constraint Agent Subgraph
# ===========================================================================


class TestConstraintAgentBuild:
    """build_constraint_agent wires tools into a LangGraph graph."""

    def test_agent_builds_without_error(self):
        from unittest.mock import MagicMock

        from procureai.agents.constraint_graph import build_constraint_agent

        llm = MagicMock()
        llm.bind_tools = MagicMock(return_value=llm)
        scenario = _make_scenario()
        documents = ["# Policy\n\nSome policy text."]

        graph, ws = build_constraint_agent(llm, scenario, documents)
        assert graph is not None
        assert ws is not None

    def test_agent_state_has_working_set(self):
        from unittest.mock import MagicMock

        from procureai.agents.constraint_graph import build_constraint_agent

        llm = MagicMock()
        llm.bind_tools = MagicMock(return_value=llm)
        scenario = _make_scenario()
        documents = ["# Policy\n\nSome policy text."]

        graph, ws = build_constraint_agent(llm, scenario, documents)
        # Working set should be accessible and empty initially
        assert ws.edit_log == []
        result = ws.get_all()
        assert "0 constraints" in result


# ===========================================================================
# Step 7: LLM Integration Tests
# ===========================================================================

# These tests require an API key and make real LLM calls.
# Run with: uv run python -m pytest tests/test_constraint_agent.py -k "llm" -v


def _can_run_llm_tests() -> bool:
    """Check if LLM tests can run by trying to load AgentConfig from .env."""
    try:
        from procureai.config import AgentConfig

        AgentConfig(model_name="claude-haiku-4-5")
        return True
    except Exception:
        return False


llm = pytest.mark.skipif(
    not _can_run_llm_tests(),
    reason="Cannot load AgentConfig (missing API key?) — skipping LLM tests",
)


@pytest.fixture
def test_config():
    """Load AgentConfig from .env with Haiku for fast LLM tests."""
    from procureai.config import AgentConfig

    return AgentConfig(model_name="claude-haiku-4-5")


@pytest.fixture
def test_llm(test_config):
    """Get a ChatAnthropic instance configured for testing."""
    from procureai.config import get_chat_model

    return get_chat_model(test_config)


@pytest.fixture
def real_scenario() -> ScenarioData:
    """Load scenario 01 (baseline) for integration testing."""
    from procureai.utils.db import load_scenario

    return load_scenario("data/scenarios/scenario_01_baseline.sqlite")


@pytest.fixture
def policy_markdown() -> str:
    """Convert procurement policy PDF to markdown."""
    import pymupdf4llm

    return pymupdf4llm.to_markdown("data/policies/procurement_policy.pdf")


def _get_memo_markdown(name: str) -> str:
    import pymupdf4llm

    return pymupdf4llm.to_markdown(f"data/memos/{name}")


@llm
class TestPolicyOnlyExtraction:
    """Feed only the procurement policy — verify core constraints are extracted."""

    def test_policy_only_extraction(self, test_llm, real_scenario, policy_markdown):
        from procureai.agents.constraint_graph import build_constraint_agent

        graph, ws = build_constraint_agent(test_llm, real_scenario, [policy_markdown])
        graph.invoke(
            {
                "messages": [
                    ("user", "Extract all constraints from the documents provided.")
                ]
            },
            config={"recursion_limit": 40},
        )

        constraints = ws.to_constraints()
        types = {c.type for c in constraints}

        # Must extract a reasonable number of constraints
        assert len(constraints) >= 5, f"Too few constraints: {len(constraints)}"

        # Must have APPROVED_SUPPLIER_ONLY (fundamental policy rule)
        assert ConstraintType.APPROVED_SUPPLIER_ONLY in types

        # Must have CONCENTRATION_LIMIT
        assert ConstraintType.CONCENTRATION_LIMIT in types

        # Must have CERT_REQUIRED for CMP-005
        assert ConstraintType.CERT_REQUIRED in types
        cert = [c for c in constraints if c.type == ConstraintType.CERT_REQUIRED]
        assert any("CMP-005" in str(c.params) for c in cert)

        # Must have HAZMAT_HANDLING
        assert ConstraintType.HAZMAT_HANDLING in types

        # Must have DOMESTIC_PREFERENCE
        assert ConstraintType.DOMESTIC_PREFERENCE in types


@llm
class TestSupersessionPolicyPlusMemo:
    """Feed policy + concentration memo — verify memo overrides policy."""

    def test_supersession_policy_plus_memo(
        self, test_llm, real_scenario, policy_markdown
    ):
        from procureai.agents.constraint_graph import build_constraint_agent

        memo_md = _get_memo_markdown("memo_2025-04-15_supplier_concentration.pdf")

        graph, ws = build_constraint_agent(
            test_llm, real_scenario, [policy_markdown, memo_md]
        )
        graph.invoke(
            {
                "messages": [
                    ("user", "Extract all constraints from the documents provided.")
                ]
            },
            config={"recursion_limit": 40},
        )

        constraints = ws.to_constraints()

        # Concentration limit should reflect memo override for magnets
        conc = [c for c in constraints if c.type == ConstraintType.CONCENTRATION_LIMIT]
        assert len(conc) >= 1
        # The value 50 or 0.5 should appear somewhere in concentration params
        # (LLM may use max_pct, neodymium_magnets_max_pct, or create a separate constraint)
        conc_str = str([c.params for c in conc])
        assert "50" in conc_str or "0.5" in conc_str, (
            f"Expected 50% concentration limit from memo, got: {conc_str}"
        )

        # Edit log should show operations (submits + updates/supersedes)
        log = ws.edit_log
        assert len(log) > 0


@llm
class TestFullDocumentSet:
    """Feed all 4 documents — verify comprehensive extraction."""

    def test_full_document_set(self, test_llm, real_scenario, policy_markdown):
        from procureai.agents.constraint_graph import build_constraint_agent

        memo1 = _get_memo_markdown("memo_2025-04-15_supplier_concentration.pdf")
        memo2 = _get_memo_markdown("memo_2025-07-01_expedited_shipping.pdf")
        memo3 = _get_memo_markdown("memo_2025-08-20_pcb_quality.pdf")

        graph, ws = build_constraint_agent(
            test_llm, real_scenario, [policy_markdown, memo1, memo2, memo3]
        )
        graph.invoke(
            {
                "messages": [
                    ("user", "Extract all constraints from the documents provided.")
                ]
            },
            config={"recursion_limit": 50},
        )

        constraints = ws.to_constraints()
        types = {c.type for c in constraints}

        # Should have PCB_QUALIFIED_ONLY
        assert (
            ConstraintType.PCB_QUALIFIED_ONLY in types
            or ConstraintType.CERT_REQUIRED in types
        )

        # Should have AIR_FREIGHT_ALLOWED
        assert ConstraintType.AIR_FREIGHT_ALLOWED in types

        # Concentration should reflect memo override
        conc = [c for c in constraints if c.type == ConstraintType.CONCENTRATION_LIMIT]
        assert len(conc) >= 1

        # Reasonable total — no excessive duplicates
        assert len(constraints) <= 20, (
            f"Too many constraints ({len(constraints)}), likely duplicates"
        )

    def test_edit_log_auditability(self, test_llm, real_scenario, policy_markdown):
        from procureai.agents.constraint_graph import build_constraint_agent

        memo1 = _get_memo_markdown("memo_2025-04-15_supplier_concentration.pdf")

        graph, ws = build_constraint_agent(
            test_llm, real_scenario, [policy_markdown, memo1]
        )
        graph.invoke(
            {
                "messages": [
                    ("user", "Extract all constraints from the documents provided.")
                ]
            },
            config={"recursion_limit": 40},
        )

        log = ws.edit_log
        # Should have submit entries for each constraint
        submits = [e for e in log if e["action"] == "submit"]
        assert len(submits) >= 3  # At minimum: blocked, cert, concentration

        # Every entry should have a timestamp
        for entry in log:
            assert "timestamp" in entry


# ===========================================================================
# Step 8: extract_constraints() Orchestrator
# ===========================================================================


@llm
class TestExtractConstraintsOrchestrator:
    """extract_constraints() wires Layer 1 + Layer 2 together."""

    def test_extract_constraints_returns_constraint_list(self, test_llm, real_scenario):
        from procureai.constraints import Constraint, extract_constraints

        result = extract_constraints(
            real_scenario.db_path.parent.parent / "policies",
            real_scenario.db_path.parent.parent / "memos",
            test_llm,
            real_scenario,
        )
        assert isinstance(result, list)
        assert len(result) >= 3
        assert all(isinstance(c, Constraint) for c in result)

    def test_extract_constraints_uses_cache(self, test_llm, real_scenario):
        from procureai.constraints import extract_constraints
        from procureai.extraction import ensure_markdown_cache

        policy_dir = real_scenario.db_path.parent.parent / "policies"
        memo_dir = real_scenario.db_path.parent.parent / "memos"

        # Pre-populate the cache
        cache_dir = policy_dir.parent / "extracted"
        ensure_markdown_cache(policy_dir, memo_dir, cache_dir)

        # Second call should use cached markdown (no re-conversion)
        result = extract_constraints(policy_dir, memo_dir, test_llm, real_scenario)
        assert len(result) >= 3

    def test_extract_constraints_logs_edit_log(self, test_llm, real_scenario, caplog):
        import logging

        from procureai.constraints import extract_constraints

        policy_dir = real_scenario.db_path.parent.parent / "policies"
        memo_dir = real_scenario.db_path.parent.parent / "memos"

        with caplog.at_level(logging.INFO, logger="procureai.constraints"):
            extract_constraints(policy_dir, memo_dir, test_llm, real_scenario)

        # Should log edit_log info
        assert any(
            "edit" in r.message.lower() or "constraint" in r.message.lower()
            for r in caplog.records
        )
