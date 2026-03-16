import { useEffect, useState } from 'react'
import { useRouter } from 'next/router'
import axios from 'axios'
import { CheckCircle, Loader2, Crown, ArrowRight } from 'lucide-react'
import Link from 'next/link'
import DashboardLayout from '../../components/dashboard/DashboardLayout'
import { supabase, UserProfile } from '../../lib/supabase'
import { PLANS } from '../../lib/stripe'

export default function UpgradePage() {
  const router = useRouter()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState<string | null>(null)

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) { router.push('/auth/login'); return }
      supabase.from('profiles').select('*').eq('id', session.user.id).single()
        .then(({ data }) => { if (data) setProfile(data) })
    })
  }, [router])

  async function handleSubscribe(planId: string) {
    const plan = PLANS[planId as keyof typeof PLANS]
    if (!plan?.priceId) { router.push('/consulting'); return }
    setLoading(planId)
    try {
      const { data: { session } } = await supabase.auth.getSession()
      const { data } = await axios.post('/api/stripe/create-checkout', { priceId: plan.priceId, userId: session?.user.id, email: session?.user.email })
      window.location.href = data.url
    } catch { setLoading(null) }
  }

  const current = profile?.subscription_status || 'free'

  return (
    <DashboardLayout profile={profile} title="Upgrade Plan">
      <div className="p-6 max-w-4xl mx-auto">
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-amber-50 border border-amber-200 text-amber-700 text-xs font-700 mb-4">
            <Crown size={12} className="text-amber-500" /> Upgrade your plan
          </div>
          <h2 className="font-display text-3xl font-800 text-ink-900 mb-3">Unlock the full platform</h2>
          <p className="text-ink-500">Free users can run any analysis. Paid users can download output and export reports.</p>
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          {Object.values(PLANS).map(plan => {
            const isCurrent = current === plan.id
            const isHot = plan.id === 'pro'
            return (
              <div key={plan.id} className={`relative rounded-2xl border p-7 flex flex-col ${isHot ? 'bg-brand-600 border-brand-600 shadow-glow' : 'bg-white border-ink-200'}`}>
                {isHot && <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-amber-400 text-ink-900 text-[10px] font-700 px-3 py-1 rounded-full uppercase">Most popular</div>}
                {isCurrent && <div className="absolute -top-3 right-4 bg-green-400 text-white text-[10px] font-700 px-3 py-1 rounded-full uppercase">Current</div>}
                <div className={`text-xs font-700 uppercase tracking-wide mb-1 ${isHot ? 'text-blue-200' : 'text-ink-500'}`}>{plan.name}</div>
                <div className={`font-display text-3xl font-800 mb-4 ${isHot ? 'text-white' : 'text-ink-900'}`}>
                  {plan.price != null ? `$${plan.price}` : 'Custom'}
                  {plan.price != null && <span className={`text-sm font-400 ${isHot ? 'text-blue-200' : 'text-ink-400'}`}>/{plan.interval}</span>}
                </div>
                <ul className="space-y-2.5 mb-7 flex-1">
                  {plan.features.map(f => (
                    <li key={f} className="flex items-start gap-2.5 text-sm">
                      <CheckCircle size={13} className={`flex-shrink-0 mt-0.5 ${isHot ? 'text-blue-200' : 'text-brand-500'}`}/>
                      <span className={isHot ? 'text-blue-100' : 'text-ink-600'}>{f}</span>
                    </li>
                  ))}
                </ul>
                <button onClick={() => !isCurrent && handleSubscribe(plan.id)} disabled={isCurrent || loading === plan.id}
                  className={`w-full py-2.5 rounded-xl text-sm font-700 flex items-center justify-center gap-2 transition-all ${isCurrent ? 'bg-green-100 text-green-700 cursor-default' : isHot ? 'bg-white text-brand-600 hover:bg-blue-50' : 'bg-brand-600 text-white hover:bg-brand-700'}`}>
                  {loading === plan.id && <Loader2 size={14} className="animate-spin"/>}
                  {isCurrent ? '✓ Current plan' : plan.price != null ? `Subscribe` : 'Contact us'}
                  {!isCurrent && loading !== plan.id && <ArrowRight size={13}/>}
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </DashboardLayout>
  )
}
