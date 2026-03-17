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
    get_top_customers, get_kpi_matrix
)

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
    is_acv = any('tcv' in c or 'contract_end' in c for c in cols_lower)
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
