"""
bridge_pivot_service.py

Exact Python replication of the PwC config.py + sql_queries.py + export_functions.py logic.

PIPELINE (mirrors config.py exactly):
  1. Load CSV with standard column renames (done in load_bridge_file)
  2. Create bridge_df2 (customer-level, long format)
  3. Create bridge_df3 (customer-level, WIDE/pivot — one col per classification)
  4. Compute customer counts using exact SQL flag conditions
  5. Build bridge pivot (Classification rows × Period columns)
  6. Build customer count pivot (same structure)
  7. Build KPI matrix (all retention metrics)
  8. Build top movers
  9. Build top customers
  10. Build product bundling (penetration %)

Expected input columns (after load_bridge_file renames):
  Customer_ID, Activity_Date, Month_Lookback, Classification, amount
  + dimension cols: Product, Channel, Region, Vintage, etc.
"""

import pandas as pd
import numpy as np
from typing import List, Optional, Dict, Any

# ── Classification order (matches Excel/config.py exactly) ────────────
MRR_BRIDGE_ORDER = [
    'Beginning MRR', '', 'Churn', 'Churn Partial', 'Downsell', 'Upsell',
    'Other Out', 'Other In', 'Cross-sell', 'New Logo', 'Lapsed', 'Returning', 'Ending MRR',
]
ACV_BRIDGE_ORDER = [
    'Prior ACV', '', 'Expiry Pool', 'Churn', 'Churn Partial', 'Downsell', 'Upsell',
    'Cross-sell', 'New Logo', 'Lapsed', 'Returning', 'Add on', 'RoB', 'Ending ACV',
]

BEGINNING_CLASSES = {'Beginning MRR', 'Beginning ARR', 'Prior ACV'}
ENDING_CLASSES    = {'Ending MRR', 'Ending ARR', 'Ending ACV'}
POSITIVE_CLASSES  = {'New Logo', 'Upsell', 'Cross-sell', 'Returning', 'Other In', 'Add on', 'RoB'}
NEGATIVE_CLASSES  = {'Churn', 'Churn Partial', 'Churn-Partial', 'Churn partial', 'Lapsed', 'Downsell', 'Other Out', 'Expiry Pool'}
CHURN_CLASSES     = {'Churn', 'Churn Partial', 'Churn-Partial', 'Churn partial'}


def _make_bridge_df3(df2: pd.DataFrame, dim: List[str]) -> pd.DataFrame:
    """
    Replicates config.py bridge_df3 creation.
    Pivots customer-level bridge_df2 so each classification becomes a column.
    This is the core data structure for all customer count SQL queries.
    """
    idx_cols = ['Customer_ID', 'Activity_Date', 'Month_Lookback'] + [d for d in dim if d in df2.columns]
    df3 = pd.pivot_table(
        df2, values='amount',
        index=idx_cols,
        columns='Classification', aggfunc='sum'
    ).reset_index()
    df3.fillna(0, inplace=True)

    # Ensure standard columns exist even if no data
    for col in ['Churn Partial', 'Cross-sell', 'Lapsed', 'Returning', 'Other In', 'Other Out']:
        if col not in df3.columns:
            df3[col] = 0

    return df3


def _get_beginning_col(df3: pd.DataFrame) -> Optional[str]:
    for c in ['Beginning MRR', 'Beginning ARR', 'Prior ACV']:
        if c in df3.columns:
            return c
    return None


def _get_ending_col(df3: pd.DataFrame) -> Optional[str]:
    for c in ['Ending MRR', 'Ending ARR', 'Ending ACV']:
        if c in df3.columns:
            return c
    return None


