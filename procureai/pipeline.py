"""Deterministic procurement pipeline — BOM explosion, demand aggregation, and gap analysis.

All functions are pure pandas transforms with no LLM dependency.
"""

from __future__ import annotations

import pandas as pd

from procureai.utils.db import ScenarioData


def explode_demand(scenario: ScenarioData) -> pd.DataFrame:
    """Join production_schedule × bom to compute per-order component demand.

    Returns DataFrame with columns:
        order_id, product_id, component_id, quantity_needed, materials_needed_by
    """
    merged = scenario.production_schedule.merge(scenario.bom, on="product_id")
    merged["quantity_needed"] = merged["quantity"] * merged["quantity_per"]
    return merged[
        ["order_id", "product_id", "component_id", "quantity_needed", "materials_needed_by"]
    ].reset_index(drop=True)


def aggregate_demand(demand: pd.DataFrame) -> pd.DataFrame:
    """Group exploded demand by component_id.

    Returns DataFrame with columns:
        component_id, total_needed, earliest_needed_by
    """
    agg = demand.groupby("component_id").agg(
        total_needed=("quantity_needed", "sum"),
        earliest_needed_by=("materials_needed_by", "min"),
    ).reset_index()
    return agg


def compute_incoming(scenario: ScenarioData) -> pd.DataFrame:
    """Sum existing purchase orders by component_id.

    Returns DataFrame with columns:
        component_id, incoming_quantity
    """
    po = scenario.purchase_orders
    if po.empty:
        return pd.DataFrame(columns=["component_id", "incoming_quantity"])

    return (
        po.groupby("component_id")["quantity"]
        .sum()
        .reset_index()
        .rename(columns={"quantity": "incoming_quantity"})
    )


def gap_analysis(scenario: ScenarioData) -> pd.DataFrame:
    """Compute component shortfalls: total_needed - on_hand - incoming.

    Only returns rows where gap > 0.

    Returns DataFrame with columns:
        component_id, total_needed, on_hand, incoming, gap, earliest_needed_by
    """
    demand = explode_demand(scenario)
    agg = aggregate_demand(demand)
    incoming = compute_incoming(scenario)

    # Start from aggregated demand
    result = agg.copy()

    # Join on-hand inventory
    inv = scenario.inventory[["component_id", "quantity_on_hand"]].copy()
    result = result.merge(inv, on="component_id", how="left")
    result["quantity_on_hand"] = result["quantity_on_hand"].fillna(0)

    # Join incoming POs
    result = result.merge(incoming, on="component_id", how="left")
    result["incoming_quantity"] = result["incoming_quantity"].fillna(0)

    # Compute gap
    result["gap"] = result["total_needed"] - result["quantity_on_hand"] - result["incoming_quantity"]

    # Rename for clarity and filter to shortfalls only
    result = result.rename(columns={
        "quantity_on_hand": "on_hand",
        "incoming_quantity": "incoming",
    })
    result = result[result["gap"] > 0].reset_index(drop=True)

    return result[
        ["component_id", "total_needed", "on_hand", "incoming", "gap", "earliest_needed_by"]
    ]
