"""Post-run verification tests for ProcureAI agent outputs.

Parameterized across all scenarios that have agent-placed POs.
Each test validates a specific constraint or correctness property.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from procureai.pipeline import gap_analysis
from procureai.utils.db import load_scenario, load_table

# ─── Blocked supplier ────────────────────────────────────────────────────────

BLOCKED_SUPPLIER = "SUP-113"


def test_no_blocked_suppliers(agent_orders):
    """SUP-113 must never receive any orders."""
    blocked = agent_orders[agent_orders["supplier_id"] == BLOCKED_SUPPLIER]
    assert blocked.empty, (
        f"Found {len(blocked)} order(s) to blocked supplier {BLOCKED_SUPPLIER}:\n"
        f"{blocked[['po_number', 'component_id', 'supplier_id']].to_string()}"
    )


# ─── PCB supplier compliance ─────────────────────────────────────────────────

PCB_COMPONENT = "CMP-005"


def test_pcb_supplier_compliance(agent_orders, suppliers):
    """CMP-005 (PCB Assembly) orders must be from ISO-9001 certified suppliers."""
    pcb_orders = agent_orders[agent_orders["component_id"] == PCB_COMPONENT]
    if pcb_orders.empty:
        pytest.skip("No PCB orders in this scenario")

    pcb_with_supplier = pcb_orders.merge(suppliers, on="supplier_id", how="left")
    for _, row in pcb_with_supplier.iterrows():
        certs = str(row.get("certifications", ""))
        assert "ISO-9001" in certs.upper() or "ISO 9001" in certs.upper(), (
            f"{row['po_number']}: PCB ordered from {row['supplier_id']} ({row.get('name', '?')}) "
            f"which lacks ISO-9001 certification. Certs: {certs}"
        )


# ─── Magnet concentration ────────────────────────────────────────────────────

MAGNET_COMPONENT = "CMP-003"


def test_magnet_concentration_max(agent_orders):
    """No single supplier should have >50% of magnet orders (per MEMO-2025-041)."""
    magnet_orders = agent_orders[agent_orders["component_id"] == MAGNET_COMPONENT]
    if magnet_orders.empty:
        pytest.skip("No magnet orders in this scenario")

    total_qty = magnet_orders["quantity"].sum()
    by_supplier = magnet_orders.groupby("supplier_id")["quantity"].sum()

    for supplier_id, qty in by_supplier.items():
        pct = qty / total_qty
        assert pct <= 0.55, (  # 5% tolerance for rounding
            f"Supplier {supplier_id} has {pct:.0%} of magnet orders "
            f"({qty}/{total_qty}), exceeding 50% concentration limit"
        )


def test_magnet_concentration_min_secondary(agent_orders):
    """Secondary magnet supplier must have ≥20% of volume (per MEMO-2025-041)."""
    magnet_orders = agent_orders[agent_orders["component_id"] == MAGNET_COMPONENT]
    if magnet_orders.empty:
        pytest.skip("No magnet orders in this scenario")

    total_qty = magnet_orders["quantity"].sum()
    by_supplier = (
        magnet_orders.groupby("supplier_id")["quantity"]
        .sum()
        .sort_values(ascending=False)
    )

    if len(by_supplier) < 2:
        pytest.fail(
            f"Only 1 magnet supplier used ({by_supplier.index[0]}). "
            f"Memo requires dual-sourcing with ≥20% secondary."
        )

    secondary_qty = by_supplier.iloc[1]
    secondary_pct = secondary_qty / total_qty
    assert secondary_pct >= 0.18, (  # 2% tolerance
        f"Secondary magnet supplier {by_supplier.index[1]} has only {secondary_pct:.0%} "
        f"({secondary_qty}/{total_qty}), below 20% minimum"
    )


# ─── MOQ compliance ──────────────────────────────────────────────────────────


def test_moq_compliance(agent_orders, supplier_catalog):
    """Every order quantity must be ≥ supplier's minimum_order_qty."""
    merged = agent_orders.merge(
        supplier_catalog[["component_id", "supplier_id", "minimum_order_qty"]],
        on=["component_id", "supplier_id"],
        how="left",
    )
    violations = merged[merged["quantity"] < merged["minimum_order_qty"]]
    assert violations.empty, (
        f"MOQ violations found:\n"
        f"{violations[['po_number', 'component_id', 'supplier_id', 'quantity', 'minimum_order_qty']].to_string()}"
    )


