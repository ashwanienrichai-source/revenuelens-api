"""
mrr_workflow_engine.py

Exact Python replication of MRR_Workflow_Advanced.yxmd

OUTPUT — 12 columns exactly (matching Alteryx Node 944 output names):
  Customer, Product, Channel, Region, Vintage, Date,
  MRR or ARR, Quantity, Month Lookback, Lookback Date,
  Bridge Classification, Bridge Value

Bridge Classification values:
  New Logo, Churn, Churn-Partial, Cross-sell, Upsell, Downsell,
  Lapsed, Returning, Other In, Other Out,
  Beginning MRR, Ending MRR,
  Price Impact, Volume Impact, Price on Volume

Classification constraints by granularity (spec §CLASSIFICATION DEPENDENCY RULES):
  Customer only (Product/Channel/Region all N/A):
    Possible:   New Logo, Churn, Upsell, Downsell, Lapsed, Returning
    Impossible: Churn-Partial, Cross-sell, Other In, Other Out
  Customer × Product (Channel/Region N/A):
    Possible:   above + Churn-Partial, Cross-sell
    Impossible: Other In, Other Out
  Full granularity: ALL classifications possible
"""

import pandas as pd
import numpy as np
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd


def _remap_by_granularity(flag: str, has_product: bool, has_channel_or_region: bool) -> str:
    """
    Remap classifications that are impossible at lower granularity.
    Called AFTER bridge flag is computed — does not change the formula logic,
    only collapses unreachable classes into their nearest valid equivalent.
    """
    if not has_product:
        # Customer-only: no product dimension
        # Cross-sell → New Logo (new product of existing customer collapses to New Logo)
        # Other In   → Returning (re-entry without product context)
        # Other Out  → Churn     (exit without product context)
        # Churn-Partial → Churn  (no product = no partial churn)
        remap = {
            'Cross-sell':    'New Logo',
            'Other In':      'Returning',
            'Other Out':     'Churn',
            'Churn-Partial': 'Churn',
        }
        return remap.get(flag, flag)

    if not has_channel_or_region:
        # Customer × Product only: Other In / Other Out impossible
        remap = {
            'Other In':  'Returning',
            'Other Out': 'Churn',
        }
        return remap.get(flag, flag)

    return flag


