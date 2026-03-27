"""
mrr_workflow_engine.py  —  V3  (Spec-Faithful)
================================================
Exact Python replication of MRR_Workflow_Advanced.yxmd Alteryx workflow.
Logic extracted from verified Alteryx XML + cross-checked against live output.

PIPELINE (matches Alteryx node order exactly):
  Step 1  Map & Normalize        — clean, snap to month-end, Revenue>0, aggregate to unit level
  Step 2  Date Spine             — dataset MIN→MAX, every month per unit, fill zeros
  Step 3  Multi-Lookback         — 3 parallel branches (1M/3M/12M), shift Prior, DTE, window filter
  Step 4  Min/Max Metadata       — CustProd_Min/Max, Customer_Min/Max, Vintage
  Step 5  Bridge Classification  — exact if/elseif order from Alteryx XML Node 936
  Step 6  Price/Volume Decomp    — Upsell/Downsell only, when Qty is real
  Step 7  Output Transformation  — CrossTab→Transpose, Beginning MRR, Ending MRR, filter zeros

OUTPUT — exactly 12 columns (Alteryx Node 944 SELECT):
  Customer | Product | Channel | Region | Vintage | Date |
  MRR or ARR | Quantity | Month Lookback | Lookback Date |
  Bridge Classification | Bridge Value

Bridge Classification values (EXACT from Alteryx, case-sensitive):
  New Logo, Churn, Churn Partial, Cross-sell, Upsell, Downsell,
  Lapsed, Returning, Other In, Other Out,
  Beginning MRR, Ending MRR,
  Price Impact, Volume Impact, Price on Volume

Granularity remapping (applied after classification):
  Customer only      → Cross-sell→New Logo, Churn Partial→Churn,
                        Other In→Returning, Other Out→Churn
  Customer × Product → Other In→Returning, Other Out→Churn
  Full               → no remapping
"""

import pandas as pd
import numpy as np
import warnings
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd

# ── Column sets ───────────────────────────────────────────────────────────
_UNIT_COLS = ['Customer', 'Product', 'Channel', 'Region']
_OUT_COLS  = [
    'Customer', 'Product', 'Channel', 'Region', 'Vintage',
    'Date', 'MRR or ARR', 'Quantity', 'Month Lookback', 'Lookback Date',
    'Bridge Classification', 'Bridge Value',
]

# ── Granularity remapping tables ──────────────────────────────────────────
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

# ── Movement classifications (for reconciliation check) ───────────────────
_MOVEMENTS = frozenset({
    'New Logo', 'Cross-sell', 'Other In', 'Returning',
    'Upsell', 'Downsell', 'Churn', 'Churn Partial', 'Other Out', 'Lapsed',
})


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
) -> pd.DataFrame:
    """
    Full MRR/ARR bridge engine — exact Alteryx replication.
    Returns 12-column bridge table. Empty DataFrame if no valid data.
    Bridge Classification and Month Lookback are ALWAYS system-computed.
    """
    # Granularity flags — determine which classifications are reachable
    has_product = bool(product_col and product_col in df_raw.columns)
    has_cr      = bool(
        (channel_col and channel_col in df_raw.columns) or
        (region_col  and region_col  in df_raw.columns)
    )

    # ── Step 1: Map & Normalize ───────────────────────────────────────
    actuals = _step1_normalize(
        df_raw, customer_col, date_col, revenue_col,
        product_col, channel_col, region_col, quantity_col,
        revenue_unit, has_product, has_cr
    )
    if actuals.empty:
        return pd.DataFrame()

    # ── Step 2: Date Spine ────────────────────────────────────────────
    spine = _step2_spine(actuals)
    if spine.empty:
        return pd.DataFrame()

    # ── Steps 3+4: Lookback branches + Metadata ───────────────────────
    state = _step3_lookbacks(spine, actuals, lookback_months)
    if state.empty:
        return pd.DataFrame()

    # ── Step 5: Classification ────────────────────────────────────────
    state = _step5_classify(state, actuals, has_product, has_cr)

    # ── Step 6: Price/Volume decomposition ───────────────────────────
    state = _step6_price_volume(state, df_raw, quantity_col)

    # ── Step 7: Output transformation ────────────────────────────────
    bridge = _step7_output(state)
    if bridge.empty:
        return pd.DataFrame()

    # ── Reconciliation check (non-blocking) ──────────────────────────
    _check_reconciliation(bridge)

    return bridge


