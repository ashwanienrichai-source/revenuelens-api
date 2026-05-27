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
import httpx
import os
from typing import Optional, List

from services.mrr_bridge_service import (
    load_bridge_file, filter_bridge_df, get_bridge_summary,
    get_bridge_by_dimension, get_fy_summary, get_top_movers,
    get_top_customers, get_kpi_matrix, compute_mrr_bridge_from_raw
)
from services.bridge_pivot_service import build_full_bridge_response
from services.mrr_workflow_engine import run_mrr_workflow
from services.acv_workflow_engine import run_acv_workflow

app = FastAPI(title="RevenueLens API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://revenuelens.ashwaniandcompany.com",
        "https://revenuelens-seven.vercel.app",
        "http://localhost:3000",
        "http://localhost:3001",
        "*",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    is_acv = any(k in cols_lower for k in ['tcv','acv','contract_end','contract end','contractend']) or \
             any('contract' in c and ('end' in c or 'start' in c) for c in cols_lower)
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
    df_raw = load_df(file)
    df_raw.columns = df_raw.columns.str.strip()

    lbs  = json.loads(lookbacks)
    dims = [d for d in json.loads(dimension_cols) if d in df_raw.columns]

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

    df_internal = df_bridge.rename(columns={
        'Customer':              'Customer_ID',
        'Date':                  'Activity_Date',
        'Month Lookback':        'Month_Lookback',
        'Bridge Classification': 'Classification',
        'Bridge Value':          'amount',
        'MRR or ARR':            'MRR_or_ARR',
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

    try:
        df_filtered = filter_bridge_df(df_internal)
    except Exception as e:
        raise HTTPException(500, f'filter_bridge_df failed: {str(e)}')

    try:
        results['bridge'] = {str(lb): get_bridge_summary(df_filtered, dims, lb, yr, period_type) for lb in lbs}
    except Exception as e:
        raise HTTPException(500, f'get_bridge_summary failed: {str(e)}')

    try:
        results['fy_summary'] = get_fy_summary(df_filtered, 'Customer_ID', lbs[-1] if lbs else 12)
    except Exception as e:
        results['fy_summary'] = []

    try:
        results['top_movers'] = get_top_movers(df_filtered, 'Customer_ID', dims, lbs[-1] if lbs else 12, n_movers, year_filter=yr, period_type=period_type)
    except Exception as e:
        results['top_movers'] = {}

    try:
        results['top_customers'] = get_top_customers(df_filtered, 'Customer_ID', dims[:2], lbs[-1] if lbs else 12, n_customers, period_type=period_type, year_filter=yr)
    except Exception as e:
        results['top_customers'] = []

    try:
        results['kpi_matrix'] = get_kpi_matrix(df_filtered, 'Customer_ID', dims, lbs[-1] if lbs else 12, period_type)
    except Exception as e:
        results['kpi_matrix'] = []

    try:
        results['output'] = df_filtered.head(1000).replace({np.nan: None}).to_dict(orient='records')
    except Exception as e:
        results['output'] = []

    try:
        results['pivot'] = build_full_bridge_response(
            df             = df_internal,
            customer_col   = 'Customer_ID',
            lookbacks      = lbs,
            period_type    = period_type,
            dimension_cols = dims,
            year_filter    = yr,
        )
    except Exception as e:
        raise HTTPException(500, f'build_full_bridge_response failed: {str(e)}')

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
    df_raw = load_df(file)
    df_raw.columns = df_raw.columns.str.strip()

    lbs  = json.loads(lookbacks)
    dims = [d for d in json.loads(dimension_cols) if d in df_raw.columns]

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

    df_internal = df_bridge.rename(columns={
        'Customer':              'Customer_ID',
        'Date':                  'Activity_Date',
        'Month Lookback':        'Month_Lookback',
        'Bridge Classification': 'Classification',
        'Bridge Value':          'amount',
        'ACV New':               'ACV_New',
        'DTE New':               'DTE_New',
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

    df_filtered = filter_bridge_df(df_internal)
    results['bridge']        = {str(lb): get_bridge_summary(df_filtered, dims, lb, yr, period_type) for lb in lbs}
    results['fy_summary']    = get_fy_summary(df_filtered, 'Customer_ID', lbs[-1] if lbs else 12)
    results['top_movers']    = get_top_movers(df_filtered, 'Customer_ID', dims, lbs[-1] if lbs else 12, n_movers, year_filter=yr, period_type=period_type)
    results['top_customers'] = get_top_customers(df_filtered, 'Customer_ID', dims[:2], lbs[-1] if lbs else 12, n_customers, period_type=period_type, year_filter=yr)
    results['kpi_matrix']    = get_kpi_matrix(df_filtered, 'Customer_ID', dims, lbs[-1] if lbs else 12, period_type)
    results['output']        = df_filtered.head(1000).replace({np.nan: None}).to_dict(orient='records')
    results['acv_table']     = result['acv'].head(500).replace({np.nan: None}).to_dict(orient='records')
    results['bookings']      = result['bookings'].head(500).replace({np.nan: None}).to_dict(orient='records')
    results['pivot'] = build_full_bridge_response(
        df             = df_internal,
        customer_col   = 'Customer_ID',
        lookbacks      = lbs,
        period_type    = period_type,
        dimension_cols = dims,
        year_filter    = yr,
    )

    return clean_json(results)


# ── AI Chat ───────────────────────────────────────────────────────────────────
from pydantic import BaseModel as PydanticBase

class ChatMessage(PydanticBase):
    role: str
    content: str

class ChatRequest(PydanticBase):
    message: str
    mode: str = "consultant"
    context: Optional[dict] = None
    history: Optional[List[ChatMessage]] = []

GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

def build_system_prompt(mode: str, context: Optional[dict]) -> str:
    base = """You are RevenueLens AI — an elite revenue intelligence system for CFOs and revenue leaders.
You have deep SaaS expertise: ARR, NRR, GRR, cohorts, waterfall analysis.
Always ground answers in provided revenue data. Never make up numbers.
Be precise, executive-ready, and actionable."""

    ctx = ""
    if context:
        lines = [f"{k}: {v}" for k, v in context.items() if v is not None]
        if lines:
            ctx = "\n\nLIVE REVENUE DATA:\n" + "\n".join(lines)

    modes = {
        "consultant": f"{base}{ctx}\n\nAnswer questions using the revenue data. Be direct and data-backed.",
        "insights":   f"{base}{ctx}\n\nGenerate executive narrative: what moved, why, business impact, risks, opportunities.",
        "educator":   f"{base}{ctx}\n\nExplain SaaS metrics in plain English, connected to the user's own data.",
        "advisor":    f"{base}{ctx}\n\nIdentify top 3 actions, upsell opportunities, churn risks, and strategic recommendations.",
    }
    return modes.get(mode, modes["consultant"])


@app.post("/chat")
async def chat(req: ChatRequest):
    system_prompt = build_system_prompt(req.mode, req.context)
    history = [{"role": m.role, "content": m.content} for m in (req.history or [])]

    # Try Groq first
    if GROQ_API_KEY:
        try:
            msgs = [{"role": "system", "content": system_prompt}]
            msgs += history[-6:]
            msgs.append({"role": "user", "content": req.message})
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json={"model": "llama-3.3-70b-versatile", "messages": msgs, "max_tokens": 1024, "temperature": 0.3}
                )
                r.raise_for_status()
                return {"response": r.json()["choices"][0]["message"]["content"], "provider": "groq", "mode": req.mode}
        except Exception as e:
            print(f"Groq failed: {e}")

    # Fallback to Gemini
    if GEMINI_API_KEY:
        try:
            contents = history[-6:] + [{"role": "user", "parts": [{"text": req.message}]}]
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
                    json={
                        "system_instruction": {"parts": [{"text": system_prompt}]},
                        "contents": contents,
                        "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.3}
                    }
                )
                r.raise_for_status()
                return {"response": r.json()["candidates"][0]["content"]["parts"][0]["text"], "provider": "gemini", "mode": req.mode}
        except Exception as e:
            raise HTTPException(500, f"All providers failed: {e}")

    raise HTTPException(500, "No AI provider configured")
