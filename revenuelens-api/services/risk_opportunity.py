"""
risk_opportunity.py — Deterministic Churn Risk & Expansion Opportunity scoring.

DESIGN PRINCIPLES (per product philosophy — no black box, no ML, no randomness):
  - Every score is built ENTIRELY from bridge classifications already computed
    by the ACV engine (Upsell, Downsell, Churn, Churn Partial, Lapsed, Add on,
    Cross-sell, Prior ACV, Ending ACV, Expiry Pool). Nothing is inferred or
    predicted — it's a weighted read of what already happened.
  - Runs on the FULL customer set (not the top-200-by-movement subset used
    elsewhere for payload size), so no customer is silently excluded.
  - Every score decomposes into named, human-readable driver reasons — a CFO
    can always see WHY an account scored the way it did.
  - Computed for the LATEST period only (v1 scope). Trend-over-time scoring
    is an intentional v2 addition, not built here.

SCORING WINDOWS:
  - Risk window:      trailing 3 periods (recent negative signal should dominate)
  - Expansion window:  trailing 6 periods (momentum builds more slowly)

CHURN RISK SCORE (0-100, higher = more risk):
  A (45%) Realized negative movement — |Downsell|+|Churn Partial|+|Lapsed|
          over trailing 3 periods, as % of current Prior ACV
  B (25%) Trend decline — negative Ending ACV slope over trailing 3 periods
  C (30%) Unprotected renewal exposure — current Expiry Pool as % of Prior
          ACV, discounted if there's been any positive signal recently
          (an expiry with recent upsell momentum is lower risk than one
          with none)

EXPANSION OPPORTUNITY SCORE (0-100, higher = more opportunity):
  A (45%) Positive momentum — Upsell+Cross-sell+Add on over trailing 6
          periods, as % of current Prior ACV, discounted if there's been
          any negative signal in the same window
  B (30%) Renewal-timing fit — current Expiry Pool as % of Prior ACV,
          weighted up when there's already positive momentum (a renewal
          conversation is a natural moment to also pitch expansion)
  C (25%) Product coverage gap — customer's distinct active product count
          vs. peer median (only computed when product dimension exists;
          weight redistributed to A/B — 60/40 — when it doesn't, matching
          the engine's existing graceful-degradation pattern)
"""

import pandas as pd
import numpy as np

RISK_WINDOW = 3
EXPANSION_WINDOW = 6

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


def _scale_pct(value_pct: float, cap: float) -> float:
    """Linearly scale a decimal percentage (e.g. 0.15 = 15%) to 0-100,
    where `cap` (also decimal) maps to 100. Clamped to [0, 100]."""
    if cap <= 0:
        return 0.0
    return float(np.clip((value_pct / cap) * 100.0, 0.0, 100.0))


def _label(score: float, kind: str) -> str:
    if kind == 'risk':
        if score >= 75: return 'Critical'
        if score >= 50: return 'Elevated'
        if score >= 25: return 'Watch'
        return 'Low'
    else:
        if score >= 75: return 'High'
        if score >= 50: return 'Good'
        if score >= 25: return 'Moderate'
        return 'Low'


def _find_material_latest_period(lb12: pd.DataFrame) -> pd.Timestamp:
    """
    The literal max(Date) in a bridge table can be a near-empty tail period
    (e.g. only residual Expiry Pool entries left, zero active Ending ACV) —
    using it as "current state of the book" would be misleading.

    This mirrors the frontend's existing `materialStart` logic (first period
    where Ending ACV > 1% of peak) but applied to find the LAST material
    period instead of the first.
    """
    ending = lb12[lb12['Bridge Classification'] == CLS_ENDING]
    totals = ending.groupby('Date')['Bridge Value'].sum().sort_index()
    if totals.empty:
        return lb12['Date'].max()
    peak = totals.max()
    threshold = peak * 0.01
    material_dates = totals[totals >= threshold].index
    if len(material_dates) == 0:
        return totals.index.max()
    return material_dates.max()


