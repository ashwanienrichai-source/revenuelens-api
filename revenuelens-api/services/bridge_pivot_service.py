"""
bridge_pivot_service.py

Produces the ARR Waterfall pivot tables exactly matching the Excel output:
  - Bridge pivot: Classification (rows) x Period (cols) → Bridge Value
  - Customer count pivot: same structure with unique customer counts
  - Retention metrics: GRR, NRR, logo retention per period
  - % of Beginning ARR per classification per period

Column names from Alteryx output (after load_bridge_file rename):
  Customer_ID, Activity_Date, Month_Lookback, amount (=Bridge Value),
  Classification (=Bridge Classification)
  Optional: Product, Channel, Region, Vintage, MRR_or_ARR, Quantity
"""

import pandas as pd
import numpy as np
from typing import List, Optional, Dict, Any

# Classification display order (matches Excel)
BRIDGE_ROW_ORDER = [
    'Beginning ARR',
    'Beginning MRR',
    'New Logo',
    'Upsell',
    'Cross-sell',
    'Returning',
    'Other In',
    'Downsell',
    'Churn Partial',
    'Churn-Partial',
    'Churn partial',
    'Lapsed',
    'Churn',
    'Other Out',
    'Ending ARR',
    'Ending MRR',
    'DTE',
]

# Which classifications are positive (in) vs negative (out)
POSITIVE_CLASSES = {'New Logo', 'Upsell', 'Cross-sell', 'Returning', 'Other In'}
NEGATIVE_CLASSES = {'Churn', 'Churn Partial', 'Churn-Partial', 'Churn partial', 'Lapsed', 'Downsell', 'Other Out'}
BEGINNING_CLASSES = {'Beginning ARR', 'Beginning MRR', 'Prior ACV'}
ENDING_CLASSES   = {'Ending ARR', 'Ending MRR', 'Ending ACV'}


def _get_display_periods(df: pd.DataFrame, lookback: int, period_type: str) -> List[pd.Timestamp]:
    """Get the sorted list of date periods to show as columns."""
    sub = df[df['Month_Lookback'] == lookback].copy()
    if sub.empty:
        return []
    
    if period_type == 'Quarter':
        # Quarter-end months: 3, 6, 9, 12
        mask = sub['Activity_Date'].dt.month.isin([3, 6, 9, 12])
        dates = sub[mask]['Activity_Date'].dt.to_period('Q').dt.to_timestamp('Q')
    else:
        # Annual: December dates only
        mask = sub['Activity_Date'].dt.month == 12
        dates = sub[mask]['Activity_Date']
        # Also include LTM (most recent period)
        ltm_date = sub['Activity_Date'].max()
        if ltm_date.month != 12:
            dates = pd.concat([dates, pd.Series([ltm_date])])
    
    return sorted(dates.unique().tolist())


def _format_period_label(dt: pd.Timestamp, period_type: str) -> str:
    """Format period as column header: Mar-21, Jun-21, FY2022, etc."""
    if period_type == 'Quarter':
        return dt.strftime('%b-%y')
    else:
        # For annual, show FY + year or just year
        if dt.month == 12:
            return f"FY{dt.year}"
        else:
            return f"LTM {dt.strftime('%b-%y')}"


