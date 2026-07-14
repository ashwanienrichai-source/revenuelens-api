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

@app.post("/api/mrr/analyze")
async def analyze_mrr(
    file:          UploadFile    = File(...),
    customer_col:  str           = Form("Customer"),
    date_col:      str           = Form("Date"),
    revenue_col:   str           = Form("MRR"),
    product_col:   Optional[str] = Form(None),
    channel_col:   Optional[str] = Form(None),
    region_col:    Optional[str] = Form(None),
    quantity_col:  Optional[str] = Form(None),
    lookback:      int           = Form(3),
    revenue_unit:  str           = Form("raw"),
    tool_type:     str           = Form("MRR"),
):
    try:
        df = read_upload(file)
        logger.info(f"/api/mrr/analyze rows={len(df)} cols={list(df.columns)[:6]}")

        result = run_mrr_workflow(
            df,
            customer_col  = customer_col,
            date_col      = date_col,
            revenue_col   = revenue_col,
            product_col   = product_col   if product_col  and product_col  in df.columns else None,
            channel_col   = channel_col   if channel_col  and channel_col  in df.columns else None,
            region_col    = region_col    if region_col   and region_col   in df.columns else None,
            quantity_col  = quantity_col  if quantity_col and quantity_col in df.columns else None,
            lookback_months = [lookback],
            revenue_unit  = revenue_unit,
        )

        return JSONResponse(content={
            "bridge":   df_to_records(result.get("bridge",   pd.DataFrame())),
            "bookings": df_to_records(result.get("bookings", pd.DataFrame())),
            "qc":       result.get("qc", {}),
            "row_count": len(df),
        })
    except Exception as e:
        logger.error(f"/api/mrr/analyze error: {e}", exc_info=True)
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
        bridge           = result.get("bridge", [])
        customer_bridge  = result.get("customer_bridge", [])   # was missing from response entirely
        acv_tbl          = result.get("acv", [])
        bookings         = result.get("bookings", [])
        qc               = result.get("qc", {})
        risk_opportunity = result.get("risk_opportunity", [])   # deterministic churn risk / expansion scoring, full customer set

        logger.info(f"/api/acv/analyze bridge_rows={len(bridge)} qc={qc}")

        return JSONResponse(content={
            "bridge":           df_to_records(bridge),
            "customer_bridge":  df_to_records(customer_bridge),
            "acv_table":        df_to_records(acv_tbl),
            "bookings":         df_to_records(bookings),
            "qc":               qc,
            "risk_opportunity": df_to_records(risk_opportunity),
            "row_count":        len(df),
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
