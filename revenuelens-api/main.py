"""
RevenueLens API v2 - Production Grade
Modular analytics engine — run only selected modules.
Preserves all existing cohort engine logic unchanged.
"""
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import io
import json

from services.mrr_bridge_service import (
    load_bridge_file, filter_bridge_df, get_bridge_summary,
    get_bridge_by_dimension, get_fy_summary, get_top_movers,
    get_top_customers, get_kpi_matrix, compute_mrr_bridge_from_raw
)
from services.bridge_pivot_service import build_full_bridge_response
from services.mrr_workflow_engine import run_mrr_workflow
from services.acv_workflow_engine import run_acv_workflow

app = FastAPI(title="RevenueLens API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


def load_df(file: UploadFile) -> pd.DataFrame:
    content = file.file.read()
    name = file.filename.lower()
    try:
        if name.endswith('.csv'):
            try:    return pd.read_csv(io.BytesIO(content), encoding='utf-8', low_memory=False)
            except: return pd.read_csv(io.BytesIO(content), encoding='latin1', low_memory=False)
        else:
            return pd.read_excel(io.BytesIO(content), engine='openpyxl')
    except Exception as e:
        raise HTTPException(400, f'Could not read file: {e}')


def clean_json(obj):
    if isinstance(obj, dict):  return {k: clean_json(v) for k, v in obj.items()}
    if isinstance(obj, list):  return [clean_json(v) for v in obj]
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)): return None
    if isinstance(obj, (np.integer, np.int64)):   return int(obj)
    if isinstance(obj, (np.floating, np.float64)): return float(obj)
    if isinstance(obj, pd.Timestamp): return str(obj)[:10]
    if isinstance(obj, pd.Period):    return str(obj)
    return obj


@app.get('/')
def root(): return {'status': 'ok', 'service': 'RevenueLens API', 'version': '2.0.0'}

@app.get('/health')
def health(): return {'status': 'healthy'}


@app.post('/api/columns')
async def detect_columns(file: UploadFile = File(...)):
    df = load_df(file)
    df.columns = df.columns.str.strip()
    preview = df.head(5).replace({np.nan: None}).to_dict(orient='records')
    cols_lower = [c.lower() for c in df.columns]
    is_bridge = any(k in cols_lower for k in ['bridge value', 'classification', 'bridge classification', 'month lookback'])
    is_acv = any(k in cols_lower for k in ['tcv','acv','contract_end','contract end','contractend']) or              any('contract' in c and ('end' in c or 'start' in c) for c in cols_lower)
    return clean_json({'columns': df.columns.tolist(), 'row_count': len(df),
                       'preview': preview, 'is_bridge_output': is_bridge, 'is_acv': is_acv})


@app.post('/api/bridge/analyze')
async def analyze_bridge(
    file:           UploadFile = File(...),
    tool_type:      str = Form('MRR'),
    revenue_unit:   str = Form('raw'),
    lookbacks:      str = Form('[1,3,12]'),
    dimension_cols: str = Form('[]'),
    modules:        str = Form('["bridge","top_movers","top_customers","kpi_matrix","output"]'),
    year_filter:    str = Form(''),
    period_type:    str = Form('Annual'),
    customer_col:   str = Form('Customer_ID'),
    n_movers:       int = Form(30),
    n_customers:    int = Form(10),
):
    df_raw = load_df(file)
    df_raw.columns = df_raw.columns.str.strip()
    try:
        df = load_bridge_file(df_raw, tool_type=tool_type, revenue_unit=revenue_unit)
    except Exception as e:
        raise HTTPException(400, f'Could not load bridge file: {e}')

    df_filtered = filter_bridge_df(df)
    lbs   = json.loads(lookbacks)
    mods  = json.loads(modules)
    dims  = [d for d in json.loads(dimension_cols) if d in df_filtered.columns]
    yr    = year_filter if year_filter else None

    results = {
        'metadata': {
            'tool_type':   tool_type,
            'revenue_unit': revenue_unit,
            'row_count':   len(df_filtered),
            'lookbacks':   lbs,
            'dimensions':  dims,
            'fiscal_years': sorted(df_filtered['Activity_Date'].dt.year.unique().tolist(), key=str) if not df_filtered.empty else [],
            'classifications': sorted(df_filtered['Classification'].unique().tolist()) if not df_filtered.empty else [],
        }
    }

    if 'bridge' in mods:
        results['bridge'] = {str(lb): get_bridge_summary(df_filtered, dims, lb, yr, period_type) for lb in lbs}
        results['fy_summary'] = get_fy_summary(df_filtered, customer_col, lbs[-1] if lbs else 12)

    if 'top_movers' in mods:
        results['top_movers'] = get_top_movers(df_filtered, customer_col, dims, lbs[-1] if lbs else 12, n_movers, year_filter=yr, period_type=period_type)

    if 'top_customers' in mods:
        results['top_customers'] = get_top_customers(df_filtered, customer_col, dims[:2], lbs[-1] if lbs else 12, n_customers, period_type=period_type, year_filter=yr)

    if 'kpi_matrix' in mods:
        results['kpi_matrix'] = get_kpi_matrix(df_filtered, customer_col, dims, lbs[-1] if lbs else 12, period_type)

    if 'bridge_by_dim' in mods and dims:
        results['bridge_by_dim'] = {dim: get_bridge_by_dimension(df_filtered, dim, lbs[-1] if lbs else 12, yr) for dim in dims[:3]}

    if 'output' in mods:
        results['output'] = df_filtered.head(1000).replace({np.nan: None}).to_dict(orient='records')

    # Add new pivot-table format response
    results['pivot'] = build_full_bridge_response(
        df=df_filtered,
        customer_col=customer_col,
        lookbacks=lbs,
        period_type=period_type,
        dimension_cols=dims,
        year_filter=yr,
    )

    return clean_json(results)