def _compute_customer_counts(df3: pd.DataFrame, dim_filter: List[str] = []) -> pd.DataFrame:
    """
    Replicates ALL SQL queries from sql_queries.py exactly.
    Returns DataFrame with columns: Activity_Date, Month_Lookback, + one per CC category.

    SQL logic:
      Beginning Customers: customers where Beginning MRR != 0 (flag = any Beginning MRR != 0)
      Customer Churn:      Churn != 0 AND Ending MRR == 0
      Customer New Logo:   New Logo != 0 AND Beginning MRR == 0
      Customer Cross-sell: Cross-sell != 0 AND Beginning MRR != 0 (has prior relationship, new product)
      Customer Upsell:     Upsell != 0
      Customer Downsell:   Downsell != 0
      Customer Churn Partial: Churn Partial != 0
      Customer Lapsed:     Lapsed != 0
      Customer Returning:  Returning != 0
      Ending Customers:    Ending MRR != 0
    """
    beg_col = _get_beginning_col(df3)
    end_col = _get_ending_col(df3)
    if not beg_col or not end_col:
        return pd.DataFrame()

    group_cols = ['Activity_Date', 'Month_Lookback']
    df = df3.copy()

    # Flag: does this customer have any Beginning MRR in this period?
    flag_beg = df.groupby(['Customer_ID', 'Activity_Date', 'Month_Lookback'])[beg_col].sum().reset_index()
    flag_beg.columns = ['Customer_ID', 'Activity_Date', 'Month_Lookback', 'flag_beg']
    df = df.merge(flag_beg, on=['Customer_ID', 'Activity_Date', 'Month_Lookback'], how='left')

    flag_end = df.groupby(['Customer_ID', 'Activity_Date', 'Month_Lookback'])[end_col].sum().reset_index()
    flag_end.columns = ['Customer_ID', 'Activity_Date', 'Month_Lookback', 'flag_end']
    df = df.merge(flag_end, on=['Customer_ID', 'Activity_Date', 'Month_Lookback'], how='left')

    results = {}

    # Beginning Customers: beg != 0
    beg_mask = (df[beg_col] != 0) & (df['flag_beg'] != 0)
    results['Beginning Customers'] = df[beg_mask].groupby(group_cols)['Customer_ID'].nunique()

    # Customer Churn: Churn != 0 AND flag_end == 0
    if 'Churn' in df.columns:
        ch_mask = (df['Churn'] != 0) & (df['flag_end'] == 0)
        results['Customer Churn'] = df[ch_mask].groupby(group_cols)['Customer_ID'].nunique() * -1

    # Customer New Logo: New Logo != 0 AND flag_beg == 0
    if 'New Logo' in df.columns:
        nl_mask = (df['New Logo'] != 0) & (df['flag_beg'] == 0)
        results['Customer New Logo'] = df[nl_mask].groupby(group_cols)['Customer_ID'].nunique()

    # Customer Cross-sell: Cross-sell != 0 AND flag_beg != 0 (existing customer, new product)
    if 'Cross-sell' in df.columns:
        cs_mask = (df['Cross-sell'] != 0) & (df['flag_beg'] != 0)
        results['Customer Cross-sell'] = df[cs_mask].groupby(group_cols)['Customer_ID'].nunique()

    # Customer Upsell
    if 'Upsell' in df.columns:
        results['Customer Upsell'] = df[df['Upsell'] != 0].groupby(group_cols)['Customer_ID'].nunique()

    # Customer Downsell
    if 'Downsell' in df.columns:
        results['Customer Downsell'] = df[df['Downsell'] != 0].groupby(group_cols)['Customer_ID'].nunique() * -1

    # Customer Churn Partial
    if 'Churn Partial' in df.columns:
        results['Customer Churn Partial'] = df[df['Churn Partial'] != 0].groupby(group_cols)['Customer_ID'].nunique() * -1

    # Customer Lapsed
    if 'Lapsed' in df.columns:
        results['Customer Lapsed'] = df[df['Lapsed'] != 0].groupby(group_cols)['Customer_ID'].nunique() * -1

    # Customer Returning
    if 'Returning' in df.columns:
        results['Customer Returning'] = df[df['Returning'] != 0].groupby(group_cols)['Customer_ID'].nunique()

    # Ending Customers: end != 0
    end_mask = (df[end_col] != 0) & (df['flag_end'] != 0)
    results['Ending Customers'] = df[end_mask].groupby(group_cols)['Customer_ID'].nunique()

    # Combine into one DataFrame
    all_periods = df[group_cols].drop_duplicates()
    cc_df = all_periods.copy()
    for label, series in results.items():
        series = series.reset_index()
        series.columns = ['Activity_Date', 'Month_Lookback', label]
        cc_df = cc_df.merge(series, on=group_cols, how='left')
        cc_df[label] = cc_df[label].fillna(0)

    return cc_df


def _get_period_label(dt: pd.Timestamp, period_type: str) -> str:
    """Format period label exactly as config.py: Mar-21, FY2022, LTM Mar-24"""
    if period_type == 'Quarter':
        return dt.strftime('%b-%y')
    else:
        if dt.month == 12:
            return f"FY{dt.year}"
        else:
            return f"LTM {dt.strftime('%b-%y')}"