# ══════════════════════════════════════════════════════════════════════════
# STEP 1 — MAP & NORMALIZE
# ══════════════════════════════════════════════════════════════════════════

def _step1_normalize(
    df_raw, customer_col, date_col, revenue_col,
    product_col, channel_col, region_col, quantity_col,
    revenue_unit, has_product, has_cr
) -> pd.DataFrame:
    """
    Spec Step 1:
    - Map raw columns to standard names
    - Normalize ALL dates to last day of month
    - Keep only MRR > 0 as in-scope
    - Aggregate duplicates at unit-date level (sum MRR, sum Qty)
    """
    df = pd.DataFrame()
    df['Customer'] = df_raw[customer_col].astype(str).str.strip()
    df['Product']  = (df_raw[product_col].astype(str).str.strip()
                      if has_product else 'N/A')
    df['Channel']  = (df_raw[channel_col].astype(str).str.strip()
                      if channel_col and channel_col in df_raw.columns else 'N/A')
    df['Region']   = (df_raw[region_col].astype(str).str.strip()
                      if region_col and region_col in df_raw.columns else 'N/A')

    # Normalize dates → last day of month
    df['Date']     = pd.to_datetime(df_raw[date_col], errors='coerce') + MonthEnd(0)
    df['Revenue']  = pd.to_numeric(df_raw[revenue_col], errors='coerce').fillna(0)
    df['Quantity'] = (
        pd.to_numeric(df_raw[quantity_col], errors='coerce').fillna(1)
        if quantity_col and quantity_col in df_raw.columns else 1.0
    )

    # Drop rows with unparseable dates
    df = df.dropna(subset=['Date'])

    # Revenue unit conversion
    if revenue_unit == 'millions':
        df['Revenue'] *= 1e-6
    elif revenue_unit == 'thousands':
        df['Revenue'] *= 1e-3

    # In-scope filter: Revenue > 0 only
    df = df[df['Revenue'] > 0].copy()
    if df.empty:
        return df

    # Aggregate to unit-date level (sum duplicates)
    return df.groupby(_UNIT_COLS + ['Date'], as_index=False).agg(
        Revenue=('Revenue', 'sum'),
        Quantity=('Quantity', 'sum'),
    )


# ══════════════════════════════════════════════════════════════════════════
# STEP 2 — DATE SPINE  (Spec: dataset MIN → dataset MAX)
# ══════════════════════════════════════════════════════════════════════════

