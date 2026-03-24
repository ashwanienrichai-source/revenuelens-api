"""
bridge_pivot_service.py

Exact Python replication of config.py + sql_queries.py + export_functions.py

CRITICAL: All customer count logic uses the EXACT flag conditions from sql_queries.py.
NOT simplified groupby — each count uses specific cross-table flag joins.

Classification name convention (matching config.py bridge labels):
  'Churn Partial'   (space, NOT hyphen — matches sql_queries.py column names)
  'Cross-sell'      (hyphen — matches config.py)
  'Other In'        (space)
  'Other Out'       (space)
"""

import pandas as pd
import numpy as np
from typing import List, Optional, Dict, Any

# ── Classification order (from config.py mrr_bridge_labels) ──────────
MRR_BRIDGE_ORDER = [
    'Beginning MRR', '',
    'Churn', 'Churn Partial', 'Downsell', 'Upsell',
    'Other Out', 'Other In', 'Cross-sell', 'New Logo',
    'Lapsed', 'Returning',
    'Ending MRR',
]
ACV_BRIDGE_ORDER = [
    'Prior ACV', '',
    'Expiry Pool', 'Churn', 'Churn Partial', 'Downsell', 'Upsell',
    'Cross-sell', 'New Logo', 'Lapsed', 'Returning',
    'Add on', 'RoB',
    'Ending ACV',
]

BEGINNING_CLASSES = {'Beginning MRR', 'Beginning ARR', 'Prior ACV'}
ENDING_CLASSES    = {'Ending MRR', 'Ending ARR', 'Ending ACV'}
CHURN_CLASSES     = {'Churn', 'Churn Partial'}
POSITIVE_CLASSES  = {'New Logo', 'Upsell', 'Cross-sell', 'Returning', 'Other In', 'Add on', 'RoB'}
NEGATIVE_CLASSES  = {'Churn', 'Churn Partial', 'Lapsed', 'Downsell', 'Other Out', 'Expiry Pool'}


def _make_bridge_df3(df2: pd.DataFrame, dim: List[str]) -> pd.DataFrame:
    """
    Replicates config.py bridge_df3 creation exactly.
    Pivots customer-level data so each Bridge Classification becomes its own column.
    This is the core data structure for all customer count SQL queries.

    Input:  df2 with columns Customer_ID, Activity_Date, Month_Lookback,
            Classification, amount, [dim cols]
    Output: wide table — one row per Customer×Date×Lookback, one col per classification
    """
    idx_cols = ['Customer_ID', 'Activity_Date', 'Month_Lookback'] + [d for d in dim if d in df2.columns]
    df3 = pd.pivot_table(
        df2, values='amount',
        index=idx_cols,
        columns='Classification',
        aggfunc='sum'
    ).reset_index()
    df3.fillna(0, inplace=True)

    # Ensure standard columns exist (matching config.py's explicit zero-fill)
    for col in ['Churn Partial', 'Cross-sell', 'Lapsed', 'Returning', 'Other In', 'Other Out']:
        if col not in df3.columns:
            df3[col] = 0

    return df3


def _get_beg_col(df3: pd.DataFrame) -> Optional[str]:
    for c in ['Beginning MRR', 'Beginning ARR', 'Prior ACV']:
        if c in df3.columns:
            return c
    return None


def _get_end_col(df3: pd.DataFrame) -> Optional[str]:
    for c in ['Ending MRR', 'Ending ARR', 'Ending ACV']:
        if c in df3.columns:
            return c
    return None


