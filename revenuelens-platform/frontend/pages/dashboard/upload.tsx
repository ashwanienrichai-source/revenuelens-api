import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/router'
import { useDropzone } from 'react-dropzone'
import Link from 'next/link'
import { Upload, FileText, CheckCircle, ArrowRight, X, Loader2, ChevronRight, AlertCircle } from 'lucide-react'
import DashboardLayout from '../../components/dashboard/DashboardLayout'
import { supabase, UserProfile } from '../../lib/supabase'

type DatasetType = 'revenue' | 'billing' | 'bookings'
type Step = 'upload' | 'map' | 'review'

const DATASET_TYPES = [
  { id: 'revenue',  label: 'Revenue Dataset',  desc: 'ARR / MRR data' },
  { id: 'billing',  label: 'Billing Dataset',  desc: 'Billing amounts' },
  { id: 'bookings', label: 'Bookings Dataset', desc: 'ACV / TCV records' },
] as const

const FIELDS = [
  { key: 'customer', label: 'Customer Column',   required: true },
  { key: 'date',     label: 'Date Column',        required: true },
  { key: 'revenue',  label: 'Revenue Column',     required: true },
  { key: 'product',  label: 'Product Column',     required: false },
  { key: 'channel',  label: 'Channel Column',     required: false },
  { key: 'region',   label: 'Region Column',      required: false },
  { key: 'fiscal',   label: 'Fiscal Year Column', required: false },
  { key: 'quantity', label: 'Quantity Column',    required: false },
]

