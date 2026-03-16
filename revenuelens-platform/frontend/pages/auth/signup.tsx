import Head from 'next/head'
import Link from 'next/link'
import { useState } from 'react'
import { useRouter } from 'next/router'
import { supabase } from '../../lib/supabase'
import { BarChart3, Eye, EyeOff, ArrowLeft, Loader2, CheckCircle } from 'lucide-react'

export default function SignupPage() {
  const router = useRouter()
  const plan   = router.query.plan as string | undefined
  const [form, setForm]       = useState({ name: '', email: '', password: '' })
  const [error, setError]     = useState('')
  const [loading, setLoading] = useState(false)
  const [done, setDone]       = useState(false)
  const [showPwd, setShowPwd] = useState(false)

  async function handleSignup(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    const { error } = await supabase.auth.signUp({
      email: form.email,
      password: form.password,
      options: {
        data: { full_name: form.name },
        emailRedirectTo: `${window.location.origin}/auth/callback`,
      },
    })
    if (error) { setError(error.message); setLoading(false); return }
    setDone(true)
    setLoading(false)
  }

  if (done) return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50 px-6">
      <div className="max-w-md w-full card p-10 text-center">
        <CheckCircle size={28} className="text-green-500 mx-auto mb-5" />
        <h2 className="font-display text-2xl font-800 text-ink-900 mb-3">Check your email</h2>
        <p className="text-ink-500 text-sm mb-6">We sent a confirmation link to <strong>{form.email}</strong>.</p>
        <Link href="/auth/login" className="btn-primary w-full justify-center">Back to sign in</Link>
      </div>
    </div>
  )

  return (
    <>
      <Head><title>Create Account — RevenueLens</title></Head>
      <div className="min-h-screen flex">
        <div className="flex-1 flex flex-col justify-center px-8 py-12 max-w-md mx-auto w-full">
          <Link href="/" className="inline-flex items-center gap-2 text-ink-500 text-sm hover:text-ink-900 mb-10">
            <ArrowLeft size={14} /> Back to site
          </Link>
          <div className="flex items-center gap-2.5 mb-8">
            <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center">
              <BarChart3 size={16} className="text-white" />
            </div>
            <span className="font-display font-700 text-ink-900">RevenueLens</span>
          </div>
          <h1 className="font-display text-2xl font-800 text-ink-900 mb-2">Create your account</h1>
          <p className="text-ink-500 text-sm mb-8">Free to start. No credit card required.</p>
          {error && <div className="mb-5 p-3.5 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">{error}</div>}
          <form onSubmit={handleSignup} className="space-y-4">
            <div>
              <label className="block text-sm font-600 text-ink-700 mb-1.5">Full name</label>
              <input type="text" required placeholder="Your name" className="input-field"
                value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
            </div>
            <div>
              <label className="block text-sm font-600 text-ink-700 mb-1.5">Email</label>
              <input type="email" required placeholder="you@company.com" className="input-field"
                value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} />
            </div>
            <div>
              <label className="block text-sm font-600 text-ink-700 mb-1.5">Password</label>
              <div className="relative">
                <input type={showPwd ? 'text' : 'password'} required minLength={8}
                  placeholder="Min 8 characters" className="input-field pr-10"
                  value={form.password} onChange={e => setForm(f => ({ ...f, password: e.target.value }))} />
                <button type="button" onClick={() => setShowPwd(!showPwd)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-400 hover:text-ink-600">
                  {showPwd ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>
            <button type="submit" disabled={loading} className="btn-primary w-full justify-center py-2.5 mt-2">
              {loading && <Loader2 size={15} className="animate-spin" />}
              {loading ? 'Creating account...' : 'Create free account'}
            </button>
          </form>
          <div className="mt-4 text-center text-sm text-ink-500">
            Already have an account?{' '}
            <Link href="/auth/login" className="text-brand-600 font-600 hover:underline">Sign in</Link>
          </div>
        </div>
        <div className="hidden lg:flex flex-1 bg-gradient-to-br from-brand-900 to-ink-950 items-center justify-center p-12">
          <div className="max-w-sm">
            <div className="font-display text-2xl font-800 text-white mb-6">What you get with RevenueLens</div>
            {['ARR bridge with 1M/3M/12M lookback', 'Cohort analytics — SG, PC, RC', 'NRR / GRR retention tracking', 'PE-grade Excel waterfall export'].map(item => (
              <div key={item} className="flex items-start gap-3 mb-4">
                <CheckCircle size={16} className="text-brand-300 flex-shrink-0 mt-0.5" />
                <span className="text-ink-200 text-sm">{item}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