def _compute_customer_counts(df3: pd.DataFrame) -> pd.DataFrame:
    """
    Exact Python replication of ALL sql_queries.py queries.

    Each count uses the specific flag logic from the SQL:

    Beginning Customers:
      WHERE `Beginning MRR` != 0 AND flag_Beginning_MRR != 0

    Customer Churn:
      WHERE `Churn` != 0 AND flag_Ending_MRR == 0

    Customer New Logo:
      WHERE `New Logo` != 0 AND flag_Beginning_MRR == 0

    Customer Cross-sell (KEY DIFFERENCE FROM SIMPLIFIED VERSION):
      WHERE `Cross-sell` != 0 AND flag_Returning == 0 AND flag_Beginning_MRR == 0

    Customer Churn Partial:
      WHERE `Churn Partial` != 0 AND flag_Lapsed == 0 AND flag_Ending_MRR == 0

    Customer Returning:
      WHERE `Returning` != 0 AND flag_Beginning_MRR == 0

    Customer Lapsed:
      WHERE `Lapsed` != 0 AND flag_Ending_MRR == 0

    Customer Other In:
      WHERE `Other In` != 0 AND flag_Returning == 0
            AND flag_Beginning_MRR == 0 AND flag_Cross-sell == 0

    Customer Other Out:
      WHERE `Other Out` != 0 AND flag_Lapsed == 0
            AND flag_Ending_MRR == 0 AND flag_Churn_Partial == 0

    Ending Customers:
      WHERE `Ending MRR` != 0 AND flag_Ending_MRR != 0
    """
    beg_col = _get_beg_col(df3)
    end_col = _get_end_col(df3)
    if not beg_col or not end_col:
        return pd.DataFrame()

    group_cols = ['Activity_Date', 'Month_Lookback']
    df = df3.copy()

    # Compute per-customer flag aggregates (equivalent to the subquery JOINs in SQL)
    flag_beg  = df.groupby(['Customer_ID', 'Activity_Date', 'Month_Lookback'])[beg_col].sum().reset_index()
    flag_beg.columns  = ['Customer_ID', 'Activity_Date', 'Month_Lookback', 'flag_beg']

    flag_end  = df.groupby(['Customer_ID', 'Activity_Date', 'Month_Lookback'])[end_col].sum().reset_index()
    flag_end.columns  = ['Customer_ID', 'Activity_Date', 'Month_Lookback', 'flag_end']

    flag_ret  = df.groupby(['Customer_ID', 'Activity_Date', 'Month_Lookback'])['Returning'].sum().reset_index()
    flag_ret.columns  = ['Customer_ID', 'Activity_Date', 'Month_Lookback', 'flag_ret']

    flag_lap  = df.groupby(['Customer_ID', 'Activity_Date', 'Month_Lookback'])['Lapsed'].sum().reset_index()
    flag_lap.columns  = ['Customer_ID', 'Activity_Date', 'Month_Lookback', 'flag_lap']

    flag_cs   = df.groupby(['Customer_ID', 'Activity_Date', 'Month_Lookback'])['Cross-sell'].sum().reset_index()
    flag_cs.columns   = ['Customer_ID', 'Activity_Date', 'Month_Lookback', 'flag_cs']

    flag_cp   = df.groupby(['Customer_ID', 'Activity_Date', 'Month_Lookback'])['Churn Partial'].sum().reset_index()
    flag_cp.columns   = ['Customer_ID', 'Activity_Date', 'Month_Lookback', 'flag_cp']

    # Join all flags onto df
    for flag_df in [flag_beg, flag_end, flag_ret, flag_lap, flag_cs, flag_cp]:
        df = df.merge(flag_df, on=['Customer_ID', 'Activity_Date', 'Month_Lookback'], how='left')

    all_periods = df[group_cols].drop_duplicates()
    results = {}

    # Beginning Customers: beg != 0 AND flag_beg != 0
    mask = (df[beg_col] != 0) & (df['flag_beg'] != 0)
    results['Beginning Customers'] = df[mask].groupby(group_cols)['Customer_ID'].nunique()

    # Customer Churn: Churn != 0 AND flag_end == 0
    if 'Churn' in df.columns:
        mask = (df['Churn'] != 0) & (df['flag_end'] == 0)
        results['Customer Churn'] = df[mask].groupby(group_cols)['Customer_ID'].nunique() * -1

    # Customer New Logo: New Logo != 0 AND flag_beg == 0
    if 'New Logo' in df.columns:
        mask = (df['New Logo'] != 0) & (df['flag_beg'] == 0)
        results['Customer New Logo'] = df[mask].groupby(group_cols)['Customer_ID'].nunique()

    # Customer Cross-sell: Cross-sell != 0 AND flag_ret == 0 AND flag_beg == 0
    if 'Cross-sell' in df.columns:
        mask = (df['Cross-sell'] != 0) & (df['flag_ret'] == 0) & (df['flag_beg'] == 0)
        results['Customer Cross-sell'] = df[mask].groupby(group_cols)['Customer_ID'].nunique()

    # Customer Churn Partial: Churn Partial != 0 AND flag_lap == 0 AND flag_end == 0
    if 'Churn Partial' in df.columns:
        mask = (df['Churn Partial'] != 0) & (df['flag_lap'] == 0) & (df['flag_end'] == 0)
        results['Customer Churn Partial'] = df[mask].groupby(group_cols)['Customer_ID'].nunique() * -1

    # Customer Returning: Returning != 0 AND flag_beg == 0
    if 'Returning' in df.columns:
        mask = (df['Returning'] != 0) & (df['flag_beg'] == 0)
        results['Customer Returning'] = df[mask].groupby(group_cols)['Customer_ID'].nunique()

    # Customer Lapsed: Lapsed != 0 AND flag_end == 0
    if 'Lapsed' in df.columns:
        mask = (df['Lapsed'] != 0) & (df['flag_end'] == 0)
        results['Customer Lapsed'] = df[mask].groupby(group_cols)['Customer_ID'].nunique() * -1

    # Customer Other In: Other In != 0 AND flag_ret==0 AND flag_beg==0 AND flag_cs==0
    if 'Other In' in df.columns:
        mask = (df['Other In'] != 0) & (df['flag_ret'] == 0) & (df['flag_beg'] == 0) & (df['flag_cs'] == 0)
        results['Customer Other In'] = df[mask].groupby(group_cols)['Customer_ID'].nunique()

    # Customer Other Out: Other Out != 0 AND flag_lap==0 AND flag_end==0 AND flag_cp==0
    if 'Other Out' in df.columns:
        mask = (df['Other Out'] != 0) & (df['flag_lap'] == 0) & (df['flag_end'] == 0) & (df['flag_cp'] == 0)
        results['Customer Other Out'] = df[mask].groupby(group_cols)['Customer_ID'].nunique() * -1

    # Ending Customers: end_col != 0 AND flag_end != 0
    mask = (df[end_col] != 0) & (df['flag_end'] != 0)
    results['Ending Customers'] = df[mask].groupby(group_cols)['Customer_ID'].nunique()

    # Combine into one DataFrame
    cc_df = all_periods.copy()
    for label, series in results.items():
        s = series.reset_index()
        s.columns = ['Activity_Date', 'Month_Lookback', label]
        cc_df = cc_df.merge(s, on=group_cols, how='left')
        cc_df[label] = cc_df[label].fillna(0)

    return cc_df


