import { useEffect, useState } from 'react'
import { useRouter } from 'next/router'
import Link from 'next/link'
import { FileText, Play, BarChart3, Lock, Loader2 } from 'lucide-react'
import DashboardLayout from '../../components/dashboard/DashboardLayout'
import { supabase, UserProfile, canDownload } from '../../lib/supabase'

export default function ReportsPage() {
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

  return (
    <DashboardLayout profile={profile} title="Reports">
      <div className="p-6 max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="font-display text-xl font-700 text-ink-900">Analytics Reports</h2>
            <p className="text-ink-400 text-sm mt-1">History of all your analytics runs</p>
          </div>
          <Link href="/dashboard/upload" className="btn-primary text-sm"><Play size={13}/> New analysis</Link>
        </div>
        {loading ? (
          <div className="flex items-center justify-center py-20"><Loader2 className="animate-spin text-brand-600" size={24}/></div>
        ) : (
          <div className="text-center py-20">
            <BarChart3 size={36} className="text-ink-200 mx-auto mb-4"/>
            <div className="font-display text-[15px] font-700 text-ink-700 mb-2">No reports yet</div>
            <p className="text-ink-400 text-sm mb-5">Upload a dataset and run your first analysis.</p>
            <Link href="/dashboard/upload" className="btn-primary text-sm">Upload dataset</Link>
          </div>
        )}
      </div>
    </DashboardLayout>
  )
}
