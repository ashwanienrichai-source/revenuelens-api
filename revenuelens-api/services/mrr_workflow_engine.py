"""
mrr_workflow_engine.py
======================
Revenue Bridge Engine — MRR / ARR Mode

PIPELINE:
  Step 0  Define UNIT = available dimensions
  Step 1  Data cleaning (date→month-end, revenue>0, fill N/A)
  Step 2  Time series spine (every month per unit, fill zeros)
  Step 3  Lookback calculation (Prior Revenue, Prior Qty, Lookback Date)
  Step 4  Metadata (Unit_Min/Max, Customer_Min/Max dates)
  Step 5  Flags (pastRevenue, futureRevenue)
  Step 6  Classification (vectorized, mutually exclusive)
  Step 7  Bridge Value = Current - Prior
  Step 8  Add Beginning MRR + Ending MRR rows
  Step 9  Granularity remapping
  Step 10 Final output — 12 exact columns

OUTPUT — 12 columns (matching config.py downstream contract):
  Customer, Product, Channel, Region, Vintage, Date,
  MRR or ARR, Quantity, Month Lookback, Lookback Date,
  Bridge Classification, Bridge Value

Classification values (CASE-SENSITIVE, matching config.py mrr_bridge_labels):
  Beginning MRR, Churn, Churn Partial, Downsell, Upsell,
  Other Out, Other In, Cross-sell, New Logo, Lapsed, Returning, Ending MRR

GRANULARITY REMAPPING (applied after classification):
  Customer only         → Cross-sell→New Logo, Churn Partial→Churn,
                          Other In→Returning, Other Out→Churn
  Customer × Product    → Other In→Returning, Other Out→Churn
  Full granularity      → no remapping

FIXES vs previous version (aligned to Alteryx MRR-Workflow-Advanced.yxmd):
  FIX 1 — pastRevenue flag now uses CUSTOMER_Min (not Unit_Min), matching
           Alteryx Step 5.1: pastMRR = date_diff(Date, Customer_Min) >= Lookback
  FIX 2 — DTE (Expiry Pool) flag now correctly marks rows beyond Unit_Max,
           matching Alteryx Step 3.x: Expiry Pool Flag = if Date > Max_Date then 1 else 0
  FIX 3 — Window filter uses dataset_max_date (not Unit_Max) for the
           lookback window boundary, matching Alteryx Step 3.x filter:
           Round(DateTimeDiff(Date, Max_Date_New,'days')/30,1) <= Month_Lookback
  FIX 4 — Beginning MRR / Ending MRR anchor rows are deduplicated per
           unit×date×lookback and use correct columns (Prior Revenue / Revenue),
           matching Alteryx Step 5.3
"""

import pandas as pd
import numpy as np
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd


# ── Granularity remapping (spec Step 9) ───────────────────────────────────
_REMAP_CUSTOMER_ONLY = {
    'Cross-sell':    'New Logo',
    'Churn Partial': 'Churn',
    'Other In':      'Returning',
    'Other Out':     'Churn',
}
_REMAP_CUST_PRODUCT = {
    'Other In':  'Returning',
    'Other Out': 'Churn',
}

def _remap(flag: str, has_product: bool, has_channel_or_region: bool) -> str:
    if not has_product:
        return _REMAP_CUSTOMER_ONLY.get(flag, flag)
    if not has_channel_or_region:
        return _REMAP_CUST_PRODUCT.get(flag, flag)
    return flag