# ─── Price accuracy ──────────────────────────────────────────────────────────


def test_price_accuracy(agent_orders, supplier_catalog):
    """Every unit_price must match the supplier_catalog exactly."""
    merged = agent_orders.merge(
        supplier_catalog[["component_id", "supplier_id", "unit_price"]],
        on=["component_id", "supplier_id"],
        how="left",
        suffixes=("_order", "_catalog"),
    )
    # Allow for floating-point tolerance
    mismatches = merged[
        (merged["unit_price_catalog"].notna())
        & ((merged["unit_price_order"] - merged["unit_price_catalog"]).abs() > 0.01)
    ]
    assert mismatches.empty, (
        f"Price mismatches found:\n"
        f"{mismatches[['po_number', 'component_id', 'supplier_id', 'unit_price_order', 'unit_price_catalog']].to_string()}"
    )


# ─── Delivery date math ──────────────────────────────────────────────────────


def test_delivery_date_math(agent_orders, supplier_catalog, scenario_config):
    """expected_delivery_date should be current_date + lead_time_days (possibly adjusted for air freight)."""
    current_date = datetime.strptime(scenario_config["current_date"], "%Y-%m-%d")

    merged = agent_orders.merge(
        supplier_catalog[["component_id", "supplier_id", "lead_time_days"]],
        on=["component_id", "supplier_id"],
        how="left",
    )

    errors = []
    for _, row in merged.iterrows():
        if pd.isna(row["lead_time_days"]):
            continue
        expected_no_af = current_date + timedelta(days=int(row["lead_time_days"]))
        actual = datetime.strptime(row["expected_delivery_date"], "%Y-%m-%d")

        # Allow air freight reduction (delivery could be earlier than catalog lead time)
        # but should never be later than catalog lead time
        if actual > expected_no_af + timedelta(days=1):  # 1 day tolerance
            errors.append(
                f"{row['po_number']}: delivery {row['expected_delivery_date']} is LATER than "
                f"catalog lead time ({int(row['lead_time_days'])}d → {expected_no_af.strftime('%Y-%m-%d')})"
            )

    assert not errors, "Delivery date errors:\n" + "\n".join(errors)


# ─── Rationale present ───────────────────────────────────────────────────────


def test_rationale_present(agent_orders):
    """Every purchase order must have a non-empty rationale."""
    missing = agent_orders[
        agent_orders["rationale"].isna() | (agent_orders["rationale"].str.strip() == "")
    ]
    assert missing.empty, (
        f"{len(missing)} order(s) missing rationale:\n"
        f"{missing[['po_number', 'component_id']].to_string()}"
    )


# ─── No hallucinated IDs ─────────────────────────────────────────────────────


def test_no_hallucinated_suppliers(agent_orders, suppliers):
    """All supplier_ids in POs must exist in the suppliers table."""
    valid_ids = set(suppliers["supplier_id"])
    order_ids = set(agent_orders["supplier_id"])
    hallucinated = order_ids - valid_ids
    assert not hallucinated, f"Hallucinated supplier IDs: {hallucinated}"


def test_no_hallucinated_components(agent_orders, components):
    """All component_ids in POs must exist in the components table."""
    valid_ids = set(components["component_id"])
    order_ids = set(agent_orders["component_id"])
    hallucinated = order_ids - valid_ids
    assert not hallucinated, f"Hallucinated component IDs: {hallucinated}"


# ─── Shortfall coverage ──────────────────────────────────────────────────────


def test_all_shortfalls_addressed(agent_orders, agent_alerts, scenario_data):
    """Every gap from gap_analysis should have either a PO or an alert mentioning it."""
    import copy

    # Compute gaps from pre-agent state (exclude agent-placed POs)
    scenario_copy = copy.copy(scenario_data)
    scenario_copy.purchase_orders = scenario_data.purchase_orders[
        ~scenario_data.purchase_orders["po_number"].str.startswith("PO-AGENT-")
    ]
    gaps = gap_analysis(scenario_copy)
    if gaps.empty:
        pytest.skip("No shortfalls in this scenario")

    ordered_components = set(agent_orders["component_id"])
    alert_text = (
        " ".join(agent_alerts["description"].tolist()) if not agent_alerts.empty else ""
    )

    unaddressed = []
    for _, row in gaps.iterrows():
        cid = row["component_id"]
        if cid not in ordered_components and cid not in alert_text:
            unaddressed.append(cid)

    assert not unaddressed, f"Shortfalls not addressed by PO or alert: {unaddressed}"


