"""
MIDDLE SECTION: Waiver Reserve actuarial model logic.

This module contains policy-level actuarial transformations only:
- Build waiver model point
- Calculate annual premium waived
- Calculate current age and remaining waiver term
- Calculate annuity factor

Important governance principle:
This module does not read Excel files and does not export outputs.
Raw data loading is handled by data_ingestion.py.
Scenario orchestration and final reserve calculation are handled by reserve_engine.py.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd

from waiver_reserve.exceptions import DataQualityError

logger = logging.getLogger("waiver_reserve.model")


PRODUCT_DIMENSION_OUTPUT_COLUMNS = ["Plan_Code", "Prod_Name", "Company"]


PRODUCT_DIMENSION_NORMALIZED_NAMES = {
    "plan_code",
    "plancode",
    "plan",
    "prod_name",
    "product_name",
    "prodname",
    "productname",
    "company",
}


def _normalize_column_name(column_name: object) -> str:
    """
    Normalize column names for defensive product-dimension detection.

    Examples:
    - 'Plan Code'  -> 'plan_code'
    - 'Plan_Code ' -> 'plan_code'
    - 'PLAN_CODE'  -> 'plan_code'
    """
    return str(column_name).strip().lower().replace(" ", "_")


def _drop_existing_product_dimension_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop product-related columns already present in master/model point.

    Governance rule:
    Product dimensions used for reserve reporting and AOM must come from
    Product_Mapping.xlsx, not from masterpolis, to avoid ambiguity such as:
    - plan_code from masterpolis vs Plan_Code from Product_Mapping
    - Plan_Code_x / Plan_Code_y after pandas merge
    """
    result = df.copy()

    cols_to_drop = [
        col
        for col in result.columns
        if _normalize_column_name(col) in PRODUCT_DIMENSION_NORMALIZED_NAMES
    ]

    if cols_to_drop:
        logger.info(
            "Dropping existing product dimension columns before Product_Mapping merge: %s",
            cols_to_drop,
        )
        result = result.drop(columns=cols_to_drop)

    return result


def _validate_product_mapping(product_mapping: pd.DataFrame) -> None:
    """
    Validate Product_Mapping columns and uniqueness before merge.
    """
    required_cols = ["policy_number", "Plan_Code", "Prod_Name", "Company"]
    missing_cols = [col for col in required_cols if col not in product_mapping.columns]

    if missing_cols:
        raise DataQualityError(
            f"Product_Mapping missing required columns before model enrichment: {missing_cols}. "
            f"Available columns: {product_mapping.columns.tolist()}"
        )

    duplicate_count = product_mapping["policy_number"].duplicated().sum()
    if duplicate_count > 0:
        duplicated_sample = (
            product_mapping.loc[
                product_mapping["policy_number"].duplicated(keep=False),
                "policy_number",
            ]
            .head(10)
            .tolist()
        )
        raise DataQualityError(
            f"Product_Mapping memiliki duplicate policy_number: {duplicate_count:,} duplicate rows. "
            f"Sample: {duplicated_sample}"
        )


