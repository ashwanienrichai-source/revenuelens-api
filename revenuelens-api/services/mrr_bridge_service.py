"""
MRR Bridge Engine
Exact Python translation of MRR_Analysis_wf_Latest_Version.yxmd processing
as implemented in config.py and export_functions.py

Column contract (from config.py rename):
  Bridge Value  → amount
  Customer      → Customer_ID
  Date          → Activity_Date
  Month Lookback → Month_Lookback
  Classification → Classification
"""
import pandas as pd
import numpy as np
from typing import List, Optional, Dict


# ── Bridge label order (from config.py mrr_bridge_labels) ─────────────
MRR_BRIDGE_ORDER = [
    'Beginning MRR',
    'Churn', 'Churn Partial', 'Downsell', 'Upsell',
    'Other Out', 'Other In', 'Cross-sell', 'New Logo',
    'Lapsed', 'Returning',
    'Ending MRR',
]

ACV_BRIDGE_ORDER = [
    'Prior ACV', 'Expiry Pool',
    'Churn', 'Churn Partial', 'Downsell', 'Upsell',
    'Cross-sell', 'New Logo', 'Lapsed', 'Returning',
    'Add on', 'RoB',
    'Ending ACV',
]

BRIDGE_COLORS = {
    'New Logo':       '#10B981',
    'Cross-sell':     '#3B82F6',
    'Other In':       '#22C55E',
    'Returning':      '#F59E0B',
    'Upsell':         '#6366F1',
    'Downsell':       '#F97316',
    'Add on':         '#8B5CF6',
    'Add-on':         '#8B5CF6',
    'Churn':          '#EF4444',
    'Churn Partial':  '#FCA5A5',
    'Churn Partial':  '#FCA5A5',
    'Other Out':      '#94A3B8',
    'Lapsed':         '#CBD5E1',
    'Beginning MRR':  '#1E3A5F',
    'Ending MRR':     '#1E3A5F',
    'Prior ACV':      '#1E3A5F',
    'Ending ACV':     '#1E3A5F',
    'RoB':            '#A78BFA',
    'Expiry Pool':    '#374151',
}


def load_bridge_file(
    df_raw: pd.DataFrame,
    tool_type: str = 'MRR',
    revenue_unit: str = 'thousands',
) -> pd.DataFrame:
    """
    Replicate config.py loading logic exactly.
    Accepts already-read DataFrame (Alteryx output or cohort_app.py output).
    """
    df = df_raw.copy()
    df.columns = df.columns.str.strip()

    # Rename columns to match config.py internal convention
    rename_map = {}
    col_lower = {c.lower(): c for c in df.columns}

    for src, dst in [
        ('bridge value',          'amount'),
        ('customer',              'Customer_ID'),
        ('customer id',           'Customer_ID'),
        ('customer_id',           'Customer_ID'),
        ('date',                  'Activity_Date'),
        ('activity_date',         'Activity_Date'),
        ('month lookback',        'Month_Lookback'),
        ('month_lookback',        'Month_Lookback'),
        ('classification',        'Classification'),
        ('bridge classification', 'Classification'),
        ('mrr or arr',            'MRR_or_ARR'),
        ('mrr_or_arr',            'MRR_or_ARR'),
        ('acv new',               'ACV_New'),
        ('dte new',               'DTE_New'),
    ]:
        if src in col_lower and dst not in df.columns:
            rename_map[col_lower[src]] = dst

    df = df.rename(columns=rename_map)

    # Validate required columns
    for req in ['Customer_ID', 'Activity_Date', 'Month_Lookback', 'amount', 'Classification']:
        if req not in df.columns:
            # Try case-insensitive fallback
            match = next((c for c in df.columns if c.lower() == req.lower()), None)
            if match:
                df = df.rename(columns={match: req})

    # Filter zero bridge values
    if 'amount' in df.columns:
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
        df = df[df['amount'] != 0].copy()

    # Convert revenue units
    if revenue_unit == 'millions':
        df['amount'] = df['amount'] * 0.000001
    elif revenue_unit == 'thousands':
        df['amount'] = df['amount'] * 0.001

    # Parse dates
    if 'Activity_Date' in df.columns:
        df['Activity_Date'] = pd.to_datetime(df['Activity_Date'], errors='coerce')

    # Parse lookback
    if 'Month_Lookback' in df.columns:
        df['Month_Lookback'] = pd.to_numeric(df['Month_Lookback'], errors='coerce').fillna(12).astype(int)

    return df


