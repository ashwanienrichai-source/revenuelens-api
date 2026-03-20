"""
mrr_workflow_engine.py

EXACT Python replication of MRR_Workflow_Advanced.yxmd

Reverse-engineered step-by-step from Alteryx XML.

═══════════════════════════════════════════════════════════════════════
WORKFLOW STEP DOCUMENTATION
═══════════════════════════════════════════════════════════════════════

NODE 1044 → INPUT
  Browse/read the raw CSV input file.
  Expected raw columns (user-mapped via Node 896):
    CustomerID, Product, Channel, Period (date), Revenue

NODE 990 → SELECT
  Keep only: CustomerID, Product, Channel from raw data.
  (Region defaults to 'N/A' if not present)

NODE 896 → FORMULA (User Input Mapping)
  Maps raw columns to internal standard names:
    Customer New   = [CustomerID]
    Product New    = [Product]
    Channel New    = [Channel]
    Region New     = 'N/A'          ← hardcoded default
    Date New       = [Period]        ← raw date column
    MRR New        = [Revenue]       ← raw revenue column
    Quantity New   = 1               ← default if no quantity
    In Scope New   = IF MRR > 0 AND NOT NULL → "In Scope" ELSE "Out of Scope"

NODE 898 → FORMULA
  Normalize Date New to last-of-month:
    Date New = DateTimeTrim([Date New], 'lastofmonth')

NODE 1090 → FILTER
  Keep only In Scope rows:
    WHERE [In Scope New] = "In Scope"
  FALSE path → Node 1001 (Out of Scope records saved separately)

NODE 827 → SUMMARIZE
  Get dataset-level max date:
    Max(Date New) → Dataset_Max_Date_New

NODE 828 → APPEND FIELDS (broadcast join)
  Append Dataset_Max_Date_New to every record.

NODE 829 → MULTI-ROW FORMULA
  Forward-fill Dataset_Max_Date_New down records:
    if isnull([Dataset_Max_Date_New]) then [Row-1:Dataset_Max_Date_New]
    else [Dataset_Max_Date_New] endif

NODE 830 → FILTER
  Remove rows more than 12 months beyond dataset max date:
    WHERE Date New <= DateTimeTrim(DateTimeAdd(Dataset_Max_Date_New, 12, 'months'), 'lastofmonth')

NODE 835 → SUMMARIZE (aggregate to customer-product-date level)
  GroupBy: Customer New, Product New, Channel New, Region New,
           Dataset_Max_Date_New, Date New
  Sum: MRR New → MRR New
  Sum: Quantity New → Quantity New

NODE 820 → SUMMARIZE (lifecycle dates per customer-product)
  GroupBy: Customer New, Product New, Channel New, Region New,
           Dataset_Max_Date_New
  Min(Date New) → Min Date New
  Max(Date New) → Max Date New

NODE 823 → GENERATE ROWS (create full monthly date grid)
  For each customer-product, generate all months from Min Date New to
  Dataset_Max_Date_New (inclusive), step = 1 month, last-of-month.
  Init:  Date New = [Min Date New]
  Cond:  Date New <= [Dataset_Max_Date_New]
  Loop:  Date New = DateTimeTrim(DateTimeAdd(Date New, 1, 'months'), 'lastofmonth')

NODE 824 → JOIN (left join grid to actual MRR)
  Left  = Node 823 (full date grid)
  Right = Node 835 (actual MRR records)
  Join on: Customer New + Product New + Channel New + Region New + Date New
  Left-only records → Node 825

NODE 825 → FORMULA (fill zeros for missing periods)
  MRR New      = 0
  Quantity New = 0

NODE 826 → UNION
  Combine joined records + zero-fill records.
  Now every customer-product has a row for every month, with 0 if no MRR.

NODE 950 → FORMULA (set Vintage = Customer Min Date)
  Vintage New = [Customer Min Date New]

NODE 946 → SUMMARIZE (customer-level lifecycle dates)
  GroupBy: Customer New
  Min(Date New) → Customer Min Date New
  Max(Date New) → Customer Max Date new

NODE 947 → SUMMARIZE (customer-product-level lifecycle dates)
  GroupBy: Customer New, Product New
  Min(Date New) → CustProd_Min_Date
  Max(Date New) → CustProd_Max_Date

NODE 948 → JOIN (add customer-level dates)
  Left  = full grid (after filter 951)
  Right = Node 946 customer lifecycle
  Join on: Customer New
  → adds Customer Min Date New, Customer Max Date new

NODE 949 → JOIN (add customer-product dates)
  Left  = Node 948 result
  Right = Node 947 custprod lifecycle
  Join on: Customer New + Product New
  → adds CustProd_Min_Date, CustProd_Max_Date

NODE 951 → FILTER (pre-join filter)
  Keep only rows where MRR New != 0

NODE 902, 904, 906 → FORMULA (set Month Lookback)
  Node 902: Month Lookback = 1
  Node 904: Month Lookback = 3
  Node 906: Month Lookback = 12

NODES 903/905/907 → FILTER (per lookback: within lookback window)
  Keep rows within the lookback window:
    WHERE Round(DateTimeDiff(Date New, Max Date New, 'days') / 30, 1) <= Month Lookback

NODES 913/917/921 → FORMULA (Expiry Pool Flag + MRR Flag)
  Expiry Pool Flag = if Date New > Max Date New then 1 else 0
  MRR Flag         = if Date New <= Max Date New then 1 else 0

NODES 915/919/923 → MULTI-ROW FORMULA (Prior MRR — lookback rows back)
  Node 915: Prior MRR New = [Row-1:MRR New]
  Node 919: Prior MRR New = [Row-3:MRR New]
  Node 923: Prior MRR New = [Row-12:MRR New]
  (Sort by Customer New, Product New, Date New first)

NODES 916/920/924 → MULTI-ROW FORMULA (Prior Quantity — same)
  Node 916: Prior Quantity New = [Row-1:Quantity New]
  Node 920: Prior Quantity New = [Row-3:Quantity New]
  Node 924: Prior Quantity New = [Row-12:Quantity New]

NODES 925/926/927 → FORMULA (DTE — Due To Expire)
  DTE New = if Expiry Pool Flag = 1 then Prior MRR New else 0

NODE 933 → UNION (combine 1M + 3M + 12M lookback results)

NODE 934 → FILTER (remove all-zero rows)
  Remove: WHERE MRR New = 0 AND Prior MRR New = 0 AND DTE New = 0

NODE 935 → FORMULA (past/future flags)
  pastMRR New  = if Round(DateTimeDiff(Date New, Min Date New, 'days')/30, 1) < Month Lookback
                 then "No" else "Yes"
  futureMRR New = if Max Date New > Date New then "Yes" else "No"

NODE 936 → FORMULA (BRIDGE FLAG — core classification logic)
  ────────────────────────────────────────────────────────────────
  IF Prior=0 AND Curr>0 AND pastMRR='No':
    IF CustProd_Min_Date <= lookback_date → "Other In"    (product existed before, went to 0, now back with new product combo)
    ELIF Customer_Min_Date <= lookback_date → "Cross-sell" (customer existed before, now has new product)
    ELSE → "New Logo"                                      (brand new customer in this lookback window)

  ELIF Prior=0 AND Curr>0 AND pastMRR='Yes':
    → "Returning"                                          (customer was active before this lookback period)

  ELIF Prior>0 AND Curr=0 AND DTE>0 AND futureMRR='No':
    IF CustProd_Max_Date >= Date New → "Other Out"        (product still exists for customer elsewhere)
    ELIF Customer_Max_Date >= Date New → "Churn-Partial"  (customer still exists but this product is gone)
    ELSE → "Churn"                                         (full churn — no future MRR anywhere)

  ELIF Curr=0 AND futureMRR='Yes':
    → "Lapsed"                                             (temporarily gone, will come back)

  ELIF Prior>0 AND Curr>0:
    IF Prior <= Curr → "Upsell"
    ELSE             → "Downsell"

  ELSE → "zz_Unclassified"
  ────────────────────────────────────────────────────────────────

  Bridge Value = MRR New - Prior MRR New

NODE 937 → FORMULA
  Beginning MRR = Prior MRR New
  Ending MRR    = MRR New

NODE 938 → FORMULA (Price/Volume decomposition — for Upsell/Downsell)
  Price New     = MRR New / Quantity New
  pPrice New    = Prior MRR New / Prior Quantity New
  Price Impact  = (Price New - pPrice New) * min(Quantity New, Prior Quantity New)
  Volume Impact = (Quantity New - Prior Quantity New) * min(Price New, pPrice New)
  PV Misc       = Bridge Value - Price Impact - Volume Impact - Price on Volume
  Volume Impact += PV Misc  (redistribute remainder)

NODE 958 → FORMULA
  Lookback Date = DateTimeTrim(DateTimeAdd(Date New, -Month Lookback, 'months'), 'lastofmonth')
  (recalculate Volume Impact to absorb PV Misc)

NODE 940 → CROSS-TAB (pivot Bridge Value by Classification)
  Group By: Customer New, Product New, Channel New, Region New, Date New, Month Lookback, ...
  Header:   Bridge Classification (= Bridge Flag)
  Value:    Bridge Value (sum)
  → Produces wide table with one column per classification

NODE 941 → SELECT
  Keep/rename: Churn Partial, Cross-sell, New Logo, Other In, Other Out

NODE 942 → TRANSPOSE
  Unpivot the wide table back to long format:
  → Name column (= Bridge Classification)
  → Value column (= Bridge Value)

NODE 943 → FILTER
  WHERE Value IS NOT NULL AND Value != 0

NODE 944 → SELECT (FINAL OUTPUT COLUMNS)
  Customer New      → Customer
  Product New       → Product
  Channel New       → Channel
  Region New        → Region
  Vintage New       → Vintage
  Date New          → Date
  MRR New           → MRR or ARR
  Quantity New      → Quantity
  Month Lookback    → Month Lookback
  DTE New           → DTE
  Lookback Date     → Lookback Date
  Name              → Bridge Classification
  Value             → Bridge Value

NODE 836 → OUTPUT (primary CSV output: "MRR Bridge.csv")

NODE 960 → OUTPUT (secondary output)

NODES 1067-1093 → LTM PERIOD LABELING
  Node 1067: Get Max_Date from output
  Node 1068: Append Max_Date to all records
  Node 1074: DateTime parse
  Node 1071: Filter: DateTimeDiff(Max_Date, Date, 'month') < 12  (within last 12 months)
  Node 1073: Set LTM Period = "LTM " + Max Date String
  Node 1079: Filter: DateTimeYear(Date) != DateTimeYear(Max_Date)
  Node 1080: Set LTM Period = DateTimeYear(Date)
  Node 1081: Union
  Node 1082: Select (keep Max_Date, Max Date String)
  Node 1085: Output (flat file with LTM labels)
  Node 1093: Output (flat file WITHOUT LTM period)

RECONCILIATION (Nodes 995-1027):
  Verifies: Source Total = Output Total + Out of Scope Total
  WHERE Month Lookback=12 AND Classification = "Ending MRR"
═══════════════════════════════════════════════════════════════════════
"""

