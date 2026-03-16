import Head from 'next/head'
import Link from 'next/link'
import { useState, useEffect } from 'react'
import {
  BarChart3, TrendingUp, Users, ArrowRight, CheckCircle, ChevronRight,
  Layers, Target, Zap, Shield, LineChart, PieChart, Play, Star
} from 'lucide-react'

// ── Nav ──────────────────────────────────────────────────────────────
function Nav() {
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const h = () => setScrolled(window.scrollY > 20)
    window.addEventListener('scroll', h)
    return () => window.removeEventListener('scroll', h)
  }, [])

  return (
    <nav className={`fixed top-0 inset-x-0 z-50 transition-all duration-300 ${
      scrolled ? 'bg-white/95 backdrop-blur-md border-b border-ink-100 shadow-sm' : 'bg-transparent'
    }`}>
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center">
            <BarChart3 size={16} className="text-white" />
          </div>
          <span className="font-display font-700 text-ink-900 text-[15px] tracking-tight">
            RevenueLens
          </span>
        </Link>

        <div className="hidden md:flex items-center gap-1">
          {['Product', 'Pricing', 'Consulting', 'About'].map(item => (
            <Link key={item} href={`/${item.toLowerCase()}`}
              className="px-4 py-2 text-[13px] font-medium text-ink-600 hover:text-ink-900 hover:bg-ink-50 rounded-lg transition-all">
              {item}
            </Link>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <Link href="/auth/login" className="btn-ghost text-[13px]">Sign in</Link>
          <Link href="/auth/signup" className="btn-primary text-[13px] py-2 px-4">
            Start free <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </nav>
  )
}

// ── Hero ─────────────────────────────────────────────────────────────
function Hero() {
  return (
    <section className="relative min-h-screen flex items-center overflow-hidden bg-ink-950">
      {/* Background mesh */}
      <div className="absolute inset-0">
        <div className="absolute top-0 left-1/4 w-96 h-96 bg-brand-600/20 rounded-full blur-[100px]" />
        <div className="absolute bottom-0 right-1/4 w-80 h-80 bg-brand-400/15 rounded-full blur-[80px]" />
        <div className="absolute inset-0" style={{
          backgroundImage: 'radial-gradient(circle at 1px 1px, rgba(255,255,255,0.04) 1px, transparent 0)',
          backgroundSize: '40px 40px'
        }} />
      </div>

      <div className="relative max-w-7xl mx-auto px-6 pt-24 pb-20">
        <div className="grid lg:grid-cols-2 gap-16 items-center">

          {/* Left */}
          <div>
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-brand-500/10 border border-brand-500/20 text-brand-400 text-xs font-semibold tracking-wide mb-8">
              <span className="w-1.5 h-1.5 rounded-full bg-brand-400 animate-pulse" />
              Revenue Intelligence Platform
            </div>

            <h1 className="font-display text-5xl lg:text-6xl font-800 text-white leading-[1.05] tracking-tight mb-6">
              Understand Why Your{' '}
              <span className="text-brand-400">SaaS Revenue</span>{' '}
              Grows or Declines
            </h1>

            <p className="text-ink-300 text-lg leading-relaxed mb-10 max-w-xl">
              Upload your billing or revenue data and get instant ARR bridge analysis,
              cohort retention, customer segmentation, and pricing diagnostics —
              without any SQL or data science expertise.
            </p>

            <div className="flex flex-wrap gap-4 mb-12">
              <Link href="/auth/signup" className="btn-primary text-sm px-6 py-3">
                Start Free Analysis <ArrowRight size={15} />
              </Link>
              <button className="inline-flex items-center gap-2.5 px-6 py-3 text-white/80 text-sm font-medium hover:text-white transition-colors">
                <div className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center">
                  <Play size={12} className="fill-white text-white ml-0.5" />
                </div>
                Watch demo (2 min)
              </button>
            </div>

            <div className="flex items-center gap-8">
              {[
                { n: '500+', l: 'Datasets analyzed' },
                { n: '98%', l: 'Accuracy vs Alteryx' },
                { n: '< 30s', l: 'Per analysis run' },
              ].map(({ n, l }) => (
                <div key={l}>
                  <div className="font-display text-2xl font-700 text-white">{n}</div>
                  <div className="text-ink-400 text-xs mt-0.5">{l}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Right — dashboard preview */}
          <div className="relative hidden lg:block">
            <div className="relative rounded-2xl overflow-hidden border border-white/10 shadow-2xl bg-ink-900">
              {/* Mock dashboard header */}
              <div className="bg-ink-950 border-b border-white/5 px-5 py-3 flex items-center gap-3">
                <div className="flex gap-1.5">
                  <div className="w-3 h-3 rounded-full bg-red-500/60" />
                  <div className="w-3 h-3 rounded-full bg-yellow-500/60" />
                  <div className="w-3 h-3 rounded-full bg-green-500/60" />
                </div>
                <div className="flex-1 bg-white/5 rounded-md h-5 mx-4" />
              </div>

              {/* KPI row */}
              <div className="p-5 grid grid-cols-3 gap-3">
                {[
                  { label: 'Total ARR', value: '$12.4M', change: '+18.2%', up: true },
                  { label: 'Net Retention', value: '112%', change: '+4.1pp', up: true },
                  { label: 'New Logo ARR', value: '$1.8M', change: '+22%', up: true },
                ].map(kpi => (
                  <div key={kpi.label} className="bg-ink-800/50 rounded-xl p-3.5 border border-white/5">
                    <div className="text-ink-400 text-[10px] font-semibold uppercase tracking-wide mb-1">{kpi.label}</div>
                    <div className="font-display text-white text-lg font-700">{kpi.value}</div>
                    <div className={`text-xs font-medium mt-1 ${kpi.up ? 'text-green-400' : 'text-red-400'}`}>{kpi.change}</div>
                  </div>
                ))}
              </div>

              {/* Bridge bars mock */}
              <div className="px-5 pb-5">
                <div className="bg-ink-800/40 rounded-xl p-4 border border-white/5">
                  <div className="text-ink-300 text-xs font-semibold mb-4">ARR Bridge — 12M Lookback</div>
                  <div className="flex items-end gap-2 h-24">
                    {[
                      { h: 70, c: '#1A3CF5', l: 'Beginning' },
                      { h: 45, c: '#10B981', l: 'New Logo' },
                      { h: 30, c: '#3B82F6', l: 'Upsell' },
                      { h: -15, c: '#F97316', l: 'Downsell' },
                      { h: -25, c: '#EF4444', l: 'Churn' },
                      { h: 80, c: '#1A3CF5', l: 'Ending' },
                    ].map((b, i) => (
                      <div key={i} className="flex-1 flex flex-col items-center gap-1">
                        <div
                          className="w-full rounded-sm opacity-90"
                          style={{
                            height: Math.abs(b.h) * 0.85 + '%',
                            background: b.c,
                            marginTop: b.h < 0 ? '0' : 'auto',
                            minHeight: 8,
                          }}
                        />
                        <div className="text-ink-500 text-[9px] text-center leading-tight">{b.l}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* Floating tag */}
            <div className="absolute -top-4 -right-4 bg-white rounded-xl px-4 py-2.5 shadow-card-lg flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-green-50 flex items-center justify-center">
                <TrendingUp size={14} className="text-green-600" />
              </div>
              <div>
                <div className="text-ink-900 text-xs font-700">NRR 112%</div>
                <div className="text-ink-400 text-[10px]">Q4 2024</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

// ── Features ──────────────────────────────────────────────────────────
const FEATURES = [
  {
    icon: Layers,
    title: 'Cohort Analytics',
    desc: 'Segment customers into SG (size group), PC (percentile), and RC (revenue contribution) cohorts. Individual and hierarchical cohort creation with fiscal year filtering.',
    color: 'text-brand-600',
    bg: 'bg-brand-50',
  },
  {
    icon: TrendingUp,
    title: 'ARR Bridge Analysis',
    desc: 'Full SaaS revenue waterfall: New Logo, Cross-sell, Upsell, Downsell, Churn, Lapsed, Returning. Exact Alteryx workflow methodology with 1M/3M/12M lookback windows.',
    color: 'text-green-600',
    bg: 'bg-green-50',
  },
  {
    icon: Users,
    title: 'Customer Analytics',
    desc: 'Retention heatmaps, NRR/GRR tracking, vintage cohort analysis, top movers, and customer segmentation by fiscal year. PE-grade output tables.',
    color: 'text-purple-600',
    bg: 'bg-purple-50',
  },
  {
    icon: Target,
    title: 'Pricing Diagnostics',
    desc: 'Price vs volume decomposition using exact Alteryx logic. Isolate price impact, volume impact, and PV miscellaneous for Upsell/Downsell movements.',
    color: 'text-amber-600',
    bg: 'bg-amber-50',
  },
  {
    icon: LineChart,
    title: 'Revenue Concentration',
    desc: 'Identify revenue concentration risk. See what percentage of revenue comes from your top 5%, 10%, 20% of customers — and how that shifts over time.',
    color: 'text-red-600',
    bg: 'bg-red-50',
  },
  {
    icon: Shield,
    title: 'PE-Grade Output',
    desc: 'Export the exact ARR waterfall table format used in private equity diligence. Matches Alteryx Cross Tab output with Bridge Classification × Date columns.',
    color: 'text-ink-600',
    bg: 'bg-ink-50',
  },
]

function Features() {
  return (
    <section className="py-24 bg-white">
      <div className="max-w-7xl mx-auto px-6">
        <div className="text-center mb-16">
          <span className="section-label">Platform Capabilities</span>
          <h2 className="font-display text-4xl font-800 text-ink-900 tracking-tight mb-4">
            Everything you need to<br />diagnose revenue health
          </h2>
          <p className="text-ink-500 text-lg max-w-2xl mx-auto">
            Built on the same methodology used by PE firms and SaaS-focused investment bankers.
            Upload your data — we handle the analysis.
          </p>
        </div>

        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
          {FEATURES.map((f) => (
            <div key={f.title} className="card p-6 card-hover">
              <div className={`w-10 h-10 rounded-xl ${f.bg} flex items-center justify-center mb-4`}>
                <f.icon size={20} className={f.color} />
              </div>
              <h3 className="font-display text-[15px] font-700 text-ink-900 mb-2">{f.title}</h3>
              <p className="text-ink-500 text-sm leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

// ── How it works ──────────────────────────────────────────────────────
const STEPS = [
  { n: '01', title: 'Upload Dataset', desc: 'Upload CSV or Excel — revenue, billing, or bookings data. Any format, any structure.' },
  { n: '02', title: 'Map Fields', desc: 'Map your columns: Customer, Date, Revenue, Product, Channel, Region, Quantity.' },
  { n: '03', title: 'Run Analytics', desc: 'Choose modules: Cohort Analytics, Customer Analytics, Revenue Bridge, Pricing.' },
  { n: '04', title: 'View & Export', desc: 'Explore interactive dashboards. Download PE-grade Excel output (paid plans).' },
]

function HowItWorks() {
  return (
    <section className="py-24 bg-ink-50">
      <div className="max-w-7xl mx-auto px-6">
        <div className="text-center mb-16">
          <span className="section-label">Workflow</span>
          <h2 className="font-display text-4xl font-800 text-ink-900 tracking-tight mb-4">
            From upload to insight<br />in under 60 seconds
          </h2>
        </div>

        <div className="grid md:grid-cols-4 gap-6">
          {STEPS.map((s, i) => (
            <div key={s.n} className="relative">
              {i < STEPS.length - 1 && (
                <div className="hidden md:block absolute top-8 left-[calc(100%-12px)] w-full h-px bg-gradient-to-r from-ink-200 to-transparent z-0" />
              )}
              <div className="relative card p-6">
                <div className="font-mono text-xs font-600 text-brand-500 mb-3">{s.n}</div>
                <h3 className="font-display text-[15px] font-700 text-ink-900 mb-2">{s.title}</h3>
                <p className="text-ink-500 text-sm leading-relaxed">{s.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

// ── Pricing ───────────────────────────────────────────────────────────
const PRICING_PLANS = [
  {
    name: 'Free',
    price: '$0',
    period: 'forever',
    desc: 'For teams exploring the platform',
    features: ['Upload & analyze datasets', 'Cohort analytics', 'View all dashboards', 'Community support'],
    cta: 'Start free',
    href: '/auth/signup',
    highlight: false,
  },
  {
    name: 'Starter',
    price: '$25',
    period: '/month',
    desc: 'For analysts and consultants',
    features: ['Everything in Free', 'Download CSV/Excel output', 'ARR bridge reports', 'Email support', '$10/analytics run'],
    cta: 'Start Starter',
    href: '/auth/signup?plan=starter',
    highlight: true,
  },
  {
    name: 'Pro',
    price: '$99',
    period: '/month',
    desc: 'For SaaS teams & investors',
    features: ['Everything in Starter', 'Unlimited analytics runs', 'API access', 'Priority support', '1 consulting hr/mo'],
    cta: 'Start Pro',
    href: '/auth/signup?plan=pro',
    highlight: false,
  },
]

function Pricing() {
  return (
    <section id="pricing" className="py-24 bg-white">
      <div className="max-w-7xl mx-auto px-6">
        <div className="text-center mb-16">
          <span className="section-label">Pricing</span>
          <h2 className="font-display text-4xl font-800 text-ink-900 tracking-tight mb-4">
            Simple, transparent pricing
          </h2>
          <p className="text-ink-500 text-lg">No hidden fees. Start free, upgrade when you need more.</p>
        </div>

        <div className="grid md:grid-cols-3 gap-6 max-w-4xl mx-auto">
          {PRICING_PLANS.map(plan => (
            <div key={plan.name} className={`relative rounded-2xl p-7 border ${
              plan.highlight
                ? 'bg-brand-600 border-brand-600 shadow-glow'
                : 'bg-white border-ink-200'
            }`}>
              {plan.highlight && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-amber-400 text-ink-900 text-[10px] font-700 px-3 py-1 rounded-full uppercase tracking-wide">
                  Most popular
                </div>
              )}

              <div className={`text-sm font-600 mb-1 ${plan.highlight ? 'text-blue-200' : 'text-ink-500'}`}>
                {plan.name}
              </div>
              <div className={`font-display text-3xl font-800 ${plan.highlight ? 'text-white' : 'text-ink-900'}`}>
                {plan.price}<span className={`text-sm font-400 ${plan.highlight ? 'text-blue-200' : 'text-ink-400'}`}>{plan.period}</span>
              </div>
              <p className={`text-sm mt-2 mb-6 ${plan.highlight ? 'text-blue-100' : 'text-ink-500'}`}>{plan.desc}</p>

              <Link href={plan.href} className={`block text-center py-2.5 px-4 rounded-lg text-sm font-600 transition-all mb-7 ${
                plan.highlight
                  ? 'bg-white text-brand-600 hover:bg-blue-50'
                  : 'bg-brand-600 text-white hover:bg-brand-700'
              }`}>
                {plan.cta}
              </Link>

              <ul className="space-y-2.5">
                {plan.features.map(f => (
                  <li key={f} className="flex items-center gap-2.5 text-sm">
                    <CheckCircle size={14} className={plan.highlight ? 'text-blue-200' : 'text-brand-500'} />
                    <span className={plan.highlight ? 'text-blue-100' : 'text-ink-600'}>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        {/* Consulting add-on */}
        <div className="mt-10 max-w-4xl mx-auto card p-6 flex flex-col md:flex-row items-start md:items-center gap-6">
          <div className="flex-1">
            <div className="font-display text-[15px] font-700 text-ink-900 mb-1">Expert Analytics Consulting</div>
            <p className="text-ink-500 text-sm leading-relaxed">
              Need someone to interpret your data, build a revenue narrative for investors, or set up your analytics model?
              Book Ashwani for a focused 1-on-1 session. Former PE analytics background, deep SaaS metrics expertise. No retainer.
            </p>
          </div>
          <div className="flex gap-3 flex-shrink-0">
            <div className="px-4 py-2 bg-ink-50 rounded-lg text-center border border-ink-200">
              <div className="font-700 text-ink-900 text-sm">1 hr</div>
              <div className="text-ink-500 text-xs">$150</div>
            </div>
            <div className="px-4 py-2 bg-ink-50 rounded-lg text-center border border-ink-200">
              <div className="font-700 text-ink-900 text-sm">2 hrs</div>
              <div className="text-ink-500 text-xs">$280</div>
            </div>
            <div className="px-4 py-2 bg-ink-50 rounded-lg text-center border border-ink-200">
              <div className="font-700 text-ink-900 text-sm">½ day</div>
              <div className="text-ink-500 text-xs">$500</div>
            </div>
          </div>
          <Link href="/consulting" className="btn-secondary text-sm flex-shrink-0">
            Book a session <ChevronRight size={14} />
          </Link>
        </div>
      </div>
    </section>
  )
}

// ── Footer ────────────────────────────────────────────────────────────
function Footer() {
  return (
    <footer className="bg-ink-950 py-16">
      <div className="max-w-7xl mx-auto px-6">
        <div className="grid md:grid-cols-4 gap-10 mb-12">
          <div>
            <div className="flex items-center gap-2 mb-4">
              <div className="w-7 h-7 rounded-lg bg-brand-600 flex items-center justify-center">
                <BarChart3 size={13} className="text-white" />
              </div>
              <span className="font-display font-700 text-white text-sm">RevenueLens</span>
            </div>
            <p className="text-ink-400 text-sm leading-relaxed">
              Revenue intelligence for SaaS companies, consultants, and investors.
            </p>
          </div>

          {[
            { title: 'Product', links: ['Cohort Analytics', 'Customer Analytics', 'ARR Bridge', 'Pricing Diagnostics'] },
            { title: 'Company', links: ['About', 'Consulting', 'Blog', 'Careers'] },
            { title: 'Legal', links: ['Privacy Policy', 'Terms of Service', 'Security'] },
          ].map(col => (
            <div key={col.title}>
              <div className="text-ink-300 text-xs font-700 uppercase tracking-widest mb-4">{col.title}</div>
              <ul className="space-y-2">
                {col.links.map(l => (
                  <li key={l}>
                    <Link href="#" className="text-ink-400 text-sm hover:text-white transition-colors">{l}</Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="border-t border-white/5 pt-8 flex flex-col md:flex-row justify-between items-center gap-4">
          <div className="text-ink-500 text-sm">© 2025 RevenueLens. All rights reserved.</div>
          <div className="flex items-center gap-2 text-ink-500 text-sm">
            <Star size={12} className="text-amber-400 fill-amber-400" />
            Built with PE-grade analytics methodology
          </div>
        </div>
      </div>
    </footer>
  )
}

// ── Page ──────────────────────────────────────────────────────────────
export default function HomePage() {
  return (
    <>
      <Head>
        <title>RevenueLens — Revenue Intelligence for SaaS</title>
        <meta name="description" content="Understand why your SaaS revenue grows or declines. ARR bridge, cohort analytics, retention metrics, and pricing diagnostics — in minutes." />
        <link rel="icon" href="/favicon.ico" />
      </Head>
      <Nav />
      <main>
        <Hero />
        <Features />
        <HowItWorks />
        <Pricing />
      </main>
      <Footer />
    </>
  )
}
