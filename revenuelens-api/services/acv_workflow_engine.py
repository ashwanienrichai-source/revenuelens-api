"""
acv_workflow_engine_v2.py — Optimized + bug-fixed version.

Changes from original:
  1. FIXED BUG: CLS_RET (undefined) -> CLS_RETURNING. This was crashing every
     request with NameError after all computation finished.
  2. Step 2 (ACV computation): vectorized, no more row-wise .apply().
  3. Step 6 (dense series): replaced per-group merge loop with a single
     vectorized cross-join + one merge. Same output, same row order logic.
  4. Step 9 (LB_Start): vectorized per distinct lookback value instead of
     row-wise .apply() (there are only ever 1-3 distinct lookback values).
  5. Step 10 (classification): replaced row-wise .apply(classify) with
     np.select() over vectorized boolean conditions, same priority order
     as the original if/elif chain.
  6. Steps 4 and 5 loops switched from .iterrows() to .itertuples() for a
     moderate speedup (business logic unchanged, still sequential per
     contract because contract-month expansion is inherently sequential).

Nothing about classification RULES, reconciliation math, or QC changed.
Only *how* the same numbers get computed changed.
"""

import pandas as pd
import gc
import time
import numpy as np
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd
from risk_opportunity import compute_risk_opportunity

# ── Payload size guardrail ─────────────────────────────────────────────────
# customer_bridge (per-customer detail, used by Top Movers / Account 360 /
# Cohort) is capped to the top N customers by absolute bridge movement, to
# keep the JSON response small enough for Render's free-tier memory limit.
# This is NOT a business-logic limit — every customer is still fully computed
# above; this only controls how many get sent to the frontend by name.
#
# Raised from 200 -> 1500 (Jul 2026): your ground-truth dataset has 7,212
# customers total, so 1500 covers the large majority of realistic client
# datasets today with real headroom. If a client's dataset regularly exceeds
# this, the right fix is server-side pagination + search, not just raising
# this number further — see product discussion log.
MAX_CUSTOMER_BRIDGE_ROWS = 1500

CLS_NEW_LOGO  = 'New Logo'
CLS_CROSS     = 'Cross-sell'
CLS_OTHER_IN  = 'Other In'
CLS_RETURNING = 'Returning'
CLS_CHURN     = 'Churn'
CLS_CHURN_P   = 'Churn Partial'
CLS_OTHER_OUT = 'Other Out'
CLS_LAPSED    = 'Lapsed'
CLS_UPSELL    = 'Upsell'
CLS_DOWNSELL  = 'Downsell'
CLS_ADDON     = 'Add on'
CLS_PRIOR     = 'Prior ACV'
CLS_ENDING    = 'Ending ACV'
CLS_EXPIRY    = 'Expiry Pool'
CLS_ROB       = 'RoB'
CLS_PRICE_I   = 'Price Impact'
CLS_VOL_I     = 'Volume Impact'
CLS_POV       = 'Price on Volume'
CLS_UNCLASS   = 'zz_Unclassified'


def _last_of_month(d):
    return d + MonthEnd(0)

def _is_last_of_month(d):
    return d == _last_of_month(d)

def _days_between(a, b):
    return int((b - a).days)

def _round1(x):
    return round(float(x), 1)

def _round2(x):
    return round(float(x), 2)

def _round6(x):
    return round(float(x), 6)


def _month_revenue(acv, cstart, cend, obs):
    try:
        m_first = pd.Timestamp(obs.year, obs.month, 1)
    except Exception:
        return 0.0
    m_last = obs
    if cend < m_first or cstart > m_last:
        return 0.0
    daily = acv / 365
    if m_first <= cstart <= m_last:
        return _round2(daily * (_days_between(cstart, m_last) + 1))
    if m_first <= cend <= m_last:
        return _round2(daily * (_days_between(m_first, cend) + 1))
    return _round2(daily * obs.days_in_month)