export default function UploadPage() {
  const router = useRouter()
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [step, setStep]   = useState<Step>('upload')
  const [file, setFile]   = useState<File | null>(null)
  const [columns, setColumns] = useState<string[]>([])
  const [datasetType, setDatasetType] = useState<DatasetType>('revenue')
  const [mapping, setMapping] = useState<Record<string,string>>({})
  const [error, setError] = useState('')

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) { router.push('/auth/login'); return }
      supabase.from('profiles').select('*').eq('id', session.user.id).single()
        .then(({ data }) => { if (data) setProfile(data) })
    })
  }, [router])

  const onDrop = useCallback((accepted: File[]) => {
    if (!accepted.length) return
    const f = accepted[0]
    setFile(f)
    const reader = new FileReader()
    reader.onload = (e) => {
      const text = e.target?.result as string
      if (f.name.endsWith('.csv')) {
        const cols = text.split('\n')[0].split(',').map(c => c.trim().replace(/^["']|["']$/g, ''))
        setColumns(cols)
      }
    }
    if (f.name.endsWith('.csv')) reader.readAsText(f)
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop, accept: { 'text/csv': ['.csv'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] },
    maxFiles: 1,
  })

  function launchAnalytics() {
    const engineUrl = process.env.NEXT_PUBLIC_ANALYTICS_ENGINE_URL || 'https://ashwani-analytics-engine.streamlit.app'
    if (typeof window !== 'undefined') {
      sessionStorage.setItem('rl_mapping', JSON.stringify({ datasetType, mapping, fileName: file?.name }))
    }
    router.push(`/app/analytics`)
  }

  return (
    <DashboardLayout profile={profile} title="Upload Dataset">
      <div className="p-6 max-w-3xl mx-auto">
        <div className="flex items-center gap-2 mb-8">
          {(['upload','map','review'] as Step[]).map((s, i) => (
            <div key={s} className="flex items-center">
              <div className={`px-4 py-2 rounded-lg text-sm font-600 flex items-center gap-2 ${step === s ? 'bg-brand-600 text-white' : i < ['upload','map','review'].indexOf(step) ? 'text-brand-600 bg-brand-50' : 'text-ink-400'}`}>
                <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-700 ${step === s ? 'bg-white/20' : 'bg-ink-200 text-ink-500'}`}>{i+1}</div>
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </div>
              {i < 2 && <ChevronRight size={14} className="text-ink-300 mx-1" />}
            </div>
          ))}
        </div>

        {step === 'upload' && (
          <div>
            <h2 className="font-display text-xl font-700 text-ink-900 mb-6">Upload your dataset</h2>
            <div className="grid grid-cols-3 gap-3 mb-6">
              {DATASET_TYPES.map(t => (
                <button key={t.id} onClick={() => setDatasetType(t.id)}
                  className={`p-4 rounded-xl border text-left transition-all ${datasetType === t.id ? 'border-brand-500 bg-brand-50' : 'border-ink-200 bg-white hover:border-ink-300'}`}>
                  <div className="text-sm font-700 text-ink-900 mb-0.5">{t.label}</div>
                  <div className="text-xs text-ink-400">{t.desc}</div>
                </button>
              ))}
            </div>
            <div {...getRootProps()} className={`rounded-2xl border-2 border-dashed p-10 text-center cursor-pointer transition-all ${isDragActive ? 'border-brand-400 bg-brand-50' : file ? 'border-green-400 bg-green-50' : 'border-ink-200 hover:border-ink-300 bg-ink-50/50'}`}>
              <input {...getInputProps()} />
              {file ? (
                <div>
                  <FileText size={32} className="text-green-500 mx-auto mb-3" />
                  <div className="font-600 text-ink-900 text-sm mb-1">{file.name}</div>
                  <div className="text-ink-400 text-xs">{columns.length} columns detected</div>
                </div>
              ) : (
                <div>
                  <Upload size={32} className="text-ink-300 mx-auto mb-3" />
                  <div className="font-600 text-ink-700 text-sm mb-1">Drag and drop your file, or click to browse</div>
                  <div className="text-ink-400 text-xs">CSV, Excel (.xlsx) · Max 200MB</div>
                </div>
              )}
            </div>
            <button onClick={() => setStep('map')} disabled={!file} className="btn-primary mt-6 disabled:opacity-40">
              Continue to field mapping <ArrowRight size={14} />
            </button>
          </div>
        )}

        {step === 'map' && (
          <div>
            <h2 className="font-display text-xl font-700 text-ink-900 mb-6">Map your columns</h2>
            <div className="card divide-y divide-ink-100">
              {FIELDS.map(field => (
                <div key={field.key} className="flex items-center gap-4 p-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-600 text-ink-900">{field.label}</span>
                      {field.required ? <span className="badge badge-blue text-[9px]">Required</span> : <span className="text-[10px] text-ink-400 bg-ink-100 px-2 py-0.5 rounded">Optional</span>}
                    </div>
                  </div>
                  <select value={mapping[field.key] || 'None'} onChange={e => setMapping(m => ({ ...m, [field.key]: e.target.value }))} className="input-field w-48 text-sm py-2">
                    <option value="None">— Select —</option>
                    {columns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              ))}
            </div>
            {error && <div className="mt-4 p-3.5 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm flex items-center gap-2"><AlertCircle size={14}/>{error}</div>}
            <div className="flex gap-3 mt-6">
              <button onClick={() => setStep('upload')} className="btn-secondary">← Back</button>
              <button onClick={() => {
                if (!mapping.customer || !mapping.date || !mapping.revenue) { setError('Please map Customer, Date, and Revenue fields.'); return }
                setError(''); setStep('review')
              }} className="btn-primary">Review & confirm <ArrowRight size={14}/></button>
            </div>
          </div>
        )}

        {step === 'review' && (
          <div>
            <h2 className="font-display text-xl font-700 text-ink-900 mb-6">Review & launch</h2>
            <div className="card p-5 mb-6">
              <div className="text-xs font-700 text-ink-500 uppercase tracking-wide mb-4">Configuration</div>
              <div className="grid grid-cols-2 gap-3">
                <div><div className="text-xs text-ink-400 mb-0.5">File</div><div className="text-sm font-600 text-ink-900">{file?.name}</div></div>
                <div><div className="text-xs text-ink-400 mb-0.5">Dataset Type</div><div className="text-sm font-600 text-ink-900 capitalize">{datasetType}</div></div>
                {Object.entries(mapping).filter(([,v]) => v && v !== 'None').map(([k,v]) => (
                  <div key={k}><div className="text-xs text-ink-400 mb-0.5 capitalize">{k}</div><div className="text-sm font-600 text-ink-900">{v}</div></div>
                ))}
              </div>
            </div>
            <div className="flex gap-3">
              <button onClick={() => setStep('map')} className="btn-secondary">← Back</button>
              <button onClick={launchAnalytics} className="btn-primary"><CheckCircle size={14}/> Launch Analytics Engine</button>
            </div>
          </div>
        )}
      </div>
    </DashboardLayout>
  )
}
