"""
mrr_workflow_engine.py
======================
Revenue Bridge Engine — MRR / ARR Mode

Implements the canonical spec exactly:
  https://[internal spec doc]

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
"""

import pandas as pd
import numpy as np
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd


# ── Granularity remapping (spec Step 9) ───────────────────────────────────
_REMAP_CUSTOMER_ONLY = {
    'Cross-sell':  'New Logo',
    'Churn Partial': 'Churn',
    'Other In':    'Returning',
    'Other Out':   'Churn',
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

    # Revenue unit conversion
    if revenue_unit == 'millions':
        df['Revenue'] = df['Revenue'] * 0.000001
    elif revenue_unit == 'thousands':
        df['Revenue'] = df['Revenue'] * 0.001

    # Remove Revenue <= 0 (In-scope filter)
    df = df[df['Revenue'] > 0].copy()
    if df.empty:
        return pd.DataFrame()

    # ══════════════════════════════════════════════════════════════════
    # Aggregate to UNIT level (handles duplicate rows in source data)
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

    # Left-join actuals → fill missing months with 0
    merged = grid.merge(agg[unit_cols + ['Date', 'Revenue', 'Quantity']],
                        on=unit_cols + ['Date'], how='left')
    merged['Revenue']  = merged['Revenue'].fillna(0)
    merged['Quantity'] = merged['Quantity'].fillna(0)

    # Attach customer lifecycle dates + Vintage
    merged = merged.merge(cust_lc, on='Customer', how='left')
    merged['Vintage'] = merged['Customer_Min']

    # ══════════════════════════════════════════════════════════════════
    # STEP 3: Lookback calculation — one pass per lookback period
    # ══════════════════════════════════════════════════════════════════
    all_lb = []
    for lb in lookback_months:
        t = merged.copy()
        t['Month Lookback'] = lb

        # Sort within unit for shift() to work correctly
        t = t.sort_values(unit_cols + ['Date']).reset_index(drop=True)

        # Prior Revenue = shift(lb) within each unit group
        t['Prior Revenue']  = t.groupby(unit_cols)['Revenue'].shift(lb).fillna(0)
        t['Prior Quantity'] = t.groupby(unit_cols)['Quantity'].shift(lb).fillna(0)

        # Lookback Date (spec Step 3)
        t['Lookback Date'] = (
            t['Date'] - t['Month Lookback'].apply(lambda x: pd.DateOffset(months=int(x)))
        ).apply(lambda d: d + MonthEnd(0))

        # Expiry Pool Flag (for DTE — not used in MRR but kept for structural parity)
        t['DTE'] = np.where(t['Date'] > t['Unit_Max'], t['Prior Revenue'], 0)

        # Window filter — keep only rows within lookback window of unit max
        days_diff = (t['Unit_Max'] - t['Date']).dt.days
        t = t[np.round(days_diff / 30, 1) <= lb].copy()

        # Remove rows where MRR=0 AND Prior=0 AND DTE=0 (no signal)
        t = t[~((t['Revenue'] == 0) & (t['Prior Revenue'] == 0) & (t['DTE'] == 0))].copy()

        all_lb.append(t)

    df_all = pd.concat(all_lb, ignore_index=True)
    if df_all.empty:
        return pd.DataFrame()

    # ══════════════════════════════════════════════════════════════════
    # STEP 5: Flags
    # pastRevenue  = unit existed BEFORE current date (>1 period old)
    # futureRevenue = unit has data AFTER current date
    # ══════════════════════════════════════════════════════════════════
    days_since_start = (df_all['Date'] - df_all['Unit_Min']).dt.days
    df_all['pastRevenue']   = np.round(days_since_start / 30, 1) >= df_all['Month Lookback']
    df_all['futureRevenue'] = df_all['Unit_Max'] > df_all['Date']

    # ══════════════════════════════════════════════════════════════════
    # STEP 6: Classification — VECTORIZED, mutually exclusive
    # Exact order per spec + Alteryx XML (Node 936)
    # ══════════════════════════════════════════════════════════════════
    pr  = df_all['Prior Revenue']
    cur = df_all['Revenue']
    pm  = df_all['pastRevenue']
    fm  = df_all['futureRevenue']
    dte = df_all['DTE']
    d   = df_all['Date']
    cm  = df_all['Customer_Min']
    cx  = df_all['Customer_Max']
    um  = df_all['Unit_Min']
    ux  = df_all['Unit_Max']
    lb_date = df_all['Lookback Date']

    # ENTRY branch conditions (Prior=0, Current>0)
    entry         = (pr == 0) & (cur > 0)
    past_no       = ~pm                          # unit appeared within current lookback window
    past_yes      = pm                           # unit existed before lookback window

    # Classification per Alteryx Node 936 (exact order):
    cond_other_in    = entry & past_no & (um <= lb_date)
    cond_cross_sell  = entry & past_no & (um > lb_date) & (cm <= lb_date)
    cond_new_logo    = entry & past_no & (um > lb_date) & (cm > lb_date)
    cond_returning   = entry & past_yes

    # EXIT branch (Prior>0, Current=0, DTE>0, no future revenue)
    exit_base        = (pr > 0) & (cur == 0) & (dte > 0) & ~fm
    cond_other_out   = exit_base & (ux >= d)
    cond_churn_partial = exit_base & (ux < d) & (cx >= d)
    cond_churn       = exit_base & (ux < d) & (cx < d)

    # Lapsed: Current=0, future revenue exists
    cond_lapsed      = (cur == 0) & fm

    # Change branch (Prior>0, Current>0)
    change           = (pr > 0) & (cur > 0)
    cond_upsell      = change & (cur > pr)
    cond_downsell    = change & (cur < pr)
    # Prior == Current → Bridge Value = 0 → filtered at output ✓

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

    # Price/Volume decomposition (for Upsell/Downsell when Quantity is meaningful)
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
    # Stack: movements + Beginning MRR + Ending MRR (+ PV rows if Qty provided)
    # ══════════════════════════════════════════════════════════════════
    base_cols = ['Customer', 'Product', 'Channel', 'Region', 'Vintage',
                 'Date', 'Revenue', 'Quantity', 'Month Lookback', 'Lookback Date']

    def make_rows(src: pd.DataFrame, cls: str, val_col: str) -> pd.DataFrame:
        sub = src[src[val_col] != 0].copy()
        if sub.empty:
            return pd.DataFrame()
        out = sub[base_cols].copy()
        out['Bridge Classification'] = cls if cls not in src.columns else sub[cls].values
        out['Bridge Value']          = sub[val_col].values
        return out[base_cols + ['Bridge Classification', 'Bridge Value']]

    parts = [
        make_rows(df_all,           'Bridge Flag',    'Bridge Value'),
        make_rows(df_all,           'Beginning MRR',  'Prior Revenue'),
        make_rows(df_all,           'Ending MRR',     'Revenue'),
    ]
    # Include Price/Volume rows only when Quantity is real data (not default=1)
    if quantity_col and quantity_col in df_raw.columns and mask_ud.any():
        parts += [
            make_rows(df_all[mask_ud], 'Price Impact',    'Price Impact'),
            make_rows(df_all[mask_ud], 'Volume Impact',   'Volume Impact'),
            make_rows(df_all[mask_ud], 'Price on Volume', 'Price on Volume'),
        ]

    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()

    df_out = pd.concat(parts, ignore_index=True)
    # Drop zero Bridge Values (Prior==Current produces BV=0 → correctly dropped here)
    df_out = df_out[df_out['Bridge Value'] != 0].copy()

    # ══════════════════════════════════════════════════════════════════
    # STEP 10: Final rename → exact 12-column output contract
    # 'MRR or ARR' matches Alteryx Node 944 output + config.py rename map
    # ══════════════════════════════════════════════════════════════════
    df_out = df_out.rename(columns={'Revenue': 'MRR or ARR'})

    df_out['Vintage'] = pd.to_datetime(df_out['Vintage']).dt.year.astype(str)

    return df_out[[
        'Customer', 'Product', 'Channel', 'Region', 'Vintage',
        'Date', 'MRR or ARR', 'Quantity', 'Month Lookback', 'Lookback Date',
        'Bridge Classification', 'Bridge Value',
    ]].reset_index(drop=True)
