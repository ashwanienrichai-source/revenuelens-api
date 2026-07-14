"""
RevenueLens API — main.py
Production-grade FastAPI server for ACV + MRR analytics.
"""

import os
import io
import json
import logging
import time
from typing import Optional

import pandas as pd
import numpy as np
import stripe
import httpx
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from services.mrr_bridge_service import (
    load_bridge_file, filter_bridge_df, get_bridge_summary,
    get_bridge_by_dimension, get_fy_summary, get_top_movers,
    get_top_customers, get_kpi_matrix, compute_mrr_bridge_from_raw,
)
from services.bridge_pivot_service import build_full_bridge_response
from services.mrr_workflow_engine import run_mrr_workflow
from services.acv_workflow_engine import run_acv_workflow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("revenuelens")

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="RevenueLens API",
    version="2.0.0",
    description="Revenue Intelligence OS — ACV / MRR Analytics Engine",
)

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

# ── Config ─────────────────────────────────────────────────────────────────────
stripe.api_key        = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID       = os.getenv("STRIPE_PRICE_ID", "")
FRONTEND_URL          = os.getenv("FRONTEND_URL", "https://revenuelens.ashwaniandcompany.com")
SUPABASE_URL          = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "")

PLAN_PRICES = {
    "starter":    os.getenv("STRIPE_PRICE_STARTER",    ""),
    "pro":        os.getenv("STRIPE_PRICE_PRO",        ""),
    "enterprise": os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def read_upload(file: UploadFile) -> pd.DataFrame:
    """Read uploaded CSV or Excel into a DataFrame."""
    content = file.file.read()
    name    = (file.filename or "").lower()
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(content))
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(content))
    # Try CSV first, fall back to Excel
    try:
        return pd.read_csv(io.BytesIO(content))
    except Exception:
        return pd.read_excel(io.BytesIO(content))


def safe_json(obj):
    """Convert pandas/numpy objects to JSON-serializable types."""
    if isinstance(obj, (pd.DataFrame,)):
        return obj.where(pd.notnull(obj), None).to_dict(orient="records")
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def df_to_records(df) -> list:
    """
    Convert DataFrame to JSON-safe list of dicts.

    FIX: some engines (e.g. the ACV pre-aggregation engine) now return
    already-converted lists of dicts directly, instead of raw DataFrames.
    This function is made tolerant of both shapes so it doesn't crash with
    AttributeError: 'list' object has no attribute 'empty' when that happens.
    """
    if df is None:
        return []
    if isinstance(df, list):
        # Already converted to records upstream (e.g. by run_acv_workflow) —
        # pass through as-is.
        return df
    if df.empty:
        return []
    df = df.copy()
    for col in df.select_dtypes(include=["datetime64[ns]", "datetime64[ns, UTC]"]).columns:
        df[col] = df[col].astype(str)
    for col in df.select_dtypes(include=["float"]).columns:
        df[col] = df[col].where(pd.notnull(df[col]), None)
    return df.to_dict(orient="records")


# ── Health (keep-alive + monitoring) ──────────────────────────────────────────

@app.get("/health")
def health():
    """Health check — used by keep-alive cron and monitoring."""
    return {"status": "ok", "version": "2.0.0", "ts": int(time.time())}


@app.get("/")
def root():
    return {"service": "RevenueLens API", "version": "2.0.0", "status": "live"}


# ── Column detection ───────────────────────────────────────────────────────────

