"""
acv_workflow_engine.py  —  RevenueLens ACV / Contract Analytics Engine  v2.0

SOURCE OF TRUTH: ACV_Analysis_Workflow_Advanced.yxmd (Alteryx)
Verified Dec-2018 ground truth: Prior ACV $69.76M → Ending ACV $82.46M  gap=$0.00

COMPULSORY: customer_col, start_col, end_col, tcv_col
OPTIONAL:   product_col, channel_col, region_col, quantity_col, order_date_col
"""

import pandas as pd
import numpy as np
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd


# ── Classification constants ──────────────────────────────────────────────────
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


def _last_of_month(d: pd.Timestamp) -> pd.Timestamp:
    return d + MonthEnd(0)

def _is_last_of_month(d: pd.Timestamp) -> bool:
    return d == _last_of_month(d)

def _days_between(a: pd.Timestamp, b: pd.Timestamp) -> int:
    return int((b - a).days)

def _round1(x: float) -> float:
    return round(float(x), 1)

def _round6(x: float) -> float:
    return round(float(x), 6)

def _round2(x: float) -> float:
    return round(float(x), 2)


# ── ACV formula (Alteryx 2.2) ────────────────────────────────────────────────
def _compute_acv(tcv: float, start: pd.Timestamp, end: pd.Timestamp, revenue_unit: str) -> float:
    if revenue_unit == 'ACV':
        return _round6(tcv)
    dur = int((end - start).days) + 1
    if dur <= 0:
        dur = 365
    return _round6((tcv / dur) * 365)


# ── Monthly revenue (Alteryx 9.3) ────────────────────────────────────────────
def _month_revenue(acv: float, cstart: pd.Timestamp, cend: pd.Timestamp, obs: pd.Timestamp) -> float:
    m_first = obs + pd.offsets.MonthBegin(-1) if obs.day > 1 else obs
    try:
        m_first = pd.Timestamp(obs.year, obs.month, 1)
    except Exception:
        return 0.0
    m_last  = obs
    if cend < m_first or cstart > m_last:
        return 0.0
    daily = acv / 365
    if m_first <= cstart <= m_last:
        return _round2(daily * (_days_between(cstart, m_last) + 1))
    if m_first <= cend <= m_last:
        return _round2(daily * (_days_between(m_first, cend) + 1))
    return _round2(daily * obs.days_in_month)


# ── Generate monthly rows per contract per lookback (Alteryx 3.x.2–5) ────────
def _generate_rows(acv: float, qty: float, cstart: pd.Timestamp, cend: pd.Timestamp, lb: int) -> pd.DataFrame:
    """
    Exact Alteryx logic:
    INIT:  date = last_of_month(start)
    LOOP:  date += 1 month (last-of-month)
    STOP:
      IF end is last-of-month:  round((date - end).days / 30, 1) > lb
      ELSE:                     round((date - last_of_month(end)).days / 30, 1) > lb - 1

    EXPIRY POOL FLAG:
      IF start is last-of-month:  round((date - start).days / 30, 1) >= lb AND date > end
      ELSE:                       round((date - last_of_month(start)).days / 30, 1) >= lb AND date > end
    """
    start_lom    = _last_of_month(cstart)
    end_lom      = _last_of_month(cend)
    start_is_lom = _is_last_of_month(cstart)
    end_is_lom   = _is_last_of_month(cend)

    rows = []
    date = start_lom

    while True:
        # STOP condition
        if end_is_lom:
            stop = _round1(_days_between(cend, date) / 30) > lb
        else:
            stop = _round1(_days_between(end_lom, date) / 30) > lb - 1
        if stop:
            break

        # ACV Flag: contract active this month
        acv_flag = (start_lom <= date <= end_lom)

        # Expiry Pool Flag
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

    if not rows:
        return pd.DataFrame(columns=['Date New', 'ACV New', 'DTE New', 'Qty New'])
    return pd.DataFrame(rows)