def compute_risk_opportunity(bridge: pd.DataFrame, has_product: bool, as_of_period=None) -> pd.DataFrame:
    """
    bridge: the FULL bridge DataFrame (all customers, all lookbacks) as
            produced by run_acv_workflow's Step 13 — columns include
            Customer, Product, Date, Month Lookback, Bridge Classification,
            Bridge Value.
    has_product: whether the dataset has a real product dimension.
    as_of_period: optional pd.Timestamp / date-like — the period to score
            as "current". If None, defaults to the last MATERIAL period
            (see _find_material_latest_period), not simply the last row in
            the data, since that can be a near-empty tail.

    Returns a DataFrame, one row per customer, for the resolved period:
      Customer, Date, PriorACV, EndingACV,
      RiskScore, RiskLabel, RiskReasons (list of str, ranked),
      ExpansionScore, ExpansionLabel, ExpansionReasons (list of str, ranked)
    """
    lb12 = bridge[bridge['Month Lookback'] == 12].copy()
    if lb12.empty:
        return pd.DataFrame()

    all_dates = sorted(lb12['Date'].unique())
    if not all_dates:
        return pd.DataFrame()

    if as_of_period is not None:
        as_of_period = pd.Timestamp(as_of_period)
        eligible_dates = [d for d in all_dates if d <= as_of_period]
        if not eligible_dates:
            return pd.DataFrame()
        latest_date = eligible_dates[-1]
    else:
        latest_date = _find_material_latest_period(lb12)

    latest_idx = all_dates.index(latest_date)
    risk_dates = all_dates[max(0, latest_idx - RISK_WINDOW + 1): latest_idx + 1]
    exp_dates  = all_dates[max(0, latest_idx - EXPANSION_WINDOW + 1): latest_idx + 1]
    # Only need rows within the widest window we use
    window_dates = set(exp_dates) | set(risk_dates)
    recent = lb12[lb12['Date'].isin(window_dates)]

    # Pivot to Customer x Date x Classification (sum, since a customer can
    # have multiple Product/Channel/Region rows collapsing to the same date)
    pivot = recent.pivot_table(
        index=['Customer', 'Date'], columns='Bridge Classification',
        values='Bridge Value', aggfunc='sum', fill_value=0.0
    )

    needed_cols = [CLS_PRIOR, CLS_ENDING, CLS_EXPIRY, CLS_CHURN, CLS_CHURN_P,
                   CLS_DOWNSELL, CLS_LAPSED, CLS_UPSELL, CLS_CROSS, CLS_ADDON,
                   CLS_NEW_LOGO]
    for c in needed_cols:
        if c not in pivot.columns:
            pivot[c] = 0.0

    customers = pivot.index.get_level_values('Customer').unique()

    def get(cust, date, col):
        try:
            return float(pivot.loc[(cust, date), col])
        except KeyError:
            return 0.0

    # Product coverage (only meaningful if has_product) — computed from the
    # UN-pivoted recent frame at the latest date, since Product detail is
    # collapsed away by the pivot above.
    product_counts = {}
    peer_median_products = None
    if has_product:
        at_latest = recent[
            (recent['Date'] == latest_date) &
            (recent['Bridge Classification'] == CLS_ENDING) &
            (recent['Bridge Value'] != 0)
        ]
        pc = at_latest.groupby('Customer')['Product'].nunique()
        product_counts = pc.to_dict()
        peer_median_products = float(pc.median()) if len(pc) else 0.0

    rows = []
    for cust in customers:
        prior_acv  = get(cust, latest_date, CLS_PRIOR)
        ending_acv = get(cust, latest_date, CLS_ENDING)
        expiry_pool = get(cust, latest_date, CLS_EXPIRY)
        prior_safe = max(prior_acv, 1.0)

        # ── Trailing sums over risk window (3 periods) ────────────────────
        risk_neg_sum = 0.0
        risk_pos_present = False
        for d in risk_dates:
            risk_neg_sum += abs(get(cust, d, CLS_DOWNSELL)) + abs(get(cust, d, CLS_CHURN_P)) + abs(get(cust, d, CLS_LAPSED))
            if get(cust, d, CLS_UPSELL) > 0 or get(cust, d, CLS_ADDON) > 0 or get(cust, d, CLS_CROSS) > 0 or get(cust, d, CLS_NEW_LOGO) > 0:
                risk_pos_present = True

        # ── Trailing sums over expansion window (6 periods) ────────────────
        exp_pos_sum = 0.0
        exp_neg_present = False
        for d in exp_dates:
            exp_pos_sum += get(cust, d, CLS_UPSELL) + get(cust, d, CLS_CROSS) + get(cust, d, CLS_ADDON)
            if get(cust, d, CLS_DOWNSELL) < 0 or get(cust, d, CLS_CHURN_P) < 0 or get(cust, d, CLS_LAPSED) != 0 or get(cust, d, CLS_CHURN) < 0:
                exp_neg_present = True

        ending_first = get(cust, exp_dates[0] if len(exp_dates) >= RISK_WINDOW else risk_dates[0], CLS_ENDING)
        ending_3ago  = get(cust, risk_dates[0], CLS_ENDING)

        # ── CHURN RISK ──────────────────────────────────────────────────────
        a_risk_pct = risk_neg_sum / prior_safe
        a_risk = _scale_pct(a_risk_pct, cap=0.30)

        slope = (ending_acv - ending_3ago) / max(ending_3ago, 1.0)
        b_risk = _scale_pct(-slope, cap=0.30) if slope < 0 else 0.0

        c_risk_pct = expiry_pool / prior_safe
        c_discount = 0.3 if risk_pos_present else 1.0
        c_risk = _scale_pct(c_risk_pct, cap=0.50) * c_discount

        risk_score = round(0.45 * a_risk + 0.25 * b_risk + 0.30 * c_risk, 1)

        risk_reasons = []
        if a_risk > 0:
            risk_reasons.append((a_risk * 0.45, f"Lost {risk_neg_sum/prior_safe*100:.1f}% of Prior ACV to downsell/churn-partial/lapsed in last {RISK_WINDOW} periods"))
        if b_risk > 0:
            risk_reasons.append((b_risk * 0.25, f"Ending ACV declined {abs(slope)*100:.1f}% over last {RISK_WINDOW} periods"))
        if c_risk > 0:
            note = " (no recent positive signal)" if risk_pos_present is False else " (some recent upsell activity)"
            risk_reasons.append((c_risk * 0.30, f"Expiry Pool is {c_risk_pct*100:.1f}% of Prior ACV{note}"))
        risk_reasons.sort(key=lambda x: -x[0])
        risk_reasons_out = [r[1] for r in risk_reasons]

        # ── EXPANSION OPPORTUNITY ───────────────────────────────────────────
        a_exp_pct = exp_pos_sum / prior_safe
        a_discount = 0.3 if exp_neg_present else 1.0
        a_exp = _scale_pct(a_exp_pct, cap=0.30) * a_discount

        b_exp_pct = expiry_pool / prior_safe
        b_weight_mult = 1.0 if exp_pos_sum > 0 else 0.3
        b_exp = _scale_pct(b_exp_pct, cap=0.50) * b_weight_mult

        if has_product and peer_median_products is not None:
            cust_products = product_counts.get(cust, 0)
            gap = max(peer_median_products - cust_products, 0)
            c_exp = _scale_pct(gap / 3.0, cap=1.0)  # gap of 3+ products = max score
            w_a, w_b, w_c = 0.45, 0.30, 0.25
        else:
            c_exp = 0.0
            w_a, w_b, w_c = 0.60, 0.40, 0.0  # redistribute C's weight proportionally

        expansion_score = round(w_a * a_exp + w_b * b_exp + w_c * c_exp, 1)

        exp_reasons = []
        if a_exp > 0:
            exp_reasons.append((a_exp * w_a, f"Upsell/Cross-sell/Add-on totaled {exp_pos_sum/prior_safe*100:.1f}% of Prior ACV over last {EXPANSION_WINDOW} periods"))
        if b_exp > 0:
            note = " with existing positive momentum" if exp_pos_sum > 0 else ""
            exp_reasons.append((b_exp * w_b, f"Expiry Pool of {b_exp_pct*100:.1f}% of Prior ACV due for renewal{note}"))
        if c_exp > 0:
            exp_reasons.append((c_exp * w_c, f"Active on {product_counts.get(cust,0):.0f} product(s) vs. peer median {peer_median_products:.0f}"))
        exp_reasons.sort(key=lambda x: -x[0])
        exp_reasons_out = [r[1] for r in exp_reasons]

        rows.append({
            'Customer': cust,
            'Date': latest_date,
            'PriorACV': prior_acv,
            'EndingACV': ending_acv,
            'RiskScore': risk_score,
            'RiskLabel': _label(risk_score, 'risk'),
            'RiskReasons': risk_reasons_out[:3],
            'ExpansionScore': expansion_score,
            'ExpansionLabel': _label(expansion_score, 'expansion'),
            'ExpansionReasons': exp_reasons_out[:3],
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values('RiskScore', ascending=False).reset_index(drop=True)
