import { useEffect } from 'react'
import { useRouter } from 'next/router'
import { supabase } from '../../lib/supabase'
import { Loader2 } from 'lucide-react'

export default function AuthCallback() {
  const router = useRouter()
  useEffect(() => {
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (session) {
        await supabase.from('profiles').upsert({
          id: session.user.id,
          email: session.user.email,
          full_name: session.user.user_metadata?.full_name || '',
          role: session.user.email === process.env.NEXT_PUBLIC_ADMIN_EMAIL ? 'admin' : 'user',
          subscription_status: 'free',
          updated_at: new Date().toISOString(),
        })
        router.push('/dashboard')
      } else {
        router.push('/auth/login')
      }
    })
  }, [router])

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50">
      <div className="text-center">
        <Loader2 className="animate-spin text-brand-600 mx-auto mb-3" size={28} />
        <p className="text-ink-500 text-sm">Setting up your account...</p>
      </div>
    </div>
  )
}
