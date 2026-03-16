"""
Cohort Analytics Engine
Exact logic from cohort_app.py — nothing changed, just wrapped as a service.
"""
import pandas as pd
import numpy as np
from typing import List, Optional


# ── Cohort Types ─────────────────────────────────────────────────────
COHORT_LABELS = {
    # Size Cohorts (SG)
    "Top 10":   "Tier 1",
    "11-25":    "Tier 2",
    "26-50":    "Tier 3",
    "Others":   "Long Tail",
    # Percentile Cohorts (PC)
    "Top 5%":     "Top 5%",
    "Top 10%":    "Top 10%",
    "Top 20%":    "Top 20%",
    "Top 50%":    "Top 50%",
    "Bottom 50%": "Bottom 50%",
    # Revenue Cohorts (RC)
    "Top Drivers": "Revenue Leaders",
    "Mid Tier":    "Growth Segment",
    "Long Tail":   "Tail Segment",
    "Bottom Tail": "Bottom Tail",
}


def cohort_engine(
    df: pd.DataFrame,
    metric: str,
    cols: List[str],
    cohort_type: str,  # "SG" | "PC" | "RC"
) -> pd.DataFrame:
    """
    Core cohort engine — exact logic from cohort_app.py.
    cohort_type: SG = Size Cohorts, PC = Percentile, RC = Revenue Contribution
    """
    temp = (
        df.groupby(cols)[metric]
        .sum()
        .reset_index()
        .sort_values(metric, ascending=False)
    )
    temp["Rank"] = temp[metric].rank(method="dense", ascending=False)
    max_rank = temp["Rank"].max()
    name = "_".join(cols)

    if cohort_type == "SG":
        def bucket(x):
            if x <= 10:  return "Tier 1"
            elif x <= 25: return "Tier 2"
            elif x <= 50: return "Tier 3"
            else:         return "Long Tail"
        temp[f"SG_{name}"] = temp["Rank"].apply(bucket)

    if cohort_type == "PC":
        temp["Pct"] = temp["Rank"] / max_rank
        def bucket(x):
            if x <= .05:  return "Top 5%"
            elif x <= .10: return "Top 10%"
            elif x <= .20: return "Top 20%"
            elif x <= .50: return "Top 50%"
            else:          return "Bottom 50%"
        temp[f"PC_{name}"] = temp["Pct"].apply(bucket)

    if cohort_type == "RC":
        temp["Cum"]   = temp[metric].cumsum()
        total = temp[metric].sum()
        temp["Share"] = temp["Cum"] / total
        def bucket(x):
            if x <= .2:  return "Revenue Leaders"
            elif x <= .5: return "Growth Segment"
            elif x <= .8: return "Tail Segment"
            else:         return "Bottom Tail"
        temp[f"RC_{name}"] = temp["Share"].apply(bucket)

    return temp


