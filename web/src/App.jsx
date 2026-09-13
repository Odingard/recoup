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
void [AlertCircle, Building2, ChevronRight, CheckCircle2, DollarSign, Download, FileText, LogIn, LogOut, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, Upload, BadgeCheck, XCircle]

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

function formatRate(value) {
  const n = Number(value || 0)
  const digits = n !== 0 && Math.abs(n) < 0.01 ? 6 : 2
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: digits })}`
}

function failureMessage(prefix, error) {
  const detail = error instanceof Error ? error.message.trim() : ''
  if (!detail || detail.startsWith('<')) return `${prefix}.`
  return `${prefix}: ${detail.slice(0, 240)}`
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
  const [bulkResult, setBulkResult] = useState(null)
  const [bulkUploading, setBulkUploading] = useState(false)
  const [contractSubmitting, setContractSubmitting] = useState(false)
  const [metrics, setMetrics] = useState(null)
  const [connectorSubmitting, setConnectorSubmitting] = useState(false)
  const [connectorStatus, setConnectorStatus] = useState('')
  const [connectorConnection, setConnectorConnection] = useState(null)
  const [renewals, setRenewals] = useState([])
  const [billing, setBilling] = useState(null)
  const [syncingRecoveries, setSyncingRecoveries] = useState(false)
  const [reviewQueue, setReviewQueue] = useState([])
  const [assurance, setAssurance] = useState(null)

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
    if (isSampleMode) {
      headers['X-Recoup-Sample'] = '1'
    } else {
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
      let detail = await res.text()
      try {
        detail = JSON.parse(detail)?.detail || detail
      } catch { /* keep raw text */ }
      throw new Error(detail)
    }
    const text = await res.text()
    return text ? JSON.parse(text) : null
  }, [firebaseUser, isSampleMode])

  const loadConnectorStatus = useCallback(async () => {
    if (!apiReady) return
    try {
      const result = await apiRequest('/connector/stripe/status')
      setConnectorConnection(result)
      setConnectorStatus(result?.message || '')
    } catch (error) {
      console.error(error)
    }
  }, [apiReady, apiRequest])

  const loadMetrics = useCallback(async () => {
    if (!apiReady) return
    try {
      const result = await apiRequest('/metrics')
      setMetrics(result)
    } catch (error) {
      console.error(error)
    }
  }, [apiReady, apiRequest])

  const loadAssurance = useCallback(async () => {
    if (!apiReady) return
    try {
      const result = await apiRequest('/assurance/status')
      setAssurance(result)
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
    void loadAssurance()
    setFindings(Array.isArray(pending) ? pending : [])
    setAllFindings(Array.isArray(all) ? all : [])
    setSelectedFinding((current) => {
      if (current && all.some((finding) => finding.finding_id === current.finding_id)) {
        return all.find((finding) => finding.finding_id === current.finding_id) || current
      }
      return (pending && pending[0]) || all[0] || null
    })
  }, [apiReady, apiRequest, loadMetrics, loadAssurance])

  useEffect(() => {
    if (!apiReady) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refreshFindings()
  }, [apiReady, refreshFindings])

  useEffect(() => {
    if (!apiReady) return
    const handle = window.setTimeout(() => {
      void loadConnectorStatus()
    }, 0)
    return () => window.clearTimeout(handle)
  }, [apiReady, loadConnectorStatus])

  useEffect(() => {
    if (!apiReady) return
    apiRequest('/renewals')
      .then((rows) => setRenewals(Array.isArray(rows) ? rows : []))
      .catch(() => setRenewals([]))
  }, [apiReady, apiRequest])

  const loadBillingStatus = useCallback(async () => {
    if (!apiReady) return
    try {
      const result = await apiRequest('/billing/status')
      setBilling(result)
    } catch (error) {
      console.error(error)
    }
  }, [apiReady, apiRequest])

  useEffect(() => {
    if (!apiReady) return
    const handle = window.setTimeout(() => {
      void loadBillingStatus()
    }, 0)
    return () => window.clearTimeout(handle)
  }, [apiReady, loadBillingStatus])

  const loadContracts = useCallback(async () => {
    if (!apiReady) return
    try {
      const result = await apiRequest('/contracts')
      const list = result?.contracts || []
      setUploadedContracts(list.map((c) => ({
        ...c, confirmed: Boolean(c.confirmed), discounts: c.discounts || [],
      })))
    } catch (error) {
      console.error(error)
    }
  }, [apiReady, apiRequest])

  useEffect(() => {
    if (!apiReady) return
    const handle = window.setTimeout(() => {
      void loadContracts()
    }, 0)
    return () => window.clearTimeout(handle)
  }, [apiReady, loadContracts])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const billingSetup = params.get('billing_setup')
    if (!billingSetup) return
    if (billingSetup === 'cancelled') {
      const handle = window.setTimeout(() => {
        setStatusMessage('Card setup cancelled.')
      }, 0)
      return () => window.clearTimeout(handle)
    }
    let cancelled = false
    const complete = async () => {
      try {
        const headers = {}
        if (isSampleMode) return
        if (!firebaseUser) return
        headers.Authorization = `Bearer ${await firebaseUser.getIdToken()}`
        headers['Content-Type'] = 'application/json'
        const res = await fetch(`${API_BASE}/billing/setup-complete`, {
          method: 'POST', headers, body: JSON.stringify({ session_id: billingSetup }),
        })
        const payload = await res.json()
        if (cancelled) return
        if (payload?.status === 'success') {
          setStatusMessage('Payment method saved — proof unlocked.')
          void loadBillingStatus()
        } else {
          setStatusMessage(payload?.message || 'Payment method was not saved.')
        }
      } catch (error) {
        console.error(error)
        if (!cancelled) setStatusMessage('Payment method was not saved.')
      }
    }
    void complete()
    return () => { cancelled = true }
  }, [firebaseUser, isSampleMode, loadBillingStatus])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const stripeConnect = params.get('stripe_connect')
    if (!stripeConnect) return
    const handle = window.setTimeout(() => {
      if (stripeConnect === 'success') {
        setConnectorStatus('Stripe App connected successfully.')
        setActiveStep(2)
      } else if (stripeConnect === 'error') {
        setConnectorStatus(params.get('message') || 'Stripe connection failed.')
        setActiveStep(2)
      }
    }, 0)
    return () => window.clearTimeout(handle)
  }, [])

  const sampleModeLogin = useCallback(async () => {
    setLoadingAuth(true)
    try {
      await signOut(auth)
    } catch {
      // Best-effort; sample mode must stay usable even if Firebase sign-out is unavailable.
    }
    setSessionMode('sample')
    setLoadingAuth(false)
  }, [])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('sample') !== '1' || sessionMode !== null) return
    const handle = window.setTimeout(() => {
      void sampleModeLogin()
    }, 0)
    return () => window.clearTimeout(handle)
  }, [sessionMode, sampleModeLogin])

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
      setConnectorConnection(null)
      setMetrics(null)
      setConnectorStatus('')
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
      setStatusMessage(failureMessage('Contract upload failed', error))
    } finally {
      setContractSubmitting(false)
    }
  }

  const handleBulkUpload = async (event) => {
    const fileList = Array.from(event.target.files || [])
    event.target.value = ''
    if (!fileList.length) return
    setSelectedFileName(fileList[0].name)
    setBulkUploading(true)
    setBulkResult(null)
    try {
      const form = new FormData()
      fileList.forEach((f) => form.append('files', f))
      const result = await apiRequest('/ingest/bulk', { method: 'POST', body: form })
      setBulkResult(result)
      setReviewQueue(result?.needs_review || [])
      const filesByName = Object.fromEntries((result?.files || []).map((f) => [f.name, f]))
      ;(result?.contract_records || []).forEach((contract) => {
        const fileEntry = filesByName[contract.customer_id] || filesByName[contract.file_name]
        const fileName = contract.file_name || fileEntry?.name
        setUploadedContracts((current) => [
          { ...contract, confirmed: false, discounts: contract.discounts || [], ...(fileName ? { file_name: fileName } : {}) },
          ...current.filter((item) => item.customer_id !== contract.customer_id),
        ])
      })
      const nr = result?.needs_review?.length
      setStatusMessage(
        result?.status === 'needs_review'
          ? (result.message || 'Bulk upload needs review.')
          : `Bulk upload: ${result.contracts} contracts, ${result.invoices} invoices, ${result.usage} usage rows.` +
            (nr ? ` ${nr} item(s) need review — see Step 5.` : ''))
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Bulk upload failed', error))
    } finally {
      setBulkUploading(false)
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
      setReviewQueue(result?.needs_review || [])
      setStatusMessage(`Reconciliation complete: ${result.findings_found} findings.` + (result?.needs_review_count ? ` ${result.needs_review_count} item(s) need review — see Step 5.` : ''))
      await refreshFindings()
      setActiveStep(5)
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Reconciliation failed', error))
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
      const result = await apiRequest(`/findings/${findingId}/${endpoint}`, {
        method: 'POST',
        body,
      })
      setStatusMessage(result.status !== 'approved' && result.status !== 'rejected'
        ? (result.message || 'Sample mode is read-only; approval was not recorded.')
        : (action === 'approve' ? 'Finding approved.' : 'Finding rejected.'))
      await refreshFindings()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not update the finding.')
    }
  }

  const [recoveryForm, setRecoveryForm] = useState(null)
  const [recoveryFields, setRecoveryFields] = useState({ ref: '', amount: '', date: '', url: '', note: '' })

  const openRecoveryForm = (finding, kind) => {
    const prefill = kind === 'payment'
      ? (finding.corrective_invoice?.amount ?? finding.monthly_recoverable)
      : finding.monthly_recoverable
    setRecoveryForm({ id: finding.finding_id, kind })
    setRecoveryFields({
      ref: '',
      amount: String(prefill ?? ''),
      date: '',
      url: '',
      note: '',
    })
  }

  const submitRecoveryForm = async () => {
    if (!recoveryForm) return
    const { id, kind } = recoveryForm
    const amount = Number(recoveryFields.amount)
    try {
      if (kind === 'invoice') {
        await apiRequest(`/findings/${id}/invoiced`, {
          method: 'POST',
          body: {
            invoice_ref: recoveryFields.ref,
            invoice_amount: amount,
            invoice_date: recoveryFields.date || null,
            invoice_url: recoveryFields.url || null,
            note: recoveryFields.note,
          },
        })
        setStatusMessage('Corrective invoice recorded.')
      } else {
        await apiRequest(`/findings/${id}/recovered`, {
          method: 'POST',
          body: {
            paid_amount: amount,
            paid_date: recoveryFields.date || null,
            payment_ref: recoveryFields.ref || null,
            note: recoveryFields.note,
          },
        })
        setStatusMessage('Payment recorded — finding recovered.')
      }
      setRecoveryForm(null)
      await refreshFindings()
      await loadMetrics()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not record recovery evidence.')
    }
  }

  const markDisputed = async (findingId) => {
    const reason = window.prompt('Reason for dispute (optional):') || ''
    try {
      await apiRequest(`/findings/${findingId}/disputed`, { method: 'POST', body: { reason } })
      setStatusMessage('Finding marked disputed.')
      await refreshFindings()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not mark the finding disputed.')
    }
  }

  const markWrittenOff = async (findingId) => {
    const reason = window.prompt('Write-off reason (optional):') || ''
    try {
      await apiRequest(`/findings/${findingId}/written-off`, { method: 'POST', body: { reason } })
      setStatusMessage('Finding written off.')
      await refreshFindings()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not write off the finding.')
    }
  }

  const renderRecoveryForm = (finding) => {
    if (!recoveryForm || recoveryForm.id !== finding.finding_id) return null
    const isInvoice = recoveryForm.kind === 'invoice'
    return (
      <div className="recovery-form">
        <label>
          {isInvoice ? 'Invoice ref' : 'Payment ref'}
          <input
            value={recoveryFields.ref}
            onChange={(event) => setRecoveryFields((f) => ({ ...f, ref: event.target.value }))}
            placeholder={isInvoice ? 'INV-1042' : 'txn / check ref'}
          />
        </label>
        <label>
          {isInvoice ? 'Invoice amount ($)' : 'Paid amount ($)'}
          <input
            type="number"
            min="0"
            step="0.01"
            value={recoveryFields.amount}
            onChange={(event) => setRecoveryFields((f) => ({ ...f, amount: event.target.value }))}
          />
        </label>
        <label>
          {isInvoice ? 'Invoice date' : 'Paid date'}
          <input
            type="date"
            value={recoveryFields.date}
            onChange={(event) => setRecoveryFields((f) => ({ ...f, date: event.target.value }))}
          />
        </label>
        {isInvoice && (
          <label>
            Invoice URL (optional)
            <input
              value={recoveryFields.url}
              onChange={(event) => setRecoveryFields((f) => ({ ...f, url: event.target.value }))}
              placeholder="https://…"
            />
          </label>
        )}
        <div className="review-actions">
          <button className="btn-primary" onClick={submitRecoveryForm}>
            {isInvoice ? 'Save invoice' : 'Save payment'}
          </button>
          <button className="btn-secondary" onClick={() => setRecoveryForm(null)}>Cancel</button>
        </div>
      </div>
    )
  }

  const exportFindings = async () => {
    try {
      const headers = {}
      if (isSampleMode) {
        headers['X-Recoup-Sample'] = '1'
      } else {
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
      setStatusMessage(failureMessage('Export failed', error))
    }
  }

  const openAuditReport = async () => {
    try {
      const result = await apiRequest('/report/share', { method: 'POST' })
      if (!result?.url) throw new Error(result?.message || 'No share URL returned')
      window.open(result.url, '_blank', 'noopener')
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not open the audit report.')
    }
  }

  const downloadReportPdf = async () => {
    try {
      const headers = {}
      if (isSampleMode) {
        headers['X-Recoup-Sample'] = '1'
      } else {
        if (!firebaseUser) throw new Error('Please sign in first')
        headers.Authorization = `Bearer ${await firebaseUser.getIdToken()}`
      }
      const res = await fetch(`${API_BASE}/report.pdf`, { headers })
      if (!res.ok) throw new Error(await res.text())
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'recoup_report.pdf'
      link.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not download the PDF report.')
    }
  }

  const startBillingSetup = async () => {
    try {
      const result = await apiRequest('/billing/setup-session', { method: 'POST' })
      if (result?.status === 'success' && result.url) {
        window.location.assign(result.url)
        return
      }
      setStatusMessage(result?.message || 'Could not start card setup.')
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not start card setup.')
    }
  }

  const syncStripeRecoveries = async () => {
    setSyncingRecoveries(true)
    try {
      const result = await apiRequest('/billing/sync-recoveries', { method: 'POST' })
      if (result?.status === 'needs_connector') {
        setStatusMessage('Connect Stripe (Step 2) to verify paid invoices.')
      } else {
        const n = result?.recovered?.length || 0
        setStatusMessage(`Checked ${result?.checked ?? 0} invoices — ${n} newly recovered.`)
        await refreshFindings()
      }
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Stripe sync failed', error))
    } finally {
      setSyncingRecoveries(false)
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

  const [trueupSender, setTrueupSender] = useState('')

  const downloadTrueupPdf = async (customerId, customerName) => {
    try {
      const headers = {}
      if (isSampleMode) {
        headers['X-Recoup-Sample'] = '1'
      } else {
        if (!firebaseUser) throw new Error('Please sign in first')
        headers.Authorization = `Bearer ${await firebaseUser.getIdToken()}`
      }
      const params = trueupSender ? `?sender=${encodeURIComponent(trueupSender)}` : ''
      const res = await fetch(`${API_BASE}/trueup/${customerId}.pdf${params}`, { headers })
      if (!res.ok) throw new Error(await res.text())
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `trueup_${customerId}.pdf`
      link.click()
      URL.revokeObjectURL(url)
      setStatusMessage(`True-up pack for ${customerName || customerId} downloaded.`)
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not generate the true-up pack.')
    }
  }

  const deleteAccountData = async () => {
    const confirm = window.prompt('Type DELETE to confirm')
    if (confirm !== 'DELETE') {
      if (confirm !== null) setStatusMessage('Deletion cancelled — type DELETE to confirm.')
      return
    }
    try {
      const result = await apiRequest('/account/data', { method: 'DELETE', body: { confirm } })
      setFindings([])
      setAllFindings([])
      setSelectedFinding(null)
      setUploadedContracts([])
      setMetrics(null)
      setStatusMessage(result?.status === 'deleted' ? 'All account data deleted.' : (result?.message || 'Deletion did not complete.'))
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Account data deletion failed', error))
    }
  }

  const approvedFindings = useMemo(
    () => allFindings.filter((finding) => finding.status === 'approved'),
    [allFindings],
  )
  const invoicedFindings = useMemo(
    () => allFindings.filter((finding) => finding.status === 'invoiced' || finding.status === 'disputed'),
    [allFindings],
  )
  const recoveredFindings = useMemo(
    () => allFindings.filter((finding) => finding.status === 'recovered'),
    [allFindings],
  )

  const trueupCustomers = useMemo(() => {
    const statuses = isSampleMode ? ['approved', 'invoiced', 'disputed', 'open'] : ['approved', 'invoiced', 'disputed']
    const map = {}
    allFindings.forEach((f) => {
      if (!statuses.includes(f.status || 'open')) return
      const entry = map[f.customer_id] ||= { customer_id: f.customer_id, customer_name: f.customer_name || f.customer_id, total: 0 }
      entry.total += f.monthly_recoverable || 0
    })
    return Object.values(map).sort((a, b) => b.total - a.total)
  }, [allFindings, isSampleMode])

  const needsHumanReview = useMemo(() => {
    const unconfirmedContracts = uploadedContracts.filter((item) => !item.confirmed)
    return [...findings, ...unconfirmedContracts]
  }, [findings, uploadedContracts])

  const reviewLabel = isSampleMode ? 'Sample data' : firebaseUser?.email || 'Authenticated'

  const proofLocked = !isSampleMode && Boolean(billing?.configured) && !billing?.card_on_file
  const lockTitle = 'Add a payment method to unlock'

  const startStripeInstall = useCallback(async () => {
    if (isSampleMode) {
      setConnectorStatus('Sample mode does not connect to Stripe.')
      return
    }
    setConnectorSubmitting(true)
    setConnectorStatus('')
    try {
      const result = await apiRequest('/connector/stripe/oauth/start', { method: 'POST' })
      if (result?.install_url) {
        setConnectorStatus('Redirecting to Stripe App install…')
        window.location.href = result.install_url
        return
      }
      setConnectorStatus(result?.message || 'Could not start the Stripe App install.')
    } catch (error) {
      console.error(error)
      setConnectorStatus(error.message || 'Could not start the Stripe App install.')
    } finally {
      setConnectorSubmitting(false)
    }
  }, [apiRequest, isSampleMode])

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

          <a className="back-to-site" href="/">&larr; Recoup</a>
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
          Sample data is active. Requests use synthetic data and are not tied to your account.
          <button className="btn-secondary" onClick={() => setSessionMode(null)}>
            Exit sample mode
          </button>
        </div>
      )}

      {proofLocked && (
        <div className="glass-panel mode-banner">
          <LockKeyhole size={16} />
          <div className="template-links">
            Add a payment method to unlock clause proof, audit reports and true-up packs.
            You're only charged 20% of dollars actually recovered — nothing upfront.
            <p className="muted-copy">
              By adding a card you agree to the <a href="/terms.html" target="_blank" rel="noreferrer">Terms of Service</a>,
              including the 20% success fee on recovered revenue.
            </p>
          </div>
          <button className="btn-primary" onClick={startBillingSetup}>
            Add payment method
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

          {assurance && (
            <div className="assurance-panel" style={{ marginTop: '1rem' }}>
              <p className="eyebrow">Continuous Assurance</p>
              <dl className="assurance-rows" style={{ margin: '0.5rem 0', fontSize: '0.85rem' }}>
                <div className="assurance-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <dt className="muted-copy">Last evaluated</dt>
                  <dd>{assurance.last_evaluated_at ? new Date(assurance.last_evaluated_at).toLocaleString() : '—'}</dd>
                </div>
                <div className="assurance-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <dt className="muted-copy">Next evaluation</dt>
                  <dd>On next billing, usage or agreement event</dd>
                </div>
                <div className="assurance-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <dt className="muted-copy">Open discrepancies</dt>
                  <dd>{assurance.open_discrepancies ?? 0}</dd>
                </div>
                <div className="assurance-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <dt className="muted-copy">Needs review</dt>
                  <dd>{assurance.needs_review ?? 0}</dd>
                </div>
              </dl>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                {(assurance.sources_monitored || []).map((src) => (
                  <span key={src} className="file-chip">{src}</span>
                ))}
              </div>
              {(assurance.recent_events || []).length > 0 && (
                <ul className="upload-history" style={{ marginTop: '0.5rem' }}>
                  {assurance.recent_events.slice(0, 5).map((ev) => (
                    <li key={ev.event_id} className="upload-history-item">
                      <span>{ev.trigger}{ev.customer_id ? ` · ${ev.customer_id}` : ''}{ev.period ? ` · ${ev.period}` : ''}</span>
                      <span className="muted-copy">{ev.status}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </aside>

        <main className="step-content">
          {activeStep === 1 && (
            <section className="glass-panel panel-card onboarding-card">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Step 1</p>
                  <h2>Upload contracts</h2>
                </div>
                <span className="hint-pill">Files or a ZIP — we sort them out</span>
              </div>

              <div className="upload-grid">
                <div className="dropzone glass-panel">
                  <Upload size={22} />
                  <div>
                    <strong>{bulkUploading ? 'Uploading…' : 'Drop files here or click to browse'}</strong>
                    <p>Drop contracts (PDF/DOCX/scans), billing + usage CSVs, or a ZIP of everything.</p>
                  </div>
                  <input
                    type="file"
                    multiple
                    disabled={bulkUploading}
                    accept=".pdf,.docx,.txt,.md,.csv,.zip,.png,.jpg,.jpeg"
                    onChange={handleBulkUpload}
                  />
                  {selectedFileName && <span className="file-chip">{selectedFileName}</span>}
                  {bulkResult?.files?.length > 0 && (
                    <ul className="upload-history">
                      {bulkResult.files.map((f, i) => (
                        <li key={`${f.name}-${i}`} className="upload-history-item">
                          <span className="upload-file-name">{f.name}</span>
                          <span className="file-chip">{f.kind} · {f.status}{f.message ? ` — ${f.message}` : ''}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="muted template-links">
                    Export templates:{' '}
                    <a href={`${API_BASE}/templates/quickbooks/invoices.csv`} download>QuickBooks</a>{' · '}
                    <a href={`${API_BASE}/templates/xero/invoices.csv`} download>Xero</a>{' · '}
                    <a href={`${API_BASE}/templates/stripe/invoices.csv`} download>Stripe</a>
                  </p>
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
                <span className={`connected-pill ${connectorConnection?.connected ? 'active' : ''}`}>
                  <ShieldCheck size={14} /> {connectorConnection?.connected ? 'Connected' : 'Not connected'}
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
                    <p>Recoup charges 20% of dollars actually paid to you — tracked on the Recovered &amp; billing step.</p>
                  </div>
                </div>
                {isSampleMode ? (
                  <div className="info-box glass-panel">
                    <FileText size={18} />
                    <div>
                      <strong>Sample mode</strong>
                      <p>Sample mode runs on synthetic data and does not connect to Stripe.</p>
                    </div>
                  </div>
                ) : (
                  <div className="stacked-copy">
                    <button className="btn-primary action-button" onClick={startStripeInstall} disabled={connectorSubmitting}>
                      {connectorSubmitting ? <RefreshCw className="spin" size={16} /> : <ShieldCheck size={16} />}
                      {connectorSubmitting ? 'Starting…' : 'Connect with Stripe'}
                    </button>
                    <div className="status-message">
                      {connectorStatus || 'Click to begin the OAuth install. Recoup stores the per-tenant read credential after Stripe redirects back.'}
                    </div>
                  </div>
                )}
                <div className="info-box glass-panel">
                  <BadgeCheck size={18} />
                  <div>
                    <strong>Connection status</strong>
                    <p>
                      {connectorConnection?.connected
                        ? `Connected as ${connectorConnection.stripe_account_id || 'the selected Stripe account'}.`
                        : connectorConnection?.message || 'Awaiting Stripe App install.'}
                    </p>
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
                          ['Overage rate', formatRate(contract.overage_rate), contract.term_meta?.overage_rate],
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
                        {(contract.discounts || []).map((discount) => (
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
                          <BadgeCheck size={16} /> Confirm terms
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              )}

              <div className="contract-review-list">
                <h3 className="queue-title">Renewal calendar</h3>
                {renewals.length === 0 ? (
                  <div className="empty-state glass-panel">
                    <BadgeCheck size={22} />
                    <p>No contracts with term data yet.</p>
                  </div>
                ) : (
                  renewals.map((row) => (
                    <article key={row.customer_id} className="glass-panel contract-review-card">
                      <div className="review-header">
                        <div>
                          <h3>{row.customer_name}</h3>
                          <p>
                            {row.term_end ? `Term ends ${row.term_end}` : 'Term end unknown'}
                            {row.auto_renew_months ? ` · auto-renews ${row.auto_renew_months}mo` : ''}
                            {row.notice_deadline ? ` · notice by ${row.notice_deadline} (${row.days_to_deadline}d)` : ''}
                          </p>
                          {row.provenance && <small>{row.provenance}</small>}
                        </div>
                        <span className={`badge ${
                          row.state === 'notice_window_open' || row.state === 'unknown' ? 'badge-pending'
                            : row.state === 'expired' ? 'badge-rejected'
                            : 'badge-approved'}`}>
                          {row.state === 'notice_window_open' ? 'Notice window open'
                            : row.state === 'upcoming_90d' ? 'Within 90 days'
                            : row.state === 'expired' ? 'Expired'
                            : row.state === 'unknown' ? 'Unknown term'
                            : 'Later'}
                        </span>
                      </div>
                    </article>
                  ))
                )}
              </div>
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
                  <small>20% of {formatCurrency(metrics?.recovered_this_month)} actually paid to you this month</small>
                </div>
                <div className="glass-panel metric-card">
                  <span className="metric-label">Potential (not yet recovered)</span>
                  <strong className="metric-value">{formatCurrency(metrics?.potential_monthly_recoverable)}</strong>
                </div>
              </div>

              <div className="review-actions">
                <button className="btn-secondary" onClick={exportFindings} disabled={proofLocked} title={proofLocked ? lockTitle : undefined}>
                  <Download size={16} /> Export findings (CSV)
                </button>
                <button className="btn-secondary" onClick={openAuditReport} disabled={proofLocked} title={proofLocked ? lockTitle : undefined}>
                  <FileText size={16} /> Audit report
                </button>
                <button className="btn-secondary" onClick={downloadReportPdf} disabled={proofLocked} title={proofLocked ? lockTitle : undefined}>
                  <Download size={16} /> Download PDF
                </button>
                {!isSampleMode && (
                  <button className="btn-secondary" onClick={syncStripeRecoveries} disabled={syncingRecoveries}>
                    <RefreshCw size={16} /> {syncingRecoveries ? 'Checking Stripe…' : 'Check Stripe for paid invoices'}
                  </button>
                )}
                <button className="btn-primary" onClick={chargeSuccessFee}>
                  <DollarSign size={16} /> Bill success fee this month
                </button>
                {!isSampleMode && billing?.card_on_file && (
                  <small className="muted-copy">
                    Card on file: {billing.card_brand || 'card'} •••• {billing.card_last4}
                  </small>
                )}
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
                        <button className="btn-secondary" onClick={() => openRecoveryForm(finding, 'invoice')}>
                          <FileText size={16} /> Record invoice
                        </button>
                        <button className="btn-primary" onClick={() => openRecoveryForm(finding, 'payment')}>
                          <DollarSign size={16} /> Record payment
                        </button>
                      </div>
                      {renderRecoveryForm(finding)}
                    </article>
                  ))
                )}

                {invoicedFindings.length > 0 && (
                  <>
                    <h3 className="queue-title">Invoiced — awaiting payment</h3>
                    {invoicedFindings.map((finding) => (
                      <article key={finding.finding_id} className="glass-panel contract-review-card">
                        <div className="review-header">
                          <div>
                            <h3>{finding.customer_name}</h3>
                            <p>{finding.title}</p>
                          </div>
                          <span className="badge badge-pending">
                            {finding.status === 'disputed' ? 'Disputed' : 'Invoiced'} • {finding.corrective_invoice?.ref || '—'} • {formatCurrency(finding.corrective_invoice?.amount ?? finding.monthly_recoverable)}
                          </span>
                        </div>
                        <div className="review-actions">
                          <button className="btn-primary" onClick={() => openRecoveryForm(finding, 'payment')}>
                            <DollarSign size={16} /> Record payment
                          </button>
                          <button className="btn-secondary" onClick={() => markDisputed(finding.finding_id)}>
                            Mark disputed
                          </button>
                          <button className="btn-danger" onClick={() => markWrittenOff(finding.finding_id)}>
                            Write off
                          </button>
                        </div>
                        {renderRecoveryForm(finding)}
                      </article>
                    ))}
                  </>
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
                          <span className="badge badge-approved">
                            Recovered • {formatCurrency(finding.recovered_amount ?? finding.monthly_recoverable)}
                            {finding.payment?.ref ? ` • ${finding.payment.ref}` : ''}
                          </span>
                          {finding.fee_charge?.status && (
                            <p className="muted-copy">
                              Recoup fee {formatCurrency(finding.fee_charge.amount)} — {finding.fee_charge.status}
                              {finding.fee_charge.invoice_id ? ` (invoice ${finding.fee_charge.invoice_id})` : ''}
                            </p>
                          )}
                        </div>
                      </article>
                    ))}
                  </>
                )}
              </div>

              <div className="contract-review-list">
                <h3 className="queue-title">True-up packs</h3>
                <div className="recovery-form">
                  <label>
                    Sender (your company name)
                    <input value={trueupSender} onChange={(e) => setTrueupSender(e.target.value)} placeholder="[Your company]" />
                  </label>
                </div>
                {trueupCustomers.length === 0 ? (
                  <div className="empty-state glass-panel">
                    <FileText size={22} />
                    <p>No collectible findings yet — approve findings first.</p>
                  </div>
                ) : (
                  trueupCustomers.map((cust) => (
                    <article key={cust.customer_id} className="glass-panel contract-review-card">
                      <div className="review-header">
                        <div>
                          <h3>{cust.customer_name}</h3>
                          <p>{formatCurrency(cust.total)} outstanding</p>
                        </div>
                        <div className="review-actions">
                          <button className="btn-secondary" onClick={() => downloadTrueupPdf(cust.customer_id, cust.customer_name)} disabled={proofLocked} title={proofLocked ? lockTitle : undefined}>
                            <Download size={16} /> Download letter + schedule (PDF)
                          </button>
                        </div>
                      </div>
                    </article>
                  ))
                )}
              </div>

              {!isSampleMode && (
                <div className="contract-review-list">
                  <h3 className="queue-title">Danger zone</h3>
                  <article className="glass-panel contract-review-card">
                    <div className="review-header">
                      <div>
                        <h3>Delete account data</h3>
                        <p>Remove all findings, invoices, usage, contracts, audit log, and the Stripe connector key.</p>
                      </div>
                      <div className="review-actions">
                        <button className="btn-danger" onClick={deleteAccountData}>
                          Delete all Recoup data for this account
                        </button>
                      </div>
                    </div>
                  </article>
                </div>
              )}
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

                {reviewQueue.length > 0 && (
                  <div className="contract-review-list">
                    <h3 className="queue-title">Needs review ({reviewQueue.length})</h3>
                    {reviewQueue.map((item, idx) => (
                      <article key={`${item.customer_id || 'unknown'}-${item.term}-${idx}`} className="glass-panel contract-review-card">
                        <div className="review-header">
                          <div>
                            <h3>{item.customer_name || 'Unknown customer'}</h3>
                            <p>{item.term}</p>
                          </div>
                        </div>
                        <p>{item.reason}</p>
                        {item.suggested_action && <small className="muted-copy">Suggested: {item.suggested_action}</small>}
                      </article>
                    ))}
                  </div>
                )}

                {selectedFinding ? (
                  <div className="glass-panel detail-view">
                    <div className="detail-header">
                      <div className="detail-topline">
                        <div>
                          <h2>{selectedFinding.customer_name}</h2>
                          <div className="detail-meta">
                            <span>ID: {selectedFinding.finding_id}</span>
                            <span>Period: {selectedFinding.period || '—'}</span>
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
                        {Number.isFinite(Number(selectedFinding.confidence_score)) && (
                          <div className="info-group">
                            <div className="info-label">Confidence score</div>
                            <div>{Math.round(Number(selectedFinding.confidence_score) * 100)}%</div>
                          </div>
                        )}
                        <div className="info-group">
                          <div className="info-label">Exact clause quote (provenance)</div>
                          <div className="provenance-box">
                            {selectedFinding.locked && <LockKeyhole size={14} />}
                            {(selectedFinding.provenance || selectedFinding.clause_text)
                              ? `“${selectedFinding.provenance || selectedFinding.clause_text}”`
                              : 'No contract clause cited — this finding should be treated as needs review.'}
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
                      <button className="btn-primary action-button" onClick={() => openRecoveryForm(selectedFinding, 'payment')}>
                        <DollarSign size={16} /> Record payment
                      </button>
                    </div>
                    {renderRecoveryForm(selectedFinding)}
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