def _period_label(dt: pd.Timestamp, period_type: str,
                  fy_last_month: int = 12) -> str:
    """Format period label matching config.py quarters_label / years_label."""
    if period_type == 'Quarter':
        return dt.strftime('%b-%y')
    else:
        if dt.month == 12:
            if fy_last_month == 12:
                return f"FY{dt.year}"
            else:
                return f"FY{str(dt.year)[-2:]}"
        else:
            return f"LTM {dt.strftime('%b-%y')}"


def _filter_periods(df: pd.DataFrame, lookback: int,
                    period_type: str) -> pd.DataFrame:
    """
    Replicates config.py bridge_df2 filter exactly (Standard mode).
    Quarter: Month_Lookback=3, month % 3 == 0
    Annual:  Month_Lookback=12, month == 12
    LTM:     Month_Lookback > 1, most recent date
    """
    sub = df[df['Month_Lookback'] == lookback].copy()
    if period_type == 'Quarter':
        sub = sub[sub['Activity_Date'].dt.month % 3 == 0]
    else:
        max_date = sub['Activity_Date'].max()
        sub = sub[
            (sub['Activity_Date'].dt.month == 12) |
            (sub['Activity_Date'] == max_date)
        ]
    return sub


def build_bridge_pivot(
    df: pd.DataFrame,
    dim: List[str] = [],
    lookback: int = 12,
    period_type: str = 'Annual',
    fy_last_month: int = 12,
) -> Dict:
    """
    Build ARR Waterfall pivot: Classification (rows) × Period (columns).
    Matches config.py + export_functions.py bridge sheet output exactly.
    """
    sub = _filter_periods(df, lookback, period_type)
    if sub.empty:
        return {'periods': [], 'rows': [], 'retention': {}}

    sub = sub.copy()
    sub['_period'] = sub['Activity_Date'].apply(
        lambda d: _period_label(d, period_type, fy_last_month)
    )
    period_order = sub.sort_values('Activity_Date')[['_period', 'Activity_Date']].drop_duplicates()
    periods = period_order['_period'].tolist()

    pivot = sub.groupby(['_period', 'Classification'])['amount'].sum().unstack(fill_value=0)

    # Detect tool type from classification names
    is_acv = any('ACV' in c or 'Prior' in c for c in pivot.columns)
    order  = ACV_BRIDGE_ORDER if is_acv else MRR_BRIDGE_ORDER

    def sort_key(c):
        try: return order.index(c)
        except ValueError: return 50

    all_cls = sorted([c for c in pivot.columns if c], key=sort_key)

    rows = []
    for cls in all_cls:
        if cls not in pivot.columns:
            continue
        values = {p: float(pivot.loc[p, cls]) if p in pivot.index else 0.0 for p in periods}
        rows.append({
            'classification': cls,
            'values':         values,
            'is_beginning':   cls in BEGINNING_CLASSES,
            'is_ending':      cls in ENDING_CLASSES,
            'is_positive':    cls in POSITIVE_CLASSES,
            'is_negative':    cls in NEGATIVE_CLASSES,
        })

    # % of Beginning
    beg_row = next((r for r in rows if r['classification'] in BEGINNING_CLASSES), None)
    if beg_row:
        for r in rows:
            pct = {}
            for p in periods:
                bv = beg_row['values'].get(p, 0)
                pct[p] = round(r['values'].get(p, 0) / bv * 100, 1) if bv else None
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
        exp = g({'Expiry Pool'}, p)

        retention[p] = {
            'beginning':            beg,
            'ending':               end,
            # MRR formulas (from config.py mrr_rr_formulas_dict)
            'churn_only_grr':       safe(beg + ch, beg),
            'grr':                  safe(beg + ch + dw, beg),
            'nrr_excl_lapsed':      safe(beg + ch + dw + up + cs, beg),
            'nrr':                  safe(beg + ch + dw + up + cs + ret + lap, beg),
            # ACV formulas (from config.py acv_rr_formulas_dict)
            'gross_renewal':        safe(exp + ch + dw, exp),
            'net_renewal':          safe(exp + ch + dw + up + cs, exp),
            'churn':                ch, 'downsell': dw, 'upsell': up,
            'cross_sell': cs,       'new_logo': nl,
        }

    return {'periods': periods, 'rows': rows, 'retention': retention}


