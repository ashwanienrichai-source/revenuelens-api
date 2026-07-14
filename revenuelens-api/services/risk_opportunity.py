"""
risk_opportunity.py — Deterministic Churn Risk & Expansion Opportunity scoring.
v2: scores EVERY period (not a single auto-picked "material" snapshot),
    vectorized across the whole table (not a per-customer Python loop),
    returns TWO outputs:
      - summary: FULL population counts + total ACV per period, per side
                 (risk / expansion) — used for header stats, never capped.
      - detail:  TOP N customers per period, per side, ONLY rows that are
                 not "Low" — this is what keeps the payload small regardless
                 of total customer count, same principle as customer_bridge's
                 existing MAX_CUSTOMER_BRIDGE_ROWS cap.

DESIGN PRINCIPLES (unchanged from v1 — no black box, no ML, no randomness):
  - Every score is built ENTIRELY from bridge classifications already
    computed by the ACV engine.
  - Runs on the FULL customer set for every period (summary side); only the
    per-period DETAIL rows are capped, and only after being scored, so no
    customer's existence is ever hidden — only how many get full reason
    strings and appear in the UI cards.
  - Every score decomposes into named, human-readable driver reasons —
    computed only for the (small) capped detail subset, not the full grid,
    to keep this cheap regardless of dataset size.

SCORING WINDOWS (unchanged from v1):
  - Risk window:      trailing 3 periods
  - Expansion window:  trailing 6 periods

PERFORMANCE NOTE: the per-customer, per-period rolling calculations are done
with pandas groupby().rolling()/shift() — vectorized across the ENTIRE dense
Customer x Date grid at once. This mirrors the same cross-join + reindex
pattern already used and proven fast in acv_workflow_engine.py's Step 6 dense
series construction, deliberately avoiding the per-customer Python loop
pattern that caused the original OOM/slowness problems earlier in this
project. Reason-string construction (which needs per-row Python logic) is
run ONLY on the capped detail subset (at most top_n * 2 * n_periods rows),
never on the full grid.
"""

import pandas as pd
import numpy as np

RISK_WINDOW = 3
EXPANSION_WINDOW = 6
DEFAULT_TOP_N = 50

CLS_NEW_LOGO  = 'New Logo'
CLS_CROSS     = 'Cross-sell'
CLS_RETURNING = 'Returning'
CLS_CHURN     = 'Churn'
CLS_CHURN_P   = 'Churn Partial'
CLS_LAPSED    = 'Lapsed'
CLS_UPSELL    = 'Upsell'
CLS_DOWNSELL  = 'Downsell'
CLS_ADDON     = 'Add on'
CLS_PRIOR     = 'Prior ACV'
CLS_ENDING    = 'Ending ACV'
CLS_EXPIRY    = 'Expiry Pool'

NEEDED_COLS = [CLS_PRIOR, CLS_ENDING, CLS_EXPIRY, CLS_CHURN, CLS_CHURN_P,
               CLS_DOWNSELL, CLS_LAPSED, CLS_UPSELL, CLS_CROSS, CLS_ADDON,
               CLS_NEW_LOGO]


def _scale_pct(series: pd.Series, cap: float) -> pd.Series:
    """Vectorized version of the v1 _scale_pct: linearly scale a decimal
    percentage series to 0-100, where `cap` maps to 100. Clamped."""
    if cap <= 0:
        return pd.Series(0.0, index=series.index)
    return (series / cap * 100.0).clip(lower=0.0, upper=100.0)


def _label_series(score: pd.Series, kind: str) -> pd.Series:
    if kind == 'risk':
        bins   = [-0.01, 25, 50, 75, 100.01]
        labels = ['Low', 'Watch', 'Elevated', 'Critical']
    else:
        bins   = [-0.01, 25, 50, 75, 100.01]
        labels = ['Low', 'Moderate', 'Good', 'High']
    return pd.cut(score, bins=bins, labels=labels).astype(str)


