import Head from 'next/head'
import Link from 'next/link'
import { useState } from 'react'
import { BarChart3, CheckCircle, Calendar, ArrowRight, Clock, Star, Users, TrendingUp } from 'lucide-react'

const PACKAGES = [
  {
    title: 'Strategy Session',
    duration: '1 hour',
    price: '$150',
    desc: 'Interpret your revenue data, understand your retention metrics, and get a clear picture of your ARR drivers.',
    includes: ['ARR bridge walkthrough', 'NRR / GRR interpretation', 'Top 3 action items', 'Summary notes'],
    cta: 'Book 1-hour session',
  },
  {
    title: 'Deep Dive',
    duration: '2 hours',
    price: '$280',
    desc: 'Full cohort analysis setup, revenue bridge build, and investor-ready narrative for your SaaS metrics.',
    includes: ['Everything in Strategy', 'Cohort analytics setup', 'Customer segmentation', 'Investor narrative draft', 'Excel waterfall output'],
    cta: 'Book 2-hour session',
    popular: true,
  },
  {
    title: 'Analytics Build',
    duration: 'Half day (4 hrs)',
    price: '$500',
    desc: 'Complete analytics model setup: data pipeline, cohort engine configuration, and PE-grade output templates.',
    includes: ['Everything in Deep Dive', 'Full model configuration', 'Custom dashboard setup', 'Team walkthrough', '1-week follow-up support'],
    cta: 'Book half-day session',
  },
]

const EXPERTISE = [
  { icon: TrendingUp, label: 'SaaS Revenue Metrics', desc: 'ARR, MRR, NRR, GRR, logo retention — the full stack' },
  { icon: Users,      label: 'Cohort Analysis',       desc: 'Customer cohorts, retention curves, vintage analysis' },
  { icon: BarChart3,  label: 'PE Diligence',           desc: 'Revenue quality analysis for investment decisions' },
  { icon: Star,       label: 'Alteryx Methodology',    desc: 'MRR bridge workflow translated to Python' },
]

