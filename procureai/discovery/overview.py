"""Scenario overview — load and display all data from scenario databases."""

import click
import pandas as pd

from procureai.utils.db import TABLE_NAMES, ScenarioData, list_scenarios, load_scenario


def print_scenario(scenario: ScenarioData) -> None:
    """Print full contents of a single scenario database."""
    print(f"{'=' * 70}")
    print(f"Scenario: {scenario.db_path.name}")
    print(f"Current Date: {scenario.current_date}")
    print(f"Description: {scenario.description}")
    print(f"{'=' * 70}")

    for table_name in TABLE_NAMES:
        df: pd.DataFrame = getattr(scenario, table_name)
        print(f"\n--- {table_name} ({len(df)} rows) ---")
        if df.empty:
            print("  (empty)")
        else:
            with pd.option_context(
                "display.max_rows",
                None,
                "display.max_columns",
                None,
                "display.width",
                200,
                "display.max_colwidth",
                60,
            ):
                print(df.to_string(index=False))
        print()


def print_all_scenarios_summary() -> None:
    """Print a comparison table across all scenario databases."""
    scenarios = list_scenarios()
    if not scenarios:
        print("No scenario databases found.")
        return

    rows = []
    for path in scenarios:
        s = load_scenario(path)
        rows.append(
            {
                "file": path.name,
                "current_date": s.current_date,
                "products": len(s.products),
                "components": len(s.components),
                "suppliers": len(s.suppliers),
                "bom_entries": len(s.bom),
                "catalog_entries": len(s.supplier_catalog),
                "prod_orders": len(s.production_schedule),
                "existing_POs": len(s.purchase_orders),
                "description": s.description[:80],
            }
        )

    summary = pd.DataFrame(rows)
    print("=" * 70)
    print("All Scenarios Summary")
    print("=" * 70)
    with pd.option_context(
        "display.max_rows",
        None,
        "display.max_columns",
        None,
        "display.width",
        200,
        "display.max_colwidth",
        80,
    ):
        print(summary.to_string(index=False))
    print()


@click.command()
@click.option(
    "--scenario",
    type=click.Path(exists=True),
    default=None,
    help="Path to a specific scenario .sqlite file",
)
def main(scenario: str | None) -> None:
    """View scenario data. With --scenario, prints all tables. Without, prints a summary of all scenarios."""
    if scenario:
        s = load_scenario(scenario)
        print_scenario(s)
    else:
        print_all_scenarios_summary()


if __name__ == "__main__":
    main()
