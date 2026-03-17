"""
Cohort Analytics Engine - exact logic from cohort_app.py
"""
import pandas as pd
import numpy as np
from typing import List, Optional


def cohort_engine(df, metric, cols, cohort_type):
    temp = (
        df.groupby(cols)[metric]
        .sum().reset_index()
        .sort_values(metric, ascending=False)
    )
    temp["Rank"] = temp[metric].rank(method="dense", ascending=False)
    max_rank = temp["Rank"].max()
    name = "_".join(cols)

    if cohort_type == "SG":
        def bucket(x):
            if x <= 10:   return "Tier 1"
            elif x <= 25: return "Tier 2"
            elif x <= 50: return "Tier 3"
            else:          return "Long Tail"
        temp[f"SG_{name}"] = temp["Rank"].apply(bucket)

    if cohort_type == "PC":
        temp["Pct"] = temp["Rank"] / max_rank
        def bucket(x):
            if x <= .05:  return "Top 5%"
            elif x <= .10: return "Top 10%"
            elif x <= .20: return "Top 20%"
            elif x <= .50: return "Top 50%"
            else:           return "Bottom 50%"
        temp[f"PC_{name}"] = temp["Pct"].apply(bucket)

    if cohort_type == "RC":
        temp["Cum"]   = temp[metric].cumsum()
        total = temp[metric].sum()
        temp["Share"] = temp["Cum"] / total
        def bucket(x):
            if x <= .2:   return "Revenue Leaders"
            elif x <= .5: return "Growth Segment"
            elif x <= .8: return "Tail Segment"
            else:           return "Bottom Tail"
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
    cohort_types: List[str],
    period_filter: str,
    selected_fiscal_year: Optional[str],
) -> dict:

    # ── Get all fiscal years before filtering ────────────────────────
    fy_vals = []
    if fiscal_col and fiscal_col != "None" and fiscal_col in df.columns:
        fy_vals = sorted(df[fiscal_col].dropna().unique().tolist(), key=str)

    # ── Period filter ─────────────────────────────────────────────────
    df_filtered = df.copy()
    if fiscal_col and fiscal_col != "None" and fiscal_col in df.columns:
        if period_filter == "latest" and fy_vals:
            df_filtered = df[df[fiscal_col] == fy_vals[-1]]
        elif period_filter == "fiscal_year" and selected_fiscal_year:
            df_filtered = df[df[fiscal_col] == selected_fiscal_year]

    # ── Run cohort engine for ALL selected types ──────────────────────
    # FIX: individual_cols AND hierarchies both run — AND logic not OR
    result = df_filtered.copy()
    cohort_cols_added = []

    # Single dimension columns
    for col in individual_cols:
        if col not in df_filtered.columns:
            continue
        for ct in cohort_types:
            t = cohort_engine(df_filtered, metric, [col], ct)
            col_name = f"{ct}_{col}"
            if col_name not in result.columns:
                result = result.merge(t[[col, col_name]], on=col, how="left")
                cohort_cols_added.append(col_name)

    # Multi dimension hierarchies — ALSO run (AND logic)
    for grp in hierarchies:
        grp = [c for c in grp if c in df_filtered.columns]
        if len(grp) < 1:
            continue
        nm = "_".join(grp)
        for ct in cohort_types:
            t = cohort_engine(df_filtered, metric, grp, ct)
            col_name = f"{ct}_{nm}"
            if col_name not in result.columns:
                result = result.merge(t[grp + [col_name]], on=grp, how="left")
                cohort_cols_added.append(col_name)

    # ── Summary KPIs ──────────────────────────────────────────────────
    total_revenue = float(df_filtered[metric].sum())
    n_customers   = int(df_filtered[customer_col].nunique()) if customer_col in df_filtered.columns else 0
    rev_per_cust  = total_revenue / n_customers if n_customers else 0

    # ── FY summary with year-level breakdown ──────────────────────────
    fy_summary = []
    if fiscal_col and fiscal_col != "None" and fiscal_col in df.columns and customer_col in df.columns:
        fy_df = (
            df.groupby(fiscal_col)
            .agg(revenue=(metric, "sum"), customers=(customer_col, "nunique"))
            .reset_index()
        )
        fy_df["rev_per_customer"] = fy_df["revenue"] / fy_df["customers"]
        fy_summary = fy_df.to_dict(orient="records")

    # ── Segmentation — overall ────────────────────────────────────────
    segmentation = []
    if customer_col in df_filtered.columns:
        seg = df_filtered.groupby(customer_col)[metric].sum().reset_index()
        seg["rank"] = seg[metric].rank(method="dense", ascending=False)
        seg["pct"]  = seg["rank"] / seg["rank"].max()
        seg["segment"] = pd.cut(
            seg["pct"], bins=[0, .05, .1, .2, 1],
            labels=["Top 5%", "Top 10%", "Top 20%", "Long Tail"]
        ).astype(str)
        seg_agg = seg.groupby("segment")[metric].sum().reset_index()
        segmentation = seg_agg.to_dict(orient="records")

    # ── Segmentation by fiscal year ───────────────────────────────────
    seg_by_fy = []
    if fiscal_col and fiscal_col != "None" and fiscal_col in df.columns and customer_col in df.columns:
        seg_fy = df.copy()
        seg_fy["fy_rank"] = seg_fy.groupby(fiscal_col)[metric].rank(method="dense", ascending=False)
        seg_fy["fy_count"] = seg_fy.groupby(fiscal_col)[metric].transform("count")
        seg_fy["pct"] = seg_fy["fy_rank"] / seg_fy["fy_count"]
        seg_fy["segment"] = pd.cut(
            seg_fy["pct"], bins=[0, .05, .1, .2, 1],
            labels=["Top 5%", "Top 10%", "Top 20%", "Long Tail"]
        ).astype(str)
        seg_agg_fy = seg_fy.groupby([fiscal_col, "segment"])[metric].sum().reset_index()
        seg_by_fy = seg_agg_fy.to_dict(orient="records")

    # ── Retention heatmap — FIX: show ALL months not just 12 ─────────
    heatmap_data   = []
    retention_data = []

    if customer_col in df_filtered.columns and date_col in df_filtered.columns:
        try:
            hdf = df_filtered.copy()
            hdf[date_col] = pd.to_datetime(hdf[date_col])
            hdf["order_month"]  = hdf[date_col].dt.to_period("M").astype(str)
            cohort_map = hdf.groupby(customer_col)["order_month"].min()
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

            # FIX: use ALL columns, not just first 12
            all_indices = sorted(pivot.columns.tolist())

            for cohort_month in pivot.index:
                row = {"cohort": str(cohort_month)}
                for idx in all_indices:
                    v = pivot.loc[cohort_month, idx]
                    row[str(idx)] = int(v) if v > 0 else None
                heatmap_data.append(row)

            # Retention %
            ret = pivot.divide(pivot.iloc[:, 0], axis=0) * 100
            for cohort_month in ret.index:
                row = {"cohort": str(cohort_month)}
                for idx in all_indices:
                    v = ret.loc[cohort_month, idx]
                    row[str(idx)] = round(float(v), 1) if v > 0 else None
                retention_data.append(row)

        except Exception as e:
            pass

    # ── Output table ──────────────────────────────────────────────────
    output_cols = cohort_cols_added + [c for c in df_filtered.columns if c not in cohort_cols_added]
    output_records = result[output_cols].head(500).replace({np.nan: None}).to_dict(orient="records") if cohort_cols_added else []

    return {
        "summary": {
            "total_revenue":    total_revenue,
            "n_customers":      n_customers,
            "rev_per_customer": rev_per_cust,
            "cohort_cols":      cohort_cols_added,
            "rows_analyzed":    len(df_filtered),
        },
        "fiscal_years":       fy_vals,           # All FY values for dropdown
        "fy_summary":         fy_summary,
        "segmentation":       segmentation,
        "segmentation_by_fy": seg_by_fy,
        "heatmap":            heatmap_data,
        "retention":          retention_data,
        "output":             output_records,
    }