def _step2_spine(actuals: pd.DataFrame) -> pd.DataFrame:
    """
    Spec Step 2:
    - Find dataset MIN and MAX dates (global across all units)
    - For each unique unit, generate one row per month from MIN to MAX
    - Left-join original data onto spine
    - Missing months: Revenue=0, Quantity=0

    NOTE: Spine uses DATASET min→max (not per-unit min→max).
    This matches Alteryx Node 823 behavior exactly.
    """
    dataset_min = actuals['Date'].min()
    dataset_max = actuals['Date'].max()

    # Unit lifecycle (needed later for metadata; also used for DTE)
    unit_lc = actuals.groupby(_UNIT_COLS, as_index=False).agg(
        Unit_Min=('Date', 'min'),
        Unit_Max=('Date', 'max'),
    )

    # Build month-integer range: dataset_min to dataset_max
    start_m = dataset_min.year * 12 + dataset_min.month
    end_m   = dataset_max.year * 12 + dataset_max.month

    # Cross-join: every unit × every month in dataset range
    records = []
    for _, r in unit_lc.iterrows():
        for m in range(start_m, end_m + 1):
            records.append((
                r['Customer'], r['Product'], r['Channel'], r['Region'],
                m, r['Unit_Min'], r['Unit_Max']
            ))
    if not records:
        return pd.DataFrame()

    spine = pd.DataFrame(
        records,
        columns=_UNIT_COLS + ['month_int', 'Unit_Min', 'Unit_Max']
    )

    # Convert month_int → last-of-month Timestamp
    yr = (spine['month_int'] - 1) // 12
    mo = (spine['month_int'] - 1) % 12 + 1
    spine['Date'] = pd.to_datetime(
        pd.DataFrame({'year': yr, 'month': mo, 'day': 1})
    ) + MonthEnd(0)
    spine.drop(columns='month_int', inplace=True)

    # Left-join actuals → Revenue=0 / Quantity=0 for missing months
    merged = spine.merge(
        actuals[_UNIT_COLS + ['Date', 'Revenue', 'Quantity']],
        on=_UNIT_COLS + ['Date'], how='left'
    )
    merged['Revenue']  = merged['Revenue'].fillna(0)
    merged['Quantity'] = merged['Quantity'].fillna(0)

    return merged


# ══════════════════════════════════════════════════════════════════════════
# STEP 3 — MULTI-LOOKBACK + STEP 4 — METADATA
# ══════════════════════════════════════════════════════════════════════════

def _step3_lookbacks(
    spine:           pd.DataFrame,
    actuals:         pd.DataFrame,
    lookback_months: List[int],
) -> pd.DataFrame:
    """
    Spec Step 3 (per lookback branch):
    1. Find each unit's max date with actual Revenue > 0
    2. Window filter: keep rows where months_between(Date, unit_Max_Date) <= lookback
    3. Expiry Pool Flag = 1 if Date > unit_Max_Date, else 0
    4. Prior Revenue = shift(Revenue, N) within unit group sorted by date
    5. Prior Quantity = shift(Qty, N)
    6. DTE = Prior Revenue if Expiry Pool Flag=1, else 0
    7. Union all branches
    8. Remove rows where Revenue=0 AND Prior Revenue=0 AND DTE=0

    Spec Step 4 (metadata, joined per branch):
    - CustProd_Min, CustProd_Max (grouped by unit — same as unit_lc)
    - Customer_Min, Customer_Max (grouped by Customer only)
    - Vintage = Customer_Min
    """
    # ── Step 4 metadata — computed from ACTUALS only ──────────────────
    # CustProd lifecycle = unit lifecycle
    cp_lc = actuals.groupby(_UNIT_COLS, as_index=False).agg(
        CustProd_Min=('Date', 'min'),
        CustProd_Max=('Date', 'max'),
    )
    # Customer lifecycle
    cust_lc = actuals.groupby('Customer', as_index=False).agg(
        Customer_Min=('Date', 'min'),
        Customer_Max=('Date', 'max'),
    )
    cust_lc['Vintage'] = cust_lc['Customer_Min']

    # Attach metadata to spine
    spine = spine.merge(cp_lc,   on=_UNIT_COLS, how='left')
    spine = spine.merge(
        cust_lc[['Customer', 'Customer_Min', 'Customer_Max', 'Vintage']],
        on='Customer', how='left'
    )

    # Sort for shift()
    spine = spine.sort_values(_UNIT_COLS + ['Date']).reset_index(drop=True)

    # ── Three parallel branches ───────────────────────────────────────
    all_frames = []
    for lb in lookback_months:

        t = spine.copy()
        t['Month Lookback'] = lb

        # ── 3.2 Window filter: keep only rows within lb months of unit's last actual date
        #    Uses integer month arithmetic (matching Alteryx DateDiffMonth exactly)
        #    NOT days/30 which gives 3.1 for exact 3-month gaps and incorrectly filters them
        months_to_max = (
            (t['Unit_Max'].dt.year  - t['Date'].dt.year)  * 12 +
            (t['Unit_Max'].dt.month - t['Date'].dt.month)
        )
        t = t[months_to_max <= lb].copy()
        if t.empty:
            continue

        # ── 3.3 Expiry Pool Flag: 1 if Date > unit_Max_Date
        t['Expiry Pool Flag'] = np.where(t['Date'] > t['Unit_Max'], 1, 0)

        # ── 3.4/3.5 Prior Revenue and Prior Quantity via shift(lb) within unit
        t['Prior Revenue']  = t.groupby(_UNIT_COLS)['Revenue'].shift(lb).fillna(0)
        t['Prior Quantity'] = t.groupby(_UNIT_COLS)['Quantity'].shift(lb).fillna(0)

        # ── 3.6 DTE = Prior Revenue if Expiry Pool, else 0
        t['DTE'] = np.where(t['Expiry Pool Flag'] == 1, t['Prior Revenue'], 0)

        # ── Lookback Date = last_of_month(Date - lb months)
        t['Lookback Date'] = (
            t['Date'].dt.to_period('M') - lb
        ).dt.to_timestamp('M') + MonthEnd(0)

        # ── 3.8 Remove no-signal rows
        t = t[~(
            (t['Revenue'] == 0) &
            (t['Prior Revenue'] == 0) &
            (t['DTE'] == 0)
        )].copy()

        if not t.empty:
            all_frames.append(t)

    if not all_frames:
        return pd.DataFrame()

    return pd.concat(all_frames, ignore_index=True)


