"""
mrr_workflow_engine.py

Exact Python replication of MRR_Workflow_Advanced.yxmd
Based on spec + XML reverse-engineering.

OUTPUT: 12 columns exactly:
  Customer, Product, Channel, Region, Vintage, Date,
  MRR, Quantity, Month Lookback, Lookback Date,
  Bridge Classification, Bridge Value

Bridge Classification values:
  New Logo, Churn, Churn-Partial, Cross-sell, Upsell, Downsell,
  Lapsed, Returning, Other In, Other Out,
  Beginning MRR, Ending MRR,
  Price Impact, Volume Impact, Price on Volume
"""

import pandas as pd
import numpy as np
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd


def run_mrr_workflow(
    df_raw: pd.DataFrame,
    customer_col: str = 'CustomerID',
    date_col: str = 'Period',
    revenue_col: str = 'Revenue',
    product_col: Optional[str] = 'Product',
    channel_col: Optional[str] = 'Channel',
    region_col: Optional[str] = None,
    quantity_col: Optional[str] = None,
    lookback_months: List[int] = [1, 3, 12],
    revenue_unit: str = 'raw',
) -> pd.DataFrame:
    """
    Full MRR workflow engine. Returns bridge table with exactly 12 output columns.
    """

    # ── STEP 1: Column mapping (Node 896) ─────────────────────────────
    df = pd.DataFrame()
    df['Customer New']  = df_raw[customer_col].astype(str).str.strip()
    df['Product New']   = df_raw[product_col].astype(str).str.strip() if product_col and product_col in df_raw.columns else 'N/A'
    df['Channel New']   = df_raw[channel_col].astype(str).str.strip() if channel_col and channel_col in df_raw.columns else 'N/A'
    df['Region New']    = df_raw[region_col].astype(str).str.strip()  if region_col  and region_col  in df_raw.columns else 'N/A'
    df['Date New']      = pd.to_datetime(df_raw[date_col], errors='coerce') + MonthEnd(0)
    df['MRR New']       = pd.to_numeric(df_raw[revenue_col], errors='coerce').fillna(0)
    df['Quantity New']  = pd.to_numeric(df_raw[quantity_col], errors='coerce').fillna(1) if quantity_col and quantity_col in df_raw.columns else 1.0

    # Revenue unit conversion
    if revenue_unit == 'millions':
        df['MRR New'] = df['MRR New'] * 0.000001
    elif revenue_unit == 'thousands':
        df['MRR New'] = df['MRR New'] * 0.001

    # ── STEP 2: In-scope filter (Node 1090) ───────────────────────────
    # Keep only MRR > 0
    df = df[df['MRR New'] > 0].copy()
    if df.empty:
        return pd.DataFrame()

    # ── STEP 3: Dataset max date (Node 827) ───────────────────────────
    dataset_max_date = df['Date New'].max()
    df['Dataset_Max_Date_New'] = dataset_max_date

    # ── STEP 4: Aggregate to unit level (Node 835) ────────────────────
    # Unit = Customer × Product × Channel × Region
    unit_cols = ['Customer New', 'Product New', 'Channel New', 'Region New']
    agg = df.groupby(unit_cols + ['Date New'], as_index=False).agg(
        MRR_New=('MRR New', 'sum'),
        Quantity_New=('Quantity New', 'sum'),
    ).rename(columns={'MRR_New': 'MRR New', 'Quantity_New': 'Quantity New'})

    # ── STEP 5: Unit lifecycle dates (Node 820) ───────────────────────
    lifecycle = agg.groupby(unit_cols, as_index=False).agg(
        Min_Date_New=('Date New', 'min'),
        Max_Date_New=('Date New', 'max'),
    ).rename(columns={'Min_Date_New': 'Min Date New', 'Max_Date_New': 'Max Date New'})
    lifecycle['Dataset_Max_Date_New'] = dataset_max_date

    # ── STEP 6: Generate monthly date spine (Node 823) ────────────────
    # For each unit: every month from Min Date to Dataset Max Date
    rows = []
    for _, r in lifecycle.iterrows():
        for dt in pd.date_range(r['Min Date New'], dataset_max_date, freq='ME'):
            rows.append({
                'Customer New':         r['Customer New'],
                'Product New':          r['Product New'],
                'Channel New':          r['Channel New'],
                'Region New':           r['Region New'],
                'Min Date New':         r['Min Date New'],
                'Max Date New':         r['Max Date New'],
                'Dataset_Max_Date_New': dataset_max_date,
                'Date New':             dt,
            })
    if not rows:
        return pd.DataFrame()
    grid = pd.DataFrame(rows)

    # ── STEP 7: Left-join actuals onto spine (Nodes 824/825/826) ──────
    join_cols = unit_cols + ['Date New']
    merged = grid.merge(agg[join_cols + ['MRR New', 'Quantity New']], on=join_cols, how='left')
    merged['MRR New']      = merged['MRR New'].fillna(0)
    merged['Quantity New'] = merged['Quantity New'].fillna(0)

    # ── STEP 8: Customer-level lifecycle dates (Node 946) ─────────────
    cust_lc = merged.groupby('Customer New', as_index=False).agg(
        cust_min=('Date New', 'min'),
        cust_max=('Date New', 'max'),
    ).rename(columns={'cust_min': 'Customer Min Date New', 'cust_max': 'Customer Max Date New'})
    merged = merged.merge(cust_lc, on='Customer New', how='left')

    # ── STEP 9: Vintage = Customer Min Date (Node 950) ────────────────
    merged['Vintage New'] = merged['Customer Min Date New']

    # ── STEP 10: CustProd lifecycle dates (Node 947) ──────────────────
    cp_lc = merged.groupby(['Customer New', 'Product New'], as_index=False).agg(
        cp_min=('Date New', 'min'),
        cp_max=('Date New', 'max'),
    ).rename(columns={'cp_min': 'CustProd_Min_Date', 'cp_max': 'CustProd_Max_Date'})
    merged = merged.merge(cp_lc, on=['Customer New', 'Product New'], how='left')

    # ── STEP 11: Lookback branches (Nodes 902–933) ────────────────────
    all_lb = []
    for lb in lookback_months:
        t = merged.copy()
        t['Month Lookback'] = lb

        # Filter: within lookback window from unit's max date (Node 907)
        days_diff = (t['Max Date New'] - t['Date New']).dt.days
        t = t[np.round(days_diff / 30, 1) <= lb].copy()

        # Expiry Pool Flag (Node 921)
        t['Expiry Pool Flag'] = np.where(t['Date New'] > t['Max Date New'], 1, 0)

        # Sort for prior-row lookup (Multi-Row Formula)
        t = t.sort_values(unit_cols + ['Date New']).reset_index(drop=True)

        # Prior MRR/Qty = shift(N) within unit group (Nodes 915/919/923)
        t['Prior MRR New']      = t.groupby(unit_cols)['MRR New'].shift(lb).fillna(0)
        t['Prior Quantity New'] = t.groupby(unit_cols)['Quantity New'].shift(lb).fillna(0)

        # DTE (Node 925)
        t['DTE New'] = np.where(t['Expiry Pool Flag'] == 1, t['Prior MRR New'], 0)

        all_lb.append(t)

    # Union all lookbacks (Node 933)
    df_all = pd.concat(all_lb, ignore_index=True)

    # Remove all-zero rows (Node 934)
    df_all = df_all[~(
        (df_all['MRR New'] == 0) &
        (df_all['Prior MRR New'] == 0) &
        (df_all['DTE New'] == 0)
    )].copy()

    # ── STEP 12: Past/Future flags (Node 935) ─────────────────────────
    days_from_start = (df_all['Date New'] - df_all['Min Date New']).dt.days
    df_all['pastMRR New']   = np.where(np.round(days_from_start / 30, 1) < df_all['Month Lookback'], 'No', 'Yes')
    df_all['futureMRR New'] = np.where(df_all['Max Date New'] > df_all['Date New'], 'Yes', 'No')

    # ── STEP 13: Bridge Classification (Node 936) ─────────────────────
    # Exact formula from workflow XML:
    # IF Prior=0 AND Curr>0 AND pastMRR='No':
    #   IF CustProd_Min <= lookback_start → Other In
    #   ELIF Customer_Min <= lookback_start → Cross-sell
    #   ELSE → New Logo
    # ELIF Prior=0 AND Curr>0 AND pastMRR='Yes' → Returning
    # ELIF Prior>0 AND Curr=0 AND DTE>0 AND futureMRR='No':
    #   IF CustProd_Max >= Date → Other Out
    #   ELIF Customer_Max >= Date → Churn-Partial
    #   ELSE → Churn
    # ELIF Curr=0 AND futureMRR='Yes' → Lapsed
    # ELIF Prior>0 AND Curr>0: Prior<=Curr → Upsell, else Downsell
    # ELSE → zz_Unclassified

    def classify(row):
        prior    = row['Prior MRR New']
        curr     = row['MRR New']
        past     = row['pastMRR New']
        future   = row['futureMRR New']
        dte      = row['DTE New']
        lb       = int(row['Month Lookback'])
        date     = row['Date New']
        lb_start = (pd.Timestamp(date) - pd.DateOffset(months=lb)) + MonthEnd(0)

        if prior == 0 and curr != 0 and past == 'No':
            if pd.Timestamp(row['CustProd_Min_Date']) <= lb_start:
                return 'Other In'
            elif pd.Timestamp(row['Customer Min Date New']) <= lb_start:
                return 'Cross-sell'
            else:
                return 'New Logo'
        elif prior == 0 and curr != 0 and past == 'Yes':
            return 'Returning'
        elif prior != 0 and curr == 0 and dte != 0 and future == 'No':
            if pd.Timestamp(row['CustProd_Max_Date']) >= pd.Timestamp(date):
                return 'Other Out'
            elif pd.Timestamp(row['Customer Max Date New']) >= pd.Timestamp(date):
                return 'Churn-Partial'
            else:
                return 'Churn'
        elif curr == 0 and future == 'Yes':
            return 'Lapsed'
        elif prior != 0 and curr != 0:
            return 'Upsell' if prior <= curr else 'Downsell'
        else:
            return 'Unclassified'

    df_all['Bridge Flag']  = df_all.apply(classify, axis=1)
    df_all['Bridge Value'] = df_all['MRR New'] - df_all['Prior MRR New']

    # ── STEP 14: Price/Volume Decomposition (Nodes 938/958) ───────────
    # Only for Upsell/Downsell rows
    mask = df_all['Bridge Flag'].isin(['Upsell', 'Downsell'])

    q   = df_all.loc[mask, 'Quantity New'].replace(0, np.nan)
    pq  = df_all.loc[mask, 'Prior Quantity New'].replace(0, np.nan)
    mrr = df_all.loc[mask, 'MRR New']
    pmr = df_all.loc[mask, 'Prior MRR New']

    p   = (mrr / q).fillna(0)
    pp  = (pmr / pq).fillna(0)

    q   = q.fillna(0)
    pq  = pq.fillna(0)

    price_impact  = (p - pp) * np.minimum(q, pq)
    volume_impact = (q - pq) * np.minimum(p, pp)

    # Price on Volume: same-direction → negate cross term; opposite → 0
    same_dir = ((p > pp) & (q > pq)) | ((p < pp) & (q < pq))
    pov = np.where(same_dir, -((p - pp) * (q - pq)), 0)

    pv_misc = (mrr - pmr) - price_impact - volume_impact - pov

    # Absorb PV Misc into Volume Impact
    volume_impact_final = volume_impact + pv_misc

    df_all['Price Impact']    = 0.0
    df_all['Volume Impact']   = 0.0
    df_all['Price on Volume'] = 0.0
    df_all.loc[mask, 'Price Impact']    = price_impact.values
    df_all.loc[mask, 'Volume Impact']   = volume_impact_final.values
    df_all.loc[mask, 'Price on Volume'] = pov

    # Lookback Date (Node 958)
    df_all['Lookback Date'] = df_all.apply(
        lambda r: (pd.Timestamp(r['Date New']) - pd.DateOffset(months=int(r['Month Lookback']))) + MonthEnd(0),
        axis=1
    )

    # ── STEP 15: Build output rows (vectorized) ──────────────────────
    # CrossTab→Transpose equivalent: stack bridge movements + Beginning + Ending + PV rows
    base = ['Customer New', 'Product New', 'Channel New', 'Region New',
            'Vintage New', 'Date New', 'MRR New', 'Quantity New',
            'Month Lookback', 'Lookback Date']

    def make_rows(df_src, cls_col_or_val, val_col):
        """Vectorized row builder for one classification type."""
        sub = df_src[df_src[val_col] != 0][base + ([cls_col_or_val] if cls_col_or_val in df_src.columns else [])].copy()
        if sub.empty:
            return pd.DataFrame()
        sub['Bridge Classification'] = sub[cls_col_or_val] if cls_col_or_val in sub.columns else cls_col_or_val
        sub['Bridge Value'] = df_src.loc[sub.index, val_col].values
        if cls_col_or_val in sub.columns and cls_col_or_val != 'Bridge Classification':
            sub = sub.drop(columns=[cls_col_or_val])
        return sub[base + ['Bridge Classification', 'Bridge Value']]

    parts = [
        make_rows(df_all, 'Bridge Flag', 'Bridge Value'),
        make_rows(df_all, 'Beginning MRR', 'Prior MRR New'),
        make_rows(df_all, 'Ending MRR',    'MRR New'),
        make_rows(df_all[mask], 'Price Impact',    'Price Impact'),
        make_rows(df_all[mask], 'Volume Impact',   'Volume Impact'),
        make_rows(df_all[mask], 'Price on Volume', 'Price on Volume'),
    ]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()
    df_out = pd.concat(parts, ignore_index=True)
    df_out = df_out[df_out['Bridge Value'] != 0].copy()

    # ── STEP 16: Final rename → exact 12-column output spec ───────────
    df_output = df_out.rename(columns={
        'Customer New':  'Customer',
        'Product New':   'Product',
        'Channel New':   'Channel',
        'Region New':    'Region',
        'Vintage New':   'Vintage',
        'Date New':      'Date',
        'MRR New':       'MRR',
        'Quantity New':  'Quantity',
    })[['Customer', 'Product', 'Channel', 'Region', 'Vintage',
        'Date', 'MRR', 'Quantity', 'Month Lookback', 'Lookback Date',
        'Bridge Classification', 'Bridge Value']]

    df_output['Vintage'] = pd.to_datetime(df_output['Vintage']).dt.year.astype(str)

    return df_output.reset_index(drop=True)
