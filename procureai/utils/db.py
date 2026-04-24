"""SQLite scenario database loading utilities."""

import dataclasses
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# All tables present in each scenario database
TABLE_NAMES = [
    "products",
    "components",
    "bom",
    "suppliers",
    "supplier_catalog",
    "inventory",
    "production_schedule",
    "purchase_orders",
    "alerts",
]

# Default data directory resolved relative to this file's location
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "scenarios"


@dataclasses.dataclass
class ScenarioData:
    """All tables from a single scenario database bundled together."""

    current_date: str
    description: str
    db_path: Path
    products: pd.DataFrame
    components: pd.DataFrame
    bom: pd.DataFrame
    suppliers: pd.DataFrame
    supplier_catalog: pd.DataFrame
    inventory: pd.DataFrame
    production_schedule: pd.DataFrame
    purchase_orders: pd.DataFrame
    alerts: pd.DataFrame


def load_table(db_path: str | Path, table: str) -> pd.DataFrame:
    """Load a single table from a scenario database into a DataFrame."""
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)


def load_scenario(db_path: str | Path) -> ScenarioData:
    """Load all tables and config from a scenario database."""
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as conn:
        config = pd.read_sql_query("SELECT * FROM scenario_config", conn)
        tables = {
            name: pd.read_sql_query(f"SELECT * FROM {name}", conn)
            for name in TABLE_NAMES
        }

    return ScenarioData(
        current_date=config.iloc[0]["current_date"],
        description=config.iloc[0]["scenario_description"],
        db_path=db_path,
        **tables,
    )


# ---------------------------------------------------------------------------
# Run log — tracks which rows each agent run inserted
# ---------------------------------------------------------------------------

_RUN_LOG_SCHEMA = """\
CREATE TABLE IF NOT EXISTS run_log (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    model TEXT,
    n_orders INTEGER DEFAULT 0,
    n_alerts INTEGER DEFAULT 0,
    total_spend REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_orders (
    run_id TEXT NOT NULL,
    po_number TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES run_log(run_id)
);

CREATE TABLE IF NOT EXISTS run_alerts (
    run_id TEXT NOT NULL,
    alert_id INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES run_log(run_id)
);

CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    rationale TEXT,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    conflict_id TEXT,
    FOREIGN KEY (run_id) REFERENCES run_log(run_id)
);
"""


def _ensure_run_tables(conn: sqlite3.Connection) -> None:
    """Create run-tracking tables if they don't exist."""
    conn.executescript(_RUN_LOG_SCHEMA)


def start_run(db_path: str | Path, model: str = "") -> str:
    """Register a new agent run and return its run_id."""
    db_path = Path(db_path)
    run_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        _ensure_run_tables(conn)
        conn.execute(
            "INSERT INTO run_log (run_id, started_at, model) VALUES (?, ?, ?)",
            (run_id, now, model),
        )
        conn.commit()
    return run_id


def write_purchase_orders(
    db_path: str | Path, orders: list[dict], run_id: str | None = None
) -> int:
    """INSERT new purchase orders into the database (preserves existing rows).

    If run_id is provided, records a mapping so the run can be cleaned up later.
    Returns the number of rows inserted.
    """
    if not orders:
        return 0
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as conn:
        cols = [
            "po_number",
            "component_id",
            "supplier_id",
            "quantity",
            "unit_price",
            "order_date",
            "expected_delivery_date",
            "rationale",
        ]
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        rows = [tuple(o[c] for c in cols) for o in orders]
        conn.executemany(
            f"INSERT INTO purchase_orders ({col_names}) VALUES ({placeholders})",
            rows,
        )
        if run_id:
            _ensure_run_tables(conn)
            conn.executemany(
                "INSERT INTO run_orders (run_id, po_number) VALUES (?, ?)",
                [(run_id, o["po_number"]) for o in orders],
            )
        conn.commit()
    return len(orders)


