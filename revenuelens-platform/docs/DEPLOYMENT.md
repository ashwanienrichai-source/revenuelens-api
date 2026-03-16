# RevenueLens — Complete Setup & Deployment Guide

## Architecture Overview

```
www.revenuelens.ai          → Next.js marketing site (Vercel)
app.revenuelens.ai          → Next.js app/dashboard (Vercel)
analytics.revenuelens.ai    → Streamlit engine (Render/Railway)
```

The Streamlit analytics engine runs as a **separate service** and is embedded
into the Next.js dashboard via iframe. This means:
- Your existing cohort_app.py is completely unchanged
- It runs independently on its own URL
- The Next.js app passes user context via URL params

---

## Step 1 — Supabase Setup

1. Create a new project at https://supabase.com
2. Go to **SQL Editor** and run the contents of `docs/supabase-schema.sql`
3. Note your project URL and anon key from Settings → API

### Storage bucket (for dataset files)
```sql
insert into storage.buckets (id, name, public)
values ('datasets', 'datasets', false);

create policy "Users can upload their own datasets"
  on storage.objects for insert
  with check (auth.uid()::text = (storage.foldername(name))[1]);

create policy "Users can read their own datasets"
  on storage.objects for select
  using (auth.uid()::text = (storage.foldername(name))[1]);
```

---

## Step 2 — Stripe Setup

1. Create account at https://stripe.com
2. Create two products in Stripe Dashboard:
   - **Starter** — $25/month recurring → copy the Price ID
   - **Pro** — $99/month recurring → copy the Price ID
3. Set up webhook endpoint (after deployment):
   - URL: `https://app.revenuelens.ai/api/stripe/webhook`
   - Events to listen for:
     - `checkout.session.completed`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
     - `invoice.payment_failed`

---

## Step 3 — Deploy Streamlit Engine (Render)

Your existing `cohort_app.py` runs as-is on Render or Streamlit Cloud.

### Option A: Keep on Streamlit Cloud (simplest)
Your existing app at `ashwani-analytics-engine.streamlit.app` is the engine URL.
Just set `NEXT_PUBLIC_ANALYTICS_ENGINE_URL=https://ashwani-analytics-engine.streamlit.app`

### Option B: Deploy to Render for custom domain
1. Create a new **Web Service** on Render
2. Connect your GitHub repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `streamlit run cohort_app.py --server.port $PORT --server.address 0.0.0.0`
5. Set environment variables from Streamlit secrets

---

## Step 4 — Deploy Next.js to Vercel

```bash
cd frontend
npm install
```

### Environment variables (set in Vercel dashboard):
```
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_STARTER_PRICE_ID=price_...
STRIPE_PRO_PRICE_ID=price_...

NEXT_PUBLIC_ANALYTICS_ENGINE_URL=https://ashwani-analytics-engine.streamlit.app
NEXT_PUBLIC_APP_URL=https://app.revenuelens.ai
NEXT_PUBLIC_ADMIN_EMAIL=ashwanivatsalarya@gmail.com
```

### Deploy:
```bash
npx vercel --prod
```

### Custom domains in Vercel:
- Add `revenuelens.ai` → points to the marketing homepage
- Add `app.revenuelens.ai` → same deployment, different domain

---

## Step 5 — Local Development

```bash
cd frontend
cp .env.local.example .env.local
# Fill in your values

npm install
npm run dev
# Opens at http://localhost:3000
```

---

## User Flows

### Free user:
1. Visits revenuelens.ai → clicks "Start free"
2. Signs up with email
3. Lands on /dashboard
4. Uploads dataset → maps fields
5. Clicks "Launch Analytics Engine" → iframe opens Streamlit
6. Runs any analysis, views dashboards
7. Download button is locked → upgrade prompt shown

### Paid user (Starter/Pro):
1. Same as above, plus:
2. After subscribing via Stripe
3. Webhook fires → Supabase profile updated
4. Download button unlocks in Streamlit engine (user_email param passed)
5. Reports saved to /dashboard/reports

### Admin (ashwanivatsalarya@gmail.com):
- Auto-assigned 'admin' role on signup
- Always has download access
- Can view all profiles (Supabase RLS policy)

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `pages/index.tsx` | Marketing homepage |
| `pages/auth/login.tsx` | Supabase login |
| `pages/auth/signup.tsx` | Signup + plan selection |
| `pages/dashboard/index.tsx` | Main dashboard |
| `pages/dashboard/upload.tsx` | Dataset upload + field mapping |
| `pages/app/analytics.tsx` | Streamlit iframe integration |
| `pages/app/cohort.tsx` | Cohort module (embeds engine) |
| `pages/app/customer.tsx` | Customer analytics (embeds engine) |
| `pages/app/bridge.tsx` | Revenue bridge (embeds engine) |
| `pages/dashboard/upgrade.tsx` | Stripe checkout |
| `pages/dashboard/reports.tsx` | Analytics run history |
| `pages/dashboard/settings.tsx` | Profile + billing settings |
| `pages/consulting.tsx` | Consulting booking page |
| `pages/api/stripe/create-checkout.ts` | Stripe checkout session |
| `pages/api/stripe/webhook.ts` | Stripe webhook handler |
| `pages/api/stripe/portal.ts` | Stripe customer portal |
| `lib/supabase.ts` | Supabase client + types |
| `lib/stripe.ts` | Stripe client + plan config |
| `hooks/useProfile.ts` | Profile + subscription hook |
| `docs/supabase-schema.sql` | Full database schema |

---

## Adding New Analytics Modules

1. Create `/pages/app/your-module.tsx` (copy from `cohort.tsx`)
2. Add to sidebar in `components/dashboard/DashboardLayout.tsx`
3. Add to module grid in `pages/dashboard/index.tsx`
4. The Streamlit engine handles the actual analytics — just change the URL params

---

## Streamlit Engine: Passing User Context

The Next.js app passes `user_email` as a URL param to the Streamlit engine:
```
https://your-engine.streamlit.app?user_email=user@email.com&embedded=true
```

In `cohort_app.py`, the download permission check:
```python
ADMIN_EMAIL = "ashwanivatsalarya@gmail.com"
user_email  = st.query_params.get("user_email", "")
is_admin    = user_email.lower() == ADMIN_EMAIL.lower()
```

This keeps download gating in sync between the Next.js layer and Streamlit layer.