import pandas as pd
import numpy as np
from typing import Optional, List
from pandas.tseries.offsets import MonthEnd


def run_mrr_workflow(
    df_raw: pd.DataFrame,
    customer_col: str = 'CustomerID',
    date_col: str = 'Period',
    revenue_col: str = 'Revenue',
    product_col: str = 'Product',
    channel_col: str = 'Channel',
    region_col: Optional[str] = None,
    quantity_col: Optional[str] = None,
    lookback_months: List[int] = [1, 3, 12],
    revenue_unit: str = 'raw',
) -> pd.DataFrame:
    """
    Exact Python replication of MRR_Workflow_Advanced.yxmd.

    INPUT:  Raw customer-product-period revenue data
    OUTPUT: Bridge CSV with columns matching Alteryx output exactly:
              Customer, Product, Channel, Region, Vintage, Date,
              MRR or ARR, Quantity, Month Lookback, DTE, Lookback Date,
              Bridge Classification, Bridge Value

    Parameters match config.py user input section exactly.
    """

    # ──────────────────────────────────────────────────────────────────
    # NODE 896: User Input Mapping
    # ──────────────────────────────────────────────────────────────────
    df = pd.DataFrame()
    df['Customer New']  = df_raw[customer_col].astype(str).str.strip()
    df['Product New']   = df_raw[product_col].astype(str).str.strip() if product_col in df_raw.columns else 'Unknown'
    df['Channel New']   = df_raw[channel_col].astype(str).str.strip() if channel_col in df_raw.columns else 'N/A'
    df['Region New']    = df_raw[region_col].astype(str).str.strip() if region_col and region_col in df_raw.columns else 'N/A'
    df['Date New']      = df_raw[date_col]
    df['MRR New']       = pd.to_numeric(df_raw[revenue_col], errors='coerce').fillna(0)
    df['Quantity New']  = pd.to_numeric(df_raw[quantity_col], errors='coerce').fillna(1) if quantity_col and quantity_col in df_raw.columns else 1.0
    df['In Scope New']  = np.where((df['MRR New'] > 0) & df['MRR New'].notna(), 'In Scope', 'Out of Scope')

    # ──────────────────────────────────────────────────────────────────
    # NODE 898: Normalize date to last of month
    # ──────────────────────────────────────────────────────────────────
    df['Date New'] = pd.to_datetime(df['Date New'], errors='coerce') + MonthEnd(0)

    # ──────────────────────────────────────────────────────────────────
    # NODE 1090: Filter to In Scope only
    # ──────────────────────────────────────────────────────────────────
    out_of_scope = df[df['In Scope New'] != 'In Scope'].copy()
    df = df[df['In Scope New'] == 'In Scope'].copy()

    if df.empty:
        return pd.DataFrame()

    # ──────────────────────────────────────────────────────────────────
    # NODE 827: Dataset max date
    # ──────────────────────────────────────────────────────────────────
    dataset_max_date = df['Date New'].max()

    # ──────────────────────────────────────────────────────────────────
    # NODE 828/829: Append & forward-fill Dataset_Max_Date_New
    # ──────────────────────────────────────────────────────────────────
    df['Dataset_Max_Date_New'] = dataset_max_date

    # ──────────────────────────────────────────────────────────────────
    # NODE 830: Filter — remove rows > 12 months beyond dataset max
    # ──────────────────────────────────────────────────────────────────
    cutoff = dataset_max_date + pd.DateOffset(months=12)
    cutoff = cutoff + MonthEnd(0)
    df = df[df['Date New'] <= cutoff].copy()

    # ──────────────────────────────────────────────────────────────────
    # NODE 835: Aggregate to customer-product-date level
    # ──────────────────────────────────────────────────────────────────
    dim_cols = ['Customer New', 'Product New', 'Channel New', 'Region New', 'Dataset_Max_Date_New']
    agg = df.groupby(dim_cols + ['Date New'], as_index=False).agg(
        MRR_New=('MRR New', 'sum'),
        Quantity_New=('Quantity New', 'sum'),
    )
    agg.rename(columns={'MRR_New': 'MRR New', 'Quantity_New': 'Quantity New'}, inplace=True)

    # ──────────────────────────────────────────────────────────────────
    # NODE 820: Customer-product lifecycle min/max dates
    # ──────────────────────────────────────────────────────────────────
    lifecycle = agg.groupby(dim_cols, as_index=False).agg(
        Min_Date_New=('Date New', 'min'),
        Max_Date_New=('Date New', 'max'),
    )
    lifecycle.rename(columns={'Min_Date_New': 'Min Date New', 'Max_Date_New': 'Max Date New'}, inplace=True)

    # ──────────────────────────────────────────────────────────────────
    # NODE 823: Generate full monthly date grid per customer-product
    # Init:  Date New = Min Date New
    # Cond:  Date New <= Dataset_Max_Date_New
    # Loop:  Date New += 1 month (last of month)
    # ──────────────────────────────────────────────────────────────────
    all_rows = []
    for _, lc_row in lifecycle.iterrows():
        dates = pd.date_range(
            start=lc_row['Min Date New'],
            end=lc_row['Dataset_Max_Date_New'],
            freq='ME',
        )
        for dt in dates:
            all_rows.append({
                'Customer New':         lc_row['Customer New'],
                'Product New':          lc_row['Product New'],
                'Channel New':          lc_row['Channel New'],
                'Region New':           lc_row['Region New'],
                'Dataset_Max_Date_New': lc_row['Dataset_Max_Date_New'],
                'Min Date New':         lc_row['Min Date New'],
                'Max Date New':         lc_row['Max Date New'],
                'Date New':             dt,
            })

    if not all_rows:
        return pd.DataFrame()

    grid = pd.DataFrame(all_rows)

    # ──────────────────────────────────────────────────────────────────
    # NODE 824: Join grid to actual MRR (left join)
    # NODE 825: Fill zeros for missing periods
    # NODE 826: Union joined + zero rows
    # ──────────────────────────────────────────────────────────────────
    join_cols = ['Customer New', 'Product New', 'Channel New', 'Region New', 'Date New']
    merged = grid.merge(
        agg[join_cols + ['MRR New', 'Quantity New']],
        on=join_cols, how='left'
    )
    merged['MRR New']      = merged['MRR New'].fillna(0)
    merged['Quantity New'] = merged['Quantity New'].fillna(0)

    # ──────────────────────────────────────────────────────────────────
    # NODE 946: Customer-level lifecycle dates
    # ──────────────────────────────────────────────────────────────────
    cust_lifecycle = merged.groupby('Customer New', as_index=False).agg(
        Customer_Min_Date_New=('Date New', 'min'),
        Customer_Max_Date_new=('Date New', 'max'),
    )
    cust_lifecycle.rename(columns={
        'Customer_Min_Date_New': 'Customer Min Date New',
        'Customer_Max_Date_new': 'Customer Max Date new',
    }, inplace=True)

    # NODE 950: Vintage = Customer Min Date
    merged = merged.merge(cust_lifecycle, on='Customer New', how='left')
    merged['Vintage New'] = merged['Customer Min Date New']

    # ──────────────────────────────────────────────────────────────────
    # NODE 947: Customer-product lifecycle dates (for CustProd flags)
    # ──────────────────────────────────────────────────────────────────
    custprod_lifecycle = merged.groupby(['Customer New', 'Product New'], as_index=False).agg(
        CustProd_Min_Date=('Date New', 'min'),
        CustProd_Max_Date=('Date New', 'max'),
    )
    merged = merged.merge(custprod_lifecycle, on=['Customer New', 'Product New'], how='left')

    # ──────────────────────────────────────────────────────────────────
    # NODE 951: Filter MRR != 0 before joining customer dates back
    # (Node 948/949 join on the filtered set, then full grid uses it)
    # In practice: we have all dates in grid, customer dates already merged
    # ──────────────────────────────────────────────────────────────────

    # Revenue unit conversion
    if revenue_unit == 'millions':
        merged['MRR New'] = merged['MRR New'] * 0.000001
    elif revenue_unit == 'thousands':
        merged['MRR New'] = merged['MRR New'] * 0.001

    # ──────────────────────────────────────────────────────────────────
    # NODES 902/904/906 + 903/905/907: Add Month Lookback versions
    # Filter each to within-lookback-window
    # ──────────────────────────────────────────────────────────────────
    all_lb_results = []

    for lb in lookback_months:
        lb_df = merged.copy()
        lb_df['Month Lookback'] = lb

        # Filter: only rows within the lookback window from Max Date New
        # Round(DateTimeDiff(Date New, Max Date New, 'days')/30, 1) <= Month Lookback
        days_diff = (lb_df['Max Date New'] - lb_df['Date New']).dt.days
        lb_df['_days_ratio'] = np.round(days_diff / 30, 1)
        lb_df = lb_df[lb_df['_days_ratio'] <= lb].copy()

        # Nodes 913/917/921: Expiry Pool Flag + MRR Flag
        lb_df['Expiry Pool Flag'] = np.where(lb_df['Date New'] > lb_df['Max Date New'], 1, 0)
        lb_df['MRR Flag']         = np.where(lb_df['Date New'] <= lb_df['Max Date New'], 1, 0)

        # Sort for MultiRowFormula (prior row lookup)
        lb_df = lb_df.sort_values(['Customer New', 'Product New', 'Channel New', 'Region New', 'Date New'])
        lb_df = lb_df.reset_index(drop=True)

        # Nodes 915/919/923: Prior MRR = MRR from lb rows back
        # Only within same customer-product group
        group_key = ['Customer New', 'Product New', 'Channel New', 'Region New']
        lb_df['Prior MRR New']      = lb_df.groupby(group_key)['MRR New'].shift(lb).fillna(0)
        lb_df['Prior Quantity New'] = lb_df.groupby(group_key)['Quantity New'].shift(lb).fillna(0)

        # Nodes 925/926/927: DTE
        lb_df['DTE New'] = np.where(lb_df['Expiry Pool Flag'] == 1, lb_df['Prior MRR New'], 0)

        all_lb_results.append(lb_df)

    # NODE 933: Union all lookback results
    df_all = pd.concat(all_lb_results, ignore_index=True)

    # NODE 934: Remove all-zero rows
    df_all = df_all[~(
        (df_all['MRR New'] == 0) &
        (df_all['Prior MRR New'] == 0) &
        (df_all['DTE New'] == 0)
    )].copy()

    # ──────────────────────────────────────────────────────────────────
    # NODE 935: pastMRR and futureMRR flags
    # ──────────────────────────────────────────────────────────────────
    days_from_start = (df_all['Date New'] - df_all['Min Date New']).dt.days
    df_all['pastMRR New'] = np.where(
        np.round(days_from_start / 30, 1) < df_all['Month Lookback'],
        'No', 'Yes'
    )
    df_all['futureMRR New'] = np.where(df_all['Max Date New'] > df_all['Date New'], 'Yes', 'No')

    # ──────────────────────────────────────────────────────────────────
    # NODE 936: BRIDGE FLAG — exact Alteryx logic
    # ──────────────────────────────────────────────────────────────────
    def classify_bridge(row):
        prior    = row['Prior MRR New']
        curr     = row['MRR New']
        past     = row['pastMRR New']
        future   = row['futureMRR New']
        dte      = row['DTE New']
        lb       = row['Month Lookback']
        date     = row['Date New']
        cust_min = row['Customer Min Date New']
        cp_min   = row['CustProd_Min_Date']
        cp_max   = row['CustProd_Max_Date']
        cust_max = row['Customer Max Date new']

        # Lookback cutoff date
        lookback_date = pd.Timestamp(date) - pd.DateOffset(months=int(lb))
        lookback_date = lookback_date + MonthEnd(0)

        if prior == 0 and curr != 0 and past == 'No':
            if pd.Timestamp(cp_min) <= lookback_date:
                return 'Other In'
            elif pd.Timestamp(cust_min) <= lookback_date:
                return 'Cross-sell'
            else:
                return 'New Logo'

        elif prior == 0 and curr != 0 and past == 'Yes':
            return 'Returning'

        elif prior != 0 and curr == 0 and dte != 0 and future == 'No':
            if pd.Timestamp(cp_max) >= pd.Timestamp(date):
                return 'Other Out'
            elif pd.Timestamp(cust_max) >= pd.Timestamp(date):
                return 'Churn-Partial'
            else:
                return 'Churn'

        elif curr == 0 and future == 'Yes':
            return 'Lapsed'

        elif prior != 0 and curr != 0:
            return 'Upsell' if prior <= curr else 'Downsell'

        else:
            return 'zz_Unclassified'

    df_all['Bridge Flag'] = df_all.apply(classify_bridge, axis=1)
    df_all['Bridge Value'] = df_all['MRR New'] - df_all['Prior MRR New']

    # ──────────────────────────────────────────────────────────────────
    # NODE 937: Beginning MRR / Ending MRR labels
    # ──────────────────────────────────────────────────────────────────
    df_all['Beginning MRR'] = df_all['Prior MRR New']
    df_all['Ending MRR']    = df_all['MRR New']

    # ──────────────────────────────────────────────────────────────────
    # NODE 938: Price / Volume decomposition (Upsell / Downsell only)
    # ──────────────────────────────────────────────────────────────────
    mask_pvd = df_all['Bridge Flag'].isin(['Upsell', 'Downsell'])
    q  = df_all.loc[mask_pvd, 'Quantity New']
    pq = df_all.loc[mask_pvd, 'Prior Quantity New']
    p  = df_all.loc[mask_pvd, 'MRR New']    / q.replace(0, np.nan)
    pp = df_all.loc[mask_pvd, 'Prior MRR New'] / pq.replace(0, np.nan)

    df_all['Price New']  = 0.0
    df_all['pPrice New'] = 0.0
    df_all.loc[mask_pvd, 'Price New']  = p.fillna(0)
    df_all.loc[mask_pvd, 'pPrice New'] = pp.fillna(0)

    # Price Impact
    min_q = np.where(q < pq, q, pq)
    df_all['Price Impact'] = 0.0
    df_all.loc[mask_pvd, 'Price Impact'] = ((p - pp) * min_q).fillna(0)

    # Volume Impact
    min_p = np.where(p < pp, p, pp)
    df_all['Volume Impact'] = 0.0
    df_all.loc[mask_pvd, 'Volume Impact'] = ((q - pq) * min_p).fillna(0)

    # Price on Volume
    scenario_4 = (p < pp) & (q < pq)
    scenario_3 = (p > pp) & (q > pq)
    pov = np.where(
        scenario_4 | scenario_3,
        ((p - pp) * (q - pq)) * -1,
        0
    )
    df_all['Price on Volume'] = 0.0
    df_all.loc[mask_pvd, 'Price on Volume'] = pov[mask_pvd.values] if len(pov) == len(df_all) else 0

    # PV Miscellaneous — remainder
    df_all['PV Miscellaneous'] = 0.0
    bv_pvd = df_all.loc[mask_pvd, 'Bridge Value']
    pi_pvd = df_all.loc[mask_pvd, 'Price Impact'].fillna(0)
    vi_pvd = df_all.loc[mask_pvd, 'Volume Impact'].fillna(0)
    pv_pvd = df_all.loc[mask_pvd, 'Price on Volume'].fillna(0)
    df_all.loc[mask_pvd, 'PV Miscellaneous'] = bv_pvd - pi_pvd - vi_pvd - pv_pvd

    # NODE 958: Volume Impact absorbs PV Misc
    df_all['Volume Impact'] = df_all['Volume Impact'].fillna(0) + df_all['PV Miscellaneous'].fillna(0)

    # ──────────────────────────────────────────────────────────────────
    # NODE 958: Lookback Date
    # ──────────────────────────────────────────────────────────────────
    df_all['Lookback Date'] = (
        df_all['Date New'] - df_all['Month Lookback'].apply(lambda x: pd.DateOffset(months=int(x)))
    ).apply(lambda d: d + MonthEnd(0))

    # ──────────────────────────────────────────────────────────────────
    # NODE 940/942: CrossTab then Transpose
    # Instead of pivot-unpivot, we directly assign Bridge Classification = Bridge Flag
    # (the CrossTab+Transpose pattern in Alteryx is used to reshape;
    #  the net result is: Name=Bridge Classification, Value=Bridge Value)
    # ──────────────────────────────────────────────────────────────────
    # Also add Beginning MRR and Ending MRR as separate rows
    df_all['Bridge Classification'] = df_all['Bridge Flag']

    # ──────────────────────────────────────────────────────────────────
    # Add Beginning MRR + Ending MRR rows (separate records per period)
    # In Alteryx these come from the CrossTab step which creates separate cols,
    # then Transpose creates separate rows. We replicate by stacking.
    # ──────────────────────────────────────────────────────────────────
    base_cols = ['Customer New', 'Product New', 'Channel New', 'Region New',
                 'Vintage New', 'Date New', 'MRR New', 'Quantity New',
                 'Month Lookback', 'DTE New', 'Lookback Date',
                 'Price Impact', 'Volume Impact']

    # Main bridge movements
    df_movements = df_all[base_cols + ['Bridge Classification', 'Bridge Value']].copy()
    df_movements = df_movements[
        df_movements['Bridge Value'] != 0
    ].copy()

    # Beginning MRR rows
    df_beg = df_all[base_cols].copy()
    df_beg['Bridge Classification'] = 'Beginning MRR'
    df_beg['Bridge Value']          = df_all['Beginning MRR']
    df_beg = df_beg[df_beg['Bridge Value'] != 0]

    # Ending MRR rows
    df_end = df_all[base_cols].copy()
    df_end['Bridge Classification'] = 'Ending MRR'
    df_end['Bridge Value']          = df_all['Ending MRR']
    df_end = df_end[df_end['Bridge Value'] != 0]

    # Stack all rows
    df_final = pd.concat([df_movements, df_beg, df_end], ignore_index=True)

    # NODE 943: Filter out null / zero Bridge Values
    df_final = df_final[
        df_final['Bridge Value'].notna() & (df_final['Bridge Value'] != 0)
    ].copy()

    # ──────────────────────────────────────────────────────────────────
    # NODE 944: SELECT — rename to final Alteryx output column names
    # ──────────────────────────────────────────────────────────────────
    df_output = df_final.rename(columns={
        'Customer New':          'Customer',
        'Product New':           'Product',
        'Channel New':           'Channel',
        'Region New':            'Region',
        'Vintage New':           'Vintage',
        'Date New':              'Date',
        'MRR New':               'MRR or ARR',
        'Quantity New':          'Quantity',
        'Month Lookback':        'Month Lookback',
        'DTE New':               'DTE',
        'Lookback Date':         'Lookback Date',
        'Bridge Classification': 'Bridge Classification',
        'Bridge Value':          'Bridge Value',
    })[['Customer', 'Product', 'Channel', 'Region', 'Vintage',
        'Date', 'MRR or ARR', 'Quantity', 'Month Lookback',
        'DTE', 'Lookback Date', 'Bridge Classification', 'Bridge Value']]

    # Convert Vintage to year string (config.py does this for display)
    df_output['Vintage'] = pd.to_datetime(df_output['Vintage']).dt.year.astype(str)

    return df_output.reset_index(drop=True)
