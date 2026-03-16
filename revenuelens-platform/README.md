# RevenueLens — Revenue Intelligence Platform

A full-stack SaaS platform wrapping the existing Streamlit analytics engine with a
Next.js marketing site, Supabase auth, Stripe billing, and a professional dashboard.

## Structure

```
revenuelens/
├── frontend/              ← Next.js app (Vercel)
│   ├── pages/
│   │   ├── index.tsx      ← Marketing homepage
│   │   ├── consulting.tsx ← Consulting page
│   │   ├── auth/          ← Login, signup, callback
│   │   ├── dashboard/     ← Protected user dashboard
│   │   └── app/           ← Analytics module pages (embed Streamlit)
│   ├── components/
│   │   └── dashboard/     ← DashboardLayout sidebar
│   ├── lib/               ← Supabase + Stripe clients
│   ├── hooks/             ← useProfile hook
│   └── styles/            ← Global CSS + design system
├── docs/
│   ├── DEPLOYMENT.md      ← Complete setup guide
│   └── supabase-schema.sql ← Database schema
└── analytics-engine/      ← Your existing cohort_app.py (unchanged)
```

## Quick Start

```bash
cd frontend
cp .env.local.example .env.local
# Fill in Supabase + Stripe keys
npm install
npm run dev
```

See `docs/DEPLOYMENT.md` for full setup instructions.

## Tech Stack
- **Frontend**: Next.js 14, TypeScript, Tailwind CSS
- **Auth**: Supabase Auth
- **Database**: Supabase Postgres
- **Payments**: Stripe
- **Analytics Engine**: Existing Streamlit app (unchanged)
- **Hosting**: Vercel (frontend) + Streamlit Cloud / Render (engine)