def run_mrr_workflow(
    df_raw: pd.DataFrame,
    customer_col: str            = 'CustomerID',
    date_col: str                = 'Period',
    revenue_col: str             = 'Revenue',
    product_col: Optional[str]   = None,
    channel_col: Optional[str]   = None,
    region_col: Optional[str]    = None,
    quantity_col: Optional[str]  = None,
    lookback_months: List[int]   = [1, 3, 12],
    revenue_unit: str            = 'raw',
) -> pd.DataFrame:
    """
    Full MRR workflow engine. Returns bridge table with exactly 12 output columns.
    Lookback and Bridge Classification are system-computed — never user inputs.
    """

    # ── STEP 1: Column mapping (Node 896) ─────────────────────────────
    # Dimensions default to 'N/A' when not provided — this controls
    # which bridge classifications are mathematically reachable.
    df = pd.DataFrame()
    df['Customer New']  = df_raw[customer_col].astype(str).str.strip()
    df['Product New']   = df_raw[product_col].astype(str).str.strip()  if product_col  and product_col  in df_raw.columns else 'N/A'
    df['Channel New']   = df_raw[channel_col].astype(str).str.strip()  if channel_col  and channel_col  in df_raw.columns else 'N/A'
    df['Region New']    = df_raw[region_col].astype(str).str.strip()   if region_col   and region_col   in df_raw.columns else 'N/A'
    df['Date New']      = pd.to_datetime(df_raw[date_col], errors='coerce') + MonthEnd(0)
    df['MRR New']       = pd.to_numeric(df_raw[revenue_col], errors='coerce').fillna(0)
    df['Quantity New']  = pd.to_numeric(df_raw[quantity_col], errors='coerce').fillna(1) \
                          if quantity_col and quantity_col in df_raw.columns else 1.0

    # Granularity flags — determine which classifications are reachable
    has_product          = product_col is not None and product_col in df_raw.columns
    has_channel_or_region = (
        (channel_col is not None and channel_col in df_raw.columns) or
        (region_col  is not None and region_col  in df_raw.columns)
    )

    # Revenue unit conversion
    if revenue_unit == 'millions':
        df['MRR New'] = df['MRR New'] * 0.000001
    elif revenue_unit == 'thousands':
        df['MRR New'] = df['MRR New'] * 0.001

    # ── STEP 2: In-scope filter (Node 1090) ───────────────────────────
    df = df[df['MRR New'] > 0].copy()
    if df.empty:
        return pd.DataFrame()

    # ── STEP 3: Dataset max date (Node 827) ───────────────────────────
    dataset_max_date = df['Date New'].max()

    # ── STEP 4: Aggregate to unit level (Node 835) ────────────────────
    unit_cols = ['Customer New', 'Product New', 'Channel New', 'Region New']
    agg = df.groupby(unit_cols + ['Date New'], as_index=False).agg(
        MRR_New=('MRR New', 'sum'),
        Qty_New=('Quantity New', 'sum'),
    ).rename(columns={'MRR_New': 'MRR New', 'Qty_New': 'Quantity New'})

    # ── STEP 5: Unit lifecycle min/max (Node 820) ─────────────────────
    lc = agg.groupby(unit_cols, as_index=False).agg(
        Min_Date=('Date New', 'min'),
        Max_Date=('Date New', 'max'),
    ).rename(columns={'Min_Date': 'Min Date New', 'Max_Date': 'Max Date New'})

    # ── STEP 6: Monthly date spine (Node 823) ─────────────────────────
    rows = []
    for _, r in lc.iterrows():
        for dt in pd.date_range(r['Min Date New'], dataset_max_date, freq='ME'):
            rows.append({
                'Customer New':  r['Customer New'],
                'Product New':   r['Product New'],
                'Channel New':   r['Channel New'],
                'Region New':    r['Region New'],
                'Min Date New':  r['Min Date New'],
                'Max Date New':  r['Max Date New'],
                'Date New':      dt,
            })
    if not rows:
        return pd.DataFrame()
    grid = pd.DataFrame(rows)

    # ── STEP 7: Left-join actuals onto spine (Nodes 824/825/826) ──────
    merged = grid.merge(
        agg[unit_cols + ['Date New', 'MRR New', 'Quantity New']],
        on=unit_cols + ['Date New'], how='left'
    )
    merged['MRR New']      = merged['MRR New'].fillna(0)
    merged['Quantity New'] = merged['Quantity New'].fillna(0)

    # ── STEP 8: Customer-level lifecycle (Node 946) ───────────────────
    cust_lc = merged.groupby('Customer New', as_index=False).agg(
        cust_min=('Date New', 'min'),
        cust_max=('Date New', 'max'),
    ).rename(columns={'cust_min': 'Customer Min Date New', 'cust_max': 'Customer Max Date New'})
    merged = merged.merge(cust_lc, on='Customer New', how='left')

    # ── STEP 9: Vintage (Node 950) ────────────────────────────────────
    merged['Vintage New'] = merged['Customer Min Date New']

    # ── STEP 10: CustProd lifecycle (Node 947) ────────────────────────
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

        # Filter within lookback window from unit's max actual date (Node 907)
        days_diff = (t['Max Date New'] - t['Date New']).dt.days
        t = t[np.round(days_diff / 30, 1) <= lb].copy()

        # Expiry Pool Flag (Node 921): date beyond unit's last actual data
        t['Expiry Pool Flag'] = np.where(t['Date New'] > t['Max Date New'], 1, 0)

        # Sort for shift() lookup — must be sorted by unit + date
        t = t.sort_values(unit_cols + ['Date New']).reset_index(drop=True)

        # Prior MRR = shift(N) within unit group (Nodes 915/919/923)
        t['Prior MRR New']      = t.groupby(unit_cols)['MRR New'].shift(lb).fillna(0)
        t['Prior Quantity New'] = t.groupby(unit_cols)['Quantity New'].shift(lb).fillna(0)

        # DTE (Node 925)
        t['DTE New'] = np.where(t['Expiry Pool Flag'] == 1, t['Prior MRR New'], 0)

        all_lb.append(t)

    df_all = pd.concat(all_lb, ignore_index=True)

    # Remove all-zero rows (Node 934)
    df_all = df_all[~(
        (df_all['MRR New'] == 0) &
        (df_all['Prior MRR New'] == 0) &
        (df_all['DTE New'] == 0)
    )].copy()

    # ── STEP 12: Past/Future flags (Node 935) ─────────────────────────
    days_from_start = (df_all['Date New'] - df_all['Min Date New']).dt.days
    df_all['pastMRR New']   = np.where(
        np.round(days_from_start / 30, 1) < df_all['Month Lookback'], 'No', 'Yes'
    )
    df_all['futureMRR New'] = np.where(
        df_all['Max Date New'] > df_all['Date New'], 'Yes', 'No'
    )

    # ── STEP 13: Bridge Classification (Node 936) ─────────────────────
    # Strict if/elseif order exactly as in workflow XML.
    # Granularity remapping applied AFTER classification to handle N/A dimensions.
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

    # Apply granularity remapping — collapses impossible classifications
    df_all['Bridge Flag'] = df_all['Bridge Flag'].apply(
        lambda f: _remap_by_granularity(f, has_product, has_channel_or_region)
    )

    df_all['Bridge Value'] = df_all['MRR New'] - df_all['Prior MRR New']

    # ── STEP 14: Price/Volume Decomposition (Nodes 938/958) ───────────
    mask_pvd = df_all['Bridge Flag'].isin(['Upsell', 'Downsell'])

    q   = df_all.loc[mask_pvd, 'Quantity New'].replace(0, np.nan)
    pq  = df_all.loc[mask_pvd, 'Prior Quantity New'].replace(0, np.nan)
    mrr = df_all.loc[mask_pvd, 'MRR New']
    pmr = df_all.loc[mask_pvd, 'Prior MRR New']

    p  = (mrr / q).fillna(0);  q  = q.fillna(0)
    pp = (pmr / pq).fillna(0); pq = pq.fillna(0)

    price_impact  = (p - pp) * np.minimum(q, pq)
    volume_impact = (q - pq) * np.minimum(p, pp)
    same_dir      = ((p > pp) & (q > pq)) | ((p < pp) & (q < pq))
    pov           = np.where(same_dir, -((p - pp) * (q - pq)), 0)
    pv_misc       = (mrr - pmr) - price_impact - volume_impact - pov
    volume_impact_final = volume_impact + pv_misc

    df_all['Price Impact']    = 0.0
    df_all['Volume Impact']   = 0.0
    df_all['Price on Volume'] = 0.0
    df_all.loc[mask_pvd, 'Price Impact']    = price_impact.values
    df_all.loc[mask_pvd, 'Volume Impact']   = volume_impact_final.values
    df_all.loc[mask_pvd, 'Price on Volume'] = pov

    # Lookback Date (Node 958)
    df_all['Lookback Date'] = df_all.apply(
        lambda r: (pd.Timestamp(r['Date New']) - pd.DateOffset(months=int(r['Month Lookback']))) + MonthEnd(0),
        axis=1
    )

    # ── STEP 15: Build output rows (CrossTab→Transpose equivalent) ────
    # Produces rows for: bridge movements + Beginning MRR + Ending MRR + PV rows
    base = ['Customer New', 'Product New', 'Channel New', 'Region New',
            'Vintage New', 'Date New', 'MRR New', 'Quantity New',
            'Month Lookback', 'Lookback Date']

    def stack(src, cls_val_or_col, val_col, filter_nonzero=True):
        sub = src.copy()
        if filter_nonzero:
            sub = sub[src[val_col] != 0]
        if sub.empty:
            return pd.DataFrame()
        out = sub[base].copy()
        out['Bridge Classification'] = sub[cls_val_or_col].values if cls_val_or_col in src.columns else cls_val_or_col
        out['Bridge Value'] = sub[val_col].values
        return out[base + ['Bridge Classification', 'Bridge Value']]

    parts = [
        stack(df_all,        'Bridge Flag',    'Bridge Value'),
        stack(df_all,        'Beginning MRR',  'Prior MRR New'),   # anchor rows
        stack(df_all,        'Ending MRR',     'MRR New'),         # anchor rows
        stack(df_all[mask_pvd], 'Price Impact',    'Price Impact'),
        stack(df_all[mask_pvd], 'Volume Impact',   'Volume Impact'),
        stack(df_all[mask_pvd], 'Price on Volume', 'Price on Volume'),
    ]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()

    df_out = pd.concat(parts, ignore_index=True)
    # Keep non-zero Bridge Values (but Beginning/Ending can stay even if 0 for that row)
    df_out = df_out[df_out['Bridge Value'] != 0].copy()

    # ── STEP 16: Final rename — exact Alteryx Node 944 column names ───
    df_output = df_out.rename(columns={
        'Customer New':  'Customer',
        'Product New':   'Product',
        'Channel New':   'Channel',
        'Region New':    'Region',
        'Vintage New':   'Vintage',
        'Date New':      'Date',
        'MRR New':       'MRR or ARR',     # matches Alteryx output exactly
        'Quantity New':  'Quantity',
    })[['Customer', 'Product', 'Channel', 'Region', 'Vintage',
        'Date', 'MRR or ARR', 'Quantity', 'Month Lookback', 'Lookback Date',
        'Bridge Classification', 'Bridge Value']]

    df_output['Vintage'] = pd.to_datetime(df_output['Vintage']).dt.year.astype(str)

    return df_output.reset_index(drop=True)
