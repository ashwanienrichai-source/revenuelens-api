import { createClient } from '@supabase/supabase-js'

const supabaseUrl  = process.env.NEXT_PUBLIC_SUPABASE_URL!
const supabaseAnon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!

// ── Simple browser client ────────────────────────────────────────────
export const supabase = createClient(supabaseUrl, supabaseAnon)

export function createBrowserClient() {
  return createClient(supabaseUrl, supabaseAnon)
}

// ── Types ─────────────────────────────────────────────────────────────
export type UserProfile = {
  id: string
  email: string
  full_name: string | null
  role: 'admin' | 'user'
  subscription_status: 'free' | 'starter' | 'pro' | 'enterprise'
  subscription_id: string | null
  stripe_customer_id: string | null
  created_at: string
  updated_at: string
}

// ── Permission helpers ─────────────────────────────────────────────────
export function canDownload(profile: UserProfile | null): boolean {
  if (!profile) return false
  if (profile.email === process.env.NEXT_PUBLIC_ADMIN_EMAIL) return true
  if (profile.role === 'admin') return true
  return ['starter', 'pro', 'enterprise'].includes(profile.subscription_status)
}

export function isPaid(profile: UserProfile | null): boolean {
  if (!profile) return false
  return ['starter', 'pro', 'enterprise'].includes(profile.subscription_status)
}
