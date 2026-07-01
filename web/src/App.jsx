import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  BadgeCheck,
  Building2,
  ChevronRight,
  CheckCircle2,
  DollarSign,
  Download,
  FileText,
  LogIn,
  LogOut,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Upload,
  XCircle,
} from 'lucide-react'
import { GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut } from 'firebase/auth'
import { auth } from './firebase'
import './App.css'

const API_BASE = (import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8001/api').replace(/\/$/, '')
const DEFAULT_PERIOD = '2026-06'
void [AlertCircle, Building2, ChevronRight, CheckCircle2, DollarSign, Download, FileText, LogIn, LogOut, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, Upload, XCircle, BadgeCheck]

const STEPS = [
  { id: 1, title: 'Upload contracts', icon: Upload },
  { id: 2, title: 'Connect Stripe', icon: ShieldCheck },
  { id: 3, title: 'Run reconciliation', icon: RefreshCw },
  { id: 4, title: 'Confirm extracted terms', icon: BadgeCheck },
  { id: 5, title: 'Review findings', icon: FileText },
  { id: 6, title: 'Recovered & billing', icon: DollarSign },
]

const emptyContractDraft = {
  customer_id: '',
  customer_name: '',
  committed_minimum_monthly: '',
  included_units: '',
  overage_rate: '',
  annual_escalator_pct: '',
  escalator_effective_date: '',
  discount_name: '',
  discount_type: 'percent',
  discount_value: '',
  discount_applies_to: 'base',
  discount_expires: '',
  clauses: {
    committed_minimum: '',
    overage: '',
    discount: '',
    escalator: '',
  },
}

function formatCurrency(value) {
  return `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

function buildReviewedContract(draft) {
  const customerId = (draft.customer_id || draft.customer_name || 'contract').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_')
  const customerName = draft.customer_name.trim()
  const committedMinimum = Number(draft.committed_minimum_monthly || 0)
  const includedUnits = Number(draft.included_units || 0)
  const overageRate = Number(draft.overage_rate || 0)
  const annualEscalatorPct = Number(draft.annual_escalator_pct || 0)
  const discountValue = Number(draft.discount_value || 0)
  const discountIsPercent = draft.discount_type === 'percent'
  const discount = draft.discount_name.trim()
    ? [{
        name: draft.discount_name.trim(),
        type: discountIsPercent ? 'percent' : 'amount',
        value: discountValue,
        applies_to: draft.discount_applies_to,
        expires: draft.discount_expires || '',
      }]
    : []

  return {
    customer_id: customerId,
    customer_name: customerName,
    committed_minimum_monthly: committedMinimum,
    included_units: includedUnits,
    overage_rate: overageRate,
    annual_escalator_pct: annualEscalatorPct,
    escalator_effective_date: draft.escalator_effective_date || '',
    discounts: discount,
    clauses: { ...draft.clauses },
    term_meta: {
      committed_minimum_monthly: {
        confidence: 0.97,
        provenance: draft.clauses.committed_minimum || 'Structured upload form',
      },
      included_units: {
        confidence: 0.97,
        provenance: draft.clauses.overage || 'Structured upload form',
      },
      overage_rate: {
        confidence: 0.97,
        provenance: draft.clauses.overage || 'Structured upload form',
      },
      annual_escalator_pct: {
        confidence: 0.97,
        provenance: draft.clauses.escalator || 'Structured upload form',
      },
      escalator_effective_date: {
        confidence: 0.97,
        provenance: draft.clauses.escalator || 'Structured upload form',
      },
      discounts: discount.length > 0 ? {
        confidence: 0.95,
        provenance: draft.clauses.discount || 'Structured upload form',
      } : undefined,
    },
  }
}

function App() {
  const [firebaseUser, setFirebaseUser] = useState(null)
  const [sessionMode, setSessionMode] = useState(null)
  const [loadingAuth, setLoadingAuth] = useState(true)
  const [activeStep, setActiveStep] = useState(1)
  const [billingPeriod, setBillingPeriod] = useState(DEFAULT_PERIOD)
  const [findings, setFindings] = useState([])
  const [allFindings, setAllFindings] = useState([])
  const [selectedFinding, setSelectedFinding] = useState(null)
  const [running, setRunning] = useState(false)
  const [statusMessage, setStatusMessage] = useState('')
  const [uploadedContracts, setUploadedContracts] = useState([])
  const [contractDraft, setContractDraft] = useState(emptyContractDraft)
  const [selectedFileName, setSelectedFileName] = useState('')
  const [contractSubmitting, setContractSubmitting] = useState(false)
  const [metrics, setMetrics] = useState(null)

  const isSampleMode = sessionMode === 'sample'
  const isAuthenticated = sessionMode === 'auth' && Boolean(firebaseUser)
  const apiReady = isSampleMode || isAuthenticated
  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (nextUser) => {
      setFirebaseUser(nextUser)
      setLoadingAuth(false)
      setSessionMode((current) => {
        if (current === 'sample') return 'sample'
        return nextUser ? 'auth' : null
      })
    })

    return unsubscribe
  }, [])

  const apiRequest = useCallback(async (path, options = {}) => {
    const headers = { ...(options.headers || {}) }
    if (!isSampleMode) {
      if (!firebaseUser) {
        throw new Error('Please sign in first')
      }
      const token = await firebaseUser.getIdToken()
      headers.Authorization = `Bearer ${token}`
    }
    let body = options.body
    if (body && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(body)
    }
    const res = await fetch(`${API_BASE}${path}`, { ...options, headers, body })
    if (!res.ok) {
      throw new Error(await res.text())
    }
    const text = await res.text()
    return text ? JSON.parse(text) : null
  }, [firebaseUser, isSampleMode])

  const loadMetrics = useCallback(async () => {
    if (!apiReady) return
    try {
      const result = await apiRequest('/metrics')
      setMetrics(result)
    } catch (error) {
      console.error(error)
    }
  }, [apiReady, apiRequest])

  const refreshFindings = useCallback(async () => {
    if (!apiReady) return
    const [pending, all] = await Promise.all([
      apiRequest('/findings/pending'),
      apiRequest('/findings'),
    ])
    void loadMetrics()
    setFindings(Array.isArray(pending) ? pending : [])
    setAllFindings(Array.isArray(all) ? all : [])
    setSelectedFinding((current) => {
      if (current && all.some((finding) => finding.finding_id === current.finding_id)) {
        return all.find((finding) => finding.finding_id === current.finding_id) || current
      }
      return (pending && pending[0]) || all[0] || null
    })
  }, [apiReady, apiRequest, loadMetrics])

  useEffect(() => {
    if (!apiReady) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refreshFindings()
  }, [apiReady, refreshFindings])

  const sampleModeLogin = async () => {
    setLoadingAuth(true)
    try {
      await signOut(auth)
    } catch {
      // Best-effort; sample mode must stay usable even if Firebase sign-out is unavailable.
    }
    setSessionMode('sample')
    setLoadingAuth(false)
  }

  const handleGoogleLogin = async () => {
    const provider = new GoogleAuthProvider()
    try {
      await signInWithPopup(auth, provider)
      setSessionMode('auth')
      setStatusMessage('Signed in with Firebase Auth.')
    } catch (error) {
      console.error(error)
      setStatusMessage('Google sign-in did not complete.')
    }
  }

  const handleLogout = async () => {
    try {
      await signOut(auth)
    } finally {
      setSessionMode(null)
      setFirebaseUser(null)
      setFindings([])
      setAllFindings([])
      setSelectedFinding(null)
      setStatusMessage('')
    }
  }

  const handleContractField = (field, value) => {
    setContractDraft((current) => ({ ...current, [field]: value }))
  }

  const handleClauseField = (field, value) => {
    setContractDraft((current) => ({
      ...current,
      clauses: { ...current.clauses, [field]: value },
    }))
  }

  const submitContract = async () => {
    if (!contractDraft.customer_name.trim()) {
      setStatusMessage('Enter a customer name before uploading.')
      return
    }
    setContractSubmitting(true)
    try {
      const payload = buildReviewedContract(contractDraft)
      await apiRequest('/ingest/contract', { method: 'POST', body: payload })
      setUploadedContracts((current) => [{ ...payload, confirmed: false, file_name: selectedFileName }, ...current.filter((item) => item.customer_id !== payload.customer_id)])
      setContractDraft(emptyContractDraft)
      setSelectedFileName('')
      setStatusMessage(`Uploaded structured contract for ${payload.customer_name}.`)
      setActiveStep(4)
    } catch (error) {
      console.error(error)
      setStatusMessage('Contract upload failed.')
    } finally {
      setContractSubmitting(false)
    }
  }

  const confirmContract = (customerId) => {
    setUploadedContracts((current) => current.map((item) => (
      item.customer_id === customerId ? { ...item, confirmed: true } : item
    )))
    setStatusMessage('Contract terms confirmed.')
  }

  const runReconciliation = async () => {
    setRunning(true)
    try {
      const result = await apiRequest(`/reconcile?period=${billingPeriod}`, { method: 'POST' })
      setStatusMessage(`Reconciliation complete: ${result.findings_found} findings.`)
      await refreshFindings()
      setActiveStep(5)
    } catch (error) {
      console.error(error)
      setStatusMessage('Reconciliation failed.')
    } finally {
      setRunning(false)
    }
  }

  const handleAction = async (findingId, action) => {
    try {
      const endpoint = action === 'approve' ? 'approve' : 'reject'
      const body = action === 'reject'
        ? { status: 'rejected', reason: 'Reviewed in dashboard' }
        : undefined
      await apiRequest(`/findings/${findingId}/${endpoint}`, {
        method: 'POST',
        body,
      })
      setStatusMessage(action === 'approve' ? 'Finding approved.' : 'Finding rejected.')
      await refreshFindings()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not update the finding.')
    }
  }

  const markRecovered = async (findingId) => {
    try {
      await apiRequest(`/findings/${findingId}/recovered`, { method: 'POST' })
      setStatusMessage('Finding marked recovered.')
      await refreshFindings()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not mark the finding recovered.')
    }
  }

  const exportFindings = async () => {
    try {
      const headers = {}
      if (!isSampleMode) {
        if (!firebaseUser) throw new Error('Please sign in first')
        headers.Authorization = `Bearer ${await firebaseUser.getIdToken()}`
      }
      const res = await fetch(`${API_BASE}/findings/export`, { headers })
      if (!res.ok) throw new Error(await res.text())
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'recoup_findings.csv'
      link.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      console.error(error)
      setStatusMessage('Export failed.')
    }
  }

  const chargeSuccessFee = async () => {
    try {
      const result = await apiRequest('/billing/charge-success-fee', { method: 'POST' })
      const billing = result?.billing || {}
      setStatusMessage(billing.message || `Success fee status: ${billing.status}`)
      await loadMetrics()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not charge the success fee.')
    }
  }

  const approvedFindings = useMemo(
    () => allFindings.filter((finding) => finding.status === 'approved'),
    [allFindings],
  )
  const recoveredFindings = useMemo(
    () => allFindings.filter((finding) => finding.status === 'recovered'),
    [allFindings],
  )

  const needsHumanReview = useMemo(() => {
    const unconfirmedContracts = uploadedContracts.filter((item) => !item.confirmed)
    return [...findings, ...unconfirmedContracts]
  }, [findings, uploadedContracts])

  const reviewLabel = isSampleMode ? 'Sample data' : firebaseUser?.email || 'Authenticated'

  if (loadingAuth) {
    return (
      <div className="app-container loading-shell">
        <RefreshCw className="spin" size={28} />
        <span>Loading session…</span>
      </div>
    )
  }

  if (!apiReady) {
    return (
      <div className="app-container auth-shell">
        <div className="glass-panel auth-card">
          <div className="auth-hero">
            <div className="auth-mark">
              <Building2 size={28} />
            </div>
            <div>
              <h1 className="title-glow">Recoup</h1>
              <p className="auth-subtitle">Revenue assurance for contract recovery workflows.</p>
            </div>
          </div>

          <div className="auth-note glass-panel">
            <LockKeyhole size={16} />
            <span>Firebase Auth is required for real data. Sample data is available explicitly below.</span>
          </div>

          <div className="auth-actions">
            <button className="btn-primary auth-button" onClick={handleGoogleLogin}>
              <LogIn size={16} />
              Sign in with Google
            </button>
            <button className="btn-secondary auth-button" onClick={sampleModeLogin}>
              <Sparkles size={16} />
              Try with sample data
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="app-container recoup-shell">
      <header className="header shell-header">
        <div>
          <div className="brand-row">
            <h1 className="title-glow brand-title">
              <Building2 size={28} />
              Recoup
            </h1>
            <span className={`session-pill ${isSampleMode ? 'sample' : 'auth'}`}>{reviewLabel}</span>
          </div>
          <p className="subtitle">Revenue recovery dashboard</p>
        </div>
        <div className="header-actions">
          <div className="period-picker glass-panel">
            <label htmlFor="billing-period">Period</label>
            <input
              id="billing-period"
              value={billingPeriod}
              onChange={(event) => setBillingPeriod(event.target.value)}
              placeholder="YYYY-MM"
            />
          </div>
          <button className="btn-primary" onClick={runReconciliation} disabled={running}>
            {running ? <RefreshCw className="spin" size={16} /> : <FileText size={16} />}
            {running ? 'Reconciling…' : 'Run reconciliation'}
          </button>
          <button className="btn-danger" onClick={handleLogout} title="Sign out">
            <LogOut size={16} />
          </button>
        </div>
      </header>

      {isSampleMode && (
        <div className="glass-panel mode-banner sample-banner">
          <Sparkles size={16} />
          Sample data is active. Requests are sent without an Authorization header.
          <button className="btn-secondary" onClick={() => setSessionMode(null)}>
            Exit sample mode
          </button>
        </div>
      )}

      <div className="stepper-grid">
        <aside className="glass-panel sidebar-panel">
          <div className="stepper-list">
            {STEPS.map((step) => {
              const Icon = step.icon
              void Icon
              const active = activeStep === step.id
              return (
                <button
                  key={step.id}
                  type="button"
                  className={`stepper-item ${active ? 'active' : ''}`}
                  onClick={() => setActiveStep(step.id)}
                >
                  <span className="step-icon">
                    <Icon size={16} />
                  </span>
                  <span className="step-copy">
                    <span className="step-index">Step {step.id}</span>
                    <span className="step-title">{step.title}</span>
                  </span>
                  <ChevronRight size={16} />
                </button>
              )
            })}
          </div>
        </aside>

        <main className="step-content">
          {activeStep === 1 && (
            <section className="glass-panel panel-card onboarding-card">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Step 1</p>
                  <h2>Upload contracts</h2>
                </div>
                <span className="hint-pill">PDF drop slots in later</span>
              </div>

              <div className="upload-grid">
                <div className="dropzone glass-panel">
                  <Upload size={22} />
                  <div>
                    <strong>Drag a contract file here</strong>
                    <p>Manual structured entry is enabled now. Raw PDF upload comes later.</p>
                  </div>
                  <input
                    type="file"
                    accept=".pdf,.txt,.doc,.docx"
                    onChange={(event) => setSelectedFileName(event.target.files?.[0]?.name || '')}
                  />
                  {selectedFileName && <span className="file-chip">{selectedFileName}</span>}
                </div>

                <div className="contract-form-grid">
                  <label>
                    Customer name
                    <input value={contractDraft.customer_name} onChange={(event) => handleContractField('customer_name', event.target.value)} />
                  </label>
                  <label>
                    Customer ID
                    <input value={contractDraft.customer_id} onChange={(event) => handleContractField('customer_id', event.target.value)} placeholder="acme" />
                  </label>
                  <label>
                    Committed minimum monthly
                    <input type="number" value={contractDraft.committed_minimum_monthly} onChange={(event) => handleContractField('committed_minimum_monthly', event.target.value)} />
                  </label>
                  <label>
                    Included units
                    <input type="number" value={contractDraft.included_units} onChange={(event) => handleContractField('included_units', event.target.value)} />
                  </label>
                  <label>
                    Overage rate
                    <input type="number" step="0.01" value={contractDraft.overage_rate} onChange={(event) => handleContractField('overage_rate', event.target.value)} />
                  </label>
                  <label>
                    Annual escalator %
                    <input type="number" step="0.01" value={contractDraft.annual_escalator_pct} onChange={(event) => handleContractField('annual_escalator_pct', event.target.value)} />
                  </label>
                  <label>
                    Escalator effective date
                    <input type="date" value={contractDraft.escalator_effective_date} onChange={(event) => handleContractField('escalator_effective_date', event.target.value)} />
                  </label>
                  <label>
                    Discount name
                    <input value={contractDraft.discount_name} onChange={(event) => handleContractField('discount_name', event.target.value)} />
                  </label>
                  <label>
                    Discount value
                    <input type="number" step="0.01" value={contractDraft.discount_value} onChange={(event) => handleContractField('discount_value', event.target.value)} />
                  </label>
                  <label>
                    Discount expires
                    <input type="date" value={contractDraft.discount_expires} onChange={(event) => handleContractField('discount_expires', event.target.value)} />
                  </label>
                  <label>
                    Discount applies to
                    <input value={contractDraft.discount_applies_to} onChange={(event) => handleContractField('discount_applies_to', event.target.value)} />
                  </label>
                </div>
              </div>

              <div className="clause-grid">
                <label>
                  Committed minimum clause quote
                  <textarea value={contractDraft.clauses.committed_minimum} onChange={(event) => handleClauseField('committed_minimum', event.target.value)} rows={3} />
                </label>
                <label>
                  Overage clause quote
                  <textarea value={contractDraft.clauses.overage} onChange={(event) => handleClauseField('overage', event.target.value)} rows={3} />
                </label>
                <label>
                  Discount clause quote
                  <textarea value={contractDraft.clauses.discount} onChange={(event) => handleClauseField('discount', event.target.value)} rows={3} />
                </label>
                <label>
                  Escalator clause quote
                  <textarea value={contractDraft.clauses.escalator} onChange={(event) => handleClauseField('escalator', event.target.value)} rows={3} />
                </label>
              </div>

              <div className="panel-footer">
                <span className="footer-note">Structured uploads are saved to the backend and shown in the confirmation step.</span>
                <button className="btn-primary" onClick={submitContract} disabled={contractSubmitting}>
                  {contractSubmitting ? <RefreshCw className="spin" size={16} /> : <Upload size={16} />}
                  {contractSubmitting ? 'Uploading…' : 'Upload contract'}
                </button>
              </div>
            </section>
          )}

          {activeStep === 2 && (
            <section className="glass-panel panel-card onboarding-card">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Step 2</p>
                  <h2>Connect Stripe</h2>
                </div>
                <span className="connected-pill">
                  <CheckCircle2 size={14} /> Server-side connected
                </span>
              </div>
              <div className="stacked-copy">
                <div className="info-box glass-panel">
                  <ShieldCheck size={18} />
                  <div>
                    <strong>Read-only Stripe access</strong>
                    <p>The Stripe API key stays on the server. This screen only confirms billing is available for reconciliation.</p>
                  </div>
                </div>
                <div className="info-box glass-panel">
                  <DollarSign size={18} />
                  <div>
                    <strong>Outcome-based pricing</strong>
                    <p>Recoup charges 20% of dollars actually recovered — tracked on the Recovered &amp; billing step.</p>
                  </div>
                </div>
              </div>
            </section>
          )}

          {activeStep === 3 && (
            <section className="glass-panel panel-card onboarding-card">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Step 3</p>
                  <h2>Run reconciliation</h2>
                </div>
                <span className="hint-pill">Period {billingPeriod}</span>
              </div>
              <div className="stacked-copy">
                <p>Run the deterministic revenue recovery pass against the currently selected billing period.</p>
                <div className="info-box glass-panel">
                  <FileText size={18} />
                  <div>
                    <strong>Findings and review items</strong>
                    <p>Any review items stay highlighted for human attention before approval.</p>
                  </div>
                </div>
                <button className="btn-primary action-button" onClick={runReconciliation} disabled={running}>
                  {running ? <RefreshCw className="spin" size={16} /> : <RefreshCw size={16} />}
                  {running ? 'Reconciling…' : 'Run reconciliation'}
                </button>
                {statusMessage && <div className="status-message">{statusMessage}</div>}
              </div>
            </section>
          )}

          {activeStep === 4 && (
            <section className="glass-panel panel-card onboarding-card">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Step 4</p>
                  <h2>Confirm extracted terms</h2>
                </div>
                <span className="warning-pill">Needs human review</span>
              </div>

              {uploadedContracts.length === 0 ? (
                <div className="empty-state glass-panel">
                  <BadgeCheck size={22} />
                  <p>Upload a contract first to confirm the extracted terms and provenance.</p>
                </div>
              ) : (
                <div className="contract-review-list">
                  {uploadedContracts.map((contract) => (
                    <article key={contract.customer_id} className="glass-panel contract-review-card">
                      <div className="review-header">
                        <div>
                          <h3>{contract.customer_name}</h3>
                          <p>{contract.customer_id}</p>
                        </div>
                        <span className={contract.confirmed ? 'badge badge-approved' : 'badge badge-pending'}>
                          {contract.confirmed ? 'Confirmed' : 'Needs human review'}
                        </span>
                      </div>

                      <div className="term-list">
                        {[
                          ['Committed minimum', formatCurrency(contract.committed_minimum_monthly), contract.term_meta?.committed_minimum_monthly],
                          ['Included units', Number(contract.included_units || 0).toLocaleString(), contract.term_meta?.included_units],
                          ['Overage rate', formatCurrency(contract.overage_rate), contract.term_meta?.overage_rate],
                          ['Annual escalator', `${Number(contract.annual_escalator_pct || 0) * 100}%`, contract.term_meta?.annual_escalator_pct],
                          ['Escalator effective date', contract.escalator_effective_date || '—', contract.term_meta?.escalator_effective_date],
                        ].map(([label, value, meta]) => (
                          <div key={label} className="term-row">
                            <span>{label}</span>
                            <strong>{value}</strong>
                            <small>
                              {meta ? `${Math.round(meta.confidence * 100)}% • ${meta.provenance}` : 'Manual structured entry'}
                            </small>
                          </div>
                        ))}
                        {contract.discounts.map((discount) => (
                          <div key={discount.name} className="term-row">
                            <span>Discount</span>
                            <strong>{discount.name}</strong>
                            <small>
                              {contract.term_meta?.discounts
                                ? `${Math.round(contract.term_meta.discounts.confidence * 100)}% • ${contract.term_meta.discounts.provenance}`
                                : 'Manual structured entry'}
                            </small>
                          </div>
                        ))}
                      </div>

                      <div className="review-actions">
                        <button className="btn-secondary" onClick={() => confirmContract(contract.customer_id)}>
                          <CheckCircle2 size={16} /> Confirm terms
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}

          {activeStep === 6 && (
            <section className="glass-panel panel-card onboarding-card">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Step 6</p>
                  <h2>Recovered &amp; billing</h2>
                </div>
                <span className="connected-pill">
                  <DollarSign size={14} /> 20% success fee
                </span>
              </div>

              <div className="metric-grid">
                <div className="glass-panel metric-card">
                  <span className="metric-label">Recovered to date</span>
                  <strong className="metric-value">{formatCurrency(metrics?.recovered_to_date)}</strong>
                </div>
                <div className="glass-panel metric-card">
                  <span className="metric-label">Your success fee this month</span>
                  <strong className="metric-value">{formatCurrency(metrics?.success_fee_this_month)}</strong>
                  <small>20% of {formatCurrency(metrics?.recovered_this_month)} recovered this month</small>
                </div>
                <div className="glass-panel metric-card">
                  <span className="metric-label">Potential (not yet recovered)</span>
                  <strong className="metric-value">{formatCurrency(metrics?.potential_monthly_recoverable)}</strong>
                </div>
              </div>

              <div className="review-actions">
                <button className="btn-secondary" onClick={exportFindings}>
                  <Download size={16} /> Export findings (CSV)
                </button>
                <button className="btn-primary" onClick={chargeSuccessFee}>
                  <DollarSign size={16} /> Bill success fee this month
                </button>
              </div>

              <div className="contract-review-list">
                <h3 className="queue-title">Approved — awaiting recovery</h3>
                {approvedFindings.length === 0 ? (
                  <div className="empty-state glass-panel">
                    <CheckCircle2 size={22} />
                    <p>No approved findings awaiting recovery.</p>
                  </div>
                ) : (
                  approvedFindings.map((finding) => (
                    <article key={finding.finding_id} className="glass-panel contract-review-card">
                      <div className="review-header">
                        <div>
                          <h3>{finding.customer_name}</h3>
                          <p>{finding.title}</p>
                        </div>
                        <span className="amount">{formatCurrency(finding.monthly_recoverable)}</span>
                      </div>
                      <div className="review-actions">
                        <button className="btn-primary" onClick={() => markRecovered(finding.finding_id)}>
                          <DollarSign size={16} /> Mark recovered
                        </button>
                      </div>
                    </article>
                  ))
                )}

                {recoveredFindings.length > 0 && (
                  <>
                    <h3 className="queue-title">Recovered</h3>
                    {recoveredFindings.map((finding) => (
                      <article key={finding.finding_id} className="glass-panel contract-review-card">
                        <div className="review-header">
                          <div>
                            <h3>{finding.customer_name}</h3>
                            <p>{finding.title}</p>
                          </div>
                          <span className="badge badge-approved">Recovered • {formatCurrency(finding.monthly_recoverable)}</span>
                        </div>
                      </article>
                    ))}
                  </>
                )}
              </div>
            </section>
          )}

          {activeStep === 5 && (
            <section className="glass-panel panel-card review-card">
              <div className="panel-heading review-heading">
                <div>
                  <p className="eyebrow">Step 5</p>
                  <h2>Review findings</h2>
                </div>
                <span className="review-tag">Needs human review • {allFindings.length} total</span>
              </div>

              {needsHumanReview.length > 0 && (
                <div className="review-callout glass-panel">
                  <AlertCircle size={18} />
                  <div>
                    <strong>There are items needing human review before approval.</strong>
                    <p>Review the active queue, confirm terms, and then approve or reject each finding.</p>
                  </div>
                </div>
              )}

              <div className="dashboard-grid">
                <div className="queue-list">
                  <h3 className="queue-title">Pending Review</h3>
                  {findings.length === 0 ? (
                    <div className="empty-state glass-panel">
                      <CheckCircle2 size={28} />
                      <p>No pending findings.</p>
                    </div>
                  ) : (
                    findings.map((finding) => (
                      <button
                        type="button"
                        key={finding.finding_id}
                        className={`glass-panel queue-item ${selectedFinding?.finding_id === finding.finding_id ? 'active' : ''}`}
                        onClick={() => setSelectedFinding(finding)}
                      >
                        <div className="queue-topline">
                          <span className="badge badge-pending">Action required</span>
                          <span className="amount">{formatCurrency(finding.monthly_recoverable)}</span>
                        </div>
                        <div className="queue-name">{finding.customer_name}</div>
                        <div className="queue-title-text">{finding.title}</div>
                      </button>
                    ))
                  )}
                </div>

                {selectedFinding ? (
                  <div className="glass-panel detail-view">
                    <div className="detail-header">
                      <div className="detail-topline">
                        <div>
                          <h2>{selectedFinding.customer_name}</h2>
                          <div className="detail-meta">
                            <span>ID: {selectedFinding.finding_id}</span>
                            <span>Period: {selectedFinding.period}</span>
                          </div>
                        </div>
                        <div className="amount detail-amount">{formatCurrency(selectedFinding.monthly_recoverable)} / mo</div>
                      </div>
                    </div>

                    <div className="detail-content">
                      <div>
                        <h3 className="detail-section-title">Discrepancy details</h3>
                        <div className="info-group">
                          <div className="info-label">Title</div>
                          <div>{selectedFinding.title}</div>
                        </div>
                        <div className="info-group">
                          <div className="info-label">Engine reasoning</div>
                          <div className="detail-copy">{selectedFinding.detail}</div>
                        </div>
                      </div>

                      <div>
                        <h3 className="detail-section-title">
                          <AlertCircle size={18} /> Contract grounding
                        </h3>
                        <div className="info-group">
                          <div className="info-label">Confidence score</div>
                          <div>{(selectedFinding.confidence_score * 100).toFixed(0)}%</div>
                        </div>
                        <div className="info-group">
                          <div className="info-label">Exact clause quote (provenance)</div>
                          <div className="provenance-box">
                            “{selectedFinding.provenance || 'No direct quote available in legacy data.'}”
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="action-bar">
                      <button className="btn-danger action-button" onClick={() => handleAction(selectedFinding.finding_id, 'reject')}>
                        <XCircle size={16} /> Reject finding
                      </button>
                      <button className="btn-success action-button" onClick={() => handleAction(selectedFinding.finding_id, 'approve')}>
                        <CheckCircle2 size={16} /> Approve &amp; draft invoice
                      </button>
                      <button className="btn-primary action-button" onClick={() => markRecovered(selectedFinding.finding_id)}>
                        <DollarSign size={16} /> Mark recovered
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="glass-panel empty-detail">
                    Select a finding from the queue to review.
                  </div>
                )}
              </div>
            </section>
          )}
        </main>
      </div>

      {statusMessage && activeStep !== 3 && <div className="floating-status glass-panel">{statusMessage}</div>}
    </div>
  )
}

export default App
