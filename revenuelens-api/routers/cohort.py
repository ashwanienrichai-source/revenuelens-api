from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import List, Optional
import pandas as pd
import numpy as np
import io
import json
from services.cohort_service import run_cohort_analysis

router = APIRouter()


def load_dataframe(file: UploadFile) -> pd.DataFrame:
    """Load CSV or Excel into DataFrame."""
    content = file.file.read()
    name    = file.filename.lower()
    try:
        if name.endswith(".csv"):
            try:    return pd.read_csv(io.BytesIO(content), encoding="utf-8")
            except: return pd.read_csv(io.BytesIO(content), encoding="latin1")
        elif name.endswith((".xlsx", ".xls")):
            return pd.read_excel(io.BytesIO(content), engine="openpyxl")
        else:
            raise HTTPException(status_code=400, detail="Unsupported file format. Use CSV or Excel.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {str(e)}")


@router.post("/columns")
async def get_columns(file: UploadFile = File(...)):
    """Return column names from uploaded file."""
    df = load_dataframe(file)
    df.columns = df.columns.str.strip()
    preview = df.head(3).replace({np.nan: None}).to_dict(orient="records")
    return {
        "columns": df.columns.tolist(),
        "row_count": len(df),
        "preview": preview,
    }


@router.post("/analyze")
async def analyze_cohort(
    file:                   UploadFile = File(...),
    metric:                 str        = Form(...),
    customer_col:           str        = Form(...),
    date_col:               str        = Form(...),
    fiscal_col:             str        = Form("None"),
    individual_cols:        str        = Form("[]"),   # JSON array
    hierarchies:            str        = Form("[]"),   # JSON array of arrays
    cohort_types:           str        = Form('["SG"]'),
    period_filter:          str        = Form("all"),  # all | latest | fiscal_year
    selected_fiscal_year:   str        = Form(""),
):
    """
    Run cohort analysis and return structured JSON results.
    All the logic is in services/cohort_service.py — exact same as cohort_app.py.
    """
    # Parse JSON fields
    try:
        ind_cols  = json.loads(individual_cols)
        hier_list = json.loads(hierarchies)
        ct_list   = json.loads(cohort_types)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in form fields")

    # Load file
    df = load_dataframe(file)
    df.columns = df.columns.str.strip()

    # Validate required columns
    for col in [metric, customer_col, date_col]:
        if col not in df.columns:
            raise HTTPException(status_code=400, detail=f"Column '{col}' not found in dataset")

    # Convert metric to numeric
    df[metric] = pd.to_numeric(df[metric], errors="coerce").fillna(0)

    # Run analysis
    try:
        result = run_cohort_analysis(
            df=df,
            metric=metric,
            customer_col=customer_col,
            date_col=date_col,
            fiscal_col=fiscal_col if fiscal_col != "None" else None,
            individual_cols=ind_cols,
            hierarchies=hier_list,
            cohort_types=ct_list,
            period_filter=period_filter,
            selected_fiscal_year=selected_fiscal_year if selected_fiscal_year else None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    return result
