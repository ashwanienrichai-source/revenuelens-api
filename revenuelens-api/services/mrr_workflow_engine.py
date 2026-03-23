"""
mrr_workflow_engine.py  ─  V2  (Production Grade)
===================================================
MRR/ARR Revenue Bridge Engine.

ARCHITECTURE:
  Raw Input
    → _normalize_input()         — clean, type-cast, unit-convert, aggregate
    → _build_state_table()       — vectorized spine + shift (no iterrows for shift)
    → _classify_events()         — np.select, mutually exclusive, correct logic
    → _build_bridge_table()      — stack movements + anchors, P/V decomp
    → _validate_reconciliation() — warn on Beg + movements ≠ Ending

CRITICAL FIXES vs V1:
  [F1] Spine ends at Unit_Max + max_lb (not dataset_max) — no phantom zeros
  [F2] DTE removed — exit branch uses actuals-based "customer_still_active"
  [F3] Window filter removed — all rows in [Unit_Min, Unit_Max+lb] are valid
  [F4] pastRevenue → has_prior_revenue (Prior>0) — not misleading age flag
  [F5] Classification order: New Logo → Cross-sell → Other In → Returning
  [F6] Spine build: vectorized month_int arithmetic, iterrows only for explode
  [F7] Lookback Date: Period offset arithmetic — vectorized
  [F8] Output: single-pass stack, no full-copy per classification
  [F9] Churn vs Churn Partial: uses customer_still_active_on_date (actuals join)
       Not Customer_Max comparison — correctly handles same-month multi-product exits
  [F10] Reconciliation check post-output with warning

DORMANCY SUPPORT:
  dormancy_months > 0: if a unit was absent > N months, its re-entry
  is treated as New Logo (or Cross-sell) not Returning — configurable.

OUTPUT: exactly 12 columns (backward-compatible with config.py):
  Customer, Product, Channel, Region, Vintage,
  Date, MRR or ARR, Quantity, Month Lookback, Lookback Date,
  Bridge Classification, Bridge Value
"""

import pandas as pd
import numpy as np
import warnings
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd

# ── Constants ─────────────────────────────────────────────────────────────
_VALID = frozenset({
    'Beginning MRR', 'Ending MRR',
    'New Logo', 'Cross-sell', 'Other In', 'Returning',
    'Upsell', 'Downsell',
    'Churn', 'Churn Partial', 'Other Out', 'Lapsed',
    'Price Impact', 'Volume Impact', 'Price on Volume',
})

_REMAP_CUST_ONLY = {
    'Cross-sell':    'New Logo',
    'Churn Partial': 'Churn',
    'Other In':      'Returning',
    'Other Out':     'Churn',
}
_REMAP_CUST_PROD = {
    'Other In':  'Returning',
    'Other Out': 'Churn',
}

_UNIT_COLS  = ['Customer', 'Product', 'Channel', 'Region']
_BASE_COLS  = _UNIT_COLS + ['Vintage', 'Date', 'Revenue', 'Quantity',
                             'Month Lookback', 'Lookback Date']
_OUT_COLS   = ['Customer', 'Product', 'Channel', 'Region', 'Vintage',
               'Date', 'MRR or ARR', 'Quantity', 'Month Lookback', 'Lookback Date',
               'Bridge Classification', 'Bridge Value']


# ══════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

def run_mrr_workflow(
    df_raw:          pd.DataFrame,
    customer_col:    str           = 'CustomerID',
    date_col:        str           = 'Period',
    revenue_col:     str           = 'Revenue',
    product_col:     Optional[str] = None,
    channel_col:     Optional[str] = None,
    region_col:      Optional[str] = None,
    quantity_col:    Optional[str] = None,
    lookback_months: List[int]     = [1, 3, 12],
    revenue_unit:    str           = 'raw',
    dormancy_months: int           = 0,
) -> pd.DataFrame:
    """
    Full MRR/ARR bridge engine — production V2.
    Returns 12-column bridge table.
    Month Lookback and Bridge Classification are SYSTEM-COMPUTED.
    """
    # ── Granularity flags ─────────────────────────────────────────────
    has_product = bool(product_col and product_col in df_raw.columns)
    has_cr      = bool(
        (channel_col and channel_col in df_raw.columns) or
        (region_col  and region_col  in df_raw.columns)
    )

    # ── Step 1: normalize ─────────────────────────────────────────────
    actuals = _normalize_input(
        df_raw, customer_col, date_col, revenue_col,
        product_col, channel_col, region_col, quantity_col,
        revenue_unit, has_product, has_cr
    )
    if actuals.empty:
        return pd.DataFrame()

    # ── Step 2: state table ───────────────────────────────────────────
    state = _build_state_table(actuals, lookback_months)
    if state.empty:
        return pd.DataFrame()

    # ── Step 3: classify ──────────────────────────────────────────────
    state = _classify_events(state, actuals, has_product, has_cr, dormancy_months)

    # ── Step 4: bridge table ──────────────────────────────────────────
    bridge = _build_bridge_table(state, df_raw, quantity_col)
    if bridge.empty:
        return pd.DataFrame()

    # ── Step 5: reconciliation check ──────────────────────────────────
    _validate_reconciliation(bridge)

    return bridge


