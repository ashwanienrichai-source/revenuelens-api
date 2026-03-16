import { useEffect, useState } from 'react'
import { useRouter } from 'next/router'
import Link from 'next/link'
import { TrendingUp, Users, DollarSign, Upload, ArrowRight, BarChart3, Layers, Target, ChevronRight, Zap } from 'lucide-react'
import DashboardLayout from '../../components/dashboard/DashboardLayout'
import { supabase, UserProfile, canDownload } from '../../lib/supabase'

function KpiCard({ icon: Icon, label, value, sub, color, bg }: any) {
  return (
    <div className="card p-5">
      <div className="w-9 h-9 rounded-xl flex items-center justify-center mb-3" style={{background: bg}}>
        <Icon size={17} className={color} />
      </div>
      <div className="font-display text-2xl font-700 text-ink-900 mb-0.5">{value}</div>
      <div className="text-ink-500 text-xs font-500">{label}</div>
      {sub && <div className="text-ink-400 text-xs mt-1">{sub}</div>}
    </div>
  )
}

export default function DashboardPage() {
  const router = useRouter()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) { router.push('/auth/login'); return }
      supabase.from('profiles').select('*').eq('id', session.user.id).single()
        .then(({ data }) => { if (data) setProfile(data); setLoading(false) })
    })
  }, [router])

  if (loading) return <div className="min-h-screen flex items-center justify-center bg-ink-50"><div className="text-ink-400 text-sm">Loading...</div></div>

  const isAdmin   = canDownload(profile)
  const firstName = profile?.full_name?.split(' ')[0] || 'there'

  const MODULES = [
    { icon: Layers, title: 'Cohort Analytics', desc: 'SG, PC, RC cohort segmentation. Individual and hierarchical cohorts.', href: '/app/cohort', color: 'text-brand-600', bg: '#EEF3FF', badge: 'Live' },
    { icon: Users, title: 'Customer Analytics', desc: 'ARR bridge, NRR/GRR, retention trends, top movers, vintage analysis.', href: '/app/customer', color: 'text-purple-600', bg: '#F5F3FF', badge: 'Live' },
    { icon: TrendingUp, title: 'Revenue Bridge', desc: 'New Logo, Upsell, Churn with 1M/3M/12M lookback. PE-grade waterfall.', href: '/app/bridge', color: 'text-green-600', bg: '#ECFDF5' },
    { icon: DollarSign, title: 'Pricing Diagnostics', desc: 'Price vs volume decomposition. Isolate price from volume changes.', href: '/app/pricing', color: 'text-amber-600', bg: '#FFFBEB' },
  ]

  return (
    <DashboardLayout profile={profile} title="Dashboard">
      <div className="p-6 max-w-6xl mx-auto">
        <div className="mb-7">
          <h2 className="font-display text-xl font-700 text-ink-900">Good morning, {firstName} 👋</h2>
          <p className="text-ink-500 text-sm mt-1">{new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}</p>
        </div>

        {!isAdmin && (
          <div className="mb-6 p-4 bg-brand-50 border border-brand-200 rounded-xl flex items-center justify-between">
            <div>
              <div className="text-sm font-600 text-brand-900">You are on the free plan</div>
              <div className="text-xs text-brand-700">Upgrade to download analytics output and export reports.</div>
            </div>
            <Link href="/dashboard/upgrade" className="btn-primary text-xs py-2 px-4">Upgrade <ArrowRight size={12} /></Link>
          </div>
        )}

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-7">
          <KpiCard icon={DollarSign} label="Total ARR" value="—" sub="Upload a dataset" color="text-brand-600" bg="#EEF3FF" />
          <KpiCard icon={Users} label="Customers" value="—" sub="Run analytics" color="text-purple-600" bg="#F5F3FF" />
          <KpiCard icon={TrendingUp} label="Net Retention" value="—" sub="Run revenue bridge" color="text-green-600" bg="#ECFDF5" />
          <KpiCard icon={BarChart3} label="Datasets" value="0" sub="Upload your first" color="text-amber-600" bg="#FFFBEB" />
        </div>

        <div className="mb-7 rounded-2xl bg-gradient-to-r from-brand-600 to-brand-700 p-6 text-white flex items-center justify-between">
          <div>
            <div className="font-display text-lg font-700 mb-1">Start your first analysis</div>
            <p className="text-blue-100 text-sm max-w-lg">Upload a CSV or Excel file. We will walk you through field mapping and run the full analytics suite.</p>
          </div>
          <Link href="/dashboard/upload" className="bg-white text-brand-700 btn-primary text-sm flex-shrink-0 ml-6">
            <Upload size={14} /> Upload Dataset
          </Link>
        </div>

        <div>
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-display text-[15px] font-700 text-ink-900">Analytics Modules</h3>
            <span className="text-ink-400 text-xs">Powered by your Streamlit engine</span>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-4">
            {MODULES.map(m => (
              <Link key={m.href} href={m.href} className="card p-5 card-hover block group">
                <div className="w-9 h-9 rounded-xl flex items-center justify-center mb-3" style={{background: m.bg}}>
                  <m.icon size={17} className={m.color} />
                </div>
                <div className="font-display text-[13px] font-700 text-ink-900 mb-1">{m.title}</div>
                <p className="text-ink-500 text-xs leading-relaxed">{m.desc}</p>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </DashboardLayout>
  )
}