export default function ConsultingPage() {
  const [formData, setFormData] = useState({ name: '', email: '', company: '', message: '', package: '' })
  const [sent, setSent] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    // In production: POST to /api/contact or a form service
    setSent(true)
  }

  return (
    <>
      <Head>
        <title>Analytics Consulting — RevenueLens</title>
        <meta name="description" content="Book Ashwani for 1-on-1 SaaS analytics consulting. Revenue bridge, cohort analysis, investor narratives. No retainer — book by the hour." />
      </Head>

      {/* Nav */}
      <nav className="fixed top-0 inset-x-0 z-50 bg-white/95 backdrop-blur border-b border-ink-100">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center">
              <BarChart3 size={15} className="text-white" />
            </div>
            <span className="font-display font-700 text-ink-900 text-[15px]">RevenueLens</span>
          </Link>
          <div className="flex items-center gap-3">
            <Link href="/auth/login" className="btn-ghost text-sm">Sign in</Link>
            <Link href="/auth/signup" className="btn-primary text-sm py-2 px-4">Try the platform <ArrowRight size={13} /></Link>
          </div>
        </div>
      </nav>

      <main className="pt-24">
        {/* Hero */}
        <section className="py-20 bg-ink-950 text-white text-center">
          <div className="max-w-3xl mx-auto px-6">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-brand-500/15 border border-brand-500/25 text-brand-300 text-xs font-700 mb-6">
              <Clock size={11} /> No retainer · Book by the hour
            </div>
            <h1 className="font-display text-5xl font-800 mb-5 leading-tight">
              Expert Analytics Consulting.<br />On Your Schedule.
            </h1>
            <p className="text-ink-300 text-lg max-w-2xl mx-auto leading-relaxed mb-8">
              Need someone to interpret your SaaS metrics, build a revenue narrative for investors,
              or set up your cohort analytics model? Book Ashwani for a focused 1-on-1 session.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <div className="flex items-center gap-2 px-4 py-2 bg-white/8 rounded-full border border-white/10 text-sm">
                <Star size={13} className="text-amber-400 fill-amber-400" />
                Former PE analytics background
              </div>
              <div className="flex items-center gap-2 px-4 py-2 bg-white/8 rounded-full border border-white/10 text-sm">
                <CheckCircle size={13} className="text-green-400" />
                Deep SaaS metrics expertise
              </div>
              <div className="flex items-center gap-2 px-4 py-2 bg-white/8 rounded-full border border-white/10 text-sm">
                <BarChart3 size={13} className="text-brand-300" />
                Alteryx-level methodology
              </div>
            </div>
          </div>
        </section>

        {/* Expertise */}
        <section className="py-16 bg-ink-50">
          <div className="max-w-5xl mx-auto px-6">
            <div className="grid md:grid-cols-4 gap-5">
              {EXPERTISE.map(e => (
                <div key={e.label} className="card p-5 text-center">
                  <div className="w-10 h-10 rounded-xl bg-brand-50 flex items-center justify-center mx-auto mb-3">
                    <e.icon size={18} className="text-brand-600" />
                  </div>
                  <div className="font-display text-[13px] font-700 text-ink-900 mb-1">{e.label}</div>
                  <div className="text-ink-400 text-xs leading-relaxed">{e.desc}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Packages */}
        <section className="py-20 bg-white">
          <div className="max-w-5xl mx-auto px-6">
            <div className="text-center mb-12">
              <span className="section-label">Consulting Packages</span>
              <h2 className="font-display text-3xl font-800 text-ink-900 mt-3">Choose your session</h2>
            </div>
            <div className="grid md:grid-cols-3 gap-6">
              {PACKAGES.map(pkg => (
                <div key={pkg.title} className={`relative rounded-2xl border p-7 flex flex-col ${
                  pkg.popular ? 'border-brand-500 bg-brand-600 text-white shadow-glow' : 'border-ink-200 bg-white'
                }`}>
                  {pkg.popular && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-amber-400 text-ink-900 text-[10px] font-700 px-3 py-1 rounded-full uppercase tracking-wide">
                      Most popular
                    </div>
                  )}
                  <div className={`text-xs font-700 uppercase tracking-wide mb-1 ${pkg.popular ? 'text-blue-200' : 'text-ink-400'}`}>
                    {pkg.duration}
                  </div>
                  <div className={`font-display text-3xl font-800 mb-1 ${pkg.popular ? 'text-white' : 'text-ink-900'}`}>
                    {pkg.price}
                  </div>
                  <div className={`font-display text-[15px] font-700 mb-3 ${pkg.popular ? 'text-white' : 'text-ink-900'}`}>
                    {pkg.title}
                  </div>
                  <p className={`text-sm leading-relaxed mb-5 ${pkg.popular ? 'text-blue-100' : 'text-ink-500'}`}>
                    {pkg.desc}
                  </p>
                  <ul className="space-y-2 mb-6 flex-1">
                    {pkg.includes.map(item => (
                      <li key={item} className="flex items-start gap-2 text-sm">
                        <CheckCircle size={13} className={`flex-shrink-0 mt-0.5 ${pkg.popular ? 'text-blue-200' : 'text-brand-500'}`} />
                        <span className={pkg.popular ? 'text-blue-100' : 'text-ink-600'}>{item}</span>
                      </li>
                    ))}
                  </ul>
                  <button
                    onClick={() => setFormData(f => ({ ...f, package: pkg.title }))}
                    className={`w-full py-2.5 rounded-xl text-sm font-700 flex items-center justify-center gap-2 transition-all ${
                      pkg.popular ? 'bg-white text-brand-600 hover:bg-blue-50' : 'bg-brand-600 text-white hover:bg-brand-700'
                    }`}>
                    <Calendar size={13} /> {pkg.cta}
                  </button>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Contact form */}
        <section className="py-20 bg-ink-50" id="book">
          <div className="max-w-xl mx-auto px-6">
            <div className="text-center mb-10">
              <span className="section-label">Book a Session</span>
              <h2 className="font-display text-3xl font-800 text-ink-900 mt-3 mb-3">Get in touch</h2>
              <p className="text-ink-500 text-sm">Fill in the form and we'll confirm a time within 24 hours.</p>
            </div>

            {sent ? (
              <div className="card p-8 text-center">
                <CheckCircle size={36} className="text-green-500 mx-auto mb-4" />
                <div className="font-display text-xl font-800 text-ink-900 mb-2">Message sent!</div>
                <p className="text-ink-500 text-sm">We'll confirm your session within 24 hours.</p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="card p-7 space-y-4">
                {formData.package && (
                  <div className="p-3 bg-brand-50 border border-brand-200 rounded-lg text-brand-700 text-sm font-600">
                    Selected: {formData.package}
                  </div>
                )}
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-600 text-ink-700 mb-1.5">Your name</label>
                    <input className="input-field" placeholder="Ashwani Vatsal" required
                      value={formData.name} onChange={e => setFormData(f => ({ ...f, name: e.target.value }))} />
                  </div>
                  <div>
                    <label className="block text-sm font-600 text-ink-700 mb-1.5">Email</label>
                    <input className="input-field" type="email" placeholder="you@company.com" required
                      value={formData.email} onChange={e => setFormData(f => ({ ...f, email: e.target.value }))} />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-600 text-ink-700 mb-1.5">Company</label>
                  <input className="input-field" placeholder="Your company name"
                    value={formData.company} onChange={e => setFormData(f => ({ ...f, company: e.target.value }))} />
                </div>
                <div>
                  <label className="block text-sm font-600 text-ink-700 mb-1.5">What do you need help with?</label>
                  <textarea className="input-field h-28 resize-none" placeholder="Describe what you'd like to cover in the session..."
                    value={formData.message} onChange={e => setFormData(f => ({ ...f, message: e.target.value }))} />
                </div>
                <div>
                  <label className="block text-sm font-600 text-ink-700 mb-1.5">Package preference</label>
                  <select className="input-field"
                    value={formData.package} onChange={e => setFormData(f => ({ ...f, package: e.target.value }))}>
                    <option value="">Select a package</option>
                    {PACKAGES.map(p => <option key={p.title} value={p.title}>{p.title} — {p.price}</option>)}
                  </select>
                </div>
                <button type="submit" className="btn-primary w-full justify-center py-3">
                  Send booking request <ArrowRight size={14} />
                </button>
              </form>
            )}
          </div>
        </section>
      </main>

      <footer className="bg-ink-950 py-10 text-center">
        <div className="text-ink-400 text-sm">
          © 2025 RevenueLens. <Link href="/" className="text-ink-300 hover:text-white transition-colors">Back to platform</Link>
        </div>
      </footer>
    </>
  )
}