# ══════════════════════════════════════════════════════════════════════════
# STEP 5 — BRIDGE CLASSIFICATION  (exact Alteryx Node 936 if/elseif order)
# ══════════════════════════════════════════════════════════════════════════

def _step5_classify(
    state:       pd.DataFrame,
    actuals:     pd.DataFrame,
    has_product: bool,
    has_cr:      bool,
) -> pd.DataFrame:
    """
    Spec Step 5 — exact classification order:

    Helper flags:
      pastMRR  = 'Yes' if months_between(Date, CustProd_Min) >= Month_Lookback
      futureMRR = 'Yes' if CustProd_Max > Date
      lookback_start = last_of_month(Date - lb months)  [= Lookback Date]

    CASE 1: Prior=0, Current>0, pastMRR='No'
       IF CustProd_Min <= lookback_start   → 'Other In'
       ELIF Customer_Min <= lookback_start → 'Cross-sell'
       ELSE                                → 'New Logo'

    CASE 2: Prior=0, Current>0, pastMRR='Yes'  → 'Returning'

    CASE 3: Prior>0, Current=0, DTE>0, futureMRR='No'
       IF CustProd_Max >= Date    → 'Other Out'
       ELIF Customer_Max >= Date  → 'Churn Partial'
       ELSE                       → 'Churn'

    CASE 4: Current=0, futureMRR='Yes'  → 'Lapsed'

    CASE 5: Prior>0, Current>0
       IF Prior <= Current → 'Upsell'
       ELSE                → 'Downsell'
    """
    state = state.copy()

    pr      = state['Prior Revenue']
    cur     = state['Revenue']
    date    = state['Date']
    lb_date = state['Lookback Date']         # = lookback_start
    cm      = state['Customer_Min']
    cx      = state['Customer_Max']
    cp_min  = state['CustProd_Min']
    cp_max  = state['CustProd_Max']
    lb      = state['Month Lookback']

    # ── Helper flags ──────────────────────────────────────────────────
    # pastMRR: unit existed >= lb months before current date
    days_from_cp_min = (date - cp_min).dt.days
    past_mrr = np.round(days_from_cp_min / 30, 1) >= lb   # True = 'Yes'
    past_no  = ~past_mrr
    past_yes = past_mrr

    # futureMRR: unit has actual revenue after current date
    future_mrr = cp_max > date

    # ── Conditions (strict spec order) ───────────────────────────────
    entry  = (pr == 0) & (cur  > 0)
    exit3  = (pr  > 0) & (cur == 0) & (state['DTE'] > 0) & ~future_mrr
    change = (pr  > 0) & (cur  > 0)

    # CASE 1 sub-conditions (pastMRR='No')
    c1_other_in   = entry & past_no & (cp_min <= lb_date)
    c1_cross_sell = entry & past_no & (cp_min >  lb_date) & (cm <= lb_date)
    c1_new_logo   = entry & past_no & (cp_min >  lb_date) & (cm >  lb_date)

    # CASE 2
    c2_returning  = entry & past_yes

    # CASE 3 sub-conditions
    c3_other_out      = exit3 & (cp_max >= date)
    c3_churn_partial  = exit3 & (cp_max <  date) & (cx >= date)
    c3_churn          = exit3 & (cp_max <  date) & (cx <  date)

    # CASE 4
    c4_lapsed = (cur == 0) & future_mrr

    # CASE 5
    c5_upsell   = change & (cur >= pr)   # spec: Prior <= Current → Upsell
    c5_downsell = change & (cur <  pr)

    # np.select — first match wins (order follows spec cases 1→5)
    conditions = [
        c1_other_in, c1_cross_sell, c1_new_logo,
        c2_returning,
        c3_other_out, c3_churn_partial, c3_churn,
        c4_lapsed,
        c5_upsell, c5_downsell,
    ]
    choices = [
        'Other In', 'Cross-sell', 'New Logo',
        'Returning',
        'Other Out', 'Churn Partial', 'Churn',
        'Lapsed',
        'Upsell', 'Downsell',
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
# STEP 6 — PRICE/VOLUME DECOMPOSITION
# ══════════════════════════════════════════════════════════════════════════

def _step6_price_volume(
    state:        pd.DataFrame,
    df_raw:       pd.DataFrame,
    quantity_col: Optional[str],
) -> pd.DataFrame:
    """
    Spec Step 6 — applied to Upsell/Downsell rows only, when Qty data is real.

    Price         = MRR / Qty
    pPrice        = Prior MRR / Prior Qty
    Price Impact  = (Price − pPrice) × min(Qty, Prior Qty)
    Volume Impact = (Qty − Prior Qty) × min(Price, pPrice)
    Price on Volume:
      Both up or both down → −(Price−pPrice) × (Qty−Prior Qty)
      Mixed direction       → 0
    PV Misc = Bridge Value − (PI + VI + PoV)
    Final Volume Impact += PV Misc
    """
    has_real_qty = bool(quantity_col and quantity_col in df_raw.columns)

    for col in ['Price Impact', 'Volume Impact', 'Price on Volume']:
        state[col] = 0.0

    mask_ud = state['Bridge Flag'].isin(['Upsell', 'Downsell'])
    if not has_real_qty or not mask_ud.any():
        return state

    idx = state.index[mask_ud]
    q   = state.loc[idx, 'Quantity'].replace(0, np.nan)
    pq  = state.loc[idx, 'Prior Quantity'].replace(0, np.nan)
    rev = state.loc[idx, 'Revenue']
    pre = state.loc[idx, 'Prior Revenue']

    p   = (rev / q).fillna(0);  q  = q.fillna(0)
    pp  = (pre / pq).fillna(0); pq = pq.fillna(0)

    pi  = (p - pp) * np.minimum(q, pq)
    vi  = (q - pq) * np.minimum(p, pp)

    same_dir = ((p > pp) & (q > pq)) | ((p < pp) & (q < pq))
    pov = np.where(same_dir, -((p - pp) * (q - pq)), 0)

    pv_misc = (rev - pre) - pi - vi - pov
    vi_final = vi + pv_misc

    state.loc[idx, 'Price Impact']    = pi.values
    state.loc[idx, 'Volume Impact']   = vi_final.values
    state.loc[idx, 'Price on Volume'] = pov

    return state


# ══════════════════════════════════════════════════════════════════════════
# STEP 7 — OUTPUT TRANSFORMATION
# ══════════════════════════════════════════════════════════════════════════

def _step7_output(state: pd.DataFrame) -> pd.DataFrame:
    """
    Spec Step 7 — CrossTab → Transpose equivalent:
    1. For each classified row, emit bridge flag row (Classification, Bridge Value)
    2. ALSO emit: Beginning MRR (= Prior Revenue), Ending MRR (= Revenue)
    3. ALSO emit: Price Impact, Volume Impact, Price on Volume (if non-zero)
    4. Filter: remove any row where Bridge Value = 0 or null

    Returns final 12-column bridge table.
    """
    base = _UNIT_COLS + ['Vintage', 'Date', 'Revenue', 'Quantity',
                          'Month Lookback', 'Lookback Date']

    def _rows(src: pd.DataFrame, cls: str, val_col: str) -> pd.DataFrame:
        sub = src[src[val_col] != 0][base].copy()
        if sub.empty:
            return pd.DataFrame()
        sub['Bridge Classification'] = cls
        sub['Bridge Value']          = src.loc[sub.index, val_col].values
        return sub[base + ['Bridge Classification', 'Bridge Value']]

    # Movement rows (Bridge Flag, Bridge Value)
    non_zero = state[state['Bridge Value'] != 0].copy()
    if non_zero.empty:
        mov = pd.DataFrame()
    else:
        mov = non_zero[base].copy()
        mov['Bridge Classification'] = non_zero['Bridge Flag'].values
        mov['Bridge Value']          = non_zero['Bridge Value'].values

    parts = [
        mov,
        _rows(state, 'Beginning MRR', 'Prior Revenue'),
        _rows(state, 'Ending MRR',    'Revenue'),
    ]

    # Price/Volume rows (only non-zero)
    mask_ud = state['Bridge Flag'].isin(['Upsell', 'Downsell'])
    if mask_ud.any():
        ud = state[mask_ud]
        for cls, col in [
            ('Price Impact',    'Price Impact'),
            ('Volume Impact',   'Volume Impact'),
            ('Price on Volume', 'Price on Volume'),
        ]:
            parts.append(_rows(ud, cls, col))

    bridge = pd.concat([p for p in parts if p is not None and not p.empty],
                       ignore_index=True)
    bridge = bridge[bridge['Bridge Value'] != 0].copy()

    if bridge.empty:
        return pd.DataFrame()

    # Final rename → exact 12-column output
    bridge.rename(columns={'Revenue': 'MRR or ARR'}, inplace=True)
    bridge['Vintage'] = pd.to_datetime(bridge['Vintage']).dt.year.astype(str)

    return bridge[_OUT_COLS].reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════
# RECONCILIATION CHECK  (non-blocking)
# ══════════════════════════════════════════════════════════════════════════

def _check_reconciliation(bridge: pd.DataFrame, tol: float = 0.01) -> None:
    """
    Validates: Beginning MRR + sum(movements) == Ending MRR
    per (Date, Month Lookback). Issues a warning on mismatch.
    Wire to your observability layer in production.
    """
    if bridge.empty:
        return

    pivot = (
        bridge.groupby(['Date', 'Month Lookback', 'Bridge Classification'])['Bridge Value']
        .sum().unstack(fill_value=0)
    )

    beg = next((c for c in ['Beginning MRR', 'Beginning ARR'] if c in pivot.columns), None)
    end = next((c for c in ['Ending MRR',    'Ending ARR']    if c in pivot.columns), None)
    if not beg or not end:
        return

    mov_cols = [c for c in pivot.columns if c in _MOVEMENTS]
    diff = (pivot[beg] + pivot[mov_cols].sum(axis=1) - pivot[end]).abs()
    bad  = diff[diff > tol]
    if not bad.empty:
        warnings.warn(
            f"[MRR Engine V3] Reconciliation mismatch in {len(bad)} period(s). "
            f"Max discrepancy: {diff.max():.4f}. "
            f"Periods: {bad.index.tolist()[:5]}",
            stacklevel=3,
        )
