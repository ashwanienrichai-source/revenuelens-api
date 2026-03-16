import Link from 'next/link'
import { useRouter } from 'next/router'
import { BarChart3, LayoutDashboard, Users, TrendingUp, DollarSign, FileText, Upload, Settings, LogOut, Crown, Layers } from 'lucide-react'
import { supabase, UserProfile, canDownload } from '../../lib/supabase'

const NAV = [
  { href: '/dashboard',         icon: LayoutDashboard, label: 'Dashboard' },
  { href: '/dashboard/upload',  icon: Upload,          label: 'Upload Dataset' },
  { href: '/app/cohort',        icon: Layers,          label: 'Cohort Analytics' },
  { href: '/app/customer',      icon: Users,           label: 'Customer Analytics' },
  { href: '/app/bridge',        icon: TrendingUp,      label: 'Revenue Bridge' },
  { href: '/app/pricing',       icon: DollarSign,      label: 'Pricing' },
  { href: '/dashboard/reports', icon: FileText,        label: 'Reports' },
  { href: '/dashboard/settings',icon: Settings,        label: 'Settings' },
]

interface Props { children: React.ReactNode; profile?: UserProfile | null; title?: string }

export default function DashboardLayout({ children, profile, title }: Props) {
  const router  = useRouter()
  const isAdmin = canDownload(profile || null)

  async function signOut() {
    await supabase.auth.signOut()
    router.push('/')
  }

  return (
    <div className="flex h-screen bg-ink-50 overflow-hidden">
      <aside className="w-52 flex flex-col bg-white border-r border-ink-200 flex-shrink-0">
        <div className="h-14 flex items-center gap-2.5 px-4 border-b border-ink-100">
          <div className="w-7 h-7 rounded-lg bg-brand-600 flex items-center justify-center flex-shrink-0">
            <BarChart3 size={13} className="text-white" />
          </div>
          <div>
            <div className="font-display font-700 text-ink-900 text-[13px]">RevenueLens</div>
            <div className="text-ink-400 text-[9px] font-mono uppercase tracking-wider">Analytics</div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto py-3 px-2">
          {NAV.map(item => {
            const active = router.pathname === item.href
            return (
              <Link key={item.href} href={item.href}
                className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-[12px] font-500 mb-0.5 transition-all ${
                  active ? 'bg-brand-50 text-brand-700 font-600' : 'text-ink-600 hover:bg-ink-50 hover:text-ink-900'
                }`}>
                <item.icon size={14} />
                {item.label}
              </Link>
            )
          })}
        </nav>

        {profile && !isAdmin && (
          <div className="px-3 pb-2">
            <div className="rounded-lg p-3 bg-ink-50 border border-ink-200">
              <div className="text-xs font-600 text-ink-700 mb-1">Free plan</div>
              <Link href="/dashboard/upgrade" className="text-[11px] text-brand-600 font-600 hover:underline">Upgrade to download →</Link>
            </div>
          </div>
        )}
        {profile && isAdmin && (
          <div className="px-3 pb-2">
            <div className="rounded-lg p-3 bg-amber-50 border border-amber-200 flex items-center gap-2">
              <Crown size={12} className="text-amber-500" />
              <span className="text-xs font-700 text-amber-700">Premium Access</span>
            </div>
          </div>
        )}

        <div className="border-t border-ink-100 p-3">
          <div className="flex items-center gap-2 p-2 rounded-lg hover:bg-ink-50 cursor-pointer group">
            <div className="w-7 h-7 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
              <span className="text-brand-700 text-xs font-700">{profile?.full_name?.charAt(0)?.toUpperCase() || 'U'}</span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[11px] font-600 text-ink-900 truncate">{profile?.full_name || 'User'}</div>
              <div className="text-[10px] text-ink-400 truncate">{profile?.email}</div>
            </div>
            <button onClick={signOut} className="opacity-0 group-hover:opacity-100 transition-opacity text-ink-400 hover:text-red-500">
              <LogOut size={13} />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="h-14 bg-white border-b border-ink-200 flex items-center px-6 flex-shrink-0">
          {title && <h1 className="font-display font-700 text-ink-900 text-[15px]">{title}</h1>}
        </header>
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
