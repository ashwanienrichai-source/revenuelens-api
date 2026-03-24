"""
acv_workflow_engine.py

Exact Python replication of ACV_Analysis_Workflow_Advanced.yxmd

OUTPUT — 12 columns exactly (matching Alteryx output):
  Customer, Product, Channel, Region, Vintage, Date,
  ACV New, Quantity, Month Lookback, DTE New,
  Bridge Classification, Bridge Value

Bridge Classification values:
  New Logo, Churn, Churn-Partial, Cross-sell, Upsell, Downsell,
  Lapsed, Returning, Other In, Other Out, Add-on,
  Prior ACV, Ending ACV, Expiry Pool, RoB

ACV produces 3 output tables:
  bridge   — bridge table (12 cols above)
  acv      — monthly revenue recognition per unit
  bookings — standardized source/bookings data
"""

import pandas as pd
import numpy as np
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd


def _remap_by_granularity(flag: str, has_product: bool, has_channel_or_region: bool) -> str:
    """Same granularity remapping as MRR engine."""
    if not has_product:
        return {'Cross-sell': 'New Logo', 'Other In': 'Returning',
                'Other Out': 'Churn', 'Churn Partial': 'Churn'}.get(flag, flag)
    if not has_channel_or_region:
        return {'Other In': 'Returning', 'Other Out': 'Churn'}.get(flag, flag)
    return flag