# ── Cohort endpoints — backward compatible, unchanged logic ────────────
@app.post('/api/cohort/columns')
async def cohort_columns(file: UploadFile = File(...)):
    return await detect_columns(file)


@app.post('/api/cohort/analyze')
async def cohort_analyze(
    file:                 UploadFile = File(...),
    metric:               str = Form(...),
    customer_col:         str = Form(...),
    date_col:             str = Form(...),
    fiscal_col:           str = Form('None'),
    individual_cols:      str = Form('[]'),
    hierarchies:          str = Form('[]'),
    cohort_types:         str = Form('["SG"]'),
    period_filter:        str = Form('all'),
    selected_fiscal_year: str = Form(''),
):
    from services.cohort_service import run_cohort_analysis
    df = load_df(file)
    df.columns = df.columns.str.strip()

    for col in [metric, customer_col, date_col]:
        if col not in df.columns:
            raise HTTPException(400, f"Column '{col}' not found")

    df[metric] = pd.to_numeric(df[metric], errors='coerce').fillna(0)

    try:
        result = run_cohort_analysis(
            df=df, metric=metric, customer_col=customer_col, date_col=date_col,
            fiscal_col=fiscal_col if fiscal_col != 'None' else None,
            individual_cols=json.loads(individual_cols),
            hierarchies=json.loads(hierarchies),
            cohort_types=json.loads(cohort_types),
            period_filter=period_filter,
            selected_fiscal_year=selected_fiscal_year if selected_fiscal_year else None,
        )
    except Exception as e:
        raise HTTPException(500, f'Cohort analysis failed: {e}')

    return clean_json(result)

