"""
acv_workflow_engine.py

Exact Python replication of ACV_Analysis_Workflow_Advanced.yxmd

OUTPUT — 12 columns exactly:
  Customer, Product, Channel, Region, Vintage, Date,
  ACV New, Quantity, Month Lookback, DTE New,
  Bridge Classification, Bridge Value

Bridge Classification values:
  New Logo, Churn, Churn-Partial, Cross-sell, Upsell, Downsell,
  Lapsed, Returning, Other In, Other Out, Add-on,
  Prior ACV, Ending ACV, Expiry Pool, RoB

ACV also produces:
  - ACV Table  (monthly revenue recognition per contract)
  - Bookings Table (standardized source data)
"""

import pandas as pd
import numpy as np
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd


def run_acv_workflow(
    df_raw: pd.DataFrame,
    customer_col: str   = 'Customer',
    order_date_col: str = 'Order Date',
    start_col: str      = 'Contract Start Date',
    end_col: str        = 'Contract End Date',
    tcv_col: str        = 'TCV',
    product_col: Optional[str]  = 'Product',
    channel_col: Optional[str]  = 'Channel',
    region_col: Optional[str]   = None,
    quantity_col: Optional[str] = None,
    lookback_months: List[int]  = [1, 3, 12],
    revenue_unit: str = 'raw',
) -> dict:
    """
    Full ACV workflow engine.

    Returns dict with keys:
      'bridge'   → bridge table DataFrame (12 cols)
      'acv'      → ACV table DataFrame (monthly revenue recognition)
      'bookings' → Bookings table DataFrame (standardized source)
    """

    # ── STEP 1: Column mapping ─────────────────────────────────────────
    df = pd.DataFrame()
    df['Customer New']  = df_raw[customer_col].astype(str).str.strip()
    df['Product New']   = df_raw[product_col].astype(str).str.strip()  if product_col  and product_col  in df_raw.columns else 'N/A'
    df['Channel New']   = df_raw[channel_col].astype(str).str.strip()  if channel_col  and channel_col  in df_raw.columns else 'N/A'
    df['Region New']    = df_raw[region_col].astype(str).str.strip()   if region_col   and region_col   in df_raw.columns else 'N/A'
    df['Order Date']    = pd.to_datetime(df_raw[order_date_col], errors='coerce') + MonthEnd(0)
    df['Start Date']    = pd.to_datetime(df_raw[start_col],      errors='coerce')
    df['End Date']      = pd.to_datetime(df_raw[end_col],        errors='coerce')
    df['TCV']           = pd.to_numeric(df_raw[tcv_col], errors='coerce').fillna(0)
    df['Quantity New']  = pd.to_numeric(df_raw[quantity_col], errors='coerce').fillna(0) if quantity_col and quantity_col in df_raw.columns else 0.0

    # Revenue unit conversion
    if revenue_unit == 'millions':
        df['TCV'] = df['TCV'] * 0.000001
    elif revenue_unit == 'thousands':
        df['TCV'] = df['TCV'] * 0.001

    # ── STEP 2: ACV calculation ────────────────────────────────────────
    # ACV = Round((TCV / duration_days) × 365, 6)
    # Duration = Contract End - Contract Start + 1 days (default 365 if null/0)
    df['Duration Days'] = (df['End Date'] - df['Start Date']).dt.days + 1
    df['Duration Days'] = df['Duration Days'].fillna(365).replace(0, 365)
    df['ACV New'] = np.round((df['TCV'] / df['Duration Days']) * 365, 6)

    # ── STEP 3: In-scope filter ────────────────────────────────────────
    # Exclude: Start > End, TCV <= 0, or (TCV > 0 AND Qty <= 0 when qty provided)
    df = df[~(df['Start Date'] > df['End Date'])].copy()
    df = df[df['TCV'] > 0].copy()
    if quantity_col and quantity_col in df_raw.columns:
        df = df[~((df['TCV'] > 0) & (df['Quantity New'] <= 0))].copy()

    if df.empty:
        return {'bridge': pd.DataFrame(), 'acv': pd.DataFrame(), 'bookings': pd.DataFrame()}

    # ── STEP 4: Build Bookings Table ───────────────────────────────────
    bookings = df[['Customer New', 'Product New', 'Channel New', 'Region New',
                   'Order Date', 'Start Date', 'End Date', 'TCV', 'ACV New', 'Quantity New']].copy()
    bookings = bookings.rename(columns={
        'Customer New': 'Customer', 'Product New': 'Product',
        'Channel New': 'Channel',   'Region New': 'Region',
        'ACV New': 'ACV',
    })

    # ── STEP 5: Generate monthly rows per contract ─────────────────────
    # For each lookback: generate months from Start to End + lookback months
    # (different from MRR which uses dataset min/max)
    unit_cols = ['Customer New', 'Product New', 'Channel New', 'Region New']

    # Dataset max date = latest End Date
    dataset_max_date = df['End Date'].max() + MonthEnd(0)

    all_monthly_rows = []
    for _, contract in df.iterrows():
        start = contract['Start Date'] + MonthEnd(0)
        end   = contract['End Date'] + MonthEnd(0)
        # Generate months from contract start to end + max lookback
        max_lb = max(lookback_months)
        gen_end = end + pd.DateOffset(months=max_lb)
        gen_end = gen_end + MonthEnd(0)

        for dt in pd.date_range(start, gen_end, freq='ME'):
            # ACV Flag: 1 if start <= date <= end
            acv_flag = 1 if (dt >= start and dt <= end) else 0
            # Monthly ACV = ACV New / 12
            monthly_acv = contract['ACV New'] / 12 if acv_flag == 1 else 0
            monthly_qty = contract['Quantity New'] / 12 if acv_flag == 1 else 0

            all_monthly_rows.append({
                **{c: contract[c] for c in unit_cols},
                'Date New':      dt,
                'Start Date':    start,
                'End Date':      end,
                'ACV New':       monthly_acv,
                'Quantity New':  monthly_qty,
                'ACV Flag':      acv_flag,
                'TCV':           contract['TCV'],
            })

    if not all_monthly_rows:
        return {'bridge': pd.DataFrame(), 'acv': pd.DataFrame(), 'bookings': bookings}

    monthly = pd.DataFrame(all_monthly_rows)

    # ── STEP 6: Build ACV Table (monthly revenue recognition) ──────────
    acv_table = monthly[monthly['ACV Flag'] == 1].groupby(
        unit_cols + ['Date New'], as_index=False
    ).agg(
        ACV=('ACV New', 'sum'),
        Quantity=('Quantity New', 'sum'),
    )
    acv_table = acv_table.rename(columns={c: c.replace(' New', '') for c in unit_cols})

    # ── STEP 7: Aggregate monthly rows to unit-date level ──────────────
    # Sum ACV, DTE (after computing), Qty across multiple contracts for same unit
    agg = monthly.groupby(unit_cols + ['Date New'], as_index=False).agg(
        ACV_New=('ACV New', 'sum'),
        Quantity_New=('Quantity New', 'sum'),
        End_Date_Max=('End Date', 'max'),
    ).rename(columns={'ACV_New': 'ACV New', 'Quantity_New': 'Quantity New'})

    # ── STEP 8: Unit lifecycle dates ───────────────────────────────────
    lifecycle = agg.groupby(unit_cols, as_index=False).agg(
        Min_Date_New=('Date New', 'min'),
        Max_Date_New=('Date New', 'max'),
    ).rename(columns={'Min_Date_New': 'Min Date New', 'Max_Date_New': 'Max Date New'})

    merged = agg.merge(lifecycle, on=unit_cols, how='left')

    # ── STEP 9: Customer-level lifecycle dates ─────────────────────────
    cust_lc = merged.groupby('Customer New', as_index=False).agg(
        cust_min=('Date New', 'min'),
        cust_max=('Date New', 'max'),
    ).rename(columns={'cust_min': 'Customer Min Date New', 'cust_max': 'Customer Max Date New'})
    merged = merged.merge(cust_lc, on='Customer New', how='left')

    # Vintage = Customer Min Date year
    merged['Vintage New'] = merged['Customer Min Date New']

    # ── STEP 10: CustProd lifecycle dates ──────────────────────────────
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

        # Expiry Pool Flag = 1 if months_since_start >= lookback AND date > end_date
        # Using unit's end date max
        t['months_since_start'] = ((t['Date New'] - t['Min Date New']).dt.days / 30).round(1)
        t['Expiry Pool Flag'] = np.where(
            (t['months_since_start'] >= lb) & (t['Date New'] > t['Max_Date_New']),
            1, 0
        )

        # Sort for prior-row lookup
        t = t.sort_values(unit_cols + ['Date New']).reset_index(drop=True)

        # Prior ACV/Qty = shift(N) within unit group
        t['Prior ACV New']      = t.groupby(unit_cols)['ACV New'].shift(lb).fillna(0)
        t['Prior Quantity New'] = t.groupby(unit_cols)['Quantity New'].shift(lb).fillna(0)

        # DTE = Prior ACV if Expiry Pool, else 0
        t['DTE New'] = np.where(t['Expiry Pool Flag'] == 1, t['Prior ACV New'], 0)

        # Filter: keep within lookback window from unit's max date
        days_diff = (t['Max Date New'] - t['Date New']).dt.days
        t = t[np.round(days_diff / 30, 1) <= lb].copy()

        # Remove all-zero rows
        t = t[~(
            (t['ACV New'] == 0) &
            (t['Prior ACV New'] == 0) &
            (t['DTE New'] == 0)
        )].copy()

        if t.empty:
            continue

        all_lb.append(t)

    if not all_lb:
        return {'bridge': pd.DataFrame(), 'acv': acv_table, 'bookings': bookings}

    df_all = pd.concat(all_lb, ignore_index=True)

    # ── STEP 12: Past/Future flags ─────────────────────────────────────
    days_from_start = (df_all['Date New'] - df_all['Min Date New']).dt.days
    df_all['pastACV New']   = np.where(np.round(days_from_start / 30, 1) < df_all['Month Lookback'], 'No', 'Yes')
    df_all['futureACV New'] = np.where(df_all['Max Date New'] > df_all['Date New'], 'Yes', 'No')

    # ── STEP 13: ACV Bridge Classification ─────────────────────────────
    # Same as MRR EXCEPT:
    #   Case 5 (Upsell/Downsell) requires DTE > 0
    #   NEW Case 6: Prior>0 AND Current>0 AND DTE=0 → Add-on
    #   RoB = pACV if Add-on, else 0
    #   Partial Expiry = pACV - DTE (for Upsell/Downsell when pACV ≠ DTE)

    def classify_acv(row):
        prior   = row['Prior ACV New']
        curr    = row['ACV New']
        past    = row['pastACV New']
        future  = row['futureACV New']
        dte     = row['DTE New']
        lb      = int(row['Month Lookback'])
        date    = row['Date New']
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

        # ACV-specific: Upsell/Downsell requires DTE > 0
        elif prior != 0 and curr != 0 and dte != 0:
            return 'Upsell' if prior <= curr else 'Downsell'

        # ACV-specific: Add-on = Prior>0, Current>0, DTE=0
        elif prior != 0 and curr != 0 and dte == 0:
            return 'Add-on'

        else:
            return 'zz_Unclassified'

    df_all['Bridge Flag']  = df_all.apply(classify_acv, axis=1)
    df_all['Bridge Value'] = df_all['ACV New'] - df_all['Prior ACV New']

    # ── STEP 14: RoB and Partial Expiry (ACV-specific) ─────────────────
    # RoB = Prior ACV if Add-on, else 0
    df_all['RoB'] = np.where(df_all['Bridge Flag'] == 'Add-on', df_all['Prior ACV New'], 0)

    # Partial Expiry Movements = Prior ACV - DTE (for Upsell/Downsell when they differ)
    mask_ud = df_all['Bridge Flag'].isin(['Upsell', 'Downsell'])
    df_all['Partial Expiry Movements'] = 0.0
    df_all.loc[mask_ud, 'Partial Expiry Movements'] = (
        df_all.loc[mask_ud, 'Prior ACV New'] - df_all.loc[mask_ud, 'DTE New']
    )

    # Final RoB = Partial Expiry + RoB
    df_all['RoB Final'] = df_all['Partial Expiry Movements'] + df_all['RoB']

    # Expiry Pool = DTE (the ACV at risk of expiry)
    df_all['Expiry Pool'] = df_all['DTE New']

    # ── STEP 15: Build output rows ──────────────────────────────────────
    base = ['Customer New', 'Product New', 'Channel New', 'Region New',
            'Vintage New', 'Date New', 'ACV New', 'Quantity New',
            'Month Lookback', 'DTE New']

    rows_list = []

    # Bridge movement rows (non-zero)
    for _, r in df_all[df_all['Bridge Value'] != 0].iterrows():
        rows_list.append({**{c: r[c] for c in base},
                          'Bridge Classification': r['Bridge Flag'],
                          'Bridge Value': r['Bridge Value']})

    # Prior ACV rows
    for _, r in df_all[df_all['Prior ACV New'] != 0].iterrows():
        rows_list.append({**{c: r[c] for c in base},
                          'Bridge Classification': 'Prior ACV',
                          'Bridge Value': r['Prior ACV New']})

    # Ending ACV rows
    for _, r in df_all[df_all['ACV New'] != 0].iterrows():
        rows_list.append({**{c: r[c] for c in base},
                          'Bridge Classification': 'Ending ACV',
                          'Bridge Value': r['ACV New']})

    # Expiry Pool rows
    for _, r in df_all[df_all['Expiry Pool'] != 0].iterrows():
        rows_list.append({**{c: r[c] for c in base},
                          'Bridge Classification': 'Expiry Pool',
                          'Bridge Value': r['Expiry Pool']})

    # RoB rows
    for _, r in df_all[df_all['RoB Final'] != 0].iterrows():
        rows_list.append({**{c: r[c] for c in base},
                          'Bridge Classification': 'RoB',
                          'Bridge Value': r['RoB Final']})

    if not rows_list:
        return {'bridge': pd.DataFrame(), 'acv': acv_table, 'bookings': bookings}

    df_out = pd.DataFrame(rows_list)
    df_out = df_out[df_out['Bridge Value'] != 0].copy()

    # ── STEP 16: Final rename → exact 12-column output spec ────────────
    df_bridge = df_out.rename(columns={
        'Customer New': 'Customer',
        'Product New':  'Product',
        'Channel New':  'Channel',
        'Region New':   'Region',
        'Vintage New':  'Vintage',
        'Date New':     'Date',
        'ACV New':      'ACV New',
        'Quantity New': 'Quantity',
        'DTE New':      'DTE New',
    })[['Customer', 'Product', 'Channel', 'Region', 'Vintage',
        'Date', 'ACV New', 'Quantity', 'Month Lookback', 'DTE New',
        'Bridge Classification', 'Bridge Value']]

    df_bridge['Vintage'] = pd.to_datetime(df_bridge['Vintage']).dt.year.astype(str)

    return {
        'bridge':   df_bridge.reset_index(drop=True),
        'acv':      acv_table.reset_index(drop=True),
        'bookings': bookings.reset_index(drop=True),
    }