def enrich_waiver_data(data: Dict[str, pd.DataFrame], config: Dict[str, Any]) -> pd.DataFrame:
    """
    Build waiver model point from standardized raw data.

    Steps:
    1. Filter masterpolis for waiver policies.
    2. Attach waiver start/end dates.
    3. Attach official product dimensions from Product_Mapping.xlsx.
    4. Attach basic premium from URLSBINF.
    5. Aggregate relevant waiver rider premium from CRLSRINF.

    Returns
    -------
    pd.DataFrame
        Policy-level waiver model point ready for actuarial calculations.
    """
    logger.info("=== MIDDLE: Enriching waiver model point ===")

    master = data["masterpolis"].copy()

    if "Status_Policy" not in master.columns:
        raise DataQualityError("masterpolis missing required column: Status_Policy")

    waiver_policies = master[master["Status_Policy"] == "B"].copy()
    logger.info("Waiver policies selected: %s", f"{len(waiver_policies):,}")

    waiver_dates = data["waiver_dates"][[
        "policy_number",
        "Waived_Start_Date",
        "Waived_End_Date",
    ]].copy()

    df = waiver_policies.merge(
        waiver_dates,
        on="policy_number",
        how="left",
        validate="m:1",
    )

    # Product dimensions must come from Product_Mapping.xlsx.
    # Drop product-like columns from master/model point first to avoid ambiguity and suffixes.
    df = _drop_existing_product_dimension_columns(df)

    if "product_mapping" in data and data["product_mapping"] is not None:
        product_mapping = data["product_mapping"][[
            "policy_number",
            "Plan_Code",
            "Prod_Name",
            "Company",
        ]].copy()

        _validate_product_mapping(product_mapping)

        df = df.merge(
            product_mapping,
            on="policy_number",
            how="left",
            validate="m:1",
        )
    else:
        logger.warning("Product_Mapping not available. Product dimensions will be set to UNMAPPED.")
        df["Plan_Code"] = "UNMAPPED"
        df["Prod_Name"] = "UNMAPPED"
        df["Company"] = "UNMAPPED"

    for col in PRODUCT_DIMENSION_OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = "UNMAPPED"
        df[col] = df[col].fillna("UNMAPPED")

    basic_premium = data["urlsbinf"][[
        "policy_number",
        "ANN_REG_PREM_CCY",
    ]].copy()

    df = df.merge(
        basic_premium,
        on="policy_number",
        how="left",
        validate="m:1",
    )

    rider_codes = config.get("waiver_rider_plan_codes", [])
    if not rider_codes:
        logger.warning("waiver_rider_plan_codes is empty. Rider premium will be zero.")

    crlsrinf = data["crlsrinf"].copy()
    rider_premium = crlsrinf[
        crlsrinf["Code"].isin(rider_codes)
    ][["policy_number", "BASIC_PREM_EXTRACT"]].copy()

    rider_sum = (
        rider_premium
        .groupby("policy_number", as_index=False)["BASIC_PREM_EXTRACT"]
        .sum()
        .rename(columns={"BASIC_PREM_EXTRACT": "rider_premium"})
    )

    df = df.merge(
        rider_sum,
        on="policy_number",
        how="left",
        validate="m:1",
    )

    df["ANN_REG_PREM_CCY"] = pd.to_numeric(df["ANN_REG_PREM_CCY"], errors="coerce")
    df["rider_premium"] = pd.to_numeric(df["rider_premium"], errors="coerce").fillna(0)

    logger.info("Model point after enrichment: %s rows", f"{len(df):,}")
    return df


def calculate_annual_premium_waived(df: pd.DataFrame, config: Dict[str, Any] | None = None) -> pd.DataFrame:
    """
    Calculate annual premium waived.

    Formula:
    annual_premium_waived = annual_basic_premium + annual_rider_premium

    where:
    annual_basic_premium = ANN_REG_PREM_CCY * payment_factor
    annual_rider_premium = rider_premium * payment_factor
    """
    logger.info("=== MIDDLE: Calculating annual premium waived ===")

    result = df.copy()

    payment_map = {
        "A": 1,
        "S": 2,
        "Q": 4,
        "M": 12,
    }

    result["payment_factor"] = result["Payment_Mode"].map(payment_map).fillna(1)

    unknown_payment_mode_count = result.loc[
        result["Payment_Mode"].notna() & result["Payment_Mode"].map(payment_map).isna(),
        "Payment_Mode",
    ].nunique()

    if unknown_payment_mode_count > 0:
        logger.warning(
            "Unknown Payment_Mode values detected. Default payment_factor=1 will be used for those rows."
        )

    result["annual_basic_premium"] = (
        result["ANN_REG_PREM_CCY"].fillna(0) * result["payment_factor"]
    )

    result["annual_rider_premium"] = (
        result["rider_premium"].fillna(0) * result["payment_factor"]
    )

    result["annual_premium_waived"] = (
        result["annual_basic_premium"] + result["annual_rider_premium"]
    )

    return result


def calculate_age_and_remaining_term(df: pd.DataFrame, valuation_date: str) -> pd.DataFrame:
    """
    Calculate current age at valuation date and remaining waiver term.

    Current simplified convention:
    - years_since_start = days from waived start date to valuation date / 365.25
    - current_age_at_val = insured age at entry + years_since_start
    - remaining_waiver_years = max(1, round(years_remaining_raw)) for active rows

    TODO / Governance note:
    Confirm that the rounding convention matches the existing Excel model.
    """
    logger.info("=== MIDDLE: Calculating age and remaining waiver term ===")

    result = df.copy()
    val_date = pd.to_datetime(valuation_date)

    result["Waived_Start_Date"] = pd.to_datetime(result["Waived_Start_Date"], errors="coerce")
    result["Waived_End_Date"] = pd.to_datetime(result["Waived_End_Date"], errors="coerce")

    missing_date_count = result[["Waived_Start_Date", "Waived_End_Date"]].isna().any(axis=1).sum()
    if missing_date_count > 0:
        logger.warning(
            "Rows with missing Waived_Start_Date or Waived_End_Date: %s",
            f"{missing_date_count:,}",
        )

    result["years_since_start"] = (
        val_date - result["Waived_Start_Date"]
    ).dt.days / 365.25

    result["current_age_at_val"] = (
        result["Insured_Age_at_entry"] + result["years_since_start"]
    )

    result["years_remaining_raw"] = (
        result["Waived_End_Date"] - val_date
    ).dt.days / 365.25

    before_count = len(result)
    result = result[result["years_remaining_raw"] >= 0].copy()
    dropped_count = before_count - len(result)

    if dropped_count > 0:
        logger.info(
            "Policies dropped because waiver has ended before valuation date: %s",
            f"{dropped_count:,}",
        )

    result["remaining_waiver_years"] = result["years_remaining_raw"].apply(
        lambda x: max(1, round(x)) if pd.notna(x) else 1
    )

    return result