@app.post('/api/mrr/analyze')
async def analyze_mrr_raw(
    file:           UploadFile = File(...),
    customer_col:   str = Form('CustomerID'),
    date_col:       str = Form('Period'),
    revenue_col:    str = Form('Revenue'),
    product_col:    str = Form('Product'),
    channel_col:    str = Form('Channel'),
    region_col:     str = Form(''),
    quantity_col:   str = Form(''),
    revenue_unit:   str = Form('raw'),
    lookbacks:      str = Form('[1,3,12]'),
    dimension_cols: str = Form('[]'),
    period_type:    str = Form('Annual'),
    year_filter:    str = Form(''),
    n_movers:       int = Form(30),
    n_customers:    int = Form(10),
    tool_type:      str = Form('MRR'),
):
    """
    Run the full MRR Workflow engine on raw data.
    Exact Python replication of MRR_Workflow_Advanced.yxmd.
    Produces bridge output identical to Alteryx CSV, then runs all analytics.
    """
    df_raw = load_df(file)
    df_raw.columns = df_raw.columns.str.strip()

    lbs  = json.loads(lookbacks)
    dims = [d for d in json.loads(dimension_cols) if d in df_raw.columns]

    # Case-insensitive column matching for required columns
    col_lower = {c.lower().replace(' ','_'): c for c in df_raw.columns}
    def find_col(target, fallbacks=[]):
        candidates = [target] + fallbacks
        for c in candidates:
            if c in df_raw.columns: return c
            norm = c.lower().replace(' ','_')
            if norm in col_lower: return col_lower[norm]
        return None

    customer_col = find_col(customer_col, ['customer','customer_id','client','account']) or customer_col
    date_col     = find_col(date_col,     ['date','period','month','activity_date'])      or date_col
    revenue_col  = find_col(revenue_col,  ['revenue','mrr','arr','amount','value'])       or revenue_col
    product_col  = find_col(product_col,  ['product','sku'])                              or product_col

    for col in [customer_col, date_col, revenue_col]:
        if col not in df_raw.columns:
            raise HTTPException(400, f"Column '{col}' not found. Available: {list(df_raw.columns)}")

    try:
        # Run the Alteryx workflow equivalent
        df_bridge = run_mrr_workflow(
            df_raw        = df_raw,
            customer_col  = customer_col,
            date_col      = date_col,
            revenue_col   = revenue_col,
            product_col   = product_col if product_col in df_raw.columns else None,
            channel_col   = channel_col if channel_col in df_raw.columns else None,
            region_col    = region_col  if region_col  in df_raw.columns else None,
            quantity_col  = quantity_col if quantity_col in df_raw.columns else None,
            lookback_months = lbs,
            revenue_unit  = revenue_unit,
        )
    except Exception as e:
        raise HTTPException(500, f'MRR workflow failed: {str(e)}')

    if df_bridge.empty:
        raise HTTPException(400, 'No output after MRR workflow. Check date and revenue columns.')

    # Rename to internal convention for bridge service
    df_internal = df_bridge.rename(columns={
        'Customer':              'Customer_ID',
        'Date':                  'Activity_Date',
        'Month Lookback':        'Month_Lookback',
        'Bridge Classification': 'Classification',
        'Bridge Value':          'amount',
    })
    df_internal['Activity_Date']  = pd.to_datetime(df_internal['Activity_Date'])
    df_internal['Month_Lookback'] = df_internal['Month_Lookback'].astype(int)
    df_internal['amount']         = pd.to_numeric(df_internal['amount'], errors='coerce').fillna(0)

    yr = year_filter if year_filter else None

    results = {
        'metadata': {
            'tool_type':        tool_type,
            'revenue_unit':     revenue_unit,
            'row_count':        len(df_bridge),
            'lookbacks':        lbs,
            'dimensions':       dims,
            'fiscal_years':     sorted(df_internal['Activity_Date'].dt.year.unique().tolist(), key=str),
            'classifications':  sorted(df_internal['Classification'].unique().tolist()),
        }
    }

    # Standard bridge analytics
    df_filtered = filter_bridge_df(df_internal)
    results['bridge']        = {str(lb): get_bridge_summary(df_filtered, dims, lb, yr, period_type) for lb in lbs}
    results['fy_summary']    = get_fy_summary(df_filtered, 'Customer_ID', lbs[-1] if lbs else 12)
    results['top_movers']    = get_top_movers(df_filtered, 'Customer_ID', dims, lbs[-1] if lbs else 12, n_movers, year_filter=yr, period_type=period_type)
    results['top_customers'] = get_top_customers(df_filtered, 'Customer_ID', dims[:2], lbs[-1] if lbs else 12, n_customers, period_type=period_type, year_filter=yr)
    results['kpi_matrix']    = get_kpi_matrix(df_filtered, 'Customer_ID', dims, lbs[-1] if lbs else 12, period_type)
    results['output']        = df_filtered.head(1000).replace({np.nan: None}).to_dict(orient='records')

    # New pivot-table format (matches Excel output exactly)
    results['pivot'] = build_full_bridge_response(
        df             = df_internal,
        customer_col   = 'Customer_ID',
        lookbacks      = lbs,
        period_type    = period_type,
        dimension_cols = dims,
        year_filter    = yr,
    )

    return clean_json(results)

