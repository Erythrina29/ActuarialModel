"""
BOTTOM SECTION: Analysis of Movement for Waiver Reserve.

AOM bridge order:
1. Reserve Last Month
2. Interest Rate Impact
3. FX Rate Impact
4. Aging Impact
5. Natural Impact - New Policies
6. Natural Impact - Terminated Policies
7. Natural Impact - Continuing Policies
8. Reserve This Month
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from waiver_reserve.config import load_monthly_config
from waiver_reserve.data_ingestion import load_all_raw_data
from waiver_reserve.export import export_excel
from waiver_reserve.logging_utils import setup_logging
from waiver_reserve.reconciliation import assert_max_abs_column, summarize_numeric_columns
from waiver_reserve.reserve_engine import calculate_waiver_reserve

logger = logging.getLogger("waiver_reserve.aom")

PRODUCT_DIMENSIONS = ["Company", "Prod_Name", "Plan_Code"]

SCENARIO_COLUMN_MAP = {
    "reserve_last_month": "reserve_last_month",
    "reserve_after_interest_rate": "reserve_after_interest_rate",
    "reserve_after_fx_rate": "reserve_after_fx_rate",
    "reserve_after_aging": "reserve_after_aging",
    "reserve_this_month": "reserve_this_month",
}


def make_run_id(process_name: str, valuation_month: str) -> str:
    """Create deterministic-readable run id."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{process_name}_{valuation_month}_{timestamp}"


def _scenario_to_policy_reserve(df: pd.DataFrame, reserve_col_name: str) -> pd.DataFrame:
    """Return one row per policy with reserve for a scenario."""
    if df.empty:
        return pd.DataFrame(columns=["policy_number", reserve_col_name, f"has_{reserve_col_name}"])

    out = df[["policy_number", "waiver_reserve_idr"]].copy()
    out = out.groupby("policy_number", as_index=False)["waiver_reserve_idr"].sum()
    out = out.rename(columns={"waiver_reserve_idr": reserve_col_name})
    out[f"has_{reserve_col_name}"] = True
    return out


def _extract_policy_dimensions(df: pd.DataFrame, source_priority: int) -> pd.DataFrame:
    """
    Extract product dimensions from a reserve scenario.

    AOM dimension convention:
    - Use closing-month dimensions first for policies existing this month.
    - Fallback to aged/last-month dimensions for ended policies.
    """
    required_cols = ["policy_number"] + PRODUCT_DIMENSIONS
    if df.empty or not all(col in df.columns for col in required_cols):
        return pd.DataFrame(columns=required_cols + ["dimension_priority"])

    dims = df[required_cols].copy()
    dims = dims.drop_duplicates("policy_number", keep="last")
    dims["dimension_priority"] = source_priority
    return dims