@app.post("/api/columns")
async def get_columns(file: UploadFile = File(...)):
    """Return column names + sample values from uploaded file."""
    try:
        df = read_upload(file)
        columns = list(df.columns)

        # Heuristic: flag likely bridge output (has Classification + Bridge Value)
        is_bridge_output = (
            any("classification" in c.lower() for c in columns) and
            any("bridge" in c.lower() and "value" in c.lower() for c in columns)
        )
        is_acv = any(c.lower() in ("tcv", "acv", "contract start", "contract start date") for c in columns)

        # Sample values per column (first 3 non-null)
        samples = {}
        for col in columns[:50]:
            vals = df[col].dropna().head(3).tolist()
            samples[col] = [str(v) for v in vals]

        return {
            "columns":          columns,
            "row_count":        len(df),
            "samples":          samples,
            "is_bridge_output": is_bridge_output,
            "is_acv":           is_acv,
        }
    except Exception as e:
        logger.error(f"/api/columns error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ── MRR / ARR Analytics ────────────────────────────────────────────────────────

def compute_bridge_qc(loaded_df: pd.DataFrame) -> dict:
    """
    Structured (non-warning) reconciliation check on a bridge DataFrame that
    has already been through load_bridge_file() — i.e. uses the internal
    column names: Activity_Date, Month_Lookback, Classification, amount.

    Mirrors mrr_workflow_engine._check_reconciliation's math, but returns a
    dict the API can surface (parity with the ACV engine's qc1/qc1_detail).
    """
    if loaded_df is None or loaded_df.empty:
        return {"qc1": False, "qc1_detail": "No bridge rows to check"}

    BEG = {'Beginning MRR', 'Prior ACV', 'Beginning MRR or ARR', 'Beginning ARR'}
    END = {'Ending MRR', 'Ending ACV', 'Ending MRR or ARR', 'Ending ARR'}
    MOVEMENTS = {'New Logo', 'Cross-sell', 'Other In', 'Returning',
                 'Upsell', 'Downsell', 'Churn', 'Churn Partial', 'Other Out', 'Lapsed'}

    required = {'Activity_Date', 'Month_Lookback', 'Classification', 'amount'}
    if not required.issubset(loaded_df.columns):
        return {"qc1": True, "qc1_detail": "Reconciliation skipped — required columns not present"}

    pivot = (loaded_df.groupby(['Activity_Date', 'Month_Lookback', 'Classification'])['amount']
             .sum().unstack(fill_value=0))

    beg_col = next((c for c in BEG if c in pivot.columns), None)
    end_col = next((c for c in END if c in pivot.columns), None)
    if not beg_col or not end_col:
        return {"qc1": True, "qc1_detail": "Reconciliation skipped — no boundary rows found"}

    mov_cols = [c for c in pivot.columns if c in MOVEMENTS]
    diff = (pivot[beg_col] + pivot[mov_cols].sum(axis=1) - pivot[end_col]).abs()
    bad = diff[diff > 0.01]

    qc1 = len(bad) == 0
    detail = ("All periods reconcile (±0.01)" if qc1
              else f"{len(bad)} period(s) mismatch — max discrepancy {diff.max():.2f}")
    return {"qc1": qc1, "qc1_detail": detail}


def build_bridge_response(
    loaded_df:      pd.DataFrame,
    customer_col:   str,
    dimension_cols: list,
    lookbacks:      list,
    year_filter,
    period_type:    str,
    n_movers:       int,
    n_customers:    int,
    output_records: list,
) -> dict:
    """
    Shared response builder used by BOTH /api/mrr/analyze (raw file →
    run_mrr_workflow → this) and /api/bridge/analyze (pre-classified bridge
    file uploaded directly → this). Assembles exactly the shape
    command-center.tsx expects:

      bridge[str(lb)]      -> get_bridge_summary()   {waterfall, by_period, retention}
      pivot[str(lb)].kpi_table -> get_kpi_matrix() for that lookback
      top_movers            -> get_top_movers()   (computed at the LARGEST lookback,
                               matching the frontend's default selLb = last lookback)
      top_customers         -> get_top_customers() (same lookback choice)
      kpi_matrix            -> flat get_kpi_matrix() at the same lookback, for the
                               fallback path command-center.tsx uses when
                               results.pivot is absent
      metadata              -> {dimensions, row_count}
      output                -> raw bridge rows (pre-load_bridge_file, engine's own
                               column names) — this is what the frontend's
                               effectiveByPeriod PATH A / CSV export consume
      qc                    -> compute_bridge_qc()
    """
    if loaded_df is None or loaded_df.empty:
        return {
            "bridge": {}, "pivot": {}, "top_movers": {}, "top_customers": [],
            "kpi_matrix": [], "metadata": {"dimensions": dimension_cols, "row_count": 0},
            "output": output_records, "qc": {"qc1": False, "qc1_detail": "No bridge rows produced"},
        }

    bridge_dict = {}
    pivot_dict  = {}
    for lb in lookbacks:
        bridge_dict[str(lb)] = get_bridge_summary(
            loaded_df, dimension_cols=dimension_cols, lookback=lb,
            year_filter=year_filter, period_type=period_type,
        )
        pivot_dict[str(lb)] = {
            "kpi_table": get_kpi_matrix(
                loaded_df, customer_col=customer_col, dimension_cols=dimension_cols,
                lookback=lb, period_type=period_type,
            )
        }

    # Top Movers / Top Customers / flat KPI matrix are computed at the
    # LARGEST requested lookback — this matches command-center.tsx's own
    # default (`setSelLb(lookbacks[lookbacks.length-1])` after a run), so the
    # numbers shown by default correspond to the same lookback the UI selects.
    primary_lb = max(lookbacks) if lookbacks else 12

    movers = get_top_movers(
        loaded_df, customer_col=customer_col, dimension_cols=dimension_cols,
        lookback=primary_lb, n=n_movers, period_type=period_type, year_filter=year_filter,
    )
    top_customers = get_top_customers(
        loaded_df, customer_col=customer_col, dimension_cols=dimension_cols,
        lookback=primary_lb, n=n_customers, period_type=period_type, year_filter=year_filter,
    )
    kpi_matrix_flat = get_kpi_matrix(
        loaded_df, customer_col=customer_col, dimension_cols=dimension_cols,
        lookback=primary_lb, period_type=period_type,
    )

    return {
        "bridge":        bridge_dict,
        "pivot":         pivot_dict,
        "top_movers":    movers,
        "top_customers": top_customers,
        "kpi_matrix":    kpi_matrix_flat,
        "metadata":      {"dimensions": dimension_cols, "row_count": len(loaded_df)},
        "output":        output_records,
        "qc":            compute_bridge_qc(loaded_df),
    }


@app.post("/api/mrr/analyze")
async def analyze_mrr(
    file:           UploadFile    = File(...),
    customer_col:   str           = Form("Customer"),
    date_col:       str           = Form("Date"),
    revenue_col:    str           = Form("MRR"),
    product_col:    Optional[str] = Form(None),
    channel_col:    Optional[str] = Form(None),
    region_col:     Optional[str] = Form(None),
    quantity_col:   Optional[str] = Form(None),
    lookbacks:      str           = Form("[1,3,12]"),
    revenue_unit:   str           = Form("raw"),
    revenue_type:   str           = Form("ARR"),   # client-side ARR/MRR ×12 toggle only — not used server-side
    dimension_cols: str           = Form("[]"),
    year_filter:    str           = Form(""),
    period_type:    str           = Form("Annual"),
    n_movers:       int           = Form(30),
    n_customers:    int           = Form(10),
    tool_type:      str           = Form("MRR"),
):
    try:
        df = read_upload(file)
        lb_list = json.loads(lookbacks) if isinstance(lookbacks, str) else lookbacks
        logger.info(f"/api/mrr/analyze rows={len(df)} cols={list(df.columns)[:6]} lookbacks={lb_list}")

        has_product = bool(product_col and product_col in df.columns)
        has_channel = bool(channel_col and channel_col in df.columns)
        has_region  = bool(region_col  and region_col  in df.columns)

        bridge_engine_df = run_mrr_workflow(
            df,
            customer_col  = customer_col,
            date_col      = date_col,
            revenue_col   = revenue_col,
            product_col   = product_col if has_product else None,
            channel_col   = channel_col if has_channel else None,
            region_col    = region_col  if has_region  else None,
            quantity_col  = quantity_col if quantity_col and quantity_col in df.columns else None,
            lookback_months = lb_list,
            revenue_unit  = revenue_unit,
        )

        if bridge_engine_df is None or bridge_engine_df.empty:
            return JSONResponse(content={
                "bridge": {}, "pivot": {}, "top_movers": {}, "top_customers": [],
                "kpi_matrix": [], "metadata": {"dimensions": [], "row_count": 0},
                "output": [],
                "qc": {"qc1": False, "qc1_detail": "No bridge rows produced — check column mapping"},
                "row_count": len(df),
            })

        # IMPORTANT: run_mrr_workflow's own output ALWAYS uses the fixed
        # column names 'Product' / 'Channel' / 'Region' (Step 1 always
        # normalizes into these, regardless of what the raw file's original
        # header was called). The frontend, however, sends dimension_cols as
        # the ORIGINAL raw header names (e.g. fieldMap.product might be
        # "Product Name" or "SKU"). Passing that raw header straight through
        # would silently fail to match any column in the bridge output and
        # dimension slicing would do nothing. Resolve dimension_cols by TYPE
        # instead, using the bridge's actual fixed column names.
        dims_resolved = []
        if has_product: dims_resolved.append('Product')
        if has_channel: dims_resolved.append('Channel')
        if has_region:  dims_resolved.append('Region')

        # Raw engine output — exact columns the frontend's effectiveByPeriod
        # PATH A / CSV export expect (Date, Bridge Classification, Bridge
        # Value, Month Lookback, etc.) — kept BEFORE load_bridge_file's
        # internal renaming.
        output_records = df_to_records(bridge_engine_df)

        # revenue_unit='raw' here is deliberate: run_mrr_workflow already
        # applied the user's chosen revenue_unit conversion in Step 1.
        # load_bridge_file() defaults to 'thousands' and would silently
        # divide every value by 1000 again if not overridden explicitly.
        loaded = load_bridge_file(bridge_engine_df, tool_type=tool_type, revenue_unit='raw')

        response = build_bridge_response(
            loaded,
            customer_col   = 'Customer_ID',
            dimension_cols = dims_resolved,
            lookbacks      = lb_list,
            year_filter    = year_filter or None,
            period_type    = period_type,
            n_movers       = n_movers,
            n_customers    = n_customers,
            output_records = output_records,
        )
        response["row_count"] = len(df)
        return JSONResponse(content=response)
    except Exception as e:
        logger.error(f"/api/mrr/analyze error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bridge/analyze")
async def analyze_bridge_output(
    file:           UploadFile    = File(...),
    tool_type:      str           = Form("MRR"),
    customer_col:   str           = Form("Customer_ID"),
    revenue_unit:   str           = Form("raw"),
    lookbacks:      str           = Form("[1,3,12]"),
    dimension_cols: str           = Form("[]"),
    year_filter:    str           = Form(""),
    period_type:    str           = Form("Annual"),
    n_movers:       int           = Form(30),
    n_customers:    int           = Form(10),
    modules:        str           = Form("[]"),   # accepted for forward-compat; all modules always computed
):
    """
    Handles the case where the uploaded file is ALREADY a pre-classified
    bridge output (has its own Bridge Classification / Bridge Value columns
    from a prior Alteryx or engine run), rather than raw transactional data.
    Frontend routes here when isBridgeOutput=true. This endpoint previously
    did not exist at all — any request to it 404'd silently into the
    frontend's generic "Analysis failed" catch block.
    """
    try:
        df = read_upload(file)
        lb_list = json.loads(lookbacks) if isinstance(lookbacks, str) else lookbacks
        dims    = json.loads(dimension_cols) if isinstance(dimension_cols, str) else dimension_cols
        logger.info(f"/api/bridge/analyze rows={len(df)} cols={list(df.columns)[:8]} lookbacks={lb_list}")

        output_records = df_to_records(df)

        loaded = load_bridge_file(df, tool_type=tool_type, revenue_unit=revenue_unit)
        if loaded is None or loaded.empty:
            return JSONResponse(content={
                "bridge": {}, "pivot": {}, "top_movers": {}, "top_customers": [],
                "kpi_matrix": [], "metadata": {"dimensions": dims, "row_count": 0},
                "output": output_records,
                "qc": {"qc1": False, "qc1_detail": "Could not parse bridge columns from this file"},
            })

        response = build_bridge_response(
            loaded,
            customer_col   = customer_col,
            dimension_cols = dims,
            lookbacks      = lb_list,
            year_filter    = year_filter or None,
            period_type    = period_type,
            n_movers       = n_movers,
            n_customers    = n_customers,
            output_records = output_records,
        )
        return JSONResponse(content=response)
    except Exception as e:
        logger.error(f"/api/bridge/analyze error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── ACV / Contract Analytics ───────────────────────────────────────────────────

@app.post("/api/acv/analyze")
async def analyze_acv(
    file:             UploadFile    = File(...),
    customer_col:     str           = Form("Customer"),
    start_col:        str           = Form("Contract Start Date"),
    end_col:          str           = Form("Contract End Date"),
    tcv_col:          str           = Form("TCV"),
    order_date_col:   Optional[str] = Form(None),
    product_col:      Optional[str] = Form(None),
    channel_col:      Optional[str] = Form(None),
    region_col:       Optional[str] = Form(None),
    quantity_col:     Optional[str] = Form(None),
    revenue_unit:     str           = Form("TCV"),
    lookbacks:        str           = Form("[1,3,12]"),
    n_movers:         int           = Form(30),
):
    try:
        df = read_upload(file)
        logger.info(f"/api/acv/analyze rows={len(df)} rev_unit={revenue_unit}")

        lb_list = json.loads(lookbacks) if isinstance(lookbacks, str) else lookbacks

        result = run_acv_workflow(
            df,
            customer_col   = customer_col,
            start_col      = start_col,
            end_col        = end_col,
            tcv_col        = tcv_col,
            order_date_col = order_date_col if order_date_col and order_date_col in df.columns else None,
            product_col    = product_col    if product_col    and product_col    in df.columns else None,
            channel_col    = channel_col    if channel_col    and channel_col    in df.columns else None,
            region_col     = region_col     if region_col     and region_col     in df.columns else None,
            quantity_col   = quantity_col   if quantity_col   and quantity_col   in df.columns else None,
            lookback_months = lb_list,
            revenue_unit   = revenue_unit,
        )

        # IMPORTANT: run_acv_workflow already returns "bridge", "customer_bridge",
        # "acv", and "bookings" as pre-aggregated LISTS of dicts (not raw
        # DataFrames) — this is part of the OOM fix (period/customer summaries
        # computed inside the engine to avoid serializing the full raw bridge
        # table). Do NOT pass these through df_to_records() as if they were
        # DataFrames; df_to_records now tolerates both shapes defensively,
        # but we also fetch them correctly here.
        bridge                   = result.get("bridge", [])
        customer_bridge          = result.get("customer_bridge", [])   # was missing from response entirely
        acv_tbl                  = result.get("acv", [])
        bookings                 = result.get("bookings", [])
        qc                       = result.get("qc", {})
        # Risk & Opportunity now scored per-PERIOD (not one auto-picked
        # snapshot). summary = full population counts/ACV per period, never
        # capped — used for header stats. detail = top-N customers per
        # period per side, capped to keep payload safe (same principle as
        # customer_bridge's own cap).
        risk_opportunity_summary = result.get("risk_opportunity_summary", [])
        risk_opportunity_detail  = result.get("risk_opportunity_detail", [])

        logger.info(f"/api/acv/analyze bridge_rows={len(bridge)} qc={qc}")

        return JSONResponse(content={
            "bridge":                   df_to_records(bridge),
            "customer_bridge":          df_to_records(customer_bridge),
            "acv_table":                df_to_records(acv_tbl),
            "bookings":                 df_to_records(bookings),
            "qc":                       qc,
            "risk_opportunity_summary": df_to_records(risk_opportunity_summary),
            "risk_opportunity_detail":  df_to_records(risk_opportunity_detail),
            "row_count":                len(df),
        })
    except Exception as e:
        logger.error(f"/api/acv/analyze error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Bridge table (pre-computed output) ────────────────────────────────────────

@app.post("/api/bridge/load")
async def load_bridge(
    file:       UploadFile    = File(...),
    period:     Optional[str] = Form(None),
    lookback:   int           = Form(12),
    dimension:  Optional[str] = Form(None),
    filter_val: Optional[str] = Form(None),
):
    try:
        df = read_upload(file)
        bridge_df = load_bridge_file(df)
        filtered  = filter_bridge_df(bridge_df, period=period, lookback=lookback,
                                     dimension=dimension, filter_val=filter_val)
        summary   = get_bridge_summary(filtered)
        by_dim    = get_bridge_by_dimension(filtered) if dimension else {}
        fy_sum    = get_fy_summary(bridge_df)
        movers    = get_top_movers(filtered)
        customers = get_top_customers(filtered)
        kpi       = get_kpi_matrix(bridge_df)
        full      = build_full_bridge_response(bridge_df, period, lookback)

        return JSONResponse(content={
            "summary":      summary,
            "by_dimension": by_dim,
            "fy_summary":   fy_sum,
            "top_movers":   movers,
            "top_customers": customers,
            "kpi_matrix":   kpi,
            "full_bridge":  full,
        })
    except Exception as e:
        logger.error(f"/api/bridge/load error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Cohort Analytics ───────────────────────────────────────────────────────────

@app.post("/api/cohort/analyze")
async def analyze_cohort(
    file:         UploadFile    = File(...),
    customer_col: str           = Form("Customer"),
    date_col:     str           = Form("Date"),
    revenue_col:  str           = Form("MRR"),
    product_col:  Optional[str] = Form(None),
    region_col:   Optional[str] = Form(None),
    cohort_grain: str           = Form("monthly"),
    periods:      int           = Form(24),
):
    try:
        from services.cohort_service import run_cohort_analysis
        df     = read_upload(file)
        result = run_cohort_analysis(
            df,
            customer_col = customer_col,
            date_col     = date_col,
            revenue_col  = revenue_col,
            product_col  = product_col if product_col and product_col in df.columns else None,
            region_col   = region_col  if region_col  and region_col  in df.columns else None,
            grain        = cohort_grain,
            periods      = periods,
        )
        return JSONResponse(content=json.loads(json.dumps(result, default=safe_json)))
    except Exception as e:
        logger.error(f"/api/cohort/analyze error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── AI Narrative (Groq + Gemini fallback) ─────────────────────────────────────

@app.post("/api/ai/narrative")
async def ai_narrative(request: Request):
    try:
        body    = await request.json()
        prompt  = body.get("prompt", "")
        context = body.get("context", {})

        system = (
            "You are a senior revenue intelligence analyst at a PE firm. "
            "Provide sharp, CFO-grade insights in 2-3 sentences. "
            "Be specific about drivers and risks. No fluff."
        )
        user_msg = f"Context: {json.dumps(context)}\n\nQuestion: {prompt}"

        # Try Groq first
        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    json={
                        "model": "llama3-8b-8192",
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user",   "content": user_msg},
                        ],
                        "max_tokens": 300,
                    },
                )
                if resp.status_code == 200:
                    return {"narrative": resp.json()["choices"][0]["message"]["content"]}

        # Gemini fallback
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={gemini_key}",
                    json={"contents": [{"parts": [{"text": f"{system}\n\n{user_msg}"}]}]},
                )
                if resp.status_code == 200:
                    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                    return {"narrative": text}

        return {"narrative": "AI narrative unavailable. Check API keys."}
    except Exception as e:
        logger.error(f"/api/ai/narrative error: {e}")
        return {"narrative": "Unable to generate narrative at this time."}


# ── Stripe Payments ────────────────────────────────────────────────────────────

@app.post("/api/stripe/create-checkout")
async def create_checkout(request: Request):
    try:
        body    = await request.json()
        plan    = body.get("plan", "pro")
        user_id = body.get("user_id", "")
        email   = body.get("email", "")
        price   = PLAN_PRICES.get(plan) or STRIPE_PRICE_ID

        if not price:
            raise HTTPException(status_code=400, detail="No price configured for plan")

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price, "quantity": 1}],
            mode="subscription",
            success_url=f"{FRONTEND_URL}/dashboard?upgraded=1",
            cancel_url=f"{FRONTEND_URL}/pricing?cancelled=1",
            client_reference_id=user_id,
            customer_email=email or None,
            metadata={"user_id": user_id, "plan": plan},
        )
        return {"url": session.url, "session_id": session.id}
    except stripe.StripeError as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"/api/stripe/create-checkout error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stripe/portal")
