"""
Debug AOM Interest Rate Impact

Run from project root:
    cd "C:\\Users\\mohammad.firdaus\\Documents\\pytho_warehouse\\claim_reserve\\waiver"
    py scripts\\debug_aom_interest.py --last-month 2026-05 --this-month 2026-06

Purpose:
1. Check last-month vs this-month RFR config.
2. Check calculate_waiver_reserve() signature.
3. Test whether interest_rates override changes annuity_factor and reserve.
4. Print full traceback if TypeError/error happens.
"""

import argparse
import inspect
import sys
import traceback
from pathlib import Path


def find_project_root() -> Path:
    """
    Assumption:
    This file is placed under:
        <project_root>/scripts/debug_aom_interest.py

    If not, fallback to current working directory.
    """
    script_path = Path(__file__).resolve()

    if script_path.parent.name.lower() == "scripts":
        return script_path.parent.parent

    return Path.cwd().resolve()


def print_section(title: str) -> None:
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)


def safe_sum(df, col):
    if col not in df.columns:
        return None
    return df[col].sum()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--last-month", required=True, help="Example: 2026-05")
    parser.add_argument("--this-month", required=True, help="Example: 2026-06")
    args = parser.parse_args()

    project_root = find_project_root()
    src_path = project_root / "src"

    sys.path.insert(0, str(src_path))

    print_section("PROJECT PATH")
    print("project_root:", project_root)
    print("src_path     :", src_path)
    print("python exe   :", sys.executable)
    print("python ver   :", sys.version)

    try:
        from waiver_reserve.config import load_monthly_config
        from waiver_reserve.data_ingestion import load_all_raw_data
        from waiver_reserve.reserve_engine import calculate_waiver_reserve
    except Exception as e:
        print_section("IMPORT ERROR")
        print("ERROR TYPE   :", type(e).__name__)
        print("ERROR MESSAGE:", str(e))
        print("FULL TRACEBACK:")
        traceback.print_exc()
        raise SystemExit(1)

    last_config_path = project_root / args.last_month / "config" / "waiver_assumptions.yaml"
    this_config_path = project_root / args.this_month / "config" / "waiver_assumptions.yaml"

    print_section("CONFIG PATHS")
    print("last_config_path:", last_config_path)
    print("this_config_path:", this_config_path)
    print("last exists     :", last_config_path.exists())
    print("this exists     :", this_config_path.exists())

    try:
        config_last = load_monthly_config(str(last_config_path))
        config_this = load_monthly_config(str(this_config_path))
    except Exception as e:
        print_section("CONFIG LOAD ERROR")
        print("ERROR TYPE   :", type(e).__name__)
        print("ERROR MESSAGE:", str(e))
        print("FULL TRACEBACK:")
        traceback.print_exc()
        raise SystemExit(1)

    print_section("CONFIG VALUES")
    print("LAST valuation_date:", config_last.get("valuation_date"))
    print("THIS valuation_date:", config_this.get("valuation_date"))
    print("LAST interest_rate :", config_last.get("interest_rate"))
    print("THIS interest_rate :", config_this.get("interest_rate"))
    print("LAST FX            :", config_last.get("usd_to_idr_rate"))
    print("THIS FX            :", config_this.get("usd_to_idr_rate"))

    if config_last.get("interest_rate") == config_this.get("interest_rate"):
        print()
        print("WARNING: LAST and THIS interest_rate are identical after config loading.")
        print("If interest impact is zero, that would be expected.")
    else:
        print()
        print("OK: LAST and THIS interest_rate are different after config loading.")

    print_section("RESERVE ENGINE SIGNATURE")
    sig = inspect.signature(calculate_waiver_reserve)
    print("calculate_waiver_reserve signature:")
    print(sig)

    parameter_names = list(sig.parameters.keys())
    print("parameters:", parameter_names)

    if "interest_rates" not in parameter_names:
        print()
        print("PROBLEM FOUND:")
        print("calculate_waiver_reserve() does NOT accept parameter 'interest_rates'.")
        print("AOM cannot isolate interest rate impact until reserve_engine.py is updated.")
        print()
        print("Expected signature should include:")
        print("    interest_rates=None, usd_to_idr_rate=None, valuation_date=None")
        raise SystemExit(2)

    print_section("LOAD LAST MONTH DATA")
    try:
        data_last = load_all_raw_data(config_last, "DEBUG_AOM_INTEREST")
    except Exception as e:
        print("ERROR TYPE   :", type(e).__name__)
        print("ERROR MESSAGE:", str(e))
        print("FULL TRACEBACK:")
        traceback.print_exc()
        raise SystemExit(1)

    print("Loaded data keys:", list(data_last.keys()))

    for key in ["masterpolis", "product_mapping", "urlsbinf", "crlsrinf", "waiver_dates", "mortality_table"]:
        if key in data_last:
            print(f"{key:20s}: rows={len(data_last[key])}, cols={len(data_last[key].columns)}")
        else:
            print(f"{key:20s}: MISSING")

    print_section("RUN SCENARIO: RESERVE LAST MONTH")
    try:
        reserve_last = calculate_waiver_reserve(
            data=data_last,
            config=config_last,
            scenario_name="reserve_last_month",
        )
    except Exception as e:
        print("ERROR TYPE   :", type(e).__name__)
        print("ERROR MESSAGE:", str(e))
        print("FULL TRACEBACK:")
        traceback.print_exc()
        raise SystemExit(1)

    print("reserve_last rows:", len(reserve_last))
    print("reserve_last columns:", list(reserve_last.columns))
    print("reserve_last total:", safe_sum(reserve_last, "waiver_reserve_idr"))

    if "Currency" in reserve_last.columns and "waiver_reserve_idr" in reserve_last.columns:
        print()
        print("reserve_last by Currency:")
        print(reserve_last.groupby("Currency")["waiver_reserve_idr"].sum())

    for col in ["interest_rate_idr_used", "interest_rate_usd_used", "usd_to_idr_rate_used", "valuation_date_used"]:
        if col in reserve_last.columns:
            print(f"{col}: {reserve_last[col].drop_duplicates().head(10).tolist()}")

    print_section("RUN SCENARIO: RESERVE AFTER INTEREST")
    try:
        reserve_after_interest = calculate_waiver_reserve(
            data=data_last,
            config=config_last,
            scenario_name="reserve_after_interest_rate",
            interest_rates=config_this["interest_rate"],
        )
    except Exception as e:
        print("ERROR TYPE   :", type(e).__name__)
        print("ERROR MESSAGE:", str(e))
        print("FULL TRACEBACK:")
        traceback.print_exc()
        raise SystemExit(1)

    print("reserve_after_interest rows:", len(reserve_after_interest))
    print("reserve_after_interest total:", safe_sum(reserve_after_interest, "waiver_reserve_idr"))

    if "Currency" in reserve_after_interest.columns and "waiver_reserve_idr" in reserve_after_interest.columns:
        print()
        print("reserve_after_interest by Currency:")
        print(reserve_after_interest.groupby("Currency")["waiver_reserve_idr"].sum())

    for col in ["interest_rate_idr_used", "interest_rate_usd_used", "usd_to_idr_rate_used", "valuation_date_used"]:
        if col in reserve_after_interest.columns:
            print(f"{col}: {reserve_after_interest[col].drop_duplicates().head(10).tolist()}")

    print_section("COMPARE LAST VS AFTER INTEREST")

    required_cols = ["policy_number", "waiver_reserve_idr"]
    missing_last = [c for c in required_cols if c not in reserve_last.columns]
    missing_after = [c for c in required_cols if c not in reserve_after_interest.columns]

    if missing_last or missing_after:
        print("Cannot compare because required columns are missing.")
        print("missing in reserve_last:", missing_last)
        print("missing in reserve_after_interest:", missing_after)
        raise SystemExit(3)

    left_cols = ["policy_number", "waiver_reserve_idr"]
    right_cols = ["policy_number", "waiver_reserve_idr"]

    optional_cols = ["Currency", "annuity_factor", "annual_premium_waived"]
    for col in optional_cols:
        if col in reserve_last.columns:
            left_cols.append(col)
        if col in reserve_after_interest.columns:
            right_cols.append(col)

    debug = reserve_last[left_cols].merge(
        reserve_after_interest[right_cols],
        on="policy_number",
        how="inner",
        suffixes=("_last", "_after_interest"),
    )

    debug["reserve_diff"] = (
        debug["waiver_reserve_idr_after_interest"]
        - debug["waiver_reserve_idr_last"]
    )

    if "annuity_factor_last" in debug.columns and "annuity_factor_after_interest" in debug.columns:
        debug["annuity_factor_diff"] = (
            debug["annuity_factor_after_interest"]
            - debug["annuity_factor_last"]
        )
    else:
        debug["annuity_factor_diff"] = 0

    print("merged rows:", len(debug))
    print("total reserve last          :", debug["waiver_reserve_idr_last"].sum())
    print("total reserve after interest:", debug["waiver_reserve_idr_after_interest"].sum())
    print("total interest impact       :", debug["reserve_diff"].sum())
    print("total annuity factor diff   :", debug["annuity_factor_diff"].sum())

    currency_col = None
    if "Currency_last" in debug.columns:
        currency_col = "Currency_last"
    elif "Currency_after_interest" in debug.columns:
        currency_col = "Currency_after_interest"

    if currency_col is not None:
        print()
        print("Impact by Currency:")
        print(
            debug.groupby(currency_col)[["reserve_diff", "annuity_factor_diff"]]
            .sum()
            .sort_index()
        )

    non_zero = debug[debug["reserve_diff"].abs() > 0.01].copy()

    print()
    print("non-zero reserve_diff rows:", len(non_zero))

    print()
    print("Sample non-zero rows:")
    sample_cols = ["policy_number", "reserve_diff"]
    for c in [
        "Currency_last",
        "Currency_after_interest",
        "annuity_factor_last",
        "annuity_factor_after_interest",
        "annuity_factor_diff",
        "waiver_reserve_idr_last",
        "waiver_reserve_idr_after_interest",
        "annual_premium_waived_last",
        "annual_premium_waived_after_interest",
    ]:
        if c in debug.columns:
            sample_cols.append(c)

    if len(non_zero) > 0:
        print(non_zero[sample_cols].head(30).to_string(index=False))
    else:
        print("No non-zero interest impact detected.")

    print_section("DIAGNOSIS")
    total_abs_impact = debug["reserve_diff"].abs().sum()

    if total_abs_impact == 0:
        print("RESULT: Interest override did NOT change reserve at policy level.")
        print()
        print("Most likely causes:")
        print("1. reserve_engine.py receives interest_rates but does not pass scenario_config to calculate_annuity_factor().")
        print("2. model.py reads the wrong config key, e.g. 'interest_rates' instead of 'interest_rate'.")
        print("3. model.py ignores config interest_rate and uses a hardcoded/default rate.")
        print("4. Currency values do not match interest rate keys and code silently falls back to default rate.")
        print()
        print("Next file to inspect: src/waiver_reserve/reserve_engine.py and src/waiver_reserve/model.py")
    else:
        print("RESULT: Interest override DOES change reserve at policy level.")
        print()
        print("Therefore if AOM output still shows interest_rate_impact = 0,")
        print("the bug is likely in aom.py movement formula, scenario merge, or summary aggregation.")
        print()
        print("Next file to inspect: src/waiver_reserve/aom.py")

    print()
    print("Debug completed.")


if __name__ == "__main__":
    main()