def _filter_periods(df: pd.DataFrame, lookback: int, period_type: str) -> pd.DataFrame:
    """Filter to display periods exactly as config.py bridge_df2 filter."""
    if period_type == 'Quarter':
        mask = (df['Month_Lookback'] == lookback) & (df['Activity_Date'].dt.month % 3 == 0)
    else:
        max_date = df['Activity_Date'].max()
        mask = (
            (df['Month_Lookback'] == lookback) &
            ((df['Activity_Date'].dt.month == 12) |
             (df['Activity_Date'] == max_date))
        )
    sub = df[mask].copy()
    sub['_period'] = sub['Activity_Date'].apply(lambda d: _get_period_label(d, period_type))
    # Sort periods by actual date
    period_order = sub.sort_values('Activity_Date')[['_period', 'Activity_Date']].drop_duplicates()
    return sub, period_order['_period'].tolist()


def build_bridge_pivot(
    df: pd.DataFrame,
    dim: List[str] = [],
    lookback: int = 12,
    period_type: str = 'Annual',
    dimension_filter: Optional[str] = None,
    dimension_value: Optional[str] = None,
) -> Dict:
    """
    Build bridge pivot: Classification (rows) × Period (columns).
    Exactly matches the Excel ARR Waterfall pivot output.
    """
    sub = df[df['Month_Lookback'] == lookback].copy()

    if dimension_filter and dimension_value and dimension_filter in sub.columns:
        sub = sub[sub[dimension_filter] == dimension_value]

    if sub.empty:
        return {'periods': [], 'rows': [], 'retention': {}}

    # Get periods
    if period_type == 'Quarter':
        sub2 = sub[sub['Activity_Date'].dt.month % 3 == 0].copy()
    else:
        max_date = sub['Activity_Date'].max()
        sub2 = sub[(sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)].copy()

    if sub2.empty:
        sub2 = sub.copy()

    sub2['_period'] = sub2['Activity_Date'].apply(lambda d: _get_period_label(d, period_type))
    period_order = sub2.sort_values('Activity_Date')[['_period', 'Activity_Date']].drop_duplicates()
    periods = period_order['_period'].tolist()

    # Pivot: sum amount by Classification × period
    pivot = sub2.groupby(['_period', 'Classification'])['amount'].sum().unstack(fill_value=0)

    # Detect tool type
    beg_col = next((c for c in BEGINNING_CLASSES if c in pivot.columns), None)
    end_col = next((c for c in ENDING_CLASSES if c in pivot.columns), None)

    # Sort classifications by config.py order
    order = MRR_BRIDGE_ORDER if beg_col == 'Beginning MRR' else ACV_BRIDGE_ORDER
    def sort_key(c):
        try: return order.index(c)
        except ValueError: return 50
    all_classes = sorted([c for c in pivot.columns if c in order or c not in ['', None]], key=sort_key)

    rows = []
    for cls in all_classes:
        if cls not in pivot.columns:
            continue
        values = {}
        for p in periods:
            values[p] = float(pivot.loc[p, cls]) if p in pivot.index else 0.0

        rows.append({
            'classification': cls,
            'values': values,
            'is_beginning': cls in BEGINNING_CLASSES,
            'is_ending':    cls in ENDING_CLASSES,
            'is_positive':  cls in POSITIVE_CLASSES,
            'is_negative':  cls in NEGATIVE_CLASSES,
        })

    # Compute % of Beginning
    beg_row = next((r for r in rows if r['classification'] in BEGINNING_CLASSES), None)
    if beg_row:
        for r in rows:
            pct = {}
            for p in periods:
                bv = beg_row['values'].get(p, 0)
                v  = r['values'].get(p, 0)
                pct[p] = round(v / bv * 100, 1) if bv != 0 else None
            r['pct_of_beginning'] = pct

    # Retention metrics per period
    def g(col_set, p):
        cols = [c for c in col_set if c in pivot.columns]
        if not cols or p not in pivot.index: return 0.0
        return float(pivot.loc[p, cols].sum())

    def safe(n, d):
        return round(n / d * 100, 1) if d and d != 0 else None

    retention = {}
    for p in periods:
        beg = g(BEGINNING_CLASSES, p)
        end = g(ENDING_CLASSES, p)
        ch  = g(CHURN_CLASSES, p)
        dw  = g({'Downsell'}, p)
        up  = g({'Upsell'}, p)
        cs  = g({'Cross-sell'}, p)
        nl  = g({'New Logo'}, p)
        ret = g({'Returning'}, p)
        lap = g({'Lapsed'}, p)

        retention[p] = {
            'beginning':           beg,
            'ending':              end,
            'churn_only_grr':      safe(beg + ch, beg),
            'grr':                 safe(beg + ch + dw, beg),
            'net_excl_lapsed':     safe(beg + ch + dw + up + cs, beg),
            'nrr':                 safe(beg + ch + dw + up + cs + ret + lap, beg),
            'churn':               ch,
            'downsell':            dw,
            'upsell':              up,
            'cross_sell':          cs,
            'new_logo':            nl,
        }

    return {'periods': periods, 'rows': rows, 'retention': retention}


