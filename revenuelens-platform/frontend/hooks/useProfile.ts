import { useEffect, useState } from 'react'
import { supabase, UserProfile, canDownload, isPaid } from '../lib/supabase'

export function useProfile() {
  const [profile, setProfile]   = useState<UserProfile | null>(null)
  const [loading, setLoading]   = useState(true)
  const [userId,  setUserId]    = useState<string | null>(null)

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) { setLoading(false); return }
      setUserId(session.user.id)
      supabase.from('profiles').select('*').eq('id', session.user.id).single()
        .then(({ data }) => { if (data) setProfile(data as UserProfile); setLoading(false) })
    })
  }, [])

  async function refresh() {
    if (!userId) return
    const { data } = await supabase.from('profiles').select('*').eq('id', userId).single()
    if (data) setProfile(data as UserProfile)
  }

  return {
    profile, loading, refresh, userId,
    isAdmin:     canDownload(profile),
    isPaid:      isPaid(profile),
    canDownload: canDownload(profile),
    subscription: profile?.subscription_status || 'free',
  }
}