def build_customer_count_pivot(
    df3: pd.DataFrame,
    lookback: int = 12,
    period_type: str = 'Annual',
    fy_last_month: int = 12,
) -> Dict:
    """
    Build customer count pivot using EXACT sql_queries.py flag logic.
    """
    sub = _filter_periods(df3, lookback, period_type)
    if sub.empty:
        return {'periods': [], 'rows': [], 'logo_retention': {}}

    sub = sub.copy()
    sub['_period'] = sub['Activity_Date'].apply(
        lambda d: _period_label(d, period_type, fy_last_month)
    )
    period_order = sub.sort_values('Activity_Date')[['_period', 'Activity_Date']].drop_duplicates()
    periods = period_order['_period'].tolist()

    # Compute customer counts with exact SQL flag logic
    cc_df = _compute_customer_counts(sub)
    if cc_df.empty:
        return {'periods': periods, 'rows': [], 'logo_retention': {}}

    cc_df['_period'] = cc_df['Activity_Date'].apply(
        lambda d: _period_label(d, period_type, fy_last_month)
    )

    # Order matches config.py cc_bridge_labels
    CC_ORDER = [
        'Beginning Customers',
        'Customer Churn', 'Customer Churn Partial',
        'Customer Cross-sell', 'Customer New Logo',
        'Customer Lapsed', 'Customer Returning',
        'Customer Other In', 'Customer Other Out',
        'Ending Customers',
    ]
    rows = []
    for col in [c for c in CC_ORDER if c in cc_df.columns]:
        values = {}
        for p in periods:
            row = cc_df[cc_df['_period'] == p]
            values[p] = int(row[col].sum()) if not row.empty else 0
        rows.append({
            'classification': col,
            'values':         values,
            'is_beginning':   col == 'Beginning Customers',
            'is_ending':      col == 'Ending Customers',
        })

    # Logo retention per period
    logo_retention = {}
    for p in periods:
        beg_vals = [r['values'].get(p, 0) for r in rows if r['classification'] == 'Beginning Customers']
        ch_vals  = [abs(r['values'].get(p, 0)) for r in rows if r['classification'] == 'Customer Churn']
        end_vals = [r['values'].get(p, 0) for r in rows if r['classification'] == 'Ending Customers']
        beg = beg_vals[0] if beg_vals else 0
        ch  = ch_vals[0]  if ch_vals  else 0
        end = end_vals[0] if end_vals else 0
        logo_retention[p] = {
            'beginning':      beg,
            'ending':         end,
            'logo_retention': round((beg - ch) / beg * 100, 1) if beg > 0 else None,
        }

    return {'periods': periods, 'rows': rows, 'logo_retention': logo_retention}


