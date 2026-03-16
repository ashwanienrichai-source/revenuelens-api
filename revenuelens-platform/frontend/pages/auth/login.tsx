import Head from 'next/head'
import Link from 'next/link'
import { useState } from 'react'
import { useRouter } from 'next/router'
import { supabase } from '../../lib/supabase'
import { BarChart3, Eye, EyeOff, ArrowLeft, Loader2 } from 'lucide-react'

export default function LoginPage() {
  const router = useRouter()
  const [form, setForm]       = useState({ email: '', password: '' })
  const [error, setError]     = useState('')
  const [loading, setLoading] = useState(false)
  const [showPwd, setShowPwd] = useState(false)

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    const { error } = await supabase.auth.signInWithPassword({
      email: form.email,
      password: form.password,
    })
    if (error) { setError(error.message); setLoading(false); return }
    router.push('/dashboard')
  }

  return (
    <>
      <Head><title>Sign In — RevenueLens</title></Head>
      <div className="min-h-screen flex">
        <div className="flex-1 flex flex-col justify-center px-8 py-12 max-w-md mx-auto w-full">
          <Link href="/" className="inline-flex items-center gap-2 text-ink-500 text-sm hover:text-ink-900 transition-colors mb-10">
            <ArrowLeft size={14} /> Back to site
          </Link>
          <div className="flex items-center gap-2.5 mb-8">
            <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center">
              <BarChart3 size={16} className="text-white" />
            </div>
            <span className="font-display font-700 text-ink-900">RevenueLens</span>
          </div>
          <h1 className="font-display text-2xl font-800 text-ink-900 mb-2">Welcome back</h1>
          <p className="text-ink-500 text-sm mb-8">Sign in to access your analytics dashboard.</p>
          {error && (
            <div className="mb-5 p-3.5 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">{error}</div>
          )}
          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="block text-sm font-600 text-ink-700 mb-1.5">Email address</label>
              <input type="email" required placeholder="you@company.com" className="input-field"
                value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} />
            </div>
            <div>
              <div className="flex justify-between mb-1.5">
                <label className="text-sm font-600 text-ink-700">Password</label>
              </div>
              <div className="relative">
                <input type={showPwd ? 'text' : 'password'} required placeholder="••••••••"
                  className="input-field pr-10" value={form.password}
                  onChange={e => setForm(f => ({ ...f, password: e.target.value }))} />
                <button type="button" className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-400 hover:text-ink-600"
                  onClick={() => setShowPwd(!showPwd)}>
                  {showPwd ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>
            <button type="submit" disabled={loading} className="btn-primary w-full justify-center py-2.5 mt-2">
              {loading ? <Loader2 size={15} className="animate-spin" /> : null}
              {loading ? 'Signing in...' : 'Sign in'}
            </button>
          </form>
          <div className="mt-6 text-center text-sm text-ink-500">
            No account?{' '}
            <Link href="/auth/signup" className="text-brand-600 font-600 hover:underline">Create one free</Link>
          </div>
        </div>
        <div className="hidden lg:flex flex-1 bg-ink-950 items-center justify-center p-12 relative overflow-hidden">
          <div className="absolute inset-0">
            <div className="absolute top-1/4 left-1/4 w-64 h-64 bg-brand-500/20 rounded-full blur-3xl" />
          </div>
          <div className="relative text-center max-w-sm">
            <div className="font-display text-2xl font-800 text-white mb-4">Revenue intelligence for modern SaaS</div>
            <p className="text-ink-300 text-sm leading-relaxed">ARR bridge, cohort retention, NRR/GRR — built on PE-grade methodology.</p>
          </div>
        </div>
      </div>
    </>
  )
}