def filter_bridge_df(
    df: pd.DataFrame,
    display_flag: str = 'Standard',
    custom_quarterly_dates: Optional[List] = None,
    custom_annual_dates: Optional[List] = None,
) -> pd.DataFrame:
    """
    Replicate config.py bridge_df2 filter logic exactly.
    Standard mode: Q=quarter-end months, A=December, plus LTM.
    """
    if display_flag == 'Custom' and custom_quarterly_dates and custom_annual_dates:
        q_dates = pd.to_datetime(custom_quarterly_dates)
        a_dates = pd.to_datetime(custom_annual_dates)
        return df[
            ((df['Activity_Date'].isin(q_dates)) & (df['Month_Lookback'] == 3)) |
            ((df['Activity_Date'].isin(a_dates)) & (df['Month_Lookback'] == 12))
        ].copy()

    # Standard mode (from config.py)
    max_year = df['Activity_Date'].dt.year.max()
    min_year = df['Activity_Date'].dt.year.min()
    max_month = df['Activity_Date'].max().month

    mask = (
        # Quarterly: month_lookback=3, quarter-end months (3,6,9,12)
        ((df['Month_Lookback'] == 3) &
         (df['Activity_Date'].dt.month % 3 == 0) &
         (df['Activity_Date'].dt.year >= min_year)) |
        # Annual: month_lookback=12, December
        ((df['Month_Lookback'] == 12) &
         (df['Activity_Date'].dt.month == 12) &
         (df['Activity_Date'].dt.year >= min_year)) |
        # LTM: most recent period across all lookbacks > 1
        ((df['Month_Lookback'] > 1) &
         (df['Activity_Date'].dt.month == max_month) &
         (df['Activity_Date'].dt.year == max_year))
    )
    return df[mask].copy()