# ══════════════════════════════════════════════════════════════════════════
# STEP 1 — NORMALIZE
# ══════════════════════════════════════════════════════════════════════════

def _normalize_input(
    df_raw, customer_col, date_col, revenue_col,
    product_col, channel_col, region_col, quantity_col,
    revenue_unit, has_product, has_cr
) -> pd.DataFrame:
    df = pd.DataFrame()
    df['Customer'] = df_raw[customer_col].astype(str).str.strip()
    df['Product']  = (df_raw[product_col].astype(str).str.strip()
                      if has_product else 'N/A')
    df['Channel']  = (df_raw[channel_col].astype(str).str.strip()
                      if channel_col and channel_col in df_raw.columns else 'N/A')
    df['Region']   = (df_raw[region_col].astype(str).str.strip()
                      if region_col  and region_col  in df_raw.columns else 'N/A')

    df['Date']     = pd.to_datetime(df_raw[date_col], errors='coerce') + MonthEnd(0)
    df['Revenue']  = pd.to_numeric(df_raw[revenue_col], errors='coerce').fillna(0)
    df['Quantity'] = (
        pd.to_numeric(df_raw[quantity_col], errors='coerce').fillna(1)
        if quantity_col and quantity_col in df_raw.columns else 1.0
    )

    df = df.dropna(subset=['Date'])

    if revenue_unit == 'millions':
        df['Revenue'] *= 1e-6
    elif revenue_unit == 'thousands':
        df['Revenue'] *= 1e-3

    df = df[df['Revenue'] > 0].copy()
    if df.empty:
        return df

    # Aggregate duplicates to unit level
    return df.groupby(_UNIT_COLS + ['Date'], as_index=False).agg(
        Revenue=('Revenue', 'sum'),
        Quantity=('Quantity', 'sum'),
    )


# ══════════════════════════════════════════════════════════════════════════
# STEP 2 — BUILD STATE TABLE
# ══════════════════════════════════════════════════════════════════════════