def build_bridge_pivot(
    df: pd.DataFrame,
    lookback: int = 12,
    period_type: str = 'Annual',
    dimension: Optional[str] = None,
    dimension_value: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the main ARR Bridge pivot table.
    
    Returns:
        {
            'periods': ['FY2022', 'FY2023', 'FY2024 LTM'],
            'rows': [
                {'classification': 'Beginning ARR', 'values': {period: amount}, 'pct_of_beg': {period: pct}},
                {'classification': 'New Logo', ...},
                ...
                {'classification': 'Ending ARR', ...},
            ],
            'retention': {period: {grr, nrr, logo_retention}},
        }
    """
    sub = df[df['Month_Lookback'] == lookback].copy()
    
    # Apply dimension filter
    if dimension and dimension_value and dimension in sub.columns:
        sub = sub[sub[dimension] == dimension_value]
    
    if sub.empty:
        return {'periods': [], 'rows': [], 'retention': {}}

    # Determine period column
    if period_type == 'Quarter':
        sub = sub[sub['Activity_Date'].dt.month.isin([3, 6, 9, 12])].copy()
        sub['_period'] = sub['Activity_Date'].apply(
            lambda d: d.strftime('%b-%y')
        )
    else:
        # Annual = December + LTM
        max_date = sub['Activity_Date'].max()
        mask = (sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)
        sub = sub[mask].copy()
        sub['_period'] = sub['Activity_Date'].apply(
            lambda d: f"FY{d.year}" if d.month == 12 else f"LTM {d.strftime('%b-%y')}"
        )

    if sub.empty:
        return {'periods': [], 'rows': [], 'retention': {}}

    # Get ordered periods
    period_dates = sub.groupby('_period')['Activity_Date'].min().sort_values()
    periods = period_dates.index.tolist()

    # Pivot: sum Bridge Value by Classification x Period
    pivot = sub.groupby(['_period', 'Classification'])['amount'].sum().unstack(fill_value=0)
    
    # Add Beginning ARR / Ending ARR rows if not present
    # (these come from the Alteryx output as separate classification rows)
    
    # Get all unique classifications present
    all_classes = pivot.columns.tolist()
    
    # Sort classifications by defined order
    def sort_key(c):
        try:
            return BRIDGE_ROW_ORDER.index(c)
        except ValueError:
            return 50
    all_classes_sorted = sorted(all_classes, key=sort_key)

    # Build rows
    rows = []
    for cls in all_classes_sorted:
        if cls not in pivot.columns:
            continue
        values_by_period = {}
        pct_by_period = {}
        
        for p in periods:
            val = float(pivot.loc[p, cls]) if p in pivot.index and cls in pivot.columns else 0.0
            values_by_period[p] = val
        
        rows.append({
            'classification': cls,
            'values': values_by_period,
            'is_beginning': cls in BEGINNING_CLASSES,
            'is_ending':    cls in ENDING_CLASSES,
            'is_positive':  cls in POSITIVE_CLASSES,
            'is_negative':  cls in NEGATIVE_CLASSES,
        })

    # Add % of Beginning ARR
    beg_row = next((r for r in rows if r['classification'] in BEGINNING_CLASSES), None)
    if beg_row:
        for r in rows:
            pct = {}
            for p in periods:
                beg_val = beg_row['values'].get(p, 0)
                val = r['values'].get(p, 0)
                pct[p] = round(val / beg_val * 100, 1) if beg_val != 0 else None
            r['pct_of_beginning'] = pct

    # Build retention metrics per period
    retention = {}
    for p in periods:
        beg = float(pivot.loc[p, [c for c in BEGINNING_CLASSES if c in pivot.columns]].sum()) if p in pivot.index else 0
        end = float(pivot.loc[p, [c for c in ENDING_CLASSES   if c in pivot.columns]].sum()) if p in pivot.index else 0
        ch  = float(pivot.loc[p, [c for c in pivot.columns if c in {'Churn','Churn Partial','Churn-Partial','Churn partial'}]].sum()) if p in pivot.index else 0
        dw  = float(pivot.loc[p, 'Downsell'] if 'Downsell' in pivot.columns and p in pivot.index else 0)
        up  = float(pivot.loc[p, 'Upsell']   if 'Upsell'   in pivot.columns and p in pivot.index else 0)
        cs  = float(pivot.loc[p, [c for c in pivot.columns if c in {'Cross-sell'}]].sum()) if p in pivot.index else 0
        
        def safe_pct(n, d):
            return round(n / d * 100, 1) if d and d != 0 else None

        retention[p] = {
            'beginning':    beg,
            'ending':       end,
            'grr':          safe_pct(beg + ch + dw, beg),          # Gross $ Retention
            'nrr':          safe_pct(beg + ch + dw + up + cs, beg), # Net $ Retention
            'churn_only_grr': safe_pct(beg + ch, beg),              # GRR churn only
            'churn':        ch,
            'downsell':     dw,
            'upsell':       up,
            'cross_sell':   cs,
        }

    return {
        'periods':   periods,
        'rows':      rows,
        'retention': retention,
    }


def build_customer_count_pivot(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    lookback: int = 12,
    period_type: str = 'Annual',
    dimension: Optional[str] = None,
    dimension_value: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the Customer Count pivot — same structure as bridge pivot
    but counts unique customers per classification per period.
    """
    sub = df[df['Month_Lookback'] == lookback].copy()
    
    if dimension and dimension_value and dimension in sub.columns:
        sub = sub[sub[dimension] == dimension_value]

    if sub.empty:
        return {'periods': [], 'rows': [], 'logo_retention': {}}

    # Period column
    if period_type == 'Quarter':
        sub = sub[sub['Activity_Date'].dt.month.isin([3, 6, 9, 12])].copy()
        sub['_period'] = sub['Activity_Date'].apply(lambda d: d.strftime('%b-%y'))
    else:
        max_date = sub['Activity_Date'].max()
        mask = (sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)
        sub = sub[mask].copy()
        sub['_period'] = sub['Activity_Date'].apply(
            lambda d: f"FY{d.year}" if d.month == 12 else f"LTM {d.strftime('%b-%y')}"
        )

    # Count unique customers per class x period
    pivot = sub.groupby(['_period', 'Classification'])[customer_col].nunique().unstack(fill_value=0)
    period_dates = sub.groupby('_period')['Activity_Date'].min().sort_values()
    periods = period_dates.index.tolist()

    all_classes = sorted(pivot.columns.tolist(), key=lambda c: BRIDGE_ROW_ORDER.index(c) if c in BRIDGE_ROW_ORDER else 50)

    rows = []
    for cls in all_classes:
        if cls not in pivot.columns:
            continue
        values = {p: int(pivot.loc[p, cls]) if p in pivot.index else 0 for p in periods}
        rows.append({'classification': f'Customer count - {cls.lower()}', 'values': values})

    # Add Customer count - beginning and ending
    # Beginning = customers with Beginning ARR classification
    # Ending = customers with Ending ARR classification
    
    # Logo retention per period
    logo_retention = {}
    for p in periods:
        beg_cls = [c for c in BEGINNING_CLASSES if c in pivot.columns]
        end_cls = [c for c in ENDING_CLASSES if c in pivot.columns]
        ch_cls  = [c for c in {'Churn','Churn Partial','Churn-Partial','Churn partial'} if c in pivot.columns]
        
        beg = int(pivot.loc[p, beg_cls].sum()) if p in pivot.index and beg_cls else 0
        end = int(pivot.loc[p, end_cls].sum()) if p in pivot.index and end_cls else 0
        ch  = int(pivot.loc[p, ch_cls].sum())  if p in pivot.index and ch_cls  else 0
        
        logo_retention[p] = {
            'beginning':       beg,
            'ending':          end,
            'logo_retention':  round((beg - abs(ch)) / beg * 100, 1) if beg > 0 else None,
        }

    return {
        'periods':        periods,
        'rows':           rows,
        'logo_retention': logo_retention,
    }


def build_kpi_table(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    lookback: int = 12,
    period_type: str = 'Annual',
) -> List[Dict]:
    """
    Build the full KPI summary table — one row per period with all metrics.
    Matches the retention analysis section in the Excel.
    """
    sub = df[df['Month_Lookback'] == lookback].copy()
    if sub.empty:
        return []

    if period_type == 'Quarter':
        sub = sub[sub['Activity_Date'].dt.month.isin([3, 6, 9, 12])].copy()
        sub['_period'] = sub['Activity_Date'].apply(lambda d: d.strftime('%b-%y'))
    else:
        max_date = sub['Activity_Date'].max()
        mask = (sub['Activity_Date'].dt.month == 12) | (sub['Activity_Date'] == max_date)
        sub = sub[mask].copy()
        sub['_period'] = sub['Activity_Date'].apply(
            lambda d: f"FY{d.year}" if d.month == 12 else f"LTM {d.strftime('%b-%y')}"
        )

    period_dates = sub.groupby('_period')['Activity_Date'].min().sort_values()
    periods = period_dates.index.tolist()
    
    pivot_val = sub.groupby(['_period','Classification'])['amount'].sum().unstack(fill_value=0)
    pivot_cnt = sub.groupby(['_period','Classification'])[customer_col].nunique().unstack(fill_value=0)

    def g(pivot, period, classes):
        cols = [c for c in classes if c in pivot.columns]
        if not cols or period not in pivot.index:
            return 0.0
        return float(pivot.loc[period, cols].sum())

    def safe(n, d):
        return round(n / d * 100, 1) if d and d != 0 else None

    rows = []
    for p in periods:
        beg = g(pivot_val, p, BEGINNING_CLASSES)
        end = g(pivot_val, p, ENDING_CLASSES)
        ch  = g(pivot_val, p, {'Churn','Churn Partial','Churn-Partial','Churn partial'})
        dw  = g(pivot_val, p, {'Downsell'})
        up  = g(pivot_val, p, {'Upsell'})
        cs  = g(pivot_val, p, {'Cross-sell'})
        nl  = g(pivot_val, p, {'New Logo'})
        ret = g(pivot_val, p, {'Returning'})
        oth_in = g(pivot_val, p, {'Other In'})
        lapsed = g(pivot_val, p, {'Lapsed'})

        # Customer counts
        beg_c = g(pivot_cnt, p, BEGINNING_CLASSES)
        ch_c  = g(pivot_cnt, p, {'Churn','Churn Partial','Churn-Partial','Churn partial'})
        nl_c  = g(pivot_cnt, p, {'New Logo'})
        end_c = g(pivot_cnt, p, ENDING_CLASSES)

        rows.append({
            'period':             p,
            'beginning_arr':      beg,
            'ending_arr':         end,
            'new_logo':           nl,
            'upsell':             up,
            'downsell':           dw,
            'cross_sell':         cs,
            'churn':              ch,
            'returning':          ret,
            'lapsed':             lapsed,
            'other_in':           oth_in,
            # Rates
            'gross_retention_churn_only': safe(beg + ch, beg),
            'gross_retention':    safe(beg + ch + dw, beg),
            'net_retention_excl_lapsed': safe(beg + ch + dw + up + cs, beg),
            'net_retention':      safe(beg + ch + dw + up + cs + ret + lapsed, beg),
            # Customer counts
            'cust_beginning':     int(beg_c),
            'cust_churn':         int(abs(ch_c)),
            'cust_new_logo':      int(nl_c),
            'cust_ending':        int(end_c),
            'logo_retention':     safe(beg_c - abs(ch_c), beg_c),
            # Per customer
            'new_logo_per_cust':  round(nl / nl_c * 1000, 1) if nl_c > 0 else None,
            'churn_per_cust':     round(abs(ch) / abs(ch_c) * 1000, 1) if ch_c != 0 else None,
            'ending_per_cust':    round(end / end_c * 1000, 1) if end_c > 0 else None,
        })

    return rows


def build_full_bridge_response(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    lookbacks: List[int] = [3, 12],
    period_type: str = 'Annual',
    dimension_cols: List[str] = [],
    year_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Master function — builds all bridge outputs for given lookbacks.
    Returns the full response matching the Excel output structure.
    """
    response = {}

    for lb in lookbacks:
        sub = df[df['Month_Lookback'] == lb].copy()
        if year_filter and year_filter != 'All':
            sub = sub[sub['Activity_Date'].dt.year.astype(str) == str(year_filter)]
        
        bridge_pivot  = build_bridge_pivot(sub, lookback=lb, period_type=period_type)
        cust_pivot    = build_customer_count_pivot(sub, customer_col=customer_col, lookback=lb, period_type=period_type)
        kpi_table     = build_kpi_table(sub, customer_col=customer_col, lookback=lb, period_type=period_type)

        response[str(lb)] = {
            'bridge_pivot':  bridge_pivot,
            'customer_pivot': cust_pivot,
            'kpi_table':     kpi_table,
        }

    # Overall waterfall (aggregate across all periods, last lookback)
    main_lb = lookbacks[-1] if lookbacks else 12
    main_sub = df[df['Month_Lookback'] == main_lb].copy()
    if not main_sub.empty:
        agg = main_sub.groupby('Classification')['amount'].sum().reset_index()
        waterfall = [
            {'category': r['Classification'], 'value': float(r['amount'])}
            for _, r in agg.iterrows()
        ]
        waterfall.sort(key=lambda x: BRIDGE_ROW_ORDER.index(x['category']) if x['category'] in BRIDGE_ROW_ORDER else 99)
        response['waterfall'] = waterfall

    return response