def get_bridge_summary(
    df: pd.DataFrame,
    dimension_cols: Optional[List[str]] = None,
    lookback: int = 12,
    year_filter: Optional[str] = None,
    period_type: str = 'Annual',
) -> Dict:
    """
    Compute bridge summary for a given lookback.
    Returns waterfall data + retention rates.
    """
    sub = df[df['Month_Lookback'] == lookback].copy()

    if year_filter and year_filter != 'All':
        sub = sub[sub['Activity_Date'].dt.year.astype(str) == str(year_filter)]

    if sub.empty:
        return {'waterfall': [], 'by_period': [], 'retention': {}}

    # ── Waterfall (aggregate all periods) ────────────────────────────
    agg = sub.groupby('Classification')['amount'].sum().reset_index()
    waterfall = []
    for _, row in agg.iterrows():
        waterfall.append({
            'category': row['Classification'],
            'value':    float(row['amount']),
            'color':    BRIDGE_COLORS.get(row['Classification'], '#CBD5E1'),
        })

    # Sort by defined order
    order = MRR_BRIDGE_ORDER if any('MRR' in c for c in agg['Classification'].tolist()) else ACV_BRIDGE_ORDER
    order_map = {v: i for i, v in enumerate(order)}
    waterfall.sort(key=lambda x: order_map.get(x['category'], 99))

    # ── By period ─────────────────────────────────────────────────────
    if period_type == 'Annual':
        sub['_period'] = sub['Activity_Date'].dt.year.astype(str)
    else:
        sub['_period'] = sub['Activity_Date'].dt.to_period('Q').astype(str)

    by_period_raw = sub.groupby(['_period', 'Classification'])['amount'].sum().reset_index()
    by_period_pivot = by_period_raw.pivot_table(
        index='_period', columns='Classification', values='amount', aggfunc='sum'
    ).fillna(0).reset_index()
    by_period = by_period_pivot.to_dict(orient='records')

    # ── Retention metrics ─────────────────────────────────────────────
    BEG_CLASSES = {'Beginning MRR', 'Prior ACV', 'Beginning MRR or ARR', 'Beginning ARR'}
    END_CLASSES = {'Ending MRR', 'Ending ACV', 'Ending MRR or ARR', 'Ending ARR'}
    CHURN_CLASSES = {'Churn', 'Churn Partial', 'Churn Partial'}
    beg = float(sub[sub['Classification'].isin(BEG_CLASSES)]['amount'].sum())
    ch  = float(sub[sub['Classification'].isin(CHURN_CLASSES)]['amount'].sum())
    dw  = float(sub[sub['Classification'] == 'Downsell']['amount'].sum())
    up  = float(sub[sub['Classification'] == 'Upsell']['amount'].sum())
    nl  = float(sub[sub['Classification'] == 'New Logo']['amount'].sum())
    cr  = float(sub[sub['Classification'] == 'Cross-sell']['amount'].sum())
    end = float(sub[sub['Classification'].isin(END_CLASSES)]['amount'].sum())

    def safe(n, d): return round(n / d * 100, 1) if d and d != 0 else None

    retention = {
        'beginning':   beg,
        'ending':      end,
        'nrr':         safe(beg + up + cr + ch + dw, beg),
        'grr':         safe(beg + ch + dw, beg),
        'new_arr':     nl,
        'lost_arr':    ch + dw,
        'upsell':      up,
        'downsell':    dw,
        'cross_sell':  cr,
        'churn':       ch,
    }

    return {
        'waterfall':  waterfall,
        'by_period':  by_period,
        'retention':  retention,
    }


def get_bridge_by_dimension(
    df: pd.DataFrame,
    dimension_col: str,
    lookback: int = 12,
    year_filter: Optional[str] = None,
) -> List[Dict]:
    """Bridge breakdown by a single dimension (Product, Region, Vintage, etc.)"""
    sub = df[df['Month_Lookback'] == lookback].copy()
    if year_filter and year_filter != 'All':
        sub = sub[sub['Activity_Date'].dt.year.astype(str) == str(year_filter)]
    if sub.empty or dimension_col not in sub.columns:
        return []

    agg = sub.groupby([dimension_col, 'Classification'])['amount'].sum().reset_index()
    pivot = agg.pivot_table(
        index=dimension_col, columns='Classification', values='amount', aggfunc='sum'
    ).fillna(0).reset_index()
    return pivot.to_dict(orient='records')


def get_fy_summary(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    lookback: int = 12,
) -> List[Dict]:
    """Fiscal year summary — revenue and customer counts."""
    sub = df[df['Month_Lookback'] == lookback].copy()
    sub['_year'] = sub['Activity_Date'].dt.year.astype(str)

    ending_mask = sub['Classification'].isin(['Ending MRR', 'Ending ACV', 'Ending MRR or ARR', 'Ending ARR'])

    rev = sub[ending_mask].groupby('_year')['amount'].sum().reset_index().rename(columns={'amount': 'revenue'})
    if customer_col in sub.columns:
        cust = sub[ending_mask].groupby('_year')[customer_col].nunique().reset_index().rename(columns={customer_col: 'customers'})
        fy = rev.merge(cust, on='_year', how='left')
    else:
        fy = rev
        fy['customers'] = 0

    fy['rev_per_customer'] = fy['revenue'] / fy['customers'].replace(0, np.nan)
    return fy.rename(columns={'_year': 'fiscal_year'}).to_dict(orient='records')