# ─── Approved supplier check ─────────────────────────────────────────────────


def test_only_approved_suppliers(agent_orders, suppliers):
    """All orders must go to suppliers on the approved list."""
    merged = agent_orders.merge(
        suppliers[["supplier_id", "on_approved_list"]], on="supplier_id", how="left"
    )
    unapproved = merged[merged["on_approved_list"] != 1]
    assert unapproved.empty, (
        f"Orders placed with unapproved suppliers:\n"
        f"{unapproved[['po_number', 'supplier_id', 'on_approved_list']].to_string()}"
    )


# ─── Duplicate order detection ───────────────────────────────────────────────


def test_no_exact_duplicate_orders(agent_orders):
    """Flag if identical (component_id, supplier_id, quantity) POs exist — likely agent error."""
    dupes = agent_orders.groupby(["component_id", "supplier_id", "quantity"]).size()
    dupes = dupes[dupes > 1]
    if not dupes.empty:
        pytest.fail(
            f"Potential duplicate orders detected:\n{dupes.to_string()}\n"
            f"Same component/supplier/quantity placed multiple times."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Scenario-specific tests
# ═══════════════════════════════════════════════════════════════════════════════

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "scenarios"


class TestScenario06:
    """Scenario 06 — Simple happy path (1 order, 2 shortfalls, 44 days)."""

    DB = DATA_DIR / "scenario_06_simple.sqlite"

    @pytest.fixture(autouse=True)
    def setup(self):
        self.orders = load_table(self.DB, "purchase_orders")
        self.agent_orders = self.orders[
            self.orders["po_number"].str.startswith("PO-AGENT-")
        ]
        self.alerts = load_table(self.DB, "alerts")

    def test_orders_placed(self):
        """Should have placed at least 2 POs for the 2 shortfalls."""
        assert len(self.agent_orders) >= 2, (
            f"Expected ≥2 POs for 2 shortfalls, got {len(self.agent_orders)}"
        )

    def test_no_critical_alerts(self):
        """Simple scenario should not generate critical alerts."""
        critical = (
            self.alerts[
                self.alerts["description"].str.contains(
                    "INFEASIBLE|CRITICAL", case=False, na=False
                )
            ]
            if not self.alerts.empty
            else pd.DataFrame()
        )
        assert critical.empty, (
            f"Unexpected critical alerts in simple scenario:\n{critical['description'].to_string()}"
        )


class TestScenario02:
    """Scenario 02 — Partial procurement (existing POs should reduce gaps)."""

    DB = DATA_DIR / "scenario_02_partial_procurement.sqlite"

    @pytest.fixture(autouse=True)
    def setup(self):
        self.scenario = load_scenario(self.DB)
        self.all_orders = load_table(self.DB, "purchase_orders")
        self.existing_orders = self.all_orders[
            ~self.all_orders["po_number"].str.startswith("PO-AGENT-")
        ]
        self.agent_orders = self.all_orders[
            self.all_orders["po_number"].str.startswith("PO-AGENT-")
        ]

    def test_existing_pos_present(self):
        """Scenario 02 should have pre-existing POs."""
        assert len(self.existing_orders) > 0, "Expected existing POs in scenario 02"

    def test_fewer_orders_than_baseline(self):
        """Should place fewer POs than scenario 01 (since some demand is already covered)."""
        s01_orders = load_table(
            DATA_DIR / "scenario_01_baseline.sqlite", "purchase_orders"
        )
        s01_agent = s01_orders[s01_orders["po_number"].str.startswith("PO-AGENT-")]
        assert len(self.agent_orders) <= len(s01_agent), (
            f"Scenario 02 placed {len(self.agent_orders)} orders vs scenario 01's {len(s01_agent)}. "
            f"Expected fewer since existing POs cover some demand."
        )

    def test_no_double_ordering_for_covered_components(self):
        """Components fully covered by existing POs (gap=0) should not get agent orders."""
        # Compute gaps using only pre-existing POs (exclude agent-placed ones)
        import copy

        scenario_copy = copy.copy(self.scenario)
        scenario_copy.purchase_orders = self.existing_orders
        gaps = gap_analysis(scenario_copy)
        gap_components = set(gaps["component_id"])

        # Only flag agent orders for components with zero gap (fully covered)
        agent_components = set(self.agent_orders["component_id"])
        fully_covered_but_ordered = agent_components - gap_components

        assert not fully_covered_but_ordered, (
            f"Agent ordered components that have no gap (fully covered by existing POs): "
            f"{fully_covered_but_ordered}"
        )


class TestScenario03:
    """Scenario 03 — Tight timeline (9-day deadline, infeasibility expected)."""

    DB = DATA_DIR / "scenario_03_tight_timeline.sqlite"

    @pytest.fixture(autouse=True)
    def setup(self):
        self.orders = load_table(self.DB, "purchase_orders")
        self.agent_orders = self.orders[
            self.orders["po_number"].str.startswith("PO-AGENT-")
        ]
        self.alerts = load_table(self.DB, "alerts")

    def test_has_deadline_alerts(self):
        """Tight timeline scenario must generate deadline/infeasibility alerts."""
        if self.alerts.empty:
            pytest.fail(
                "No alerts generated for tight timeline scenario — expected deadline alerts"
            )

        deadline_alerts = self.alerts[
            self.alerts["description"].str.contains(
                "DEADLINE|INFEASIBLE|cannot meet|late|risk", case=False, na=False
            )
        ]
        assert len(deadline_alerts) >= 1, (
            f"Expected deadline-related alerts. Got {len(self.alerts)} alert(s) but none about deadlines."
        )

    def test_no_impossible_delivery_dates(self):
        """No POs should promise delivery before the order date."""
        for _, row in self.agent_orders.iterrows():
            order_dt = datetime.strptime(row["order_date"], "%Y-%m-%d")
            delivery_dt = datetime.strptime(row["expected_delivery_date"], "%Y-%m-%d")
            assert delivery_dt >= order_dt, (
                f"{row['po_number']}: delivery date {row['expected_delivery_date']} "
                f"is before order date {row['order_date']}"
            )


class TestScenario05:
    """Scenario 05 — Competing demand (date: 2025-10-05, air freight expired)."""

    DB = DATA_DIR / "scenario_05_competing_demand.sqlite"

    @pytest.fixture(autouse=True)
    def setup(self):
        self.orders = load_table(self.DB, "purchase_orders")
        self.agent_orders = self.orders[
            self.orders["po_number"].str.startswith("PO-AGENT-")
        ]
        self.scenario = load_scenario(self.DB)

    def test_correct_current_date(self):
        """Scenario 05 current_date should be 2025-10-05."""
        assert self.scenario.current_date == "2025-10-05", (
            f"Expected current_date 2025-10-05, got {self.scenario.current_date}"
        )

    def test_no_air_freight_reduction(self):
        """Air freight memo expires Sept 30. No lead time reductions should be applied."""
        catalog = self.scenario.supplier_catalog
        suppliers = self.scenario.suppliers
        international_suppliers = set(
            suppliers[suppliers["is_domestic"] == 0]["supplier_id"]
        )

        current_date = datetime.strptime(self.scenario.current_date, "%Y-%m-%d")

        for _, row in self.agent_orders.iterrows():
            if row["supplier_id"] not in international_suppliers:
                continue
            cat_entry = catalog[
                (catalog["component_id"] == row["component_id"])
                & (catalog["supplier_id"] == row["supplier_id"])
            ]
            if cat_entry.empty:
                continue
            catalog_lead = int(cat_entry.iloc[0]["lead_time_days"])
            expected_delivery = current_date + timedelta(days=catalog_lead)
            actual_delivery = datetime.strptime(
                row["expected_delivery_date"], "%Y-%m-%d"
            )

            # Delivery should NOT be earlier than catalog lead time (no air freight)
            assert actual_delivery >= expected_delivery - timedelta(days=1), (
                f"{row['po_number']}: international supplier {row['supplier_id']} delivery "
                f"{row['expected_delivery_date']} is earlier than catalog lead time "
                f"({catalog_lead}d → {expected_delivery.strftime('%Y-%m-%d')}). "
                f"Air freight should not be applied after Sept 30."
            )
