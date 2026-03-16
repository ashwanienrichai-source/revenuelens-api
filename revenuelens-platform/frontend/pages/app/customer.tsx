import { useEffect, useState } from 'react'
import { useRouter } from 'next/router'
import { ExternalLink, RefreshCw, ChevronLeft, Loader2, Lock, Unlock } from 'lucide-react'
import Link from 'next/link'
import DashboardLayout from '../../components/dashboard/DashboardLayout'
import { supabase, UserProfile, canDownload } from '../../lib/supabase'

export default function Page() {
  const router = useRouter()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [iframeKey, setIframeKey] = useState(0)

  const engineBase = process.env.NEXT_PUBLIC_ANALYTICS_ENGINE_URL || 'https://ashwani-analytics-engine.streamlit.app'

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) { router.push('/auth/login'); return }
      supabase.from('profiles').select('*').eq('id', session.user.id).single()
        .then(({ data }) => { if (data) setProfile(data) })
    })
  }, [router])

  const isAdmin = canDownload(profile)
  const engineUrl = `${engineBase}?user_email=${encodeURIComponent(profile?.email || '')}&embedded=true`

  return (
    <DashboardLayout profile={profile} title="Analytics">
      <div className="flex flex-col h-[calc(100vh-56px)]">
        <div className="flex items-center gap-3 px-4 py-2.5 bg-white border-b border-ink-200 flex-shrink-0">
          <Link href="/dashboard" className="btn-ghost text-xs py-1.5 px-2.5"><ChevronLeft size={13}/> Dashboard</Link>
          <div className="flex items-center gap-2 ml-2">
            <div className={`w-2 h-2 rounded-full ${loading ? 'bg-amber-400 animate-pulse' : 'bg-green-400'}`} />
            <span className="text-xs text-ink-500">{loading ? 'Loading engine...' : 'Engine ready'}</span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-600 ${isAdmin ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-ink-50 text-ink-500 border border-ink-200'}`}>
              {isAdmin ? <Unlock size={11}/> : <Lock size={11}/>}
              {isAdmin ? 'Downloads enabled' : 'Upgrade to download'}
            </div>
            <button onClick={() => setIframeKey(k => k+1)} className="btn-ghost text-xs py-1.5 px-2.5"><RefreshCw size={13}/> Refresh</button>
            <a href={engineBase} target="_blank" rel="noopener" className="btn-ghost text-xs py-1.5 px-2.5"><ExternalLink size={13}/> Open standalone</a>
          </div>
        </div>
        {!isAdmin && (
          <div className="flex items-center gap-3 px-5 py-2 bg-amber-50 border-b border-amber-200 flex-shrink-0">
            <span className="text-xs text-amber-800">Free plan — analytics visible, downloads locked. <Link href="/dashboard/upgrade" className="font-600 hover:underline">Upgrade →</Link></span>
          </div>
        )}
        <div className="relative flex-1 bg-ink-50">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-ink-50 z-10">
              <div className="text-center">
                <Loader2 className="animate-spin text-brand-600 mx-auto mb-3" size={28}/>
                <div className="text-ink-500 text-sm">Loading analytics engine...</div>
                <div className="text-ink-400 text-xs mt-1">May take 10-20 seconds on first load</div>
              </div>
            </div>
          )}
          <iframe key={iframeKey} src={engineUrl} className="w-full h-full border-0"
            onLoad={() => setLoading(false)} title="RevenueLens Analytics Engine"
            allow="clipboard-read; clipboard-write"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads" />
        </div>
      </div>
    </DashboardLayout>
  )
}