def write_alerts(
    db_path: str | Path, alerts: list[str], run_id: str | None = None
) -> int:
    """INSERT alert descriptions into the alerts table.

    If run_id is provided, records a mapping so the run can be cleaned up later.
    Returns the number of rows inserted.
    """
    if not alerts:
        return 0
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as conn:
        _ensure_run_tables(conn)
        alert_ids = []
        for a in alerts:
            cur = conn.execute("INSERT INTO alerts (description) VALUES (?)", (a,))
            alert_ids.append(cur.lastrowid)
        if run_id:
            conn.executemany(
                "INSERT INTO run_alerts (run_id, alert_id) VALUES (?, ?)",
                [(run_id, aid) for aid in alert_ids],
            )
        conn.commit()
    return len(alerts)


def write_decision_log(db_path: str | Path, decisions: list[dict], run_id: str) -> int:
    """INSERT decision log entries into the decision_log table.

    Each dict should have: component_id, action, details (JSON string),
    rationale, source, timestamp, conflict_id.
    Returns the number of rows inserted.
    """
    if not decisions:
        return 0
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as conn:
        _ensure_run_tables(conn)
        cols = [
            "run_id",
            "component_id",
            "action",
            "details",
            "rationale",
            "source",
            "timestamp",
            "conflict_id",
        ]
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        rows = [
            (
                run_id,
                d["component_id"],
                d["action"],
                d.get("details", ""),
                d.get("rationale", ""),
                d["source"],
                d["timestamp"],
                d.get("conflict_id"),
            )
            for d in decisions
        ]
        conn.executemany(
            f"INSERT INTO decision_log ({col_names}) VALUES ({placeholders})",
            rows,
        )
        conn.commit()
    return len(decisions)


def finalize_run(
    db_path: str | Path, run_id: str, n_orders: int, n_alerts: int, total_spend: float
) -> None:
    """Update the run_log with final counts after the agent completes."""
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE run_log SET n_orders=?, n_alerts=?, total_spend=? WHERE run_id=?",
            (n_orders, n_alerts, total_spend, run_id),
        )
        conn.commit()


def clean_run(db_path: str | Path, run_id: str | None = None) -> dict:
    """Remove all rows inserted by a specific run (or the latest run if None).

    Returns a summary of what was deleted.
    """
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as conn:
        _ensure_run_tables(conn)

        # Resolve run_id
        if run_id is None:
            row = conn.execute(
                "SELECT run_id FROM run_log ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {"run_id": None, "orders_deleted": 0, "alerts_deleted": 0}
            run_id = row[0]

        # Delete POs
        po_numbers = [
            r[0]
            for r in conn.execute(
                "SELECT po_number FROM run_orders WHERE run_id=?", (run_id,)
            ).fetchall()
        ]
        orders_deleted = 0
        if po_numbers:
            placeholders = ", ".join("?" for _ in po_numbers)
            cur = conn.execute(
                f"DELETE FROM purchase_orders WHERE po_number IN ({placeholders})",
                po_numbers,
            )
            orders_deleted = cur.rowcount

        # Delete alerts
        alert_ids = [
            r[0]
            for r in conn.execute(
                "SELECT alert_id FROM run_alerts WHERE run_id=?", (run_id,)
            ).fetchall()
        ]
        alerts_deleted = 0
        if alert_ids:
            placeholders = ", ".join("?" for _ in alert_ids)
            cur = conn.execute(
                f"DELETE FROM alerts WHERE alert_id IN ({placeholders})",
                alert_ids,
            )
            alerts_deleted = cur.rowcount

        # Delete decision log entries
        conn.execute("DELETE FROM decision_log WHERE run_id=?", (run_id,))

        # Clean up run tracking tables
        conn.execute("DELETE FROM run_orders WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM run_alerts WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM run_log WHERE run_id=?", (run_id,))
        conn.commit()

    return {
        "run_id": run_id,
        "orders_deleted": orders_deleted,
        "alerts_deleted": alerts_deleted,
    }


def list_runs(db_path: str | Path) -> pd.DataFrame:
    """List all recorded agent runs for a scenario."""
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as conn:
        _ensure_run_tables(conn)
        return pd.read_sql_query("SELECT * FROM run_log ORDER BY started_at DESC", conn)


def list_scenarios(data_dir: str | Path | None = None) -> list[Path]:
    """Find all .sqlite scenario files in the given directory, sorted by name.

    Skips empty files and files without the expected scenario_config table.
    """
    data_dir = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
    valid = []
    for path in sorted(data_dir.glob("scenario_*.sqlite")):
        if path.stat().st_size == 0:
            continue
        valid.append(path)
    return valid