def run_mrr_workflow(
    df_raw: pd.DataFrame,
    customer_col:  str           = 'CustomerID',
    date_col:      str           = 'Period',
    revenue_col:   str           = 'Revenue',
    product_col:   Optional[str] = None,
    channel_col:   Optional[str] = None,
    region_col:    Optional[str] = None,
    quantity_col:  Optional[str] = None,
    lookback_months: List[int]   = [1, 3, 12],
    revenue_unit:  str           = 'raw',
) -> pd.DataFrame:
    """
    Full MRR/ARR bridge engine.
    Returns bridge table with exactly 12 output columns.
    Month Lookback and Bridge Classification are SYSTEM-COMPUTED — never user inputs.
    """

    # ══════════════════════════════════════════════════════════════════
    # STEP 0: Define UNIT — which dimensions are active
    # ══════════════════════════════════════════════════════════════════
    has_product           = bool(product_col and product_col in df_raw.columns)
    has_channel_or_region = bool(
        (channel_col and channel_col in df_raw.columns) or
        (region_col  and region_col  in df_raw.columns)
    )

    # ══════════════════════════════════════════════════════════════════
    # STEP 1: Data cleaning
    # ══════════════════════════════════════════════════════════════════
    df = pd.DataFrame()
    df['Customer']  = df_raw[customer_col].astype(str).str.strip()
    df['Product']   = df_raw[product_col].astype(str).str.strip() if has_product else 'N/A'
    df['Channel']   = df_raw[channel_col].astype(str).str.strip() if channel_col and channel_col in df_raw.columns else 'N/A'
    df['Region']    = df_raw[region_col].astype(str).str.strip()  if region_col  and region_col  in df_raw.columns else 'N/A'
    df['Date']      = pd.to_datetime(df_raw[date_col], errors='coerce') + MonthEnd(0)
    df['Revenue']   = pd.to_numeric(df_raw[revenue_col], errors='coerce').fillna(0)
    df['Quantity']  = pd.to_numeric(df_raw[quantity_col], errors='coerce').fillna(1) \
                      if quantity_col and quantity_col in df_raw.columns else 1.0

    if revenue_unit == 'millions':
        df['Revenue'] = df['Revenue'] * 0.000001
    elif revenue_unit == 'thousands':
        df['Revenue'] = df['Revenue'] * 0.001

    # Remove Revenue <= 0 (matches Alteryx Step 1.5)
    df = df[df['Revenue'] > 0].copy()
    if df.empty:
        return pd.DataFrame()

    # ══════════════════════════════════════════════════════════════════
    # Aggregate to UNIT level
    # ══════════════════════════════════════════════════════════════════
    unit_cols = ['Customer', 'Product', 'Channel', 'Region']
    agg = df.groupby(unit_cols + ['Date'], as_index=False).agg(
        Revenue=('Revenue', 'sum'),
        Quantity=('Quantity', 'sum'),
    )

    dataset_max_date = agg['Date'].max()

    # ══════════════════════════════════════════════════════════════════
    # STEP 4: Metadata — unit and customer lifecycle dates
    # ══════════════════════════════════════════════════════════════════
    unit_lc = agg.groupby(unit_cols, as_index=False).agg(
        Unit_Min=('Date', 'min'),
        Unit_Max=('Date', 'max'),
    )
    # FIX 1a: Customer_Min/Max based on ALL units for that customer
    # Alteryx Step 4.4/4.6 summarises at Customer level, not unit level
    cust_lc = agg.groupby('Customer', as_index=False).agg(
        Customer_Min=('Date', 'min'),
        Customer_Max=('Date', 'max'),
    )

    # ══════════════════════════════════════════════════════════════════
    # STEP 2: Time series spine — every month from Unit_Min to dataset max
    # ══════════════════════════════════════════════════════════════════
    spine_rows = []
    for _, r in unit_lc.iterrows():
        for dt in pd.date_range(r['Unit_Min'], dataset_max_date, freq='ME'):
            spine_rows.append({
                'Customer': r['Customer'], 'Product': r['Product'],
                'Channel':  r['Channel'],  'Region':  r['Region'],
                'Unit_Min': r['Unit_Min'], 'Unit_Max': r['Unit_Max'],
                'Date':     dt,
            })
    if not spine_rows:
        return pd.DataFrame()
    grid = pd.DataFrame(spine_rows)

    merged = grid.merge(agg[unit_cols + ['Date', 'Revenue', 'Quantity']],
                        on=unit_cols + ['Date'], how='left')
    merged['Revenue']  = merged['Revenue'].fillna(0)
    merged['Quantity'] = merged['Quantity'].fillna(0)

    merged = merged.merge(cust_lc, on='Customer', how='left')
    merged['Vintage'] = merged['Customer_Min']

    # ══════════════════════════════════════════════════════════════════
    # STEP 3: Lookback calculation — one pass per lookback period
    # ══════════════════════════════════════════════════════════════════
    all_lb = []
    for lb in lookback_months:
        t = merged.copy()
        t['Month Lookback'] = lb

        t = t.sort_values(unit_cols + ['Date']).reset_index(drop=True)

        t['Prior Revenue']  = t.groupby(unit_cols)['Revenue'].shift(lb).fillna(0)
        t['Prior Quantity'] = t.groupby(unit_cols)['Quantity'].shift(lb).fillna(0)

        t['Lookback Date'] = (
            t['Date'] - t['Month Lookback'].apply(lambda x: pd.DateOffset(months=int(x)))
        ).apply(lambda d: d + MonthEnd(0))

        # FIX 2: Expiry Pool Flag = 1 when Date is beyond Unit's last real date
        # Matches Alteryx: Expiry Pool Flag = if Date > Max_Date then 1 else 0
        t['Expiry_Pool_Flag'] = (t['Date'] > t['Unit_Max']).astype(int)
        t['DTE'] = np.where(t['Expiry_Pool_Flag'] == 1, t['Prior Revenue'], 0)

        # FIX 3: Window filter uses DATASET max date (global), not Unit_Max
        # Matches Alteryx: Round(DateTimeDiff(Date, Max_Date_New,'days')/30,1) <= Month_Lookback
        days_diff = (dataset_max_date - t['Date']).dt.days
        t = t[np.round(days_diff / 30, 1) <= lb].copy()

        # Remove rows with no signal
        t = t[~((t['Revenue'] == 0) & (t['Prior Revenue'] == 0) & (t['DTE'] == 0))].copy()

        all_lb.append(t)

    df_all = pd.concat(all_lb, ignore_index=True)
    if df_all.empty:
        return pd.DataFrame()

    # ══════════════════════════════════════════════════════════════════
    # STEP 5: Flags
    # FIX 1 (MAIN): pastRevenue uses CUSTOMER_Min, NOT Unit_Min
    # Matches Alteryx Step 5.1:
    #   pastMRR = if Round(datetimediff(Date, Customer_Min,'days')/30,1)
    #              > Month_Lookback then 'Yes' else 'No'
    # Using Unit_Min would misclassify cross-sells/new products as New Logos.
    # ══════════════════════════════════════════════════════════════════
    days_since_customer_start = (df_all['Date'] - df_all['Customer_Min']).dt.days
    df_all['pastRevenue']   = np.round(days_since_customer_start / 30, 1) > df_all['Month Lookback']
    df_all['futureRevenue'] = df_all['Unit_Max'] > df_all['Date']

    # ══════════════════════════════════════════════════════════════════
    # STEP 6: Classification — VECTORIZED, mutually exclusive
    # Exact order per Alteryx XML Step 5.2 Bridge Flag formula
    # ══════════════════════════════════════════════════════════════════
    pr      = df_all['Prior Revenue']
    cur     = df_all['Revenue']
    pm      = df_all['pastRevenue']
    fm      = df_all['futureRevenue']
    dte     = df_all['DTE']
    d       = df_all['Date']
    cm      = df_all['Customer_Min']
    cx      = df_all['Customer_Max']
    um      = df_all['Unit_Min']
    ux      = df_all['Unit_Max']
    lb_date = df_all['Lookback Date']

    # ENTRY branch (Prior=0, Current>0)
    entry    = (pr == 0) & (cur > 0)
    past_no  = ~pm
    past_yes = pm

    cond_other_in      = entry & past_no & (um <= lb_date)
    cond_cross_sell    = entry & past_no & (um > lb_date) & (cm <= lb_date)
    cond_new_logo      = entry & past_no & (um > lb_date) & (cm > lb_date)
    cond_returning     = entry & past_yes   # FIX 1: now correctly uses Customer_Min

    # EXIT branch (Prior>0, Current=0, DTE>0, no future revenue)
    # FIX 2: DTE is now only non-zero for rows genuinely beyond Unit_Max
    exit_base          = (pr > 0) & (cur == 0) & (dte > 0) & ~fm
    cond_other_out     = exit_base & (ux >= d)
    cond_churn_partial = exit_base & (ux < d) & (cx >= d)
    cond_churn         = exit_base & (ux < d) & (cx < d)

    # Lapsed: Current=0, future revenue exists
    cond_lapsed   = (cur == 0) & fm

    # Change branch (Prior>0, Current>0)
    change        = (pr > 0) & (cur > 0)
    cond_upsell   = change & (cur > pr)
    cond_downsell = change & (cur < pr)

    conditions = [
        cond_other_in, cond_cross_sell, cond_new_logo, cond_returning,
        cond_other_out, cond_churn_partial, cond_churn,
        cond_lapsed, cond_upsell, cond_downsell,
    ]
    choices = [
        'Other In', 'Cross-sell', 'New Logo', 'Returning',
        'Other Out', 'Churn Partial', 'Churn',
        'Lapsed', 'Upsell', 'Downsell',
    ]

    df_all['Bridge Flag'] = np.select(conditions, choices, default='Unclassified')

    # ══════════════════════════════════════════════════════════════════
    # STEP 9: Granularity remapping (vectorized)
    # ══════════════════════════════════════════════════════════════════
    df_all['Bridge Flag'] = df_all['Bridge Flag'].map(
        lambda f: _remap(f, has_product, has_channel_or_region)
    )

    # ══════════════════════════════════════════════════════════════════
    # STEP 7: Bridge Value = Current - Prior
    # ══════════════════════════════════════════════════════════════════
    df_all['Bridge Value'] = df_all['Revenue'] - df_all['Prior Revenue']

    mask_ud = df_all['Bridge Flag'].isin(['Upsell', 'Downsell'])
    if mask_ud.any():
        q   = df_all.loc[mask_ud, 'Quantity'].replace(0, np.nan)
        pq  = df_all.loc[mask_ud, 'Prior Quantity'].replace(0, np.nan)
        rev = df_all.loc[mask_ud, 'Revenue']
        pre = df_all.loc[mask_ud, 'Prior Revenue']
        p   = (rev / q).fillna(0);  q  = q.fillna(0)
        pp  = (pre / pq).fillna(0); pq = pq.fillna(0)
        pi  = (p - pp) * np.minimum(q, pq)
        vi  = (q - pq) * np.minimum(p, pp)
        same_dir = ((p > pp) & (q > pq)) | ((p < pp) & (q < pq))
        pov = np.where(same_dir, -((p - pp) * (q - pq)), 0)
        pv_misc = (rev - pre) - pi - vi - pov
        df_all.loc[mask_ud, 'Price Impact']    = pi.values
        df_all.loc[mask_ud, 'Volume Impact']   = (vi + pv_misc).values
        df_all.loc[mask_ud, 'Price on Volume'] = pov
    df_all[['Price Impact', 'Volume Impact', 'Price on Volume']] = \
        df_all[['Price Impact', 'Volume Impact', 'Price on Volume']].fillna(0)

    # ══════════════════════════════════════════════════════════════════
    # STEP 8: Build output rows
    # FIX 4: Separate movement rows from anchor rows (Beginning/Ending MRR)
    # Beginning MRR = Prior Revenue, Ending MRR = current Revenue
    # Deduplicated per unit×date×lookback to avoid double-counting
    # Matches Alteryx Step 5.3
    # ══════════════════════════════════════════════════════════════════
    base_cols = ['Customer', 'Product', 'Channel', 'Region', 'Vintage',
                 'Date', 'Revenue', 'Quantity', 'Month Lookback', 'Lookback Date']

    def make_movement_rows(src: pd.DataFrame) -> pd.DataFrame:
        sub = src[src['Bridge Value'] != 0].copy()
        if sub.empty:
            return pd.DataFrame()
        out = sub[base_cols].copy()
        out['Bridge Classification'] = sub['Bridge Flag'].values
        out['Bridge Value']          = sub['Bridge Value'].values
        return out[base_cols + ['Bridge Classification', 'Bridge Value']]

    def make_anchor_rows(src: pd.DataFrame, classification: str, val_col: str) -> pd.DataFrame:
        sub = src[src[val_col] != 0].copy()
        if sub.empty:
            return pd.DataFrame()
        dedup_cols = ['Customer', 'Product', 'Channel', 'Region', 'Date', 'Month Lookback']
        sub = sub.sort_values(val_col, ascending=False).drop_duplicates(subset=dedup_cols)
        out = sub[base_cols].copy()
        out['Bridge Classification'] = classification
        out['Bridge Value']          = sub[val_col].values
        return out[base_cols + ['Bridge Classification', 'Bridge Value']]

    parts = [
        make_movement_rows(df_all),
        make_anchor_rows(df_all, 'Beginning MRR', 'Prior Revenue'),
        make_anchor_rows(df_all, 'Ending MRR',    'Revenue'),
    ]

    if quantity_col and quantity_col in df_raw.columns and mask_ud.any():
        parts += [
            make_anchor_rows(df_all[mask_ud], 'Price Impact',    'Price Impact'),
            make_anchor_rows(df_all[mask_ud], 'Volume Impact',   'Volume Impact'),
            make_anchor_rows(df_all[mask_ud], 'Price on Volume', 'Price on Volume'),
        ]

    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()

    df_out = pd.concat(parts, ignore_index=True)
    df_out = df_out[df_out['Bridge Value'] != 0].copy()

    # ══════════════════════════════════════════════════════════════════
    # STEP 10: Final rename → exact 12-column output contract
    # ══════════════════════════════════════════════════════════════════
    df_out = df_out.rename(columns={'Revenue': 'MRR or ARR'})
    df_out['Vintage'] = pd.to_datetime(df_out['Vintage']).dt.year.astype(str)

    return df_out[[
        'Customer', 'Product', 'Channel', 'Region', 'Vintage',
        'Date', 'MRR or ARR', 'Quantity', 'Month Lookback', 'Lookback Date',
        'Bridge Classification', 'Bridge Value',
    ]].reset_index(drop=True)