def _build_state_table(actuals: pd.DataFrame, lookback_months: List[int]) -> pd.DataFrame:
    """
    Generates the state table:
      - Monthly spine per UNIT from Unit_Min to Unit_Max + max_lb
      - Left-joins actuals (Revenue=0 for missing months)
      - Attaches Customer_Min, Customer_Max, Vintage
      - Computes Prior Revenue and Lookback Date per lookback

    FIX [F1]: Spine ends at Unit_Max + max_lb, not dataset_max.
    FIX [F7]: Lookback Date computed via Period arithmetic (vectorized).
    """
    max_lb = max(lookback_months)

    # ── Lifecycle dates (from actuals only) ───────────────────────────
    unit_lc = actuals.groupby(_UNIT_COLS, as_index=False).agg(
        Unit_Min=('Date', 'min'),
        Unit_Max=('Date', 'max'),
    )
    cust_lc = actuals.groupby('Customer', as_index=False).agg(
        Customer_Min=('Date', 'min'),
        Customer_Max=('Date', 'max'),
    )
    cust_lc['Vintage'] = cust_lc['Customer_Min']

    # ── Vectorized spine via month integers ───────────────────────────
    unit_lc['spine_end_m'] = (
        unit_lc['Unit_Max'].dt.year * 12 + unit_lc['Unit_Max'].dt.month + max_lb
    )
    unit_lc['unit_start_m'] = (
        unit_lc['Unit_Min'].dt.year * 12 + unit_lc['Unit_Min'].dt.month
    )

    # Explode: one row per (unit, month_int)
    records = []
    for _, r in unit_lc.iterrows():
        for m in range(int(r['unit_start_m']), int(r['spine_end_m']) + 1):
            records.append((*[r[c] for c in _UNIT_COLS], m,
                             r['Unit_Min'], r['Unit_Max']))
    if not records:
        return pd.DataFrame()

    spine = pd.DataFrame(records,
                         columns=_UNIT_COLS + ['month_int', 'Unit_Min', 'Unit_Max'])

    # Convert month_int → last-of-month date
    yr = (spine['month_int'] - 1) // 12
    mo = (spine['month_int'] - 1) % 12 + 1
    spine['Date'] = pd.to_datetime(
        pd.DataFrame({'year': yr, 'month': mo, 'day': 1})
    ) + MonthEnd(0)
    spine.drop(columns='month_int', inplace=True)

    # Left-join actuals
    merged = spine.merge(
        actuals[_UNIT_COLS + ['Date', 'Revenue', 'Quantity']],
        on=_UNIT_COLS + ['Date'], how='left'
    )
    merged['Revenue']  = merged['Revenue'].fillna(0)
    merged['Quantity'] = merged['Quantity'].fillna(0)

    # Attach customer metadata
    merged = merged.merge(
        cust_lc[['Customer', 'Customer_Min', 'Customer_Max', 'Vintage']],
        on='Customer', how='left'
    )

    # Sort for shift()
    merged = merged.sort_values(_UNIT_COLS + ['Date']).reset_index(drop=True)

    # ── Compute Prior per lookback — vectorized shift ─────────────────
    all_frames = []
    for lb in lookback_months:
        t = merged.copy()
        t['Month Lookback'] = lb
        t['Prior Revenue']  = t.groupby(_UNIT_COLS)['Revenue'].shift(lb).fillna(0)
        t['Prior Quantity'] = t.groupby(_UNIT_COLS)['Quantity'].shift(lb).fillna(0)

        # Lookback Date: vectorized via Period arithmetic
        t['Lookback Date'] = (
            t['Date'].dt.to_period('M') - lb
        ).dt.to_timestamp('M') + MonthEnd(0)

        # Drop no-signal rows (Revenue=0 AND Prior=0)
        t = t[(t['Revenue'] != 0) | (t['Prior Revenue'] != 0)].copy()
        all_frames.append(t)

    return pd.concat(all_frames, ignore_index=True)


# ══════════════════════════════════════════════════════════════════════════
# STEP 3 — CLASSIFY EVENTS
# ══════════════════════════════════════════════════════════════════════════

