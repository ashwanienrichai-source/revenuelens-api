import { useEffect, useState } from 'react'
import { useRouter } from 'next/router'
import axios from 'axios'
import { User, CreditCard, Shield, Save, Loader2, CheckCircle, ExternalLink, LogOut } from 'lucide-react'
import DashboardLayout from '../../components/dashboard/DashboardLayout'
import { supabase, UserProfile, canDownload } from '../../lib/supabase'

export default function SettingsPage() {
  const router = useRouter()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [tab, setTab]   = useState<'profile'|'billing'|'security'>('profile')
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved,  setSaved]  = useState(false)

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) { router.push('/auth/login'); return }
      supabase.from('profiles').select('*').eq('id', session.user.id).single()
        .then(({ data }) => { if (data) { setProfile(data); setName(data.full_name || '') } })
    })
  }, [router])

  async function save() {
    if (!profile) return
    setSaving(true)
    await axios.patch(`/api/auth/profile?userId=${profile.id}`, { full_name: name })
    setSaving(false); setSaved(true); setTimeout(() => setSaved(false), 3000)
  }

  async function signOut() { await supabase.auth.signOut(); router.push('/') }

  const TABS = [
    { id: 'profile'  as const, label: 'Profile',  icon: User },
    { id: 'billing'  as const, label: 'Billing',  icon: CreditCard },
    { id: 'security' as const, label: 'Security', icon: Shield },
  ]

  return (
    <DashboardLayout profile={profile} title="Settings">
      <div className="p-6 max-w-3xl mx-auto">
        <div className="flex gap-1 mb-7 p-1 bg-ink-100 rounded-xl w-fit">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-600 transition-all ${tab === t.id ? 'bg-white shadow-sm text-ink-900' : 'text-ink-500 hover:text-ink-700'}`}>
              <t.icon size={14}/>{t.label}
            </button>
          ))}
        </div>

        {tab === 'profile' && (
          <div className="card p-6">
            <h2 className="font-display text-[15px] font-700 text-ink-900 mb-5">Profile</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-600 text-ink-700 mb-1.5">Full name</label>
                <input className="input-field" value={name} onChange={e => setName(e.target.value)}/>
              </div>
              <div>
                <label className="block text-sm font-600 text-ink-700 mb-1.5">Email</label>
                <input className="input-field bg-ink-50 cursor-not-allowed" value={profile?.email || ''} disabled/>
              </div>
            </div>
            <div className="flex items-center gap-3 mt-6 pt-5 border-t border-ink-100">
              <button onClick={save} disabled={saving} className="btn-primary text-sm py-2">
                {saving ? <Loader2 size={13} className="animate-spin"/> : <Save size={13}/>}
                {saving ? 'Saving...' : 'Save changes'}
              </button>
              {saved && <div className="flex items-center gap-1.5 text-green-600 text-sm"><CheckCircle size={14}/> Saved</div>}
            </div>
          </div>
        )}

        {tab === 'billing' && (
          <div className="card p-6">
            <h2 className="font-display text-[15px] font-700 text-ink-900 mb-4">Subscription</h2>
            <div className="p-4 bg-ink-50 rounded-xl border border-ink-200 mb-4 flex items-center justify-between">
              <div>
                <div className="font-700 text-ink-900 text-sm capitalize">{profile?.subscription_status || 'free'} plan</div>
                <div className="text-xs text-ink-400 mt-0.5">{canDownload(profile) ? 'Full platform access' : 'View only — upgrade to download'}</div>
              </div>
              {!canDownload(profile) && (
                <button onClick={() => router.push('/dashboard/upgrade')} className="btn-primary text-xs py-2 px-3">Upgrade</button>
              )}
            </div>
          </div>
        )}

        {tab === 'security' && (
          <div className="card p-6">
            <h2 className="font-display text-[15px] font-700 text-ink-900 mb-5">Security</h2>
            <div className="flex items-center justify-between p-4 bg-ink-50 rounded-xl border border-ink-200 mb-3">
              <div><div className="text-sm font-700 text-ink-900">Password</div></div>
              <button onClick={async () => { await supabase.auth.resetPasswordForEmail(profile?.email || ''); alert('Reset email sent.') }}
                className="btn-secondary text-xs py-2 px-3">Reset password</button>
            </div>
            <div className="flex items-center justify-between p-4 bg-ink-50 rounded-xl border border-ink-200">
              <div className="text-sm font-700 text-ink-900">Sign out</div>
              <button onClick={signOut} className="flex items-center gap-1.5 text-red-600 text-xs font-600 hover:text-red-700">
                <LogOut size={13}/> Sign out
              </button>
            </div>
          </div>
        )}
      </div>
    </DashboardLayout>
  )
}