def build_kpi_table(
    df: pd.DataFrame,
    df3: pd.DataFrame,
    lookback: int = 12,
    period_type: str = 'Annual',
    fy_last_month: int = 12,
) -> List[Dict]:
    """
    Full KPI matrix matching config.py kpi_list output.
    Includes all retention rates using the exact formulas from mrr_rr_formulas_dict
    and acv_rr_formulas_dict.
    """
    sub = _filter_periods(df, lookback, period_type)
    if sub.empty:
        return []

    sub = sub.copy()
    sub['_period'] = sub['Activity_Date'].apply(
        lambda d: _period_label(d, period_type, fy_last_month)
    )
    period_order = sub.sort_values('Activity_Date')[['_period', 'Activity_Date']].drop_duplicates()
    periods = period_order['_period'].tolist()

    pv = sub.groupby(['_period', 'Classification'])['amount'].sum().unstack(fill_value=0)

    # Customer counts from df3
    cc_df = pd.DataFrame()
    if df3 is not None and not df3.empty:
        sub3 = _filter_periods(df3, lookback, period_type)
        if not sub3.empty:
            sub3 = sub3.copy()
            sub3['_period'] = sub3['Activity_Date'].apply(
                lambda d: _period_label(d, period_type, fy_last_month)
            )
            cc_df = _compute_customer_counts(sub3)
            if not cc_df.empty:
                cc_df['_period'] = cc_df['Activity_Date'].apply(
                    lambda d: _period_label(d, period_type, fy_last_month)
                )

    def gv(col_set, p):
        cols = [c for c in col_set if c in pv.columns]
        if not cols or p not in pv.index: return 0.0
        return float(pv.loc[p, cols].sum())

    def gcc(col, p):
        if cc_df.empty or col not in cc_df.columns: return 0
        row = cc_df[cc_df['_period'] == p]
        return int(abs(row[col].sum())) if not row.empty else 0

    def safe(n, d):
        return round(n / d * 100, 1) if d and d != 0 else None

    rows = []
    for p in periods:
        beg  = gv(BEGINNING_CLASSES, p)
        end  = gv(ENDING_CLASSES, p)
        ch   = gv(CHURN_CLASSES, p)
        dw   = gv({'Downsell'}, p)
        up   = gv({'Upsell'}, p)
        cs   = gv({'Cross-sell'}, p)
        nl   = gv({'New Logo'}, p)
        ret  = gv({'Returning'}, p)
        lap  = gv({'Lapsed'}, p)
        oi   = gv({'Other In'}, p)
        oo   = gv({'Other Out'}, p)
        exp  = gv({'Expiry Pool'}, p)
        cp   = gv({'Churn Partial'}, p)

        beg_c = gcc('Beginning Customers', p)
        end_c = gcc('Ending Customers', p)
        ch_c  = gcc('Customer Churn', p)
        nl_c  = gcc('Customer New Logo', p)
        cp_c  = gcc('Customer Churn Partial', p)

        rows.append({
            'period':              p,
            'beginning_arr':       beg,
            'ending_arr':          end,
            'new_logo':            nl,
            'upsell':              up,
            'cross_sell':          cs,
            'returning':           ret,
            'downsell':            dw,
            'churn':               ch,
            'churn_partial':       cp,
            'lapsed':              lap,
            'other_in':            oi,
            'other_out':           oo,
            'expiry_pool':         exp,
            # MRR retention rates (mrr_rr_formulas_dict)
            'grr_churn_only':      safe(beg + ch, beg),
            'gross_retention':     safe(beg + ch + dw, beg),
            'net_retention_excl':  safe(beg + ch + dw + up + cs, beg),
            'net_retention':       safe(beg + ch + dw + up + cs + ret + lap, beg),
            # ACV retention rates (acv_rr_formulas_dict)
            'gross_renewal':       safe(exp + ch + dw, exp),
            'net_renewal':         safe(exp + ch + dw + up + cs, exp),
            # Customer counts
            'cust_beginning':      beg_c,
            'cust_ending':         end_c,
            'cust_churn':          ch_c,
            'cust_new_logo':       nl_c,
            'cust_churn_partial':  cp_c,
            # Logo retention
            'logo_retention':      safe(beg_c - ch_c, beg_c),
            # Per customer metrics (mrr_oth_formulas_dict)
            'new_logo_per_cust':   round(nl / nl_c, 1) if nl_c > 0 else None,
            'churn_per_cust':      round(abs(ch) / ch_c, 1) if ch_c > 0 else None,
            'ending_per_cust':     round(end / end_c, 1) if end_c > 0 else None,
            # Customer count churn % (kpi_cc_list)
            'cc_churn_pct':        safe(-ch_c, beg_c),
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
    Top N movers per classification — matches config.py top_movers_sheets.
    Uses exact dimension groupings from config.
    """
    sub = df[df['Month_Lookback'] == lookback].copy()
    if year_filter and year_filter != 'All':
        sub = sub[sub['Activity_Date'].dt.year.astype(str) == str(year_filter)]
    if period_type == 'Quarter':
        sub = sub[sub['Activity_Date'].dt.month % 3 == 0]
    else:
        max_date = sub['Activity_Date'].max()
        sub = sub[(sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)]

    sub['_period'] = sub['Activity_Date'].apply(
        lambda d: d.strftime('%b-%y') if period_type == 'Quarter'
        else (f"FY{d.year}" if d.month == 12 else f"LTM {d.strftime('%b-%y')}")
    )

    # top_movers_sheets config equivalent
    movers_config = {
        'Churn':         [], 'New Logo':  [], 'Upsell':   [], 'Downsell':  [],
        'Churn Partial': dim[:1] if dim else [],
        'Cross-sell':    dim[:1] if dim else [],
        'Lapsed':        dim[:1] if dim else [],
        'Returning':     dim[:1] if dim else [],
    }

    result = {}
    for cls, extra_dims in movers_config.items():
        cls_data = sub[sub['Classification'] == cls].copy()
        if cls_data.empty:
            continue
        g_cols = [customer_col, '_period'] + [d for d in extra_dims if d in cls_data.columns]
        agg = cls_data.groupby(g_cols)['amount'].sum().reset_index()
        agg['_abs'] = agg['amount'].abs()
        top = agg.nlargest(n, '_abs').drop('_abs', axis=1)
        movers = []
        for _, row in top.iterrows():
            m = {'customer': str(row[customer_col]), 'period': str(row['_period']), 'value': float(row['amount'])}
            for d in extra_dims:
                if d in row: m[d] = str(row[d])
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
    """Top N customers by Ending ARR — matches config.py top_cust_sheets."""
    sub = df[(df['Month_Lookback'] == lookback) &
             (df['Classification'].isin(ENDING_CLASSES))].copy()
    if year_filter and year_filter != 'All':
        sub = sub[sub['Activity_Date'].dt.year.astype(str) == str(year_filter)]
    if sub.empty:
        return []
    latest = sub['Activity_Date'].max()
    sub = sub[sub['Activity_Date'] == latest]
    g_cols = [customer_col] + [d for d in dim[:2] if d in sub.columns]
    agg = sub.groupby(g_cols)['amount'].sum().reset_index()
    agg = agg.nlargest(n, 'amount')
    result = []
    for _, row in agg.iterrows():
        r = {customer_col: str(row[customer_col]), 'ending_arr': float(row['amount'])}
        for d in dim[:2]:
            if d in row: r[d] = str(row[d])
        result.append(r)
    return result


def build_product_bundling(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    product_col: str = 'Product',
    lookback: int = 3,
    period_type: str = 'Annual',
) -> Dict:
    """Product penetration analysis — matches config.py bundling logic."""
    if product_col not in df.columns:
        return {'periods': [], 'products': [], 'rows': []}
    sub = df[(df['Month_Lookback'] == lookback) &
             df['Classification'].isin(ENDING_CLASSES)].copy()
    if period_type == 'Quarter':
        sub = sub[sub['Activity_Date'].dt.month % 3 == 0]
    else:
        max_date = sub['Activity_Date'].max()
        sub = sub[(sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)]
    sub['_period'] = sub['Activity_Date'].apply(
        lambda d: d.strftime('%b-%y') if period_type == 'Quarter'
        else (f"FY{d.year}" if d.month == 12 else f"LTM {d.strftime('%b-%y')}")
    )
    periods  = sub.sort_values('Activity_Date')['_period'].unique().tolist()
    products = sorted(sub[product_col].unique().tolist())
    total_c  = sub.groupby('_period')[customer_col].nunique()
    rows = []
    for prod in products:
        pd_data = sub[sub[product_col] == prod]
        rev  = pd_data.groupby('_period')['amount'].sum()
        custs = pd_data.groupby('_period')[customer_col].nunique()
        row = {'product': prod}
        for p in periods:
            rv = float(rev.get(p, 0))
            cu = int(custs.get(p, 0))
            to = int(total_c.get(p, 1))
            row[p] = {'revenue': rv, 'customers': cu,
                      'rev_per_cust': round(rv/cu, 1) if cu > 0 else 0,
                      'penetration':  round(cu/to*100, 1) if to > 0 else 0}
        rows.append(row)
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
    fy_last_month: int = 12,
) -> Dict[str, Any]:
    """
    Master function — builds all outputs for all lookbacks.
    Exact equivalent of running all export_functions.py output sections.
    """
    df_work = df.copy()
    if year_filter and year_filter != 'All':
        df_work = df_work[df_work['Activity_Date'].dt.year.astype(str) == str(year_filter)]

    # Build bridge_df3 — the pivoted customer-level table for SQL queries
    df3 = _make_bridge_df3(df_work, dimension_cols)

    response = {}
    for lb in lookbacks:
        bridge_pivot  = build_bridge_pivot(df_work, dimension_cols, lb, period_type, fy_last_month)
        cust_pivot    = build_customer_count_pivot(df3, lb, period_type, fy_last_month)
        kpi_table     = build_kpi_table(df_work, df3, lb, period_type, fy_last_month)
        response[str(lb)] = {
            'bridge_pivot':   bridge_pivot,
            'customer_pivot': cust_pivot,
            'kpi_table':      kpi_table,
        }

    main_lb = max(lookbacks) if lookbacks else 12
    response['top_movers']    = build_top_movers(df_work, customer_col, dimension_cols, main_lb, period_type=period_type, year_filter=year_filter)
    response['top_customers'] = build_top_customers(df_work, customer_col, dimension_cols, main_lb, period_type=period_type, year_filter=year_filter)

    if 'Product' in df_work.columns:
        bund_lb = 3 if 3 in lookbacks else lookbacks[0]
        response['product_bundling'] = build_product_bundling(df_work, customer_col, 'Product', bund_lb, period_type)

    # Overall waterfall
    sub_main = df_work[df_work['Month_Lookback'] == main_lb]
    if not sub_main.empty:
        agg = sub_main.groupby('Classification')['amount'].sum().reset_index()
        is_acv = any('ACV' in c for c in agg['Classification'].tolist())
        order = ACV_BRIDGE_ORDER if is_acv else MRR_BRIDGE_ORDER
        wf = [{'category': r['Classification'], 'value': float(r['amount'])} for _, r in agg.iterrows()]
        wf.sort(key=lambda x: order.index(x['category']) if x['category'] in order else 99)
        response['waterfall'] = wf

    return response