@app.post('/api/acv/analyze')
async def analyze_acv_raw(
    file:             UploadFile = File(...),
    customer_col:     str = Form('Customer'),
    order_date_col:   str = Form('Order Date'),
    start_col:        str = Form('Contract Start Date'),
    end_col:          str = Form('Contract End Date'),
    tcv_col:          str = Form('TCV'),
    product_col:      str = Form('Product'),
    channel_col:      str = Form('Channel'),
    region_col:       str = Form(''),
    quantity_col:     str = Form(''),
    revenue_unit:     str = Form('raw'),
    lookbacks:        str = Form('[1,3,12]'),
    dimension_cols:   str = Form('[]'),
    period_type:      str = Form('Annual'),
    year_filter:      str = Form(''),
    n_movers:         int = Form(30),
    n_customers:      int = Form(10),
):
    """
    Run the full ACV workflow engine on raw contract/booking data.
    Produces bridge table, ACV table, and bookings table.
    """
    df_raw = load_df(file)
    df_raw.columns = df_raw.columns.str.strip()

    lbs  = json.loads(lookbacks)
    dims = [d for d in json.loads(dimension_cols) if d in df_raw.columns]

    # Case-insensitive column matching
    col_lower = {c.lower().replace(' ', '_'): c for c in df_raw.columns}
    def find_col(target, fallbacks=[]):
        for c in [target] + fallbacks:
            if c in df_raw.columns: return c
            norm = c.lower().replace(' ', '_')
            if norm in col_lower: return col_lower[norm]
        return None

    customer_col   = find_col(customer_col,   ['customer','customer_id','client','account']) or customer_col
    order_date_col = find_col(order_date_col,  ['order_date','signing_date','date'])          or order_date_col
    start_col      = find_col(start_col,       ['contract_start','start_date','start'])       or start_col
    end_col        = find_col(end_col,         ['contract_end','end_date','expiry','end'])     or end_col
    tcv_col        = find_col(tcv_col,         ['tcv','acv','total_contract','contract_value','amount']) or tcv_col

    for col in [customer_col, start_col, end_col, tcv_col]:
        if col not in df_raw.columns:
            raise HTTPException(400, f"Column '{col}' not found. Available: {list(df_raw.columns)}")

    try:
        result = run_acv_workflow(
            df_raw          = df_raw,
            customer_col    = customer_col,
            order_date_col  = order_date_col if order_date_col in df_raw.columns else start_col,
            start_col       = start_col,
            end_col         = end_col,
            tcv_col         = tcv_col,
            product_col     = product_col   if product_col   in df_raw.columns else None,
            channel_col     = channel_col   if channel_col   in df_raw.columns else None,
            region_col      = region_col    if region_col    in df_raw.columns else None,
            quantity_col    = quantity_col  if quantity_col  in df_raw.columns else None,
            lookback_months = lbs,
            revenue_unit    = revenue_unit,
        )
    except Exception as e:
        raise HTTPException(500, f'ACV workflow failed: {str(e)}')

    df_bridge = result['bridge']
    if df_bridge.empty:
        raise HTTPException(400, 'No output after ACV workflow. Check contract start/end and TCV columns.')

    # Rename to internal convention (ACV uses 'ACV New' column)
    df_internal = df_bridge.rename(columns={
        'Customer':              'Customer_ID',
        'Date':                  'Activity_Date',
        'Month Lookback':        'Month_Lookback',
        'Bridge Classification': 'Classification',
        'Bridge Value':          'amount',
        'ACV New':               'ACV_New',
        'DTE New':               'DTE',
    })
    df_internal['Activity_Date']  = pd.to_datetime(df_internal['Activity_Date'])
    df_internal['Month_Lookback'] = df_internal['Month_Lookback'].astype(int)
    df_internal['amount']         = pd.to_numeric(df_internal['amount'], errors='coerce').fillna(0)

    yr = year_filter if year_filter else None

    results = {
        'metadata': {
            'tool_type':       'ACV',
            'revenue_unit':    revenue_unit,
            'row_count':       len(df_bridge),
            'lookbacks':       lbs,
            'dimensions':      dims,
            'fiscal_years':    sorted(df_internal['Activity_Date'].dt.year.unique().tolist(), key=str),
            'classifications': sorted(df_internal['Classification'].unique().tolist()),
        }
    }

    # Bridge analytics using existing service (works for both MRR and ACV)
    df_filtered = filter_bridge_df(df_internal)
    results['bridge']        = {str(lb): get_bridge_summary(df_filtered, dims, lb, yr, period_type) for lb in lbs}
    results['fy_summary']    = get_fy_summary(df_filtered, 'Customer_ID', lbs[-1] if lbs else 12)
    results['top_movers']    = get_top_movers(df_filtered, 'Customer_ID', dims, lbs[-1] if lbs else 12, n_movers, year_filter=yr, period_type=period_type)
    results['top_customers'] = get_top_customers(df_filtered, 'Customer_ID', dims[:2], lbs[-1] if lbs else 12, n_customers, period_type=period_type, year_filter=yr)
    results['kpi_matrix']    = get_kpi_matrix(df_filtered, 'Customer_ID', dims, lbs[-1] if lbs else 12, period_type)
    results['output']        = df_filtered.head(1000).replace({np.nan: None}).to_dict(orient='records')

    # ACV-specific tables
    results['acv_table']     = result['acv'].head(500).replace({np.nan: None}).to_dict(orient='records')
    results['bookings']      = result['bookings'].head(500).replace({np.nan: None}).to_dict(orient='records')

    # Pivot tables
    results['pivot'] = build_full_bridge_response(
        df             = df_internal,
        customer_col   = 'Customer_ID',
        lookbacks      = lbs,
        period_type    = period_type,
        dimension_cols = dims,
        year_filter    = yr,
    )

    return clean_json(results)