def run_acv_workflow(
    df_raw: pd.DataFrame,
    customer_col: str            = 'Customer',
    order_date_col: str          = 'Order Date',
    start_col: str               = 'Contract Start Date',
    end_col: str                 = 'Contract End Date',
    tcv_col: str                 = 'TCV',
    product_col: Optional[str]   = None,
    channel_col: Optional[str]   = None,
    region_col: Optional[str]    = None,
    quantity_col: Optional[str]  = None,
    lookback_months: List[int]   = [1, 3, 12],
    revenue_unit: str            = 'raw',
) -> dict:
    """
    Full ACV workflow engine.
    Returns dict: { 'bridge', 'acv', 'bookings' }
    """

    # ── STEP 1: Column mapping ─────────────────────────────────────────
    df = pd.DataFrame()
    df['Customer New']  = df_raw[customer_col].astype(str).str.strip()
    df['Product New']   = df_raw[product_col].astype(str).str.strip()  if product_col  and product_col  in df_raw.columns else 'N/A'
    df['Channel New']   = df_raw[channel_col].astype(str).str.strip()  if channel_col  and channel_col  in df_raw.columns else 'N/A'
    df['Region New']    = df_raw[region_col].astype(str).str.strip()   if region_col   and region_col   in df_raw.columns else 'N/A'
    df['Order Date']    = pd.to_datetime(df_raw[order_date_col], errors='coerce') + MonthEnd(0) \
                          if order_date_col in df_raw.columns else pd.NaT
    df['Start Date']    = pd.to_datetime(df_raw[start_col],  errors='coerce')
    df['End Date']      = pd.to_datetime(df_raw[end_col],    errors='coerce')
    df['TCV']           = pd.to_numeric(df_raw[tcv_col], errors='coerce').fillna(0)
    df['Quantity New']  = pd.to_numeric(df_raw[quantity_col], errors='coerce').fillna(0) \
                          if quantity_col and quantity_col in df_raw.columns else 0.0

    # Granularity flags
    has_product           = product_col is not None and product_col in df_raw.columns
    has_channel_or_region = (
        (channel_col is not None and channel_col in df_raw.columns) or
        (region_col  is not None and region_col  in df_raw.columns)
    )

    # Revenue unit conversion
    if revenue_unit == 'millions':
        df['TCV'] = df['TCV'] * 0.000001
    elif revenue_unit == 'thousands':
        df['TCV'] = df['TCV'] * 0.001

    # ── STEP 2: ACV = Round((TCV / duration_days) × 365, 6) ───────────
    df['Duration Days'] = (df['End Date'] - df['Start Date']).dt.days + 1
    df['Duration Days'] = df['Duration Days'].fillna(365).replace(0, 365)
    df['ACV New'] = np.round((df['TCV'] / df['Duration Days']) * 365, 6)

    # ── STEP 3: In-scope filter ────────────────────────────────────────
    df = df[~(df['Start Date'] > df['End Date'])].copy()
    df = df[df['TCV'] > 0].copy()
    if quantity_col and quantity_col in df_raw.columns:
        df = df[~((df['TCV'] > 0) & (df['Quantity New'] <= 0))].copy()

    if df.empty:
        return {'bridge': pd.DataFrame(), 'acv': pd.DataFrame(), 'bookings': pd.DataFrame()}

    # ── STEP 4: Bookings table ─────────────────────────────────────────
    bookings = df[['Customer New', 'Product New', 'Channel New', 'Region New',
                   'Order Date', 'Start Date', 'End Date', 'TCV', 'ACV New', 'Quantity New']].copy()
    bookings.rename(columns={
        'Customer New': 'Customer', 'Product New': 'Product',
        'Channel New':  'Channel',  'Region New':  'Region', 'ACV New': 'ACV',
    }, inplace=True)

    # ── STEP 5: Generate monthly rows per contract ─────────────────────
    unit_cols    = ['Customer New', 'Product New', 'Channel New', 'Region New']
    max_lb       = max(lookback_months)
    monthly_rows = []

    for _, c in df.iterrows():
        start     = c['Start Date'] + MonthEnd(0)
        end       = c['End Date']   + MonthEnd(0)
        gen_end   = end + pd.DateOffset(months=max_lb) + MonthEnd(0)

        for dt in pd.date_range(start, gen_end, freq='ME'):
            acv_flag    = 1 if (dt >= start and dt <= end) else 0
            monthly_acv = c['ACV New'] / 12 if acv_flag == 1 else 0
            monthly_qty = c['Quantity New'] / 12 if acv_flag == 1 else 0
            monthly_rows.append({
                **{col: c[col] for col in unit_cols},
                'Date New':     dt,
                'Start Date':   start,
                'End Date':     end,
                'ACV New':      monthly_acv,
                'Quantity New': monthly_qty,
                'ACV Flag':     acv_flag,
                'TCV':          c['TCV'],
            })

    if not monthly_rows:
        return {'bridge': pd.DataFrame(), 'acv': pd.DataFrame(), 'bookings': bookings}

    monthly = pd.DataFrame(monthly_rows)

    # ── STEP 6: ACV recognition table ─────────────────────────────────
    acv_table = monthly[monthly['ACV Flag'] == 1].groupby(
        unit_cols + ['Date New'], as_index=False
    ).agg(ACV=('ACV New', 'sum'), Quantity=('Quantity New', 'sum'))
    acv_table.rename(columns={c: c.replace(' New', '') for c in unit_cols}, inplace=True)

    # ── STEP 7: Aggregate monthly rows to unit-date level ─────────────
    agg = monthly.groupby(unit_cols + ['Date New'], as_index=False).agg(
        ACV_New=('ACV New', 'sum'),
        Qty_New=('Quantity New', 'sum'),
        End_Date_Max=('End Date', 'max'),
    ).rename(columns={'ACV_New': 'ACV New', 'Qty_New': 'Quantity New'})

    # ── STEP 8: Unit lifecycle dates ───────────────────────────────────
    lc = agg.groupby(unit_cols, as_index=False).agg(
        Min_Date=('Date New', 'min'),
        Max_Date=('Date New', 'max'),
    ).rename(columns={'Min_Date': 'Min Date New', 'Max_Date': 'Max Date New'})
    # IMPORTANT: use consistent 'Max Date New' (with spaces) throughout
    merged = agg.merge(lc, on=unit_cols, how='left')

    # ── STEP 9: Customer-level lifecycle ──────────────────────────────
    cust_lc = merged.groupby('Customer New', as_index=False).agg(
        cust_min=('Date New', 'min'),
        cust_max=('Date New', 'max'),
    ).rename(columns={'cust_min': 'Customer Min Date New', 'cust_max': 'Customer Max Date New'})
    merged = merged.merge(cust_lc, on='Customer New', how='left')
    merged['Vintage New'] = merged['Customer Min Date New']

    # ── STEP 10: CustProd lifecycle ────────────────────────────────────
    cp_lc = merged.groupby(['Customer New', 'Product New'], as_index=False).agg(
        cp_min=('Date New', 'min'),
        cp_max=('Date New', 'max'),
    ).rename(columns={'cp_min': 'CustProd_Min_Date', 'cp_max': 'CustProd_Max_Date'})
    merged = merged.merge(cp_lc, on=['Customer New', 'Product New'], how='left')

    # ── STEP 11: Lookback branches ─────────────────────────────────────
    all_lb = []
    for lb in lookback_months:
        t = merged.copy()
        t['Month Lookback'] = lb

        # months_since_start for Expiry Pool Flag
        t['months_since_start'] = ((t['Date New'] - t['Min Date New']).dt.days / 30).round(1)

        # Expiry Pool Flag = 1 if months_since_start >= lb AND date > unit's max date
        # Use 'Max Date New' (consistent column name with spaces)
        t['Expiry Pool Flag'] = np.where(
            (t['months_since_start'] >= lb) & (t['Date New'] > t['Max Date New']),
            1, 0
        )

        # Sort for shift() lookup
        t = t.sort_values(unit_cols + ['Date New']).reset_index(drop=True)

        # Prior ACV/Qty = shift(N) within unit group
        t['Prior ACV New']      = t.groupby(unit_cols)['ACV New'].shift(lb).fillna(0)
        t['Prior Quantity New'] = t.groupby(unit_cols)['Quantity New'].shift(lb).fillna(0)

        # DTE = Prior ACV when in Expiry Pool
        t['DTE New'] = np.where(t['Expiry Pool Flag'] == 1, t['Prior ACV New'], 0)

        # Filter within lookback window from unit's max date
        days_diff = (t['Max Date New'] - t['Date New']).dt.days
        t = t[np.round(days_diff / 30, 1) <= lb].copy()

        # Remove all-zero rows
        t = t[~(
            (t['ACV New'] == 0) &
            (t['Prior ACV New'] == 0) &
            (t['DTE New'] == 0)
        )].copy()

        if not t.empty:
            all_lb.append(t)

    if not all_lb:
        return {'bridge': pd.DataFrame(), 'acv': acv_table, 'bookings': bookings}

    df_all = pd.concat(all_lb, ignore_index=True)

    # ── STEP 12: Past/Future flags ─────────────────────────────────────
    days_from_start = (df_all['Date New'] - df_all['Min Date New']).dt.days
    df_all['pastACV New']   = np.where(
        np.round(days_from_start / 30, 1) < df_all['Month Lookback'], 'No', 'Yes'
    )
    df_all['futureACV New'] = np.where(
        df_all['Max Date New'] > df_all['Date New'], 'Yes', 'No'
    )

    # ── STEP 13: ACV Bridge Classification ─────────────────────────────
    # Same as MRR EXCEPT: Upsell/Downsell requires DTE>0; Add-on when DTE=0
    def classify_acv(row):
        prior    = row['Prior ACV New']
        curr     = row['ACV New']
        past     = row['pastACV New']
        future   = row['futureACV New']
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
                return 'Churn Partial'
            else:
                return 'Churn'
        elif curr == 0 and future == 'Yes':
            return 'Lapsed'
        elif prior != 0 and curr != 0 and dte != 0:   # ACV: Upsell/Downsell requires DTE>0
            return 'Upsell' if prior <= curr else 'Downsell'
        elif prior != 0 and curr != 0 and dte == 0:   # ACV: Add-on when DTE=0
            return 'Add-on'
        else:
            return 'Unclassified'

    df_all['Bridge Flag'] = df_all.apply(classify_acv, axis=1)
    df_all['Bridge Flag'] = df_all['Bridge Flag'].apply(
        lambda f: _remap_by_granularity(f, has_product, has_channel_or_region)
    )
    df_all['Bridge Value'] = df_all['ACV New'] - df_all['Prior ACV New']

    # ── STEP 14: RoB and Partial Expiry ────────────────────────────────
    df_all['RoB'] = np.where(df_all['Bridge Flag'] == 'Add-on', df_all['Prior ACV New'], 0)

    mask_ud = df_all['Bridge Flag'].isin(['Upsell', 'Downsell'])
    df_all['Partial Expiry'] = 0.0
    df_all.loc[mask_ud, 'Partial Expiry'] = (
        df_all.loc[mask_ud, 'Prior ACV New'] - df_all.loc[mask_ud, 'DTE New']
    )
    df_all['RoB Final']   = df_all['Partial Expiry'] + df_all['RoB']
    df_all['Expiry Pool'] = df_all['DTE New']

    # ── STEP 15: Build output rows ──────────────────────────────────────
    base = ['Customer New', 'Product New', 'Channel New', 'Region New',
            'Vintage New', 'Date New', 'ACV New', 'Quantity New',
            'Month Lookback', 'DTE New']

    def stack(src, cls_val_or_col, val_col):
        sub = src[src[val_col] != 0].copy()
        if sub.empty:
            return pd.DataFrame()
        out = sub[base].copy()
        out['Bridge Classification'] = sub[cls_val_or_col].values \
            if cls_val_or_col in src.columns else cls_val_or_col
        out['Bridge Value'] = sub[val_col].values
        return out[base + ['Bridge Classification', 'Bridge Value']]

    parts = [
        stack(df_all,        'Bridge Flag',  'Bridge Value'),
        stack(df_all,        'Prior ACV',    'Prior ACV New'),
        stack(df_all,        'Ending ACV',   'ACV New'),
        stack(df_all,        'Expiry Pool',  'Expiry Pool'),
        stack(df_all,        'RoB',          'RoB Final'),
    ]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return {'bridge': pd.DataFrame(), 'acv': acv_table, 'bookings': bookings}

    df_out = pd.concat(parts, ignore_index=True)
    df_out = df_out[df_out['Bridge Value'] != 0].copy()

    # ── STEP 16: Final rename — exact Alteryx output column names ──────
    df_bridge = df_out.rename(columns={
        'Customer New': 'Customer',
        'Product New':  'Product',
        'Channel New':  'Channel',
        'Region New':   'Region',
        'Vintage New':  'Vintage',
        'Date New':     'Date',
        'Quantity New': 'Quantity',
    })[['Customer', 'Product', 'Channel', 'Region', 'Vintage',
        'Date', 'ACV New', 'Quantity', 'Month Lookback', 'DTE New',
        'Bridge Classification', 'Bridge Value']]

    df_bridge['Vintage'] = pd.to_datetime(df_bridge['Vintage']).dt.year.astype(str)

    return {
        'bridge':   df_bridge.reset_index(drop=True),
        'acv':      acv_table.reset_index(drop=True),
        'bookings': bookings.reset_index(drop=True),
    }