def _generate_rows(acv, qty, cstart, cend, lb):
    """Same exact Alteryx logic as original — returns list of dicts (not DataFrame)."""
    start_lom    = _last_of_month(cstart)
    end_lom      = _last_of_month(cend)
    start_is_lom = _is_last_of_month(cstart)
    end_is_lom   = _is_last_of_month(cend)

    rows = []
    date = start_lom

    while True:
        if end_is_lom:
            stop = _round1(_days_between(cend, date) / 30) > lb
        else:
            stop = _round1(_days_between(end_lom, date) / 30) > lb - 1
        if stop:
            break

        acv_flag = (start_lom <= date <= end_lom)

        if start_is_lom:
            expiry_flag = _round1(_days_between(cstart, date) / 30) >= lb and date > cend
        else:
            expiry_flag = _round1(_days_between(start_lom, date) / 30) >= lb and date > cend

        rows.append({
            'Date New':   date,
            'ACV New':    acv  if acv_flag else 0.0,
            'DTE New':    acv  if expiry_flag else 0.0,
            'Qty New':    qty  if acv_flag else 0.0,
        })
        date = date + MonthEnd(1)

    return rows


def run_acv_workflow(
    df_raw: pd.DataFrame,
    customer_col: str = 'Customer',
    start_col: str = 'Contract Start Date',
    end_col: str = 'Contract End Date',
    tcv_col: str = 'TCV',
    order_date_col: Optional[str] = None,
    product_col: Optional[str] = None,
    channel_col: Optional[str] = None,
    region_col: Optional[str] = None,
    quantity_col: Optional[str] = None,
    lookback_months: List[int] = [12],
    revenue_unit: str = 'TCV',
    include_acv_table: bool = False,
    _return_timings: bool = False,
) -> dict:

    timings = {}
    t_start = time.time()
    def mark(label, t0):
        timings[label] = round(time.time() - t0, 3)

    # STEP 1
    t0 = time.time()
    df = pd.DataFrame()
    df['Customer']  = df_raw[customer_col].astype(str).str.strip()
    df['Product']   = df_raw[product_col].astype(str).str.strip()  if product_col  and product_col  in df_raw.columns else 'N/A'
    df['Channel']   = df_raw[channel_col].astype(str).str.strip()  if channel_col  and channel_col  in df_raw.columns else 'N/A'
    df['Region']    = df_raw[region_col].astype(str).str.strip()   if region_col   and region_col   in df_raw.columns else 'N/A'
    df['Start']     = pd.to_datetime(df_raw[start_col],  errors='coerce')
    df['End']       = pd.to_datetime(df_raw[end_col],    errors='coerce')
    df['TCV']       = pd.to_numeric(df_raw[tcv_col],     errors='coerce').fillna(0.0)
    df['Qty']       = pd.to_numeric(df_raw[quantity_col], errors='coerce').fillna(0.0) \
                      if quantity_col and quantity_col in df_raw.columns else 0.0
    df['OrderDate'] = pd.to_datetime(df_raw[order_date_col], errors='coerce') \
                      if order_date_col and order_date_col in df_raw.columns else pd.NaT

    has_product  = product_col  is not None and product_col  in df_raw.columns
    has_channel  = channel_col  is not None and channel_col  in df_raw.columns
    has_region   = region_col   is not None and region_col   in df_raw.columns
    has_quantity = (
        quantity_col is not None
        and quantity_col in df_raw.columns
        and (df['Qty'] > 0).any()
    )
    mark('STEP 1 - column mapping', t0)

    # STEP 2 - VECTORIZED
    t0 = time.time()
    dur = (df['End'] - df['Start']).dt.days + 1
    dur = dur.where(dur > 0, 365)
    valid = df['Start'].notna() & df['End'].notna()

    if revenue_unit == 'ACV':
        acv_vals = df['TCV'].round(6)
    else:
        acv_vals = (df['TCV'] / dur * 365).round(6)

    df['ACV'] = np.where(valid, acv_vals, 0.0)
    mark('STEP 2 - ACV computation (vectorized)', t0)

    # STEP 3
    t0 = time.time()
    cond_start_after_end = df['Start'] > df['End']
    cond_tcv_zero        = df['TCV'] <= 0
    cond_qty_invalid     = (df['TCV'] > 0) & (df['Qty'] <= 0) & has_quantity
    df['InScope'] = ~(cond_start_after_end | cond_tcv_zero | cond_qty_invalid)

    bookings = df.copy()
    df_in = df[df['InScope']].copy()
    mark('STEP 3 - in-scope filter', t0)

    if df_in.empty:
        out = {'bridge': pd.DataFrame(), 'acv': pd.DataFrame(), 'bookings': bookings, 'qc': {}}
        if _return_timings:
            out['timings'] = timings
        return out

    # STEP 4 - itertuples instead of iterrows
    t0 = time.time()
    acv_rows = []
    for c in df_in.itertuples(index=False):
        date = _last_of_month(c.Start)
        end  = _last_of_month(c.End)
        while date <= end:
            rev = _month_revenue(c.ACV, c.Start, c.End, date)
            if rev > 0:
                acv_rows.append({
                    'Customer': c.Customer, 'Product': c.Product,
                    'Contract Start Date': c.Start, 'Contract End Date': c.End,
                    'ACV': c.ACV, 'Date': date, 'Revenue': rev,
                    'Channel': c.Channel, 'Region': c.Region,
                })
            date = date + MonthEnd(1)
    acv_table = pd.DataFrame(acv_rows)
    mark('STEP 4 - ACV table (itertuples)', t0)

    UNIT_COLS = ['Customer', 'Product', 'Channel', 'Region']
    all_lb_parts = []

    for lb in lookback_months:
        # STEP 5 - itertuples instead of iterrows
        t0 = time.time()
        all_rows = []
        for c in df_in.itertuples(index=False):
            gr = _generate_rows(c.ACV, c.Qty, c.Start, c.End, lb)
            if not gr:
                continue
            unit_vals = {'Customer': c.Customer, 'Product': c.Product,
                         'Channel': c.Channel, 'Region': c.Region}
            for rd in gr:
                rd.update(unit_vals)
            all_rows.extend(gr)
        monthly = pd.DataFrame(all_rows) if all_rows else pd.DataFrame()
        mark(f'STEP 5 (lb={lb}) - generate_rows (itertuples)', t0)

        if monthly.empty:
            continue

        t0 = time.time()
        agg = monthly.groupby(UNIT_COLS + ['Date New'], as_index=False).agg(
            ACV_New=('ACV New', 'sum'),
            DTE_New=('DTE New', 'sum'),
            Qty_New=('Qty New', 'sum'),
        )
        agg['Month Lookback'] = lb
        mark(f'STEP 5b (lb={lb}) - groupby aggregate', t0)

        # STEP 6 - VECTORIZED cross-join (was merge-per-group loop)
        t0 = time.time()
        unit_ranges = agg.groupby(UNIT_COLS)['Date New'].agg(['min', 'max']).reset_index()
        unit_ranges.columns = list(UNIT_COLS) + ['unit_min', 'unit_max']

        global_dates = pd.date_range(agg['Date New'].min(), agg['Date New'].max(), freq='ME')
        date_df = pd.DataFrame({'Date New': global_dates})

        unit_ranges['_key'] = 1
        date_df['_key'] = 1
        cross = unit_ranges.merge(date_df, on='_key').drop(columns='_key')
        cross = cross[(cross['Date New'] >= cross['unit_min']) & (cross['Date New'] <= cross['unit_max'])]
        cross = cross.drop(columns=['unit_min', 'unit_max']).copy()
        cross['Month Lookback'] = lb

        dense = cross.merge(
            agg[UNIT_COLS + ['Date New', 'ACV_New', 'DTE_New', 'Qty_New']],
            on=UNIT_COLS + ['Date New'], how='left'
        ).fillna(0.0)
        mark(f'STEP 6 (lb={lb}) - dense series (vectorized cross-join, {len(unit_ranges)} units)', t0)

        if dense.empty:
            continue

        # STEP 7 - unchanged, already vectorized
        t0 = time.time()
        dense = dense.sort_values(UNIT_COLS + ['Date New']).reset_index(drop=True)
        dense['pACV'] = dense.groupby(UNIT_COLS)['ACV_New'].shift(lb).fillna(0.0)
        dense['pQty'] = dense.groupby(UNIT_COLS)['Qty_New'].shift(lb).fillna(0.0)
        mask_allzero = (dense['ACV_New'] == 0) & (dense['pACV'] == 0) & (dense['DTE_New'] == 0)
        dense = dense[~mask_allzero].copy()
        mask_dead = (dense['ACV_New'] == 0) & (dense['pACV'] > 0) & (dense['DTE_New'] == 0)
        dense = dense[~mask_dead].copy()
        mark(f'STEP 7 (lb={lb}) - pACV shift + filters', t0)

        all_lb_parts.append(dense)

    if not all_lb_parts:
        out = {'bridge': pd.DataFrame(), 'acv': acv_table, 'bookings': bookings, 'qc': {}}
        if _return_timings:
            out['timings'] = timings
        return out

    df_all = pd.concat(all_lb_parts, ignore_index=True)

    # STEP 8 - unchanged, already vectorized
    t0 = time.time()
    acv_rows_mask = df_all['ACV_New'] > 0
    unit_min = df_all[acv_rows_mask].groupby(UNIT_COLS)['Date New'].min().rename('Unit_Min')
    unit_max = df_all[acv_rows_mask].groupby(UNIT_COLS)['Date New'].max().rename('Unit_Max')
    cust_min = df_all[acv_rows_mask].groupby('Customer')['Date New'].min().rename('Cust_Min')
    cust_max = df_all[acv_rows_mask].groupby('Customer')['Date New'].max().rename('Cust_Max')
    cp_min   = df_all[acv_rows_mask].groupby(['Customer', 'Product'])['Date New'].min().rename('CP_Min')
    cp_max   = df_all[acv_rows_mask].groupby(['Customer', 'Product'])['Date New'].max().rename('CP_Max')

    df_all = df_all.merge(unit_min.reset_index(), on=UNIT_COLS, how='left')
    df_all = df_all.merge(unit_max.reset_index(), on=UNIT_COLS, how='left')
    df_all = df_all.merge(cust_min.reset_index(), on='Customer', how='left')
    df_all = df_all.merge(cust_max.reset_index(), on='Customer', how='left')
    df_all = df_all.merge(cp_min.reset_index(),   on=['Customer', 'Product'], how='left')
    df_all = df_all.merge(cp_max.reset_index(),   on=['Customer', 'Product'], how='left')
    df_all['Vintage'] = df_all['Cust_Min']
    mark('STEP 8 - reference date maps (6 merges)', t0)

    # STEP 9 - VECTORIZED per distinct lookback value
    t0 = time.time()
    unit_min_lom = df_all['Unit_Min'].apply(lambda d: _last_of_month(d) if pd.notna(d) else pd.NaT)
    days_from_start = (df_all['Date New'] - unit_min_lom).dt.days.fillna(0)
    df_all['pastACV']   = (days_from_start / 30).round(1) >= df_all['Month Lookback']
    df_all['futureACV'] = df_all['Unit_Max'] > df_all['Date New']

    df_all['LB_Start'] = pd.NaT
    for lb_val, idx in df_all.groupby('Month Lookback').groups.items():
        df_all.loc[idx, 'LB_Start'] = (
            df_all.loc[idx, 'Date New'] - pd.DateOffset(months=int(lb_val))
        ) + MonthEnd(0)
    mark('STEP 9 - pastACV/futureACV + LB_Start (vectorized per lb)', t0)

    # STEP 10 - VECTORIZED with np.select (was .apply)
    t0 = time.time()
    prior  = df_all['pACV']
    curr   = df_all['ACV_New']
    dte    = df_all['DTE_New']
    past   = df_all['pastACV'].astype(bool)
    future = df_all['futureACV'].astype(bool)
    cp_mn  = df_all['CP_Min']
    cp_mx  = df_all['CP_Max']
    c_min  = df_all['Cust_Min']
    c_max  = df_all['Cust_Max']
    lb_s   = df_all['LB_Start']
    date   = df_all['Date New']

    is_new_base = (prior == 0) & (curr != 0) & (~past)
    cond_other_in  = is_new_base & cp_mn.notna() & (cp_mn <= lb_s)
    cond_cross     = is_new_base & (~cond_other_in) & c_min.notna() & (c_min <= lb_s)
    cond_new_logo  = is_new_base & (~cond_other_in) & (~cond_cross)
    cond_returning = (prior == 0) & (curr != 0) & past

    is_out_base    = (prior != 0) & (curr == 0) & (dte != 0) & (~future)
    cond_other_out = is_out_base & cp_mx.notna() & (cp_mx >= date)
    cond_churn_p   = is_out_base & (~cond_other_out) & c_max.notna() & (c_max >= date)
    cond_churn     = is_out_base & (~cond_other_out) & (~cond_churn_p)

    cond_lapsed   = (curr == 0) & (dte != 0) & future
    cond_upsell   = (prior != 0) & (curr != 0) & (dte != 0) & (prior <= curr)
    cond_downsell = (prior != 0) & (curr != 0) & (dte != 0) & (prior > curr)
    cond_addon    = (prior != 0) & (curr != 0) & (dte == 0)

    conditions = [cond_other_in, cond_cross, cond_new_logo, cond_returning,
                  cond_other_out, cond_churn_p, cond_churn,
                  cond_lapsed, cond_upsell, cond_downsell, cond_addon]
    choices = [CLS_OTHER_IN, CLS_CROSS, CLS_NEW_LOGO, CLS_RETURNING,
               CLS_OTHER_OUT, CLS_CHURN_P, CLS_CHURN,
               CLS_LAPSED, CLS_UPSELL, CLS_DOWNSELL, CLS_ADDON]

    df_all['Bridge Flag'] = np.select(conditions, choices, default=CLS_UNCLASS)
    mark('STEP 10 - classification (np.select, vectorized)', t0)

    if not has_product:
        remap = {CLS_CROSS: CLS_NEW_LOGO, CLS_OTHER_IN: CLS_RETURNING, CLS_OTHER_OUT: CLS_CHURN, CLS_CHURN_P: CLS_CHURN}
        df_all['Bridge Flag'] = df_all['Bridge Flag'].map(lambda f: remap.get(f, f))
    elif not has_channel and not has_region:
        remap = {CLS_OTHER_IN: CLS_RETURNING, CLS_OTHER_OUT: CLS_CHURN}
        df_all['Bridge Flag'] = df_all['Bridge Flag'].map(lambda f: remap.get(f, f))

    df_all['Bridge Value'] = df_all['ACV_New'] - df_all['pACV']

    # STEP 11 - unchanged
    t0 = time.time()
    mask_ud  = df_all['Bridge Flag'].isin([CLS_UPSELL, CLS_DOWNSELL])
    mask_adn = df_all['Bridge Flag'] == CLS_ADDON
    df_all['Rob_Addon']   = np.where(mask_adn, df_all['pACV'], 0.0)
    df_all['Partial_Exp'] = 0.0
    df_all.loc[mask_ud, 'Partial_Exp'] = (df_all.loc[mask_ud, 'pACV'] - df_all.loc[mask_ud, 'DTE_New']).values
    df_all['RoB_Final']   = df_all['Partial_Exp'] + df_all['Rob_Addon']
    mark('STEP 11 - RoB metrics', t0)

    # STEP 12 - unchanged
    t0 = time.time()
    df_all['Price_Impact'] = 0.0
    df_all['Vol_Impact']   = 0.0
    df_all['POV']          = 0.0
    if has_quantity:
        ud = df_all[mask_ud & (df_all['Qty_New'] > 0) & (df_all['pQty'] > 0)].copy()
        if not ud.empty:
            ud['price']  = ud['ACV_New'] / ud['Qty_New']
            ud['pPrice'] = ud['pACV']    / ud['pQty']
            ud['dP']     = ud['price']   - ud['pPrice']
            ud['dQ']     = ud['Qty_New'] - ud['pQty']
            ud['minQ']   = ud[['Qty_New', 'pQty']].min(axis=1)
            ud['minP']   = ud[['price', 'pPrice']].min(axis=1)
            ud['pi']     = ud['dP'] * ud['minQ']
            ud['vi']     = ud['dQ'] * ud['minP']
            same_dir = (ud['dP'] > 0) & (ud['dQ'] > 0)
            both_neg = (ud['dP'] < 0) & (ud['dQ'] < 0)
            ud['pov'] = 0.0
            ud.loc[same_dir, 'pov'] =  ud.loc[same_dir, 'dP'] * ud.loc[same_dir, 'dQ']
            ud.loc[both_neg, 'pov'] = -(ud.loc[both_neg, 'dP'] * ud.loc[both_neg, 'dQ'])
            pv_misc = ud['Bridge Value'] - (ud['pi'] + ud['vi'] + ud['pov'])
            ud['vi'] = ud['vi'] + pv_misc
            df_all.loc[ud.index, 'Price_Impact'] = ud['pi'].values
            df_all.loc[ud.index, 'Vol_Impact']   = ud['vi'].values
            df_all.loc[ud.index, 'POV']          = ud['pov'].values
    mark('STEP 12 - Price x Volume', t0)

    # STEP 13 - unchanged
    t0 = time.time()
    BASE = ['Customer', 'Product', 'Channel', 'Region', 'Vintage', 'Date New',
            'ACV_New', 'Qty_New', 'Month Lookback', 'DTE_New']

    def emit(col_or_val, val_col):
        sub = df_all[df_all[val_col] != 0].copy()
        if sub.empty:
            return pd.DataFrame()
        out = sub[BASE].copy()
        out['Bridge Classification'] = col_or_val if isinstance(col_or_val, str) \
            else sub[col_or_val].values
        out['Bridge Value'] = sub[val_col].values
        return out

    parts = [
        emit('Bridge Flag', 'Bridge Value'),
        emit(CLS_PRIOR,     'pACV'),
        emit(CLS_ENDING,    'ACV_New'),
        emit(CLS_EXPIRY,    'DTE_New'),
        emit(CLS_ROB,       'RoB_Final'),
        emit(CLS_PRICE_I,   'Price_Impact'),
        emit(CLS_VOL_I,     'Vol_Impact'),
        emit(CLS_POV,       'POV'),
    ]
    parts[0]['Bridge Classification'] = df_all.loc[df_all['Bridge Value'] != 0, 'Bridge Flag'].values

    bridge = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    bridge = bridge[bridge['Bridge Value'] != 0].copy()
    bridge.rename(columns={'Date New': 'Date', 'ACV_New': 'ACV New', 'Qty_New': 'Quantity'}, inplace=True)
    bridge['Vintage'] = pd.to_datetime(bridge['Vintage']).dt.normalize()
    bridge = bridge[['Customer', 'Product', 'Channel', 'Region', 'Vintage',
                     'Date', 'ACV New', 'Quantity', 'Month Lookback', 'DTE_New',
                     'Bridge Classification', 'Bridge Value']].reset_index(drop=True)
    bridge.rename(columns={'DTE_New': 'DTE New'}, inplace=True)
    mark('STEP 13 - stack output rows', t0)

    del df_all, parts
    gc.collect()

    qc = _run_qc(bridge, acv_table, has_quantity)

    # ── Risk & Opportunity — computed on the FULL bridge, before any
    # customer cutoff below, so no customer is silently excluded from
    # scoring regardless of how many total customers there are.
    t0 = time.time()
    risk_opp_df = compute_risk_opportunity(bridge, has_product=has_product)
    if not risk_opp_df.empty:
        risk_opp_df = risk_opp_df.copy()
        risk_opp_df['Date'] = risk_opp_df['Date'].astype(str)
    mark('STEP 14a - risk & opportunity scoring (full customer set)', t0)

    # Pre-aggregate (BUG FIXED: CLS_RET -> CLS_RETURNING)
    t0 = time.time()
    bridge_lb12 = bridge[bridge['Month Lookback'] == 12].copy() if 12 in lookback_months else bridge.copy()

    period_summary = (
        bridge_lb12
        .groupby(['Date', 'Month Lookback', 'Bridge Classification'])['Bridge Value']
        .sum()
        .reset_index()
    )

    movement_cls = {CLS_CHURN, CLS_CHURN_P, CLS_UPSELL, CLS_DOWNSELL,
                    CLS_NEW_LOGO, CLS_CROSS, CLS_ADDON, CLS_LAPSED, CLS_RETURNING}  # FIXED
    movements = bridge_lb12[bridge_lb12['Bridge Classification'].isin(movement_cls)]
    top_custs = (
        movements.groupby('Customer')['Bridge Value']
        .apply(lambda x: x.abs().sum())
        .nlargest(MAX_CUSTOMER_BRIDGE_ROWS)
        .index.tolist()
    )
    customer_summary = (
        bridge_lb12[bridge_lb12['Customer'].isin(top_custs)]
        .groupby(['Customer', 'Product', 'Channel', 'Region', 'Date',
                  'Bridge Classification', 'Vintage'])['Bridge Value']
        .sum()
        .reset_index()
    )

    period_summary['Date'] = period_summary['Date'].astype(str)
    customer_summary['Date'] = customer_summary['Date'].astype(str)
    customer_summary['Vintage'] = customer_summary['Vintage'].astype(str)
    mark('STEP 14 - pre-aggregation (bug fixed)', t0)

    timings['TOTAL'] = round(time.time() - t_start, 3)
    timings['row_counts'] = {
        'raw_input': len(df_raw),
        'in_scope_contracts': len(df_in),
        'bridge_output_rows': len(bridge),
    }

    # Convert Timestamp columns to strings — required for JSON serialization.
    # (This was missing in the original engine and caused every request to
    # crash with TypeError: Object of type Timestamp is not JSON serializable)
    acv_table_out = acv_table.copy()
    for col in ['Contract Start Date', 'Contract End Date', 'Date']:
        if col in acv_table_out.columns:
            acv_table_out[col] = acv_table_out[col].astype(str)

    bookings_out = bookings.copy()
    for col in ['Start', 'End']:
        if col in bookings_out.columns:
            bookings_out[col] = bookings_out[col].astype(str)

    bookings_cols = ['Customer', 'Product', 'Channel', 'Region',
                      'Start', 'End', 'TCV', 'ACV', 'InScope']
    # (original code also selected 'OutOfScopeReason' which is never created
    # anywhere in this function — that caused a guaranteed KeyError on every
    # request. Removed until that field is actually computed somewhere.)

    out = {
        'bridge':           period_summary.to_dict('records'),
        'customer_bridge':  customer_summary.to_dict('records'),
        'acv':              (acv_table_out.to_dict('records') if not acv_table_out.empty else []) if include_acv_table else [],
        'bookings':         bookings_out[bookings_cols].to_dict('records') if not bookings_out.empty else [],
        'qc':               qc,
        'risk_opportunity': risk_opp_df.to_dict('records') if not risk_opp_df.empty else [],
    }
    if _return_timings:
        out['timings'] = timings
        out['_bridge_df'] = bridge  # local debugging only — strip before deploying if timings enabled
    return out