def get_top_movers(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    dimension_cols: Optional[List[str]] = None,
    lookback: int = 12,
    n: int = 30,
    categories: Optional[List[str]] = None,
    period_type: str = 'Annual',
    year_filter: Optional[str] = None,
) -> Dict:
    """Top N movers by bridge category."""
    sub = df[df['Month_Lookback'] == lookback].copy()
    if year_filter and year_filter != 'All':
        sub = sub[sub['Activity_Date'].dt.year.astype(str) == str(year_filter)]
    if sub.empty:
        return {}

    if period_type == 'Annual':
        sub['_period'] = sub['Activity_Date'].dt.year.astype(str)
    else:
        sub['_period'] = sub['Activity_Date'].dt.to_period('Q').astype(str)

    cats = categories or ['Churn', 'New Logo', 'Upsell', 'Downsell', 'Cross-sell', 'Lapsed', 'Returning']
    dims = [d for d in (dimension_cols or []) if d in sub.columns]
    g_cols = [customer_col, '_period'] + dims if customer_col in sub.columns else ['_period'] + dims

    results = {}
    for cat in cats:
        cat_df = sub[sub['Classification'] == cat]
        if cat_df.empty:
            continue
        agg = cat_df.groupby(g_cols)['amount'].sum().reset_index()
        agg['_abs'] = agg['amount'].abs()
        top = agg.nlargest(n, '_abs').drop('_abs', axis=1)
        top = top.rename(columns={'amount': 'value', '_period': 'period'})
        results[cat] = top.replace({np.nan: None}).to_dict(orient='records')

    return results


def get_top_customers(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    dimension_cols: Optional[List[str]] = None,
    lookback: int = 12,
    n: int = 10,
    period_type: str = 'Annual',
    year_filter: Optional[str] = None,
) -> List[Dict]:
    """Top N customers by ending ARR/ACV."""
    sub = df[df['Month_Lookback'] == lookback].copy()
    if year_filter and year_filter != 'All':
        sub = sub[sub['Activity_Date'].dt.year.astype(str) == str(year_filter)]

    ending = sub[sub['Classification'].isin(['Ending MRR', 'Ending ACV', 'Ending MRR or ARR', 'Ending ARR'])]
    if ending.empty or customer_col not in ending.columns:
        return []

    dims = [d for d in (dimension_cols or []) if d in ending.columns]
    g_cols = [customer_col] + dims

    agg = ending.groupby(g_cols)['amount'].sum().reset_index()
    top = agg.nlargest(n, 'amount')
    return top.rename(columns={'amount': 'ending_arr'}).replace({np.nan: None}).to_dict(orient='records')


def get_kpi_matrix(
    df: pd.DataFrame,
    customer_col: str = 'Customer_ID',
    dimension_cols: Optional[List[str]] = None,
    lookback: int = 12,
    period_type: str = 'Annual',
) -> List[Dict]:
    """Full KPI matrix by period and optionally by dimension."""
    sub = df[df['Month_Lookback'] == lookback].copy()
    if sub.empty:
        return []

    if period_type == 'Annual':
        sub['_period'] = sub['Activity_Date'].dt.year.astype(str)
    else:
        sub['_period'] = sub['Activity_Date'].dt.to_period('Q').astype(str)

    dims = [d for d in (dimension_cols or []) if d in sub.columns]
    g_cols = ['_period'] + dims

    pivot = sub.groupby(g_cols + ['Classification'])['amount'].sum().reset_index()
    pivot = pivot.pivot_table(
        index=g_cols, columns='Classification', values='amount', aggfunc='sum'
    ).fillna(0).reset_index()

    rows = []
    for _, row in pivot.iterrows():
        beg = row.get('Beginning MRR', row.get('Prior ACV', row.get('Beginning MRR or ARR', 0))) or 0
        ch  = row.get('Churn', 0) + row.get('Churn Partial', 0) + row.get('Churn Partial', 0)
        dw  = row.get('Downsell', 0)
        up  = row.get('Upsell', 0)
        cr  = row.get('Cross-sell', 0)
        nl  = row.get('New Logo', 0)
        end = row.get('Ending MRR', row.get('Ending ACV', row.get('Ending MRR or ARR', 0))) or 0

        def safe(n, d): return round(n / d * 100, 1) if d and d != 0 else None

        entry = {
            'period':    row.get('_period', ''),
            'beginning': beg,
            'ending':    end,
            'new_logo':  nl,
            'upsell':    up,
            'downsell':  dw,
            'churn':     ch,
            'cross_sell': cr,
            'nrr':       safe(beg + up + cr + ch + dw, beg),
            'grr':       safe(beg + ch + dw, beg),
        }
        for d in dims:
            entry[d] = row.get(d, '')

        # Customer counts
        if customer_col in sub.columns:
            period_val = row.get('_period', '')
            sub_period = sub[sub['_period'] == period_val]
            entry['beg_customers'] = int(sub_period[sub_period['Classification'].isin(
                ['Beginning MRR', 'Prior ACV'])][customer_col].nunique())
            entry['end_customers'] = int(sub_period[sub_period['Classification'].isin(
                ['Ending MRR', 'Ending ACV'])][customer_col].nunique())
            entry['churn_customers'] = int(sub_period[sub_period['Classification'].isin(
                ['Churn', 'Churn Partial', 'Churn Partial'])][customer_col].nunique())
            entry['new_customers'] = int(sub_period[sub_period['Classification'] == 'New Logo'][customer_col].nunique())

        rows.append(entry)

    return rows