def build_customer_count_pivot(
    df3: pd.DataFrame,
    lookback: int = 12,
    period_type: str = 'Annual',
) -> Dict:
    """
    Build customer count pivot using exact SQL flag logic from sql_queries.py.
    """
    sub = df3[df3['Month_Lookback'] == lookback].copy()
    if sub.empty:
        return {'periods': [], 'rows': [], 'logo_retention': {}}

    if period_type == 'Quarter':
        sub2 = sub[sub['Activity_Date'].dt.month % 3 == 0].copy()
    else:
        max_date = sub['Activity_Date'].max()
        sub2 = sub[(sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)].copy()

    if sub2.empty:
        sub2 = sub.copy()

    sub2['_period'] = sub2['Activity_Date'].apply(lambda d: _get_period_label(d, period_type))
    period_order = sub2.sort_values('Activity_Date')[['_period', 'Activity_Date']].drop_duplicates()
    periods = period_order['_period'].tolist()

    # Compute customer counts using SQL-equivalent flag logic
    cc_df = _compute_customer_counts(sub2)
    if cc_df.empty:
        return {'periods': periods, 'rows': [], 'logo_retention': {}}

    cc_df['_period'] = cc_df['Activity_Date'].apply(lambda d: _get_period_label(d, period_type))

    # CC row order (matches config.py cc_bridge_labels)
    CC_ORDER = [
        'Beginning Customers', 'Customer Churn', 'Customer Churn Partial',
        'Customer Cross-sell', 'Customer New Logo', 'Customer Lapsed',
        'Customer Returning', 'Customer Upsell', 'Customer Downsell',
        'Ending Customers',
    ]

    rows = []
    cc_cols = [c for c in CC_ORDER if c in cc_df.columns]
    for col in cc_cols:
        values = {}
        for p in periods:
            row = cc_df[cc_df['_period'] == p]
            values[p] = int(row[col].sum()) if not row.empty else 0
        rows.append({
            'classification': col,
            'values': values,
            'is_beginning': col == 'Beginning Customers',
            'is_ending':    col == 'Ending Customers',
        })

    # Logo retention
    logo_retention = {}
    for p in periods:
        beg_vals = [r['values'].get(p, 0) for r in rows if r['classification'] == 'Beginning Customers']
        ch_vals  = [abs(r['values'].get(p, 0)) for r in rows if r['classification'] == 'Customer Churn']
        end_vals = [r['values'].get(p, 0) for r in rows if r['classification'] == 'Ending Customers']
        beg = beg_vals[0] if beg_vals else 0
        ch  = ch_vals[0]  if ch_vals  else 0
        end = end_vals[0] if end_vals else 0
        logo_retention[p] = {
            'beginning': beg,
            'ending':    end,
            'logo_retention': round((beg - ch) / beg * 100, 1) if beg > 0 else None,
        }

    return {'periods': periods, 'rows': rows, 'logo_retention': logo_retention}