def _build_policy_dimensions(scenario_outputs: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build one dimension row per policy using scenario priority."""
    priority_order = [
        "reserve_this_month",
        "reserve_after_aging",
        "reserve_after_fx_rate",
        "reserve_after_interest_rate",
        "reserve_last_month",
    ]

    pieces = []
    for priority, scenario_name in enumerate(priority_order):
        if scenario_name in scenario_outputs:
            pieces.append(_extract_policy_dimensions(scenario_outputs[scenario_name], priority))

    if not pieces:
        return pd.DataFrame(columns=["policy_number"] + PRODUCT_DIMENSIONS)

    dims = pd.concat(pieces, ignore_index=True)
    if dims.empty:
        return pd.DataFrame(columns=["policy_number"] + PRODUCT_DIMENSIONS)

    dims = dims.sort_values(["policy_number", "dimension_priority"])
    dims = dims.drop_duplicates("policy_number", keep="first")
    dims = dims[["policy_number"] + PRODUCT_DIMENSIONS].copy()

    for col in PRODUCT_DIMENSIONS:
        dims[col] = dims[col].fillna("UNMAPPED").astype(str).str.strip()
        dims.loc[dims[col].eq(""), col] = "UNMAPPED"

    return dims


def _merge_scenarios(scenario_outputs: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Outer-merge reserve outputs from all bridge scenarios."""
    merged: Optional[pd.DataFrame] = None

    for reserve_col_name, df in scenario_outputs.items():
        scenario_df = _scenario_to_policy_reserve(df, reserve_col_name)
        if merged is None:
            merged = scenario_df
        else:
            merged = merged.merge(scenario_df, on="policy_number", how="outer")

    if merged is None:
        merged = pd.DataFrame(columns=["policy_number"])

    for reserve_col_name in scenario_outputs.keys():
        if reserve_col_name not in merged.columns:
            merged[reserve_col_name] = 0.0
        merged[reserve_col_name] = merged[reserve_col_name].fillna(0.0)

        flag_col = f"has_{reserve_col_name}"
        if flag_col not in merged.columns:
            merged[flag_col] = False
        merged[flag_col] = merged[flag_col].fillna(False).astype(bool)

    dims = _build_policy_dimensions(scenario_outputs)
    merged = merged.merge(dims, on="policy_number", how="left")
    for col in PRODUCT_DIMENSIONS:
        if col not in merged.columns:
            merged[col] = "UNMAPPED"
        merged[col] = merged[col].fillna("UNMAPPED").astype(str).str.strip()
        merged.loc[merged[col].eq(""), col] = "UNMAPPED"

    return merged


def _assign_policy_status(row: pd.Series) -> str:
    """Assign AOM policy status based on policy presence in opening and closing scenario."""
    in_last = bool(row.get("has_reserve_last_month", False))
    in_this = bool(row.get("has_reserve_this_month", False))

    if in_last and in_this:
        return "Continuing Policies"
    if not in_last and in_this:
        return "New Policies"
    if in_last and not in_this:
        return "Terminated Policies"
    return "Bridge Only"


def build_movement_table(
    scenario_outputs: Dict[str, pd.DataFrame],
    run_id: str,
    config_last: Dict[str, Any],
    config_this: Dict[str, Any],
    tolerance: float = 1.0,
) -> pd.DataFrame:
    """Build per-policy movement table and perform reconciliation checks."""
    logger.info("=== BOTTOM: Build per-policy AOM table ===")
    df = _merge_scenarios(scenario_outputs)

    df["status"] = df.apply(_assign_policy_status, axis=1)

    df["interest_rate_impact"] = df["reserve_after_interest_rate"] - df["reserve_last_month"]
    df["fx_rate_impact"] = df["reserve_after_fx_rate"] - df["reserve_after_interest_rate"]
    df["aging_impact"] = df["reserve_after_aging"] - df["reserve_after_fx_rate"]

    # Natural Impact replaces the old generic Portfolio Impact.
    # It explains the bridge from aged last-month portfolio to actual this-month portfolio.
    df["natural_impact_new"] = 0.0
    df["natural_impact_terminated"] = 0.0
    df["natural_impact_continuing"] = 0.0

    df.loc[
        df["status"] == "New Policies",
        "natural_impact_new",
    ] = df["reserve_this_month"]

    df.loc[
        df["status"] == "Terminated Policies",
        "natural_impact_terminated",
    ] = -df["reserve_after_aging"]

    df.loc[
        df["status"] == "Continuing Policies",
        "natural_impact_continuing",
    ] = df["reserve_this_month"] - df["reserve_after_aging"]

    df["natural_impact_total"] = (
        df["natural_impact_new"]
        + df["natural_impact_terminated"]
        + df["natural_impact_continuing"]
    )

    # Backward-compatible alias. Do not show this in the main final report.
    df["portfolio_impact"] = df["natural_impact_total"]

    df["total_impact"] = df["reserve_this_month"] - df["reserve_last_month"]
    df["explained_impact"] = (
        df["interest_rate_impact"]
        + df["fx_rate_impact"]
        + df["aging_impact"]
        + df["natural_impact_total"]
    )
    df["reconciliation_diff"] = df["total_impact"] - df["explained_impact"]

    df["run_id"] = run_id
    df["last_valuation_date"] = config_last.get("valuation_date")
    df["this_valuation_date"] = config_this.get("valuation_date")
    df["last_config_path"] = config_last.get("_config_path")
    df["this_config_path"] = config_this.get("_config_path")
    df["calculation_timestamp"] = datetime.now()

    assert_max_abs_column(df, "reconciliation_diff", tolerance, "Per-policy AOM reconciliation")

    display_cols = [
        "policy_number",
        "Company",
        "Prod_Name",
        "Plan_Code",
        "status",
        "reserve_last_month",
        "interest_rate_impact",
        "fx_rate_impact",
        "aging_impact",
        "natural_impact_new",
        "natural_impact_terminated",
        "natural_impact_continuing",
        "natural_impact_total",
        "total_impact",
        "reserve_this_month",
        "reserve_after_interest_rate",
        "reserve_after_fx_rate",
        "reserve_after_aging",
        "explained_impact",
        "reconciliation_diff",
        "run_id",
        "last_valuation_date",
        "this_valuation_date",
        "last_config_path",
        "this_config_path",
        "calculation_timestamp",
    ]

    return df[display_cols].sort_values(["Company", "Prod_Name", "policy_number"]).reset_index(drop=True)


def build_scenario_totals(scenario_outputs: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build scenario-level reserve totals for audit."""
    rows = []
    for scenario_name, df in scenario_outputs.items():
        rows.append(
            {
                "scenario": scenario_name,
                "policy_count": len(df),
                "reserve_total_idr": float(df["waiver_reserve_idr"].sum()) if "waiver_reserve_idr" in df.columns else 0.0,
                "annual_premium_waived_idr_total": float(df["annual_premium_waived_idr"].sum()) if "annual_premium_waived_idr" in df.columns else None,
                "annual_premium_waived_ccy_total": float(df["annual_premium_waived_ccy"].sum()) if "annual_premium_waived_ccy" in df.columns else None,
            }
        )
    return pd.DataFrame(rows)


def build_aom_summary_by_product(movement: pd.DataFrame) -> pd.DataFrame:
    """Aggregate AOM bridge by Company, Prod_Name, and Plan_Code."""
    numeric_cols = [
        "reserve_last_month",
        "interest_rate_impact",
        "fx_rate_impact",
        "aging_impact",
        "natural_impact_new",
        "natural_impact_terminated",
        "natural_impact_continuing",
        "natural_impact_total",
        "total_impact",
        "reserve_this_month",
        "explained_impact",
        "reconciliation_diff",
    ]

    return (
        movement.groupby(PRODUCT_DIMENSIONS, dropna=False, as_index=False)
        .agg(
            policy_count=("policy_number", "nunique"),
            **{col: (col, "sum") for col in numeric_cols},
        )
        .sort_values(["Company", "Prod_Name", "Plan_Code"])
        .reset_index(drop=True)
    )


def build_aom_summary_by_company(movement: pd.DataFrame) -> pd.DataFrame:
    """Aggregate AOM bridge by Company."""
    numeric_cols = [
        "reserve_last_month",
        "interest_rate_impact",
        "fx_rate_impact",
        "aging_impact",
        "natural_impact_new",
        "natural_impact_terminated",
        "natural_impact_continuing",
        "natural_impact_total",
        "total_impact",
        "reserve_this_month",
        "explained_impact",
        "reconciliation_diff",
    ]

    return (
        movement.groupby(["Company"], dropna=False, as_index=False)
        .agg(
            policy_count=("policy_number", "nunique"),
            **{col: (col, "sum") for col in numeric_cols},
        )
        .sort_values("Company")
        .reset_index(drop=True)
    )


def run_analysis_of_movement(
    config_last_path: str | Path,
    config_this_path: str | Path,
    tolerance: float = 1.0,
    output_path: str | Path | None = None,
) -> Path:
    """
    Run full waiver AOM process.

    Returns exported Excel path.
    """
    config_last = load_monthly_config(config_last_path)
    config_this = load_monthly_config(config_this_path)

    run_id = make_run_id("WAIVER_AOM", config_this.get("_valuation_month", "UNKNOWN"))
    setup_logging(config_this["output_dir"], run_id)

    logger.info("AOM last config: %s", config_last["_config_path"])
    logger.info("AOM this config: %s", config_this["_config_path"])

    data_last = load_all_raw_data(config_last, run_id=f"{run_id}_LAST")
    data_this = load_all_raw_data(config_this, run_id=f"{run_id}_THIS")

    # Scenario 0: Opening reserve, last-month everything.
    reserve_last = calculate_waiver_reserve(
        data=data_last,
        config=config_last,
        run_id=run_id,
        scenario_name="reserve_last_month",
    )

    # Scenario 1: Change discount rates only, on last-month portfolio and last valuation date.
    reserve_after_interest = calculate_waiver_reserve(
        data=data_last,
        config=config_last,
        run_id=run_id,
        scenario_name="reserve_after_interest_rate",
        interest_rates=config_this.get("interest_rate"),
        yield_curve=data_this.get("ifrs17_yield_curve"),
    )

    # Scenario 2: Change FX after discount rates, still last-month portfolio and last valuation date.
    reserve_after_fx = calculate_waiver_reserve(
        data=data_last,
        config=config_last,
        run_id=run_id,
        scenario_name="reserve_after_fx_rate",
        interest_rates=config_this.get("interest_rate"),
        usd_to_idr_rate=config_this.get("usd_to_idr_rate"),
        yield_curve=data_this.get("ifrs17_yield_curve"),
    )

    # Scenario 3: Change valuation date / aging, still last-month portfolio.
    reserve_after_aging = calculate_waiver_reserve(
        data=data_last,
        config=config_last,
        run_id=run_id,
        scenario_name="reserve_after_aging",
        interest_rates=config_this.get("interest_rate"),
        usd_to_idr_rate=config_this.get("usd_to_idr_rate"),
        valuation_date=config_this.get("valuation_date"),
        yield_curve=data_this.get("ifrs17_yield_curve"),
    )

    # Scenario 4: Closing reserve, this-month everything.
    reserve_this = calculate_waiver_reserve(
        data=data_this,
        config=config_this,
        run_id=run_id,
        scenario_name="reserve_this_month",
    )

    scenario_outputs = {
        "reserve_last_month": reserve_last,
        "reserve_after_interest_rate": reserve_after_interest,
        "reserve_after_fx_rate": reserve_after_fx,
        "reserve_after_aging": reserve_after_aging,
        "reserve_this_month": reserve_this,
    }

    movement = build_movement_table(
        scenario_outputs=scenario_outputs,
        run_id=run_id,
        config_last=config_last,
        config_this=config_this,
        tolerance=tolerance,
    )

    numeric_cols = [
        "reserve_last_month",
        "interest_rate_impact",
        "fx_rate_impact",
        "aging_impact",
        "natural_impact_new",
        "natural_impact_terminated",
        "natural_impact_continuing",
        "natural_impact_total",
        "total_impact",
        "reserve_this_month",
        "explained_impact",
        "reconciliation_diff",
    ]
    summary = summarize_numeric_columns(movement, numeric_cols)
    summary.insert(0, "run_id", run_id)

    status_summary = (
        movement.groupby("status", as_index=False)
        .agg(
            policy_count=("policy_number", "count"),
            reserve_last_month=("reserve_last_month", "sum"),
            interest_rate_impact=("interest_rate_impact", "sum"),
            fx_rate_impact=("fx_rate_impact", "sum"),
            aging_impact=("aging_impact", "sum"),
            natural_impact_new=("natural_impact_new", "sum"),
            natural_impact_terminated=("natural_impact_terminated", "sum"),
            natural_impact_continuing=("natural_impact_continuing", "sum"),
            natural_impact_total=("natural_impact_total", "sum"),
            reserve_this_month=("reserve_this_month", "sum"),
            total_impact=("total_impact", "sum"),
            reconciliation_diff=("reconciliation_diff", "sum"),
        )
        .sort_values("status")
    )

    scenario_totals = build_scenario_totals(scenario_outputs)
    summary_by_product = build_aom_summary_by_product(movement)
    summary_by_company = build_aom_summary_by_company(movement)

    if output_path is None:
        output_path = Path(config_this["output_dir"]) / f"Waiver_AOM_{config_this['_valuation_month']}_{run_id}.xlsx"

    exported_path = export_excel(
        output_path=output_path,
        sheets={
            "Movement_Per_Policy": movement,
            "Summary": summary,
            "Summary_By_Product": summary_by_product,
            "Summary_By_Company": summary_by_company,
            "Scenario_Totals": scenario_totals,
            "Status_Summary": status_summary,
        },
    )

    logger.info("AOM completed successfully. Output: %s", exported_path)
    return exported_path