def run_cohort_analysis(
    df: pd.DataFrame,
    metric: str,
    customer_col: str,
    date_col: str,
    fiscal_col: Optional[str],
    individual_cols: List[str],
    hierarchies: List[List[str]],
    cohort_types: List[str],          # ["SG", "PC", "RC"]
    period_filter: str,               # "all" | "latest" | "fiscal_year"
    selected_fiscal_year: Optional[str],
) -> dict:
    """
    Run the full cohort analysis and return structured results.
    """
    # ── Period filter ─────────────────────────────────────────────────
    df_filtered = df.copy()

    if fiscal_col and fiscal_col != "None" and fiscal_col in df.columns:
        fy_vals = sorted(df[fiscal_col].dropna().unique().tolist())
        if period_filter == "latest" and fy_vals:
            df_filtered = df[df[fiscal_col] == fy_vals[-1]]
        elif period_filter == "fiscal_year" and selected_fiscal_year:
            df_filtered = df[df[fiscal_col] == selected_fiscal_year]
    else:
        fy_vals = []

    # ── Run cohort engine ─────────────────────────────────────────────
    result = df_filtered.copy()
    cohort_cols_added = []

    for col in individual_cols:
        if col not in df_filtered.columns:
            continue
        for ct in cohort_types:
            t = cohort_engine(df_filtered, metric, [col], ct)
            col_name = f"{ct}_{col}"
            result = result.merge(t[[col, col_name]], on=col, how="left")
            cohort_cols_added.append(col_name)

    for grp in hierarchies:
        grp = [c for c in grp if c in df_filtered.columns]
        if not grp:
            continue
        nm = "_".join(grp)
        for ct in cohort_types:
            t = cohort_engine(df_filtered, metric, grp, ct)
            col_name = f"{ct}_{nm}"
            result = result.merge(t[grp + [col_name]], on=grp, how="left")
            cohort_cols_added.append(col_name)

    # ── Summary KPIs ──────────────────────────────────────────────────
    total_revenue = float(df_filtered[metric].sum())
    n_customers   = int(df_filtered[customer_col].nunique()) if customer_col in df_filtered.columns else 0
    rev_per_cust  = total_revenue / n_customers if n_customers else 0

    # ── Fiscal year summary table ─────────────────────────────────────
    fy_summary = []
    if fiscal_col and fiscal_col != "None" and fiscal_col in df.columns and customer_col in df.columns:
        fy_df = (
            df.groupby(fiscal_col)
            .agg(revenue=(metric, "sum"), customers=(customer_col, "nunique"))
            .reset_index()
        )
        fy_df["rev_per_customer"] = fy_df["revenue"] / fy_df["customers"]
        fy_summary = fy_df.to_dict(orient="records")

    # ── Segmentation data ─────────────────────────────────────────────
    segmentation = []
    if customer_col in df_filtered.columns:
        seg = df_filtered.groupby(customer_col)[metric].sum().reset_index()
        seg["rank"] = seg[metric].rank(method="dense", ascending=False)
        seg["pct"]  = seg["rank"] / seg["rank"].max()
        seg["segment"] = pd.cut(
            seg["pct"],
            bins=[0, .05, .1, .2, 1],
            labels=["Top 5%", "Top 10%", "Top 20%", "Long Tail"]
        ).astype(str)
        seg_agg = seg.groupby("segment")[metric].sum().reset_index()
        segmentation = seg_agg.to_dict(orient="records")

    # ── Segmentation by fiscal year ───────────────────────────────────
    seg_by_fy = []
    if fiscal_col and fiscal_col != "None" and fiscal_col in df.columns and customer_col in df.columns:
        seg_fy = df.copy()
        seg_fy["fy_rank"] = seg_fy.groupby(fiscal_col)[metric].rank(method="dense", ascending=False)
        seg_fy["fy_max"]  = seg_fy.groupby(fiscal_col)[metric].transform("count")
        seg_fy["pct"]     = seg_fy["fy_rank"] / seg_fy["fy_max"]
        seg_fy["segment"] = pd.cut(
            seg_fy["pct"],
            bins=[0, .05, .1, .2, 1],
            labels=["Top 5%", "Top 10%", "Top 20%", "Long Tail"]
        ).astype(str)
        seg_agg_fy = seg_fy.groupby([fiscal_col, "segment"])[metric].sum().reset_index()
        seg_by_fy  = seg_agg_fy.to_dict(orient="records")

    # ── Cohort heatmap ────────────────────────────────────────────────
    heatmap_data     = []
    retention_data   = []

    if customer_col in df_filtered.columns and date_col in df_filtered.columns:
        try:
            hdf = df_filtered.copy()
            hdf[date_col]       = pd.to_datetime(hdf[date_col])
            hdf["order_month"]  = hdf[date_col].dt.to_period("M").astype(str)
            cohort_map          = hdf.groupby(customer_col)["order_month"].min()
            hdf["cohort_month"] = hdf[customer_col].map(cohort_map)
            hdf["cohort_index"] = (
                pd.to_datetime(hdf["order_month"]) -
                pd.to_datetime(hdf["cohort_month"])
            ).dt.days // 30

            pivot = pd.pivot_table(
                hdf,
                values=customer_col,
                index="cohort_month",
                columns="cohort_index",
                aggfunc="nunique"
            ).fillna(0)

            # Heatmap — replace zeros with None for blank cells
            for cohort_month in pivot.index:
                row = {"cohort": str(cohort_month)}
                for idx in pivot.columns:
                    v = pivot.loc[cohort_month, idx]
                    row[str(idx)] = int(v) if v > 0 else None
                heatmap_data.append(row)

            # Retention %
            ret = pivot.divide(pivot.iloc[:, 0], axis=0) * 100
            for cohort_month in ret.index:
                row = {"cohort": str(cohort_month)}
                for idx in ret.columns:
                    v = ret.loc[cohort_month, idx]
                    row[str(idx)] = round(float(v), 1) if v > 0 else None
                retention_data.append(row)

        except Exception:
            pass

    # ── Output table (first 500 rows for API response) ─────────────────
    output_records = result[cohort_cols_added + list(df_filtered.columns)].head(500).to_dict(orient="records") if cohort_cols_added else []

    return {
        "summary": {
            "total_revenue":   total_revenue,
            "n_customers":     n_customers,
            "rev_per_customer": rev_per_cust,
            "cohort_cols":     cohort_cols_added,
            "rows_analyzed":   len(df_filtered),
        },
        "fiscal_years":      fy_vals,
        "fy_summary":        fy_summary,
        "segmentation":      segmentation,
        "segmentation_by_fy": seg_by_fy,
        "heatmap":           heatmap_data,
        "retention":         retention_data,
        "output":            output_records,
    }