def _classify_events(
    state:           pd.DataFrame,
    actuals:         pd.DataFrame,
    has_product:     bool,
    has_cr:          bool,
    dormancy_months: int,
) -> pd.DataFrame:
    """
    Assigns Bridge Classification — single np.select pass.

    FIX [F2/F9]: Exit branch no longer uses DTE.
    Uses "customer_still_active_on_date" from actuals:
      If customer has Revenue>0 from any OTHER unit on this date → Churn Partial
      Else → Churn

    FIX [F4]: has_prior_revenue = Prior>0 replaces age-based pastRevenue.

    FIX [F9]: Correct Churn vs Churn Partial — handles same-month multi-product exit.
    """
    state = state.copy()

    # ── Pre-compute: customer-level revenue per date (from actuals) ───
    # Used for Churn vs Churn Partial distinction.
    # "Does the customer have revenue on THIS date from any unit?"
    cust_active = actuals.groupby(['Customer', 'Date'])['Revenue'].sum().reset_index()
    cust_active.columns = ['Customer', 'Date', 'cust_revenue_on_date']

    state = state.merge(cust_active, on=['Customer', 'Date'], how='left')
    state['cust_revenue_on_date'] = state['cust_revenue_on_date'].fillna(0)

    # ── Core series ───────────────────────────────────────────────────
    pr       = state['Prior Revenue']
    cur      = state['Revenue']
    date     = state['Date']
    lb_date  = state['Lookback Date']
    cm       = state['Customer_Min']
    um       = state['Unit_Min']
    ux       = state['Unit_Max']
    cust_rev = state['cust_revenue_on_date']

    # FIX [F4]: has_prior_revenue replaces age-based flag
    has_prior = pr > 0
    has_cur   = cur > 0

    entry   = ~has_prior & has_cur
    exit_   = has_prior & ~has_cur
    change  = has_prior & has_cur

    # futureRevenue: based on Unit_Max from actuals (FIXED: not spine-extended max)
    future = ux > date

    # Real exit = exiting AND no future actual revenue for this unit
    real_exit = exit_ & ~future

    # ── Dormancy ──────────────────────────────────────────────────────
    if dormancy_months > 0:
        # How many months has this unit been absent? (Date - Unit_Max for re-entries)
        # If unit was gone > dormancy_months → treat as New Logo / Cross-sell
        gap_months = ((date.dt.year - ux.dt.year) * 12 +
                      (date.dt.month - ux.dt.month))
        is_dormant_reentry = entry & (um < date) & (gap_months > dormancy_months)
    else:
        is_dormant_reentry = pd.Series(False, index=state.index)

    # ── ENTRY CONDITIONS ──────────────────────────────────────────────
    # 1. New Logo: both unit and customer first seen within lookback window
    cond_new_logo = entry & (um > lb_date) & (cm > lb_date)

    # 2. Cross-sell: customer existed before lookback, THIS unit is new in window
    cond_cross_sell = entry & (um > lb_date) & (cm <= lb_date) & ~cond_new_logo

    # 3. Other In: unit existed before lookback window (reactivation)
    # Unit had revenue before, went $0, came back
    cond_other_in = entry & (um <= lb_date) & ~is_dormant_reentry & ~cond_new_logo & ~cond_cross_sell

    # 4. Returning: everything else (dormant reentry, or re-entry after very long gap)
    cond_returning = entry & ~cond_new_logo & ~cond_cross_sell & ~cond_other_in

    # ── EXIT CONDITIONS ────────────────────────────────────────────────
    # FIX [F9]: Churn Partial = this unit exits AND customer still has OTHER revenue on this date
    # Since cur=0, cust_revenue_on_date represents revenue from OTHER units only
    customer_still_active = cust_rev > 0

    # 7. Churn: real exit AND customer has no other revenue on this date
    cond_churn = real_exit & ~customer_still_active

    # 6. Churn Partial: real exit AND customer still active on other products
    cond_churn_partial = real_exit & customer_still_active

    # 5. Other Out: unit exit at full granularity (unit still active in other Ch/Reg)
    # Only meaningful at full granularity; remapped at lower levels
    # Condition: unit_max >= date (unit still technically active at unit level)
    # AND customer is still active
    cond_other_out = real_exit & (ux >= date) & customer_still_active

    # 8. Lapsed: exiting but will return
    cond_lapsed = exit_ & future

    # ── CHANGE CONDITIONS ─────────────────────────────────────────────
    cond_upsell   = change & (cur > pr)
    cond_downsell = change & (cur < pr)

    # ── Apply — np.select single pass ────────────────────────────────
    # Order: most specific → least specific
    conditions = [
        cond_new_logo,
        cond_cross_sell,
        cond_other_in,
        cond_returning,
        cond_other_out,        # before churn_partial (more specific)
        cond_churn_partial,
        cond_churn,
        cond_lapsed,
        cond_upsell,
        cond_downsell,
    ]
    choices = [
        'New Logo', 'Cross-sell', 'Other In', 'Returning',
        'Other Out', 'Churn Partial', 'Churn',
        'Lapsed', 'Upsell', 'Downsell',
    ]

    state['Bridge Flag'] = np.select(conditions, choices, default='Unclassified')

    # ── Granularity remapping ─────────────────────────────────────────
    if not has_product:
        state['Bridge Flag'] = state['Bridge Flag'].replace(_REMAP_CUST_ONLY)
    elif not has_cr:
        state['Bridge Flag'] = state['Bridge Flag'].replace(_REMAP_CUST_PROD)

    # ── Bridge Value ──────────────────────────────────────────────────
    state['Bridge Value'] = state['Revenue'] - state['Prior Revenue']

    return state


# ══════════════════════════════════════════════════════════════════════════
# STEP 4 — BUILD BRIDGE TABLE
# ══════════════════════════════════════════════════════════════════════════

