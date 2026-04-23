"""Shared fixtures for verification tests."""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from procureai.utils.db import ScenarioData, load_scenario, load_table

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "scenarios"

SCENARIO_FILES = sorted(DATA_DIR.glob("scenario_*.sqlite"))


def _has_agent_orders(db_path: Path) -> bool:
    """Check if the scenario DB has any agent-placed POs (PO-AGENT-*)."""
    with sqlite3.connect(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM purchase_orders WHERE po_number LIKE 'PO-AGENT-%'"
            ).fetchone()
            return row[0] > 0
        except Exception:
            return False


def _scenario_label(path: Path) -> str:
    """Extract short label like 'scenario_01' from path."""
    return path.stem


# Build list of scenarios that have been run (have agent POs)
_RUN_SCENARIOS = [p for p in SCENARIO_FILES if _has_agent_orders(p)]


@pytest.fixture(params=_RUN_SCENARIOS, ids=[_scenario_label(p) for p in _RUN_SCENARIOS])
def scenario_db(request) -> Path:
    """Parameterized fixture yielding each scenario DB path that has agent runs."""
    return request.param


@pytest.fixture
def scenario_data(scenario_db) -> ScenarioData:
    """Load full scenario data for a scenario DB."""
    return load_scenario(scenario_db)


@pytest.fixture
def agent_orders(scenario_db) -> pd.DataFrame:
    """Load only agent-placed purchase orders."""
    df = load_table(scenario_db, "purchase_orders")
    return df[df["po_number"].str.startswith("PO-AGENT-")].reset_index(drop=True)


@pytest.fixture
def agent_alerts(scenario_db) -> pd.DataFrame:
    """Load all alerts from the scenario."""
    return load_table(scenario_db, "alerts")


@pytest.fixture
def supplier_catalog(scenario_db) -> pd.DataFrame:
    return load_table(scenario_db, "supplier_catalog")


@pytest.fixture
def suppliers(scenario_db) -> pd.DataFrame:
    return load_table(scenario_db, "suppliers")


@pytest.fixture
def components(scenario_db) -> pd.DataFrame:
    return load_table(scenario_db, "components")


@pytest.fixture
def scenario_config(scenario_db) -> dict:
    """Return current_date and description."""
    with sqlite3.connect(scenario_db) as conn:
        row = conn.execute("SELECT * FROM scenario_config").fetchone()
        cols = [d[0] for d in conn.execute("SELECT * FROM scenario_config").description]
    return dict(zip(cols, row))