def build_kpi_table(
    df: pd.DataFrame,
    df3: pd.DataFrame,
    lookback: int = 12,
    period_type: str = 'Annual',
) -> List[Dict]:
    """
    Build full KPI matrix matching config.py kpi_list output.
    Includes: ARR, Customers, ARR per Customer, GRR, NRR, Logo Retention,
              Churn per Customer, New Logo per Customer, Ending per Customer.
    """
    sub  = df[df['Month_Lookback'] == lookback].copy()
    sub3 = df3[df3['Month_Lookback'] == lookback].copy() if df3 is not None else pd.DataFrame()

    if sub.empty:
        return []

    if period_type == 'Quarter':
        sub = sub[sub['Activity_Date'].dt.month % 3 == 0].copy()
        if not sub3.empty:
            sub3 = sub3[sub3['Activity_Date'].dt.month % 3 == 0].copy()
    else:
        max_date = sub['Activity_Date'].max()
        sub = sub[(sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)].copy()
        if not sub3.empty:
            sub3 = sub3[(sub3['Activity_Date'].dt.month == 12) | (sub3['Activity_Date'] == max_date)].copy()

    if sub.empty:
        return []

    sub['_period']  = sub['Activity_Date'].apply(lambda d: _get_period_label(d, period_type))
    if not sub3.empty:
        sub3['_period'] = sub3['Activity_Date'].apply(lambda d: _get_period_label(d, period_type))

    period_order = sub.sort_values('Activity_Date')[['_period', 'Activity_Date']].drop_duplicates()
    periods = period_order['_period'].tolist()

    pivot_val = sub.groupby(['_period', 'Classification'])['amount'].sum().unstack(fill_value=0)

    def gv(col_set, p):
        cols = [c for c in col_set if c in pivot_val.columns]
        if not cols or p not in pivot_val.index: return 0.0
        return float(pivot_val.loc[p, cols].sum())

    def safe(n, d):
        return round(n / d * 100, 1) if d and d != 0 else None

    # Customer counts from df3
    cc_df = pd.DataFrame()
    if not sub3.empty:
        cc_df = _compute_customer_counts(sub3)
        if not cc_df.empty:
            cc_df['_period'] = cc_df['Activity_Date'].apply(lambda d: _get_period_label(d, period_type))

    def gcc(col, p):
        if cc_df.empty or col not in cc_df.columns: return 0
        row = cc_df[cc_df['_period'] == p]
        return int(abs(row[col].sum())) if not row.empty else 0

    rows = []
    for p in periods:
        beg = gv(BEGINNING_CLASSES, p)
        end = gv(ENDING_CLASSES, p)
        ch  = gv(CHURN_CLASSES, p)
        dw  = gv({'Downsell'}, p)
        up  = gv({'Upsell'}, p)
        cs  = gv({'Cross-sell'}, p)
        nl  = gv({'New Logo'}, p)
        ret = gv({'Returning'}, p)
        lap = gv({'Lapsed'}, p)
        oth_in  = gv({'Other In'}, p)
        oth_out = gv({'Other Out'}, p)

        beg_c = gcc('Beginning Customers', p)
        end_c = gcc('Ending Customers', p)
        ch_c  = gcc('Customer Churn', p)
        nl_c  = gcc('Customer New Logo', p)
        ch_p_c = gcc('Customer Churn Partial', p)

        grr_churn_only = safe(beg + ch, beg)
        grr            = safe(beg + ch + dw, beg)
        nrr_excl       = safe(beg + ch + dw + up + cs, beg)
        nrr            = safe(beg + ch + dw + up + cs + ret + lap, beg)
        logo_ret       = safe(beg_c - ch_c, beg_c)

        rows.append({
            'period':                p,
            'beginning_arr':         beg,
            'ending_arr':            end,
            'new_logo':              nl,
            'upsell':                up,
            'cross_sell':            cs,
            'returning':             ret,
            'downsell':              dw,
            'churn':                 ch,
            'churn_partial':         gv({'Churn Partial'}, p),
            'lapsed':                lap,
            'other_in':              oth_in,
            'other_out':             oth_out,
            # Retention rates
            'gross_retention_churn_only': grr_churn_only,
            'gross_retention':       grr,
            'net_retention_excl_lapsed': nrr_excl,
            'net_retention':         nrr,
            # Customer counts
            'cust_beginning':        beg_c,
            'cust_ending':           end_c,
            'cust_churn':            ch_c,
            'cust_new_logo':         nl_c,
            'cust_churn_partial':    ch_p_c,
            # Logo retention
            'logo_retention':        logo_ret,
            # Per customer metrics (in $000s if revenue in thousands)
            'new_logo_per_cust':     round(nl / nl_c, 1) if nl_c > 0 else None,
            'churn_per_cust':        round(abs(ch) / ch_c, 1) if ch_c > 0 else None,
            'ending_per_cust':       round(end / end_c, 1) if end_c > 0 else None,
        })

    return rows