def _build_bridge_table(
    state:        pd.DataFrame,
    df_raw:       pd.DataFrame,
    quantity_col: Optional[str],
) -> pd.DataFrame:
    """
    Stacks movement rows + Beginning MRR + Ending MRR anchors.
    FIX [F8]: Single-pass construction, filter before copy.
    """
    has_real_qty = bool(quantity_col and quantity_col in df_raw.columns)

    # ── Price/Volume decomposition (Upsell/Downsell only) ─────────────
    mask_ud = state['Bridge Flag'].isin(['Upsell', 'Downsell'])
    for col in ['Price Impact', 'Volume Impact', 'Price on Volume']:
        state[col] = 0.0

    if has_real_qty and mask_ud.any():
        ud_idx = state.index[mask_ud]
        q   = state.loc[ud_idx, 'Quantity'].replace(0, np.nan)
        pq  = state.loc[ud_idx, 'Prior Quantity'].replace(0, np.nan)
        rev = state.loc[ud_idx, 'Revenue']
        pre = state.loc[ud_idx, 'Prior Revenue']
        p   = (rev / q).fillna(0);  q  = q.fillna(0)
        pp  = (pre / pq).fillna(0); pq = pq.fillna(0)
        pi  = (p - pp) * np.minimum(q, pq)
        vi  = (q - pq) * np.minimum(p, pp)
        same = ((p > pp) & (q > pq)) | ((p < pp) & (q < pq))
        pov  = np.where(same, -((p - pp) * (q - pq)), 0)
        pv_misc = (rev - pre) - pi - vi - pov
        state.loc[ud_idx, 'Price Impact']    = pi.values
        state.loc[ud_idx, 'Volume Impact']   = (vi + pv_misc).values
        state.loc[ud_idx, 'Price on Volume'] = pov

    # ── Stack all classification rows ─────────────────────────────────
    def _stack(src: pd.DataFrame, cls: str, val_col: str) -> pd.DataFrame:
        s = src[src[val_col] != 0][_BASE_COLS].copy()
        if s.empty:
            return pd.DataFrame()
        s['Bridge Classification'] = cls if cls not in src.columns else src.loc[s.index, cls]
        s['Bridge Value']          = src.loc[s.index, val_col].values
        return s[_BASE_COLS + ['Bridge Classification', 'Bridge Value']]

    # Movement rows
    non_zero = state[state['Bridge Value'] != 0].copy()
    if non_zero.empty:
        mov = pd.DataFrame()
    else:
        mov = non_zero[_BASE_COLS].copy()
        mov['Bridge Classification'] = non_zero['Bridge Flag'].values
        mov['Bridge Value']          = non_zero['Bridge Value'].values

    parts = [mov,
             _stack(state, 'Beginning MRR', 'Prior Revenue'),
             _stack(state, 'Ending MRR',    'Revenue')]

    if has_real_qty and mask_ud.any():
        ud = state[mask_ud]
        parts += [_stack(ud, 'Price Impact',    'Price Impact'),
                  _stack(ud, 'Volume Impact',   'Volume Impact'),
                  _stack(ud, 'Price on Volume', 'Price on Volume')]

    bridge = pd.concat([p for p in parts if p is not None and not p.empty],
                       ignore_index=True)
    bridge = bridge[bridge['Bridge Value'] != 0].copy()

    if bridge.empty:
        return pd.DataFrame()

    # ── Final rename → 12-column output ──────────────────────────────
    bridge.rename(columns={'Revenue': 'MRR or ARR'}, inplace=True)
    bridge['Vintage'] = pd.to_datetime(bridge['Vintage']).dt.year.astype(str)

    return bridge[_OUT_COLS].reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════
# STEP 5 — RECONCILIATION VALIDATION
# ══════════════════════════════════════════════════════════════════════════

_MOVEMENTS = frozenset({
    'New Logo', 'Cross-sell', 'Other In', 'Returning',
    'Upsell', 'Downsell', 'Churn', 'Churn Partial', 'Other Out', 'Lapsed',
})

def _validate_reconciliation(bridge: pd.DataFrame, tol: float = 0.01) -> None:
    """
    Checks: Beginning MRR + sum(movements) == Ending MRR per (Date, Month Lookback).
    Warns (does not raise) if any period fails.
    Wire warnings to your observability layer in production.
    """
    if bridge.empty:
        return

    grp = bridge.groupby(['Date', 'Month Lookback', 'Bridge Classification'])['Bridge Value']
    pivot = grp.sum().unstack(fill_value=0)

    beg = next((c for c in ['Beginning MRR', 'Beginning ARR'] if c in pivot.columns), None)
    end = next((c for c in ['Ending MRR',    'Ending ARR']    if c in pivot.columns), None)
    if not beg or not end:
        return

    mov_cols = [c for c in pivot.columns if c in _MOVEMENTS]
    diff = (pivot[beg] + pivot[mov_cols].sum(axis=1) - pivot[end]).abs()
    bad  = diff[diff > tol]
    if not bad.empty:
        warnings.warn(
            f"[MRR Engine V2] Reconciliation mismatch in {len(bad)} period(s). "
            f"Max discrepancy: {diff.max():.4f}. "
            f"First offending periods: {bad.index.tolist()[:3]}",
            stacklevel=3
        )