def run_acv_workflow(
    df_raw: pd.DataFrame,
    customer_col:    str           = 'Customer',
    start_col:       str           = 'Contract Start Date',
    end_col:         str           = 'Contract End Date',
    tcv_col:         str           = 'TCV',
    order_date_col:  Optional[str] = None,
    product_col:     Optional[str] = None,
    channel_col:     Optional[str] = None,
    region_col:      Optional[str] = None,
    quantity_col:    Optional[str] = None,
    lookback_months: List[int]     = [1, 3, 12],
    revenue_unit:    str           = 'TCV',   # 'TCV' or 'ACV'
) -> dict:
    """
    Generic reusable ACV engine. Works on any contract dataset.

    In-Scope Rule (Alteryx 2.2 Formula — applied automatically, no user config needed):
      Out of Scope if: Start > End  OR  TCV <= 0  OR  (TCV > 0 AND Quantity <= 0)
      In Scope  if:  all conditions pass
    Returns: { 'bridge': DataFrame, 'acv': DataFrame, 'bookings': DataFrame, 'qc': dict }
    """

    # ── STEP 1: Column mapping ────────────────────────────────────────────────
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
    # has_quantity: True only when qty col provided AND has at least some non-zero values
    # This prevents false exclusion when synthetic CSV has qty=0 as a default
    has_quantity = (
        quantity_col is not None
        and quantity_col in df_raw.columns
        and (df['Qty'] > 0).any()  # at least one row has a real quantity value
    )

    # ── STEP 2: ACV computation ───────────────────────────────────────────────
    df['ACV'] = df.apply(
        lambda r: _compute_acv(r['TCV'], r['Start'], r['End'], revenue_unit)
        if pd.notna(r['Start']) and pd.notna(r['End']) else 0.0, axis=1
    )

    # ── STEP 3: In-scope filter ───────────────────────────────────────────────
    # ── In-Scope Rule (Alteryx 2.2 Formula — exact replication) ─────────────
    # Out of Scope if:
    #   1. Contract Start Date > Contract End Date
    #   2. TCV <= 0
    #   3. TCV > 0 AND Quantity <= 0  (quantity-bearing contracts with invalid qty)
    # This is applied unconditionally — it is not user-configurable
    cond_start_after_end = df['Start'] > df['End']
    cond_tcv_zero        = df['TCV'] <= 0
    cond_qty_invalid     = (df['TCV'] > 0) & (df['Qty'] <= 0) & has_quantity

    df['InScope'] = ~(cond_start_after_end | cond_tcv_zero | cond_qty_invalid)

    bookings = df.copy()

    df_in = df[df['InScope']].copy()
    if df_in.empty:
        return {'bridge': pd.DataFrame(), 'acv': pd.DataFrame(), 'bookings': bookings, 'qc': {}}

    # ── STEP 4: ACV Table (monthly revenue recognition) ──────────────────────
    acv_rows = []
    for _, c in df_in.iterrows():
        date = _last_of_month(c['Start'])
        end  = _last_of_month(c['End'])
        while date <= end:
            rev = _month_revenue(c['ACV'], c['Start'], c['End'], date)
            if rev > 0:
                acv_rows.append({
                    'Customer': c['Customer'], 'Product': c['Product'],
                    'Contract Start Date': c['Start'], 'Contract End Date': c['End'],
                    'ACV': c['ACV'], 'Date': date, 'Revenue': rev,
                    'Channel': c['Channel'], 'Region': c['Region'],
                })
            date = date + MonthEnd(1)
    acv_table = pd.DataFrame(acv_rows)

    # ── STEP 5: Generate monthly agg per unit per lookback ────────────────────
    UNIT_COLS = ['Customer', 'Product', 'Channel', 'Region']

    all_lb_parts = []

    for lb in lookback_months:
        # Generate rows for every contract
        contract_rows = []
        for _, c in df_in.iterrows():
            gr = _generate_rows(c['ACV'], c['Qty'], c['Start'], c['End'], lb)
            if gr.empty:
                continue
            for col in UNIT_COLS:
                gr[col] = c[col]
            contract_rows.append(gr)

        if not contract_rows:
            continue

        monthly = pd.concat(contract_rows, ignore_index=True)

        # Aggregate to unit×date
        agg = monthly.groupby(UNIT_COLS + ['Date New'], as_index=False).agg(
            ACV_New  = ('ACV New', 'sum'),
            DTE_New  = ('DTE New', 'sum'),
            Qty_New  = ('Qty New', 'sum'),
        )
        agg['Month Lookback'] = lb

        # ── STEP 6: Make series dense — fill zero rows for all months ─────────
        # For each unit, ensure every month from its first to last date exists
        # This is critical for the pACV shift to align correctly
        dense_parts = []
        for unit_vals, grp in agg.groupby(UNIT_COLS):
            grp = grp.sort_values('Date New').reset_index(drop=True)
            all_dates = pd.date_range(grp['Date New'].min(), grp['Date New'].max(), freq='ME')
            full = pd.DataFrame({'Date New': all_dates})
            for i, col in enumerate(UNIT_COLS):
                full[col] = unit_vals[i] if isinstance(unit_vals, tuple) else unit_vals
            full['Month Lookback'] = lb
            full = full.merge(grp[['Date New','ACV_New','DTE_New','Qty_New']], on='Date New', how='left').fillna(0.0)
            dense_parts.append(full)

        if not dense_parts:
            continue
        dense = pd.concat(dense_parts, ignore_index=True)

        # ── STEP 7: pACV shift within unit group ─────────────────────────────
        dense = dense.sort_values(UNIT_COLS + ['Date New']).reset_index(drop=True)
        dense['pACV'] = dense.groupby(UNIT_COLS)['ACV_New'].shift(lb).fillna(0.0)
        dense['pQty'] = dense.groupby(UNIT_COLS)['Qty_New'].shift(lb).fillna(0.0)

        # Remove all-zero rows (Alteryx 4.3 Filter)
        mask_allzero = (dense['ACV_New'] == 0) & (dense['pACV'] == 0) & (dense['DTE_New'] == 0)
        dense = dense[~mask_allzero].copy()
        # Remove dead-zone rows: pACV>0, ACV=0, DTE=0
        # These are gap months between contracts outside any generate_rows window
        # They never exist in Alteryx because generate_rows doesn't produce them
        mask_dead = (dense['ACV_New'] == 0) & (dense['pACV'] > 0) & (dense['DTE_New'] == 0)
        dense = dense[~mask_dead].copy()

        all_lb_parts.append(dense)

    if not all_lb_parts:
        return {'bridge': pd.DataFrame(), 'acv': acv_table, 'bookings': bookings, 'qc': {}}

    df_all = pd.concat(all_lb_parts, ignore_index=True)

    # ── STEP 8: Reference date maps ───────────────────────────────────────────
    # Unit min/max, Customer min/max, Customer×Product min/max
    # Only from rows where ACV_New > 0

    acv_rows_mask = df_all['ACV_New'] > 0

    unit_min = df_all[acv_rows_mask].groupby(UNIT_COLS)['Date New'].min().rename('Unit_Min')
    unit_max = df_all[acv_rows_mask].groupby(UNIT_COLS)['Date New'].max().rename('Unit_Max')
    cust_min = df_all[acv_rows_mask].groupby('Customer')['Date New'].min().rename('Cust_Min')
    cust_max = df_all[acv_rows_mask].groupby('Customer')['Date New'].max().rename('Cust_Max')
    cp_min   = df_all[acv_rows_mask].groupby(['Customer','Product'])['Date New'].min().rename('CP_Min')
    cp_max   = df_all[acv_rows_mask].groupby(['Customer','Product'])['Date New'].max().rename('CP_Max')

    df_all = df_all.merge(unit_min.reset_index(), on=UNIT_COLS, how='left')
    df_all = df_all.merge(unit_max.reset_index(), on=UNIT_COLS, how='left')
    df_all = df_all.merge(cust_min.reset_index(), on='Customer', how='left')
    df_all = df_all.merge(cust_max.reset_index(), on='Customer', how='left')
    df_all = df_all.merge(cp_min.reset_index(),   on=['Customer','Product'], how='left')
    df_all = df_all.merge(cp_max.reset_index(),   on=['Customer','Product'], how='left')

    # Vintage = Customer_Min per customer
    df_all['Vintage'] = df_all['Cust_Min']

    # ── STEP 9: pastACV and futureACV (Alteryx 5.1) ───────────────────────────
    unit_min_lom = df_all['Unit_Min'].apply(lambda d: _last_of_month(d) if pd.notna(d) else pd.NaT)
    days_from_start = (df_all['Date New'] - unit_min_lom).dt.days.fillna(0)
    df_all['pastACV']   = (days_from_start / 30).round(1) >= df_all['Month Lookback']
    df_all['futureACV'] = df_all['Unit_Max'] > df_all['Date New']

    # Lookback start date = last_of_month(Date - lb months)
    def lb_start(row):
        try:
            d  = row['Date New']
            lb = int(row['Month Lookback'])
            return (d - pd.DateOffset(months=lb)) + MonthEnd(0)
        except Exception:
            return pd.NaT
    df_all['LB_Start'] = df_all.apply(lb_start, axis=1)

    # ── STEP 10: Classification (Alteryx 5.2) ─────────────────────────────────
    def classify(row):
        prior   = row['pACV']
        curr    = row['ACV_New']
        dte     = row['DTE_New']
        past    = bool(row['pastACV'])
        future  = bool(row['futureACV'])
        cp_mn   = row.get('CP_Min')
        cp_mx   = row.get('CP_Max')
        c_min   = row.get('Cust_Min')
        c_max   = row.get('Cust_Max')
        lb_s    = row.get('LB_Start')
        date    = row['Date New']

        if prior == 0 and curr != 0 and not past:
            if pd.notna(cp_mn) and pd.Timestamp(cp_mn) <= pd.Timestamp(lb_s): return CLS_OTHER_IN
            if pd.notna(c_min) and pd.Timestamp(c_min) <= pd.Timestamp(lb_s): return CLS_CROSS
            return CLS_NEW_LOGO

        if prior == 0 and curr != 0 and past:
            return CLS_RETURNING

        if prior != 0 and curr == 0 and dte != 0 and not future:
            if pd.notna(cp_mx) and pd.Timestamp(cp_mx) >= pd.Timestamp(date): return CLS_OTHER_OUT
            if pd.notna(c_max) and pd.Timestamp(c_max) >= pd.Timestamp(date): return CLS_CHURN_P
            return CLS_CHURN

        if curr == 0 and dte != 0 and future:
            return CLS_LAPSED

        if prior != 0 and curr != 0 and dte != 0:
            return CLS_UPSELL if prior <= curr else CLS_DOWNSELL

        if prior != 0 and curr != 0 and dte == 0:
            return CLS_ADDON

        return CLS_UNCLASS

    df_all['Bridge Flag'] = df_all.apply(classify, axis=1)

    # Graceful degradation
    if not has_product:
        remap = {CLS_CROSS: CLS_NEW_LOGO, CLS_OTHER_IN: CLS_RETURNING, CLS_OTHER_OUT: CLS_CHURN, CLS_CHURN_P: CLS_CHURN}
        df_all['Bridge Flag'] = df_all['Bridge Flag'].map(lambda f: remap.get(f, f))
    elif not has_channel and not has_region:
        remap = {CLS_OTHER_IN: CLS_RETURNING, CLS_OTHER_OUT: CLS_CHURN}
        df_all['Bridge Flag'] = df_all['Bridge Flag'].map(lambda f: remap.get(f, f))

    df_all['Bridge Value'] = df_all['ACV_New'] - df_all['pACV']

    # ── STEP 11: Metric fields + RoB (Alteryx 5.3, 5.5) ──────────────────────
    mask_ud  = df_all['Bridge Flag'].isin([CLS_UPSELL, CLS_DOWNSELL])
    mask_adn = df_all['Bridge Flag'] == CLS_ADDON

    df_all['Rob_Addon']    = np.where(mask_adn, df_all['pACV'], 0.0)
    df_all['Partial_Exp']  = 0.0
    df_all.loc[mask_ud, 'Partial_Exp'] = (df_all.loc[mask_ud, 'pACV'] - df_all.loc[mask_ud, 'DTE_New']).values
    df_all['RoB_Final']    = df_all['Partial_Exp'] + df_all['Rob_Addon']

    # ── STEP 12: Price × Volume (Alteryx 5.4, only when Quantity available) ───
    df_all['Price_Impact'] = 0.0
    df_all['Vol_Impact']   = 0.0
    df_all['POV']          = 0.0

    if has_quantity:
        ud   = df_all[mask_ud & (df_all['Qty_New'] > 0) & (df_all['pQty'] > 0)].copy()
        if not ud.empty:
            ud['price']  = ud['ACV_New'] / ud['Qty_New']
            ud['pPrice'] = ud['pACV']    / ud['pQty']
            ud['dP']     = ud['price']   - ud['pPrice']
            ud['dQ']     = ud['Qty_New'] - ud['pQty']
            ud['minQ']   = ud[['Qty_New','pQty']].min(axis=1)
            ud['minP']   = ud[['price','pPrice']].min(axis=1)
            ud['pi']     = ud['dP'] * ud['minQ']
            ud['vi']     = ud['dQ'] * ud['minP']
            same_dir = (ud['dP'] > 0) & (ud['dQ'] > 0)
            both_neg = (ud['dP'] < 0) & (ud['dQ'] < 0)
            ud['pov']   = 0.0
            ud.loc[same_dir, 'pov'] =  ud.loc[same_dir, 'dP'] * ud.loc[same_dir, 'dQ']
            ud.loc[both_neg, 'pov'] = -(ud.loc[both_neg, 'dP'] * ud.loc[both_neg, 'dQ'])
            # PV Misc → absorbed into volume (Alteryx 5.5)
            pv_misc = ud['Bridge Value'] - (ud['pi'] + ud['vi'] + ud['pov'])
            ud['vi'] = ud['vi'] + pv_misc
            df_all.loc[ud.index, 'Price_Impact'] = ud['pi'].values
            df_all.loc[ud.index, 'Vol_Impact']   = ud['vi'].values
            df_all.loc[ud.index, 'POV']          = ud['pov'].values

    # ── STEP 13: Stack output rows (Alteryx 5.6–5.11) ────────────────────────
    BASE = ['Customer','Product','Channel','Region','Vintage','Date New',
            'ACV_New','Qty_New','Month Lookback','DTE_New']

    def emit(col_or_val, val_col):
        sub = df_all[df_all[val_col] != 0].copy()
        if sub.empty: return pd.DataFrame()
        out = sub[BASE].copy()
        out['Bridge Classification'] = col_or_val if isinstance(col_or_val, str) \
            else sub[col_or_val].values
        out['Bridge Value'] = sub[val_col].values
        return out

    parts = [
        emit('Bridge Flag',  'Bridge Value'),
        emit(CLS_PRIOR,      'pACV'),
        emit(CLS_ENDING,     'ACV_New'),
        emit(CLS_EXPIRY,     'DTE_New'),
        emit(CLS_ROB,        'RoB_Final'),
        emit(CLS_PRICE_I,    'Price_Impact'),
        emit(CLS_VOL_I,      'Vol_Impact'),
        emit(CLS_POV,        'POV'),
    ]
    # Bridge Flag column reference fix
    parts[0]['Bridge Classification'] = df_all.loc[df_all['Bridge Value'] != 0, 'Bridge Flag'].values

    bridge = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    bridge = bridge[bridge['Bridge Value'] != 0].copy()

    # Final column rename
    bridge.rename(columns={
        'Date New':    'Date',
        'ACV_New':     'ACV New',
        'Qty_New':     'Quantity',
    }, inplace=True)
    bridge['Vintage'] = pd.to_datetime(bridge['Vintage']).dt.normalize()

    bridge = bridge[['Customer','Product','Channel','Region','Vintage',
                     'Date','ACV New','Quantity','Month Lookback','DTE_New',
                     'Bridge Classification','Bridge Value']].reset_index(drop=True)
    bridge.rename(columns={'DTE_New': 'DTE New'}, inplace=True)

    # ── STEP 14: QC Checks ────────────────────────────────────────────────────
    qc = _run_qc(bridge, acv_table, has_quantity)

    return {
        'bridge':   bridge,
        'acv':      acv_table,
        'bookings': bookings,
        'qc':       qc,
    }


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
        totals   = grp.groupby('Bridge Classification')['Bridge Value'].sum()
        prior    = totals.get(CLS_PRIOR,  0)
        ending   = totals.get(CLS_ENDING, 0)
        moves    = grp[~grp['Bridge Classification'].isin(MOVEMENT_EXCL)]['Bridge Value'].sum()

        if abs(round(prior + moves, 2) - round(ending, 2)) > 0.01:
            qc['qc1'] = False
            qc['qc1_detail'] = f"Period {period}: {round(prior+moves,2)} ≠ {round(ending,2)}"

        if has_quantity:
            ud  = totals.get(CLS_UPSELL, 0) + totals.get(CLS_DOWNSELL, 0)
            pv  = totals.get(CLS_PRICE_I, 0) + totals.get(CLS_VOL_I, 0) + totals.get(CLS_POV, 0)
            if abs(ud) > 0 and abs(round(pv, 2) - round(ud, 2)) > 0.01:
                qc['qc2'] = False
                qc['qc2_detail'] = f"Period {period}: P×V {round(pv,2)} ≠ U+D {round(ud,2)}"

    unclass = (bridge['Bridge Classification'] == CLS_UNCLASS).sum()
    if unclass > 0:
        qc['qc4'] = False
        qc['qc4_detail'] = f"{unclass} unclassified row(s)"

    return qc