def compute_mrr_bridge_from_raw(
    df: pd.DataFrame,
    customer_col: str,
    date_col: str,
    revenue_col: str,
    dimension_cols: List[str],
    lookback_months: List[int] = [1, 3, 12],
    revenue_unit: str = 'raw',
) -> pd.DataFrame:
    """
    Compute MRR bridge from RAW data (no Alteryx needed).
    Mirrors the Alteryx MRR workflow logic in Python.
    """
    df = df.copy()
    df.columns = df.columns.str.strip()

    # Normalize
    df[date_col]    = pd.to_datetime(df[date_col], errors='coerce')
    df[revenue_col] = pd.to_numeric(df[revenue_col], errors='coerce').fillna(0)
    df = df[df[revenue_col] > 0].copy()

    # Normalize date to last of month
    df['Activity_Date'] = df[date_col] + pd.offsets.MonthEnd(0)

    # Revenue unit conversion
    if revenue_unit == 'millions':
        df[revenue_col] = df[revenue_col] * 0.000001
    elif revenue_unit == 'thousands':
        df[revenue_col] = df[revenue_col] * 0.001

    dims = [d for d in dimension_cols if d in df.columns]
    keys = [customer_col] + dims

    # Aggregate to customer-date level
    agg = df.groupby(keys + ['Activity_Date'], as_index=False)[revenue_col].sum()
    agg.rename(columns={revenue_col: 'MRR'}, inplace=True)

    # Dataset max date (for expiry pool)
    max_date = agg['Activity_Date'].max()

    # Build monthly grid
    all_months = pd.date_range(
        agg['Activity_Date'].min(),
        max_date + pd.DateOffset(months=1),
        freq='ME'
    )

    # Lifecycle dates
    lifecycle = agg.groupby(keys, as_index=False).agg(
        Min_Date=('Activity_Date', 'min'),
        Max_Date=('Activity_Date', 'max'),
    )

    lifecycle['_k'] = 1
    months_df = pd.DataFrame({'Activity_Date': all_months, '_k': 1})
    grid = lifecycle.merge(months_df, on='_k').drop('_k', axis=1)
    grid = grid[grid['Activity_Date'] >= grid['Min_Date']].copy()

    # Join actual MRR
    df_grid = grid.merge(agg[keys + ['Activity_Date', 'MRR']], on=keys + ['Activity_Date'], how='left')
    df_grid['MRR'] = df_grid['MRR'].fillna(0)
    df_grid['Dataset_Max_Date'] = max_date

    # Vintage = customer min date year
    cust_min = df_grid.groupby(customer_col)['Activity_Date'].min().reset_index().rename(columns={'Activity_Date':'Customer_Min_Date'})
    df_grid = df_grid.merge(cust_min, on=customer_col, how='left')
    df_grid['Vintage'] = df_grid['Customer_Min_Date'].dt.year.astype(str)

    results = []
    for lb in lookback_months:
        t = df_grid.copy()
        t['Month_Lookback'] = lb

        # Expiry pool flag: Date > Max_Date
        t['Expiry_Pool_Flag'] = np.where(t['Activity_Date'] > t['Dataset_Max_Date'], 1, 0)
        t['DTE'] = np.where(t['Expiry_Pool_Flag'] == 1, t['MRR'], 0)

        # Sort and compute prior
        t = t.sort_values(keys + ['Activity_Date']).reset_index(drop=True)
        t['Prior_MRR'] = t.groupby(keys)['MRR'].transform(lambda s: s.shift(lb)).fillna(0)

        # Filter: lookback filter
        days_beyond = (t['Activity_Date'] - t['Max_Date']).dt.days
        t = t[np.round(days_beyond / 30, 1) <= lb].copy()

        # Remove all-zero rows
        t = t[~((t['MRR'] == 0) & (t['Prior_MRR'] == 0))].copy()

        # Past/future flags
        days_from_first = (t['Activity_Date'] - t['Min_Date']).dt.days
        t['pastMRR'] = np.where(np.round(days_from_first / 30, 1) < lb, 'No', 'Yes')

        cust_max = df_grid.groupby(customer_col)['Activity_Date'].max().reset_index().rename(columns={'Activity_Date':'Customer_Max_Date'})
        t = t.merge(cust_max, on=customer_col, how='left')

        prod_keys = [customer_col] + ([dims[0]] if dims else [])
        prod_min = df_grid.groupby(prod_keys)['Activity_Date'].min().reset_index().rename(columns={'Activity_Date':'CustProd_Min_Date'})
        t = t.merge(prod_min, on=prod_keys, how='left')

        t['futureMRR'] = np.where(t['Customer_Max_Date'] > t['Activity_Date'], 'Yes', 'No')
        t['Lookback_Date'] = (t['Activity_Date'] - pd.DateOffset(months=lb)) + pd.offsets.MonthEnd(0)

        p   = t['Prior_MRR']
        cur = t['MRR']
        past = t['pastMRR']
        fut  = t['futureMRR']
        cm   = t.get('Customer_Max_Date', t['Activity_Date'])
        pm   = t.get('CustProd_Min_Date', t['Activity_Date'])
        ld   = t['Lookback_Date']
        cmin = t['Min_Date']

        conditions = [
            (p==0)&(cur!=0)&(past=='No')&(pm<=ld),
            (p==0)&(cur!=0)&(past=='No')&(cmin<=ld)&(pm>ld),
            (p==0)&(cur!=0)&(past=='No')&(cmin>ld),
            (p==0)&(cur!=0)&(past=='Yes'),
            (p!=0)&(cur==0)&(fut=='No')&(cm>=t['Activity_Date']),
            (p!=0)&(cur==0)&(fut=='No')&(cm<t['Activity_Date']),
            (p!=0)&(cur==0)&(fut=='Yes'),
            (p!=0)&(cur!=0)&(p<=cur),
            (p!=0)&(cur!=0)&(p>cur),
        ]
        choices = ['Other In','Cross-sell','New Logo','Returning','Churn Partial','Churn','Lapsed','Upsell','Downsell']
        t['Classification'] = np.select(conditions, choices, default='Unclassified')
        t['Bridge_Value']   = cur - p
        t['Beginning MRR']  = p
        t['Ending MRR']     = cur
        t['amount']         = t['Bridge_Value']
        t['Customer_ID']    = t[customer_col]
        t['Month_Lookback'] = lb

        results.append(t)

    if not results:
        return pd.DataFrame()

    combined = pd.concat(results, ignore_index=True)
    combined.rename(columns={
        'Activity_Date': 'Activity_Date',
        'Month_Lookback': 'Month_Lookback',
        'Classification': 'Classification',
        'amount': 'amount',
    }, inplace=True)

    return combined