def calculate_annuity_factor(
    df: pd.DataFrame,
    mortality_table: pd.DataFrame,
    config: Dict[str, Any],
    yield_curve: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Calculate waiver annuity factor.

    Current Local basis convention:
    - Flat annual interest rate by currency from config['interest_rate']
    - Annual survival using mortality_table columns MNSQ[x] / FNSQ[x]
    - Payment timing uses v**k, equivalent to annuity-due style timing

    TODO / Governance note:
    Confirm payment timing and remaining-term rounding against the existing Excel model.
    """
    logger.info("=== MIDDLE: Calculating annuity factor ===")

    result = df.copy()
    basis = str(config.get("basis", "Local")).upper()

    mortality = mortality_table.copy()
    mortality["Age"] = pd.to_numeric(mortality["Age"], errors="coerce")

    required_mort_cols = ["Age", "MNSQ[x]", "FNSQ[x]"]
    missing_mort_cols = [col for col in required_mort_cols if col not in mortality.columns]
    if missing_mort_cols:
        raise DataQualityError(f"Mortality table missing columns: {missing_mort_cols}")

    mortality_lookup = mortality.set_index("Age")

    missing_age_count = 0
    invalid_sex_count = 0
    invalid_currency_count = 0

    def get_annuity(row: pd.Series) -> float:
        nonlocal missing_age_count, invalid_sex_count, invalid_currency_count

        age = row["current_age_at_val"]
        remaining = int(row["remaining_waiver_years"])
        sex = row["Sex"]
        currency = row["Currency"]

        if remaining <= 0:
            return 0.0

        if basis == "LOCAL":
            interest_rates = config.get("interest_rate", {"IDR": 0.05, "USD": 0.05})

            if currency not in interest_rates:
                invalid_currency_count += 1

            i = interest_rates.get(currency, 0.05)
            v = 1 / (1 + i)

            if sex == "M":
                q_col = "MNSQ[x]"
            elif sex == "F":
                q_col = "FNSQ[x]"
            else:
                invalid_sex_count += 1
                q_col = "FNSQ[x]"

            surv = 1.0
            annuity = 0.0

            for k in range(remaining):
                annuity += surv * (v ** k)

                current_age = int(round(age + k))

                if current_age in mortality_lookup.index:
                    q = mortality_lookup.loc[current_age, q_col]
                    if isinstance(q, pd.Series):
                        q = q.iloc[0]
                else:
                    missing_age_count += 1
                    q = 0.01

                if pd.isna(q):
                    missing_age_count += 1
                    q = 0.01

                surv *= 1 - float(q)

            return float(annuity)

        if basis == "IFRS17":
            if yield_curve is None:
                raise DataQualityError("IFRS17 basis requires yield_curve, but yield_curve is None.")
            raise NotImplementedError("IFRS17 yield curve annuity factor is not implemented in this model.py version.")

        raise DataQualityError(f"Unsupported basis: {basis}")

    result["annuity_factor"] = result.apply(get_annuity, axis=1)

    if missing_age_count > 0:
        logger.warning(
            "Mortality age/qx fallback used %s times. Default q=0.01 was applied.",
            f"{missing_age_count:,}",
        )

    if invalid_sex_count > 0:
        logger.warning(
            "Invalid Sex values detected %s times. FNSQ[x] fallback was used.",
            f"{invalid_sex_count:,}",
        )

    if invalid_currency_count > 0:
        logger.warning(
            "Currency not found in interest_rate config %s times. Default i=0.05 was used.",
            f"{invalid_currency_count:,}",
        )

    return result