def build_top_movers(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    dim: List[str] = [],
    lookback: int = 12,
    n: int = 30,
    period_type: str = 'Annual',
    year_filter: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """
    Build top movers per classification — matches config.py top_movers_sheets logic.
    Returns dict: {classification: [list of top N movers with customer + dims + period + value]}
    """
    sub = df[df['Month_Lookback'] == lookback].copy()
    if year_filter and year_filter != 'All':
        sub = sub[sub['Activity_Date'].dt.year.astype(str) == str(year_filter)]

    if period_type == 'Quarter':
        sub = sub[sub['Activity_Date'].dt.month % 3 == 0].copy()
    else:
        max_date = sub['Activity_Date'].max()
        sub = sub[(sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)].copy()

    sub['_period'] = sub['Activity_Date'].apply(lambda d: _get_period_label(d, period_type))

    movers_config = {
        'Churn':         ('negative', []),
        'New Logo':      ('positive', []),
        'Upsell':        ('positive', []),
        'Downsell':      ('negative', []),
        'Churn Partial': ('negative', dim[:1] if dim else []),
        'Cross-sell':    ('positive', dim[:1] if dim else []),
        'Lapsed':        ('negative', dim[:1] if dim else []),
        'Returning':     ('positive', dim[:1] if dim else []),
    }

    result = {}
    for cls, (direction, extra_dims) in movers_config.items():
        cls_data = sub[sub['Classification'] == cls].copy()
        if cls_data.empty:
            continue

        group_cols = [customer_col, '_period'] + [d for d in extra_dims if d in cls_data.columns]
        agg = cls_data.groupby(group_cols)['amount'].sum().reset_index()
        agg.columns = group_cols + ['value']

        # Sort: for negative, most negative first; for positive, largest first
        agg = agg.sort_values('value', ascending=(direction == 'negative'))
        top = agg.head(n)

        movers = []
        for _, row in top.iterrows():
            m = {customer_col: str(row[customer_col]), 'period': str(row['_period']), 'value': float(row['value'])}
            for d in extra_dims:
                if d in row:
                    m[d] = str(row[d])
            movers.append(m)

        result[cls] = movers

    return result


def build_top_customers(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    dim: List[str] = [],
    lookback: int = 12,
    n: int = 10,
    period_type: str = 'Annual',
    year_filter: Optional[str] = None,
) -> List[Dict]:
    """
    Build top N customers by ending ARR — matches config.py top_cust_sheets logic.
    """
    sub = df[df['Month_Lookback'] == lookback].copy()
    end_classes = list(ENDING_CLASSES)
    sub = sub[sub['Classification'].isin(end_classes)].copy()

    if year_filter and year_filter != 'All':
        sub = sub[sub['Activity_Date'].dt.year.astype(str) == str(year_filter)]

    if period_type == 'Quarter':
        sub = sub[sub['Activity_Date'].dt.month % 3 == 0]
    else:
        max_date = sub['Activity_Date'].max()
        sub = sub[(sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)]

    if sub.empty:
        return []

    # Most recent period only
    latest = sub['Activity_Date'].max()
    sub = sub[sub['Activity_Date'] == latest]

    group_cols = [customer_col] + [d for d in dim[:2] if d in sub.columns]
    agg = sub.groupby(group_cols)['amount'].sum().reset_index()
    agg = agg.sort_values('amount', ascending=False).head(n)

    result = []
    for _, row in agg.iterrows():
        r = {customer_col: str(row[customer_col]), 'ending_arr': float(row['amount'])}
        for d in dim[:2]:
            if d in row:
                r[d] = str(row[d])
        result.append(r)

    return result


def build_product_bundling(
    df: pd.DataFrame,
    df3: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    product_col: str = 'Product',
    lookback: int = 3,
    period_type: str = 'Annual',
) -> Dict:
    """
    Build product bundling / penetration analysis.
    For each product: Revenue, Customers, Revenue per Customer, Penetration %, Customer Composition %.
    Matches config.py print_product_bundling logic.
    """
    if product_col not in df.columns:
        return {'periods': [], 'products': [], 'rows': []}

    sub = df[df['Month_Lookback'] == lookback].copy()
    end_classes = list(ENDING_CLASSES)
    sub_end = sub[sub['Classification'].isin(end_classes)].copy()

    if period_type == 'Quarter':
        sub_end = sub_end[sub_end['Activity_Date'].dt.month % 3 == 0]
    else:
        max_date = sub_end['Activity_Date'].max()
        sub_end = sub_end[(sub_end['Activity_Date'].dt.month == 12) | (sub_end['Activity_Date'] == max_date)]

    sub_end['_period'] = sub_end['Activity_Date'].apply(lambda d: _get_period_label(d, period_type))
    periods = sub_end.sort_values('Activity_Date')['_period'].unique().tolist()

    if sub_end.empty:
        return {'periods': periods, 'products': [], 'rows': []}

    products = sorted(sub_end[product_col].unique().tolist())

    # Total customers per period (denominator for penetration)
    total_custs = sub_end.groupby('_period')[customer_col].nunique()

    rows = []
    for prod in products:
        prod_data = sub_end[sub_end[product_col] == prod]
        rev_by_period   = prod_data.groupby('_period')['amount'].sum()
        custs_by_period = prod_data.groupby('_period')[customer_col].nunique()

        row = {'product': prod}
        for p in periods:
            rev   = float(rev_by_period.get(p, 0))
            custs = int(custs_by_period.get(p, 0))
            total = int(total_custs.get(p, 1))
            row[p] = {
                'revenue':       rev,
                'customers':     custs,
                'rev_per_cust':  round(rev / custs, 1) if custs > 0 else 0,
                'penetration':   round(custs / total * 100, 1) if total > 0 else 0,
            }
        rows.append(row)

    # Sort by ending revenue descending
    last_p = periods[-1] if periods else None
    if last_p:
        rows.sort(key=lambda r: r.get(last_p, {}).get('revenue', 0), reverse=True)

    return {'periods': periods, 'products': products, 'rows': rows}


def build_full_bridge_response(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    lookbacks: List[int] = [3, 12],
    period_type: str = 'Annual',
    dimension_cols: List[str] = [],
    year_filter: Optional[str] = None,
    include_bundling: bool = True,
) -> Dict[str, Any]:
    """
    Master function — builds ALL outputs for every lookback.
    Returns the complete response structure matching the Excel output.
    """
    # Apply year filter if specified
    df_work = df.copy()
    if year_filter and year_filter != 'All':
        df_work = df_work[df_work['Activity_Date'].dt.year.astype(str) == str(year_filter)]

    # Create bridge_df3 (customer-level pivot) — core for customer count SQL logic
    df3 = _make_bridge_df3(df_work, dimension_cols)

    response = {}

    for lb in lookbacks:
        bridge_pivot  = build_bridge_pivot(df_work, dim=dimension_cols, lookback=lb, period_type=period_type)
        cust_pivot    = build_customer_count_pivot(df3, lookback=lb, period_type=period_type)
        kpi_table     = build_kpi_table(df_work, df3, lookback=lb, period_type=period_type)

        response[str(lb)] = {
            'bridge_pivot':   bridge_pivot,
            'customer_pivot': cust_pivot,
            'kpi_table':      kpi_table,
        }

    # Top movers and top customers (use largest lookback)
    main_lb = max(lookbacks) if lookbacks else 12
    response['top_movers']    = build_top_movers(df_work, customer_col, dimension_cols, main_lb, period_type=period_type, year_filter=year_filter)
    response['top_customers'] = build_top_customers(df_work, customer_col, dimension_cols, main_lb, period_type=period_type, year_filter=year_filter)

    # Product bundling (use 3M lookback if available)
    if include_bundling and 'Product' in df_work.columns:
        bund_lb = 3 if 3 in lookbacks else lookbacks[0]
        response['product_bundling'] = build_product_bundling(df_work, df3, customer_col, 'Product', bund_lb, period_type)

    # Overall waterfall (all periods aggregated, main lookback)
    sub_main = df_work[df_work['Month_Lookback'] == main_lb]
    if not sub_main.empty:
        agg = sub_main.groupby('Classification')['amount'].sum().reset_index()
        order = MRR_BRIDGE_ORDER if any('MRR' in c for c in agg['Classification'].tolist()) else ACV_BRIDGE_ORDER
        waterfall = [{'category': r['Classification'], 'value': float(r['amount'])} for _, r in agg.iterrows()]
        waterfall.sort(key=lambda x: order.index(x['category']) if x['category'] in order else 99)
        response['waterfall'] = waterfall

    return response