def _build_dense_grid(lb12: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot to Customer x Date x Classification, then reindex to a full dense
    Customer x (every date in the dataset) grid, filling gaps with 0 — same
    cross-join + reindex approach already used in the main engine's Step 6,
    needed here so trailing-window rolling sums are computed over real
    calendar months, not just a customer's own sparse active rows.
    """
    pivot = lb12.pivot_table(
        index=['Customer', 'Date'], columns='Bridge Classification',
        values='Bridge Value', aggfunc='sum', fill_value=0.0
    )
    for c in NEEDED_COLS:
        if c not in pivot.columns:
            pivot[c] = 0.0
    pivot = pivot[NEEDED_COLS].reset_index()

    all_dates = pd.Index(sorted(lb12['Date'].unique()), name='Date')
    customers = pd.Index(pivot['Customer'].unique(), name='Customer')

    full_index = pd.MultiIndex.from_product([customers, all_dates])
    dense = (pivot.set_index(['Customer', 'Date'])
                   .reindex(full_index, fill_value=0.0)
                   .reset_index())
    dense = dense.sort_values(['Customer', 'Date']).reset_index(drop=True)
    return dense


def _compute_product_coverage(lb12: pd.DataFrame, dense: pd.DataFrame, has_product: bool) -> pd.Series:
    """Vectorized per-Date peer product-count median vs. each customer's own
    product count at that date. Returns a Series aligned to `dense`'s index
    giving the 0-100 scaled coverage-gap score (0 if has_product is False)."""
    if not has_product:
        return pd.Series(0.0, index=dense.index)

    ending = lb12[(lb12['Bridge Classification'] == CLS_ENDING) & (lb12['Bridge Value'] != 0)]
    prod_counts = ending.groupby(['Customer', 'Date'])['Product'].nunique().reset_index(name='n_products')
    peer_median = prod_counts.groupby('Date')['n_products'].median().reset_index(name='peer_median')

    merged = dense.merge(prod_counts, on=['Customer', 'Date'], how='left')
    merged = merged.merge(peer_median, on='Date', how='left')
    merged['n_products']  = merged['n_products'].fillna(0)
    merged['peer_median'] = merged['peer_median'].fillna(0)

    gap = (merged['peer_median'] - merged['n_products']).clip(lower=0)
    return _scale_pct(gap / 3.0, cap=1.0)


def _vectorized_scores(bridge: pd.DataFrame, has_product: bool) -> pd.DataFrame:
    """
    Core vectorized computation. Returns `dense` with RiskScore, RiskLabel,
    ExpansionScore, ExpansionLabel, and all intermediate component columns
    needed to build reason strings later (for the capped subset only).
    """
    lb12 = bridge[bridge['Month Lookback'] == 12].copy()
    if lb12.empty:
        return pd.DataFrame()

    dense = _build_dense_grid(lb12)
    if dense.empty:
        return dense

    grp = dense.groupby('Customer')

    # ── Risk window (trailing 3 periods) ───────────────────────────────
    neg_component = dense[CLS_DOWNSELL].abs() + dense[CLS_CHURN_P].abs() + dense[CLS_LAPSED].abs()
    dense['_risk_neg_sum'] = neg_component.groupby(dense['Customer']).transform(
        lambda s: s.rolling(RISK_WINDOW, min_periods=1).sum())

    pos_flag = ((dense[CLS_UPSELL] > 0) | (dense[CLS_ADDON] > 0) |
                (dense[CLS_CROSS] > 0) | (dense[CLS_NEW_LOGO] > 0)).astype(float)
    dense['_risk_pos_present'] = (pos_flag.groupby(dense['Customer']).transform(
        lambda s: s.rolling(RISK_WINDOW, min_periods=1).max()) > 0)

    dense['_ending_lag_risk'] = grp[CLS_ENDING].shift(RISK_WINDOW - 1).fillna(0.0)

    # ── Expansion window (trailing 6 periods) ──────────────────────────
    pos_component = dense[CLS_UPSELL] + dense[CLS_CROSS] + dense[CLS_ADDON]
    dense['_exp_pos_sum'] = pos_component.groupby(dense['Customer']).transform(
        lambda s: s.rolling(EXPANSION_WINDOW, min_periods=1).sum())

    neg_flag = ((dense[CLS_DOWNSELL] < 0) | (dense[CLS_CHURN_P] < 0) |
                (dense[CLS_LAPSED] != 0) | (dense[CLS_CHURN] < 0)).astype(float)
    dense['_exp_neg_present'] = (neg_flag.groupby(dense['Customer']).transform(
        lambda s: s.rolling(EXPANSION_WINDOW, min_periods=1).max()) > 0)

    prior_safe = dense[CLS_PRIOR].clip(lower=1.0)

    # ── CHURN RISK ────────────────────────────────────────────────────
    a_risk = _scale_pct(dense['_risk_neg_sum'] / prior_safe, cap=0.30)

    slope = (dense[CLS_ENDING] - dense['_ending_lag_risk']) / dense['_ending_lag_risk'].clip(lower=1.0)
    b_risk = np.where(slope < 0, _scale_pct(-slope, cap=0.30), 0.0)

    c_risk_pct = dense[CLS_EXPIRY] / prior_safe
    c_discount = np.where(dense['_risk_pos_present'], 0.3, 1.0)
    c_risk = _scale_pct(c_risk_pct, cap=0.50) * c_discount

    dense['RiskScore'] = (0.45 * a_risk + 0.25 * b_risk + 0.30 * c_risk).round(1)
    dense['RiskLabel'] = _label_series(dense['RiskScore'], 'risk')
    dense['_a_risk'], dense['_b_risk'], dense['_c_risk'] = a_risk, b_risk, c_risk
    dense['_c_risk_pct'] = c_risk_pct
    dense['_slope'] = slope

    # ── EXPANSION OPPORTUNITY ────────────────────────────────────────
    a_exp_pct = dense['_exp_pos_sum'] / prior_safe
    a_discount = np.where(dense['_exp_neg_present'], 0.3, 1.0)
    a_exp = _scale_pct(a_exp_pct, cap=0.30) * a_discount

    b_exp_pct = dense[CLS_EXPIRY] / prior_safe
    b_mult = np.where(dense['_exp_pos_sum'] > 0, 1.0, 0.3)
    b_exp = _scale_pct(b_exp_pct, cap=0.50) * b_mult

    c_exp = _compute_product_coverage(lb12, dense, has_product)
    if has_product:
        w_a, w_b, w_c = 0.45, 0.30, 0.25
    else:
        w_a, w_b, w_c = 0.60, 0.40, 0.0

    dense['ExpansionScore'] = (w_a * a_exp + w_b * b_exp + w_c * c_exp).round(1)
    dense['ExpansionLabel'] = _label_series(dense['ExpansionScore'], 'expansion')
    dense['_a_exp'], dense['_b_exp'], dense['_c_exp'] = a_exp, b_exp, c_exp
    dense['_a_exp_pct'], dense['_b_exp_pct'] = a_exp_pct, b_exp_pct

    # Exclude customers with no real presence at this date (both Prior and
    # Ending ACV are zero — i.e. not actually part of the book at that point)
    active_mask = (dense[CLS_PRIOR] != 0) | (dense[CLS_ENDING] != 0)
    return dense[active_mask].reset_index(drop=True)


def _build_reasons_for_subset(sub: pd.DataFrame, has_product: bool) -> pd.DataFrame:
    """
    Reason-string construction — deliberately runs ONLY on a small capped
    subset (top_n rows per period per side), not the full dense grid, so
    this stays cheap regardless of total dataset size.
    """
    risk_reasons = []
    exp_reasons  = []
    for _, r in sub.iterrows():
        rr = []
        if r['_a_risk'] > 0:
            pct = r['_risk_neg_sum'] / max(r[CLS_PRIOR], 1.0) * 100
            rr.append((r['_a_risk'] * 0.45, f"Lost {pct:.1f}% of Prior ACV to downsell/churn-partial/lapsed in last {RISK_WINDOW} periods"))
        if r['_b_risk'] > 0:
            rr.append((r['_b_risk'] * 0.25, f"Ending ACV declined {abs(r['_slope'])*100:.1f}% over last {RISK_WINDOW} periods"))
        if r['_c_risk'] > 0:
            note = " (no recent positive signal)" if not r['_risk_pos_present'] else " (some recent upsell activity)"
            rr.append((r['_c_risk'] * 0.30, f"Expiry Pool is {r['_c_risk_pct']*100:.1f}% of Prior ACV{note}"))
        rr.sort(key=lambda x: -x[0])
        risk_reasons.append([x[1] for x in rr][:3])

        er = []
        w_a, w_b = (0.45, 0.30) if has_product else (0.60, 0.40)
        if r['_a_exp'] > 0:
            er.append((r['_a_exp'] * w_a, f"Upsell/Cross-sell/Add-on totaled {r['_a_exp_pct']*100:.1f}% of Prior ACV over last {EXPANSION_WINDOW} periods"))
        if r['_b_exp'] > 0:
            note = " with existing positive momentum" if r['_exp_pos_sum'] > 0 else ""
            er.append((r['_b_exp'] * w_b, f"Expiry Pool of {r['_b_exp_pct']*100:.1f}% of Prior ACV due for renewal{note}"))
        if has_product and r['_c_exp'] > 0:
            er.append((r['_c_exp'] * 0.25, "Below peer median product coverage"))
        er.sort(key=lambda x: -x[0])
        exp_reasons.append([x[1] for x in er][:3])

    sub = sub.copy()
    sub['RiskReasons'] = risk_reasons
    sub['ExpansionReasons'] = exp_reasons
    return sub


def compute_risk_opportunity(bridge: pd.DataFrame, has_product: bool,
                              top_n: int = DEFAULT_TOP_N):
    """
    bridge: the FULL bridge DataFrame (all customers, all lookbacks).
    has_product: whether the dataset has a real product dimension.
    top_n: max customers shown per period, per side (risk / expansion) in
           the DETAIL output. The SUMMARY output is never capped.

    Returns (summary_df, detail_df):
      summary_df: one row per Date — RiskCount, RiskACV, ExpansionCount,
                  ExpansionACV — computed from the FULL scored population.
      detail_df:  top_n risk rows + top_n expansion rows per Date (only
                  non-"Low" labels), with Customer, PriorACV, EndingACV,
                  RiskScore/Label/Reasons, ExpansionScore/Label/Reasons.
    """
    dense = _vectorized_scores(bridge, has_product)
    if dense.empty:
        return pd.DataFrame(), pd.DataFrame()

    RISK_ALERT_LABELS = {'Elevated', 'Critical'}
    EXP_ALERT_LABELS  = {'Good', 'High'}

    is_risk_alert = dense['RiskLabel'].isin(RISK_ALERT_LABELS)
    is_exp_alert  = dense['ExpansionLabel'].isin(EXP_ALERT_LABELS)

    dense['_is_risk_alert'] = is_risk_alert
    dense['_is_exp_alert']  = is_exp_alert

    # ── Summary: FULL population, grouped by Date ───────────────────────
    summary = dense.groupby('Date').agg(
        RiskCount=('_is_risk_alert', 'sum'),
        ExpansionCount=('_is_exp_alert', 'sum'),
    ).reset_index()

    risk_acv = dense[is_risk_alert].groupby('Date')[CLS_ENDING].sum().rename('RiskACV')
    exp_acv  = dense[is_exp_alert].groupby('Date')[CLS_ENDING].sum().rename('ExpansionACV')
    summary = summary.merge(risk_acv, on='Date', how='left').merge(exp_acv, on='Date', how='left')
    summary[['RiskACV', 'ExpansionACV']] = summary[['RiskACV', 'ExpansionACV']].fillna(0.0)
    summary['RiskCount'] = summary['RiskCount'].astype(int)
    summary['ExpansionCount'] = summary['ExpansionCount'].astype(int)

    # ── Detail: top_n per Date per side, non-"Low" only ────────────────
    risk_pool = dense[is_risk_alert].copy()
    exp_pool  = dense[is_exp_alert].copy()

    risk_top = (risk_pool.sort_values('RiskScore', ascending=False)
                .groupby('Date', group_keys=False).head(top_n))
    exp_top  = (exp_pool.sort_values('ExpansionScore', ascending=False)
                .groupby('Date', group_keys=False).head(top_n))

    # Union (a customer can appear in both lists for the same date if,
    # unusually, both scores are elevated — rare given the discounting
    # logic, but not excluded by construction)
    detail_idx = risk_top.index.union(exp_top.index)
    detail = dense.loc[detail_idx].copy()
    detail = _build_reasons_for_subset(detail, has_product)

    keep_cols = ['Customer', 'Date', CLS_PRIOR, CLS_ENDING,
                 'RiskScore', 'RiskLabel', 'RiskReasons',
                 'ExpansionScore', 'ExpansionLabel', 'ExpansionReasons']
    detail = detail[keep_cols].rename(columns={CLS_PRIOR: 'PriorACV', CLS_ENDING: 'EndingACV'})
    detail = detail.sort_values(['Date', 'RiskScore'], ascending=[True, False]).reset_index(drop=True)

    return summary, detail