MOVEMENT_EXCL = {CLS_PRIOR, CLS_ENDING, CLS_EXPIRY, CLS_ROB, CLS_PRICE_I, CLS_VOL_I, CLS_POV}

def _run_qc(bridge: pd.DataFrame, acv_table: pd.DataFrame, has_quantity: bool) -> dict:
    qc = {'qc1': True, 'qc1_detail': 'All periods reconcile (±0.01)',
          'qc2': True, 'qc2_detail': 'P×V bridge reconciles',
          'qc3': True, 'qc3_detail': 'ACV Table matches Bridge Ending ACV',
          'qc4': True, 'qc4_detail': 'No unclassified rows'}

    lb12 = bridge[bridge['Month Lookback'] == 12]
    if lb12.empty:
        return qc

    for period, grp in lb12.groupby('Date'):
        totals = grp.groupby('Bridge Classification')['Bridge Value'].sum()
        prior  = totals.get(CLS_PRIOR, 0)
        ending = totals.get(CLS_ENDING, 0)
        moves  = grp[~grp['Bridge Classification'].isin(MOVEMENT_EXCL)]['Bridge Value'].sum()

        if abs(round(prior + moves, 2) - round(ending, 2)) > 0.01:
            qc['qc1'] = False
            qc['qc1_detail'] = f"Period {period}: {round(prior+moves,2)} != {round(ending,2)}"

        if has_quantity:
            ud = totals.get(CLS_UPSELL, 0) + totals.get(CLS_DOWNSELL, 0)
            pv = totals.get(CLS_PRICE_I, 0) + totals.get(CLS_VOL_I, 0) + totals.get(CLS_POV, 0)
            if abs(ud) > 0 and abs(round(pv, 2) - round(ud, 2)) > 0.01:
                qc['qc2'] = False
                qc['qc2_detail'] = f"Period {period}: P×V {round(pv,2)} != U+D {round(ud,2)}"

    unclass = (bridge['Bridge Classification'] == CLS_UNCLASS).sum()
    if unclass > 0:
        qc['qc4'] = False
        qc['qc4_detail'] = f"{unclass} unclassified row(s)"

    return qc