async def stripe_portal(request: Request):
    try:
        body        = await request.json()
        customer_id = body.get("customer_id", "")
        if not customer_id:
            raise HTTPException(status_code=400, detail="customer_id required")

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{FRONTEND_URL}/dashboard",
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig     = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle subscription events
    if event["type"] == "checkout.session.completed":
        session  = event["data"]["object"]
        user_id  = session.get("client_reference_id", "")
        plan     = session.get("metadata", {}).get("plan", "pro")
        sub_id   = session.get("subscription", "")
        customer = session.get("customer", "")
        logger.info(f"Checkout complete: user={user_id} plan={plan}")

        if SUPABASE_URL and SUPABASE_SERVICE_KEY and user_id:
            try:
                async with httpx.AsyncClient() as client:
                    await client.patch(
                        f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                        headers={
                            "apikey":        SUPABASE_SERVICE_KEY,
                            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                            "Content-Type":  "application/json",
                        },
                        json={
                            "plan":            plan,
                            "stripe_customer": customer,
                            "stripe_sub":      sub_id,
                        },
                    )
            except Exception as e:
                logger.error(f"Supabase update failed: {e}")

    elif event["type"] in ("customer.subscription.deleted", "customer.subscription.updated"):
        sub    = event["data"]["object"]
        status = sub.get("status", "")
        logger.info(f"Subscription {event['type']}: status={status}")

    return {"received": True}


# ── Supabase profile helpers ───────────────────────────────────────────────────

@app.get("/api/user/plan")
async def get_user_plan(user_id: str):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"plan": "free"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=plan",
                headers={
                    "apikey":        SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                },
            )
            data = resp.json()
            return {"plan": data[0].get("plan", "free") if data else "free"}
    except Exception as e:
        logger.error(f"/api/user/plan error: {e}")
        return {"plan": "free"}
