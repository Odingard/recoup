import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Building2,
  ChevronRight,
  DollarSign,
  Download,
  FileText,
  LayoutDashboard,
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
import RecoveryActions, { RecoveryActionSelect } from './RecoveryActions'
import './App.css'

const API_BASE = (import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8001/api').replace(/\/$/, '')
const DEFAULT_PERIOD = '2026-06'
void [AlertCircle, Building2, ChevronRight, DollarSign, Download, FileText, LayoutDashboard, LogIn, LogOut, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, Upload, XCircle, RecoveryActions, RecoveryActionSelect]

const NAV_ITEMS = [
  { id: 'overview', title: 'Overview', icon: LayoutDashboard },
  { id: 'opportunities', title: 'Opportunities', icon: FileText },
  { id: 'agreements', title: 'Agreements', icon: Upload },
  { id: 'recoveries', title: 'Recoveries', icon: DollarSign },
  { id: 'integrations', title: 'Integrations', icon: ShieldCheck },
  { id: 'settings', title: 'Settings', icon: LockKeyhole },
]

const METRIC_TILES = [
  ['potential_recoverable_value', 'Potential recoverable'],
  ['verified_value', 'Verified'],
  ['needs_review', 'Needs review'],
  ['approved', 'Approved'],
  ['in_recovery', 'In recovery'],
  ['disputed', 'Disputed'],
  ['realized_value', 'Realized'],
  ['written_off', 'Written off'],
]

const RECOVERY_BASES = [
  'cash_payment', 'settlement', 'refund', 'rebate', 'reimbursement',
  'contractual_credit', 'offset', 'other_verified_value',
]

const EMPTY_FILTERS = {
  customer: '', status: '', type: '', minValue: '',
  maxAge: '', minConfidence: '', agreement: '', period: '',
}

const LEGAL_NEXT_ACTIONS = {
  open: ['approve', 'reject'],
  approved: ['invoice', 'payment', 'writeoff', 'reject'],
  invoiced: ['payment', 'dispute', 'writeoff'],
  disputed: ['payment', 'writeoff'],
  rejected: [], recovered: [], written_off: [],
}

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

function formatDate(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString()
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

function normalizeHash() {
  const raw = (window.location.hash || '#/overview').replace(/^#\/?/, '')
  const [screen, id] = raw.split('/')
  return { screen: screen || 'overview', id: id || null }
}

function App() {
  const [firebaseUser, setFirebaseUser] = useState(null)
  const [sessionMode, setSessionMode] = useState(null)
  const [loadingAuth, setLoadingAuth] = useState(true)
  const [route, setRoute] = useState(normalizeHash())
  const [billingPeriod, setBillingPeriod] = useState(DEFAULT_PERIOD)
  const [, setFindings] = useState([])
  const [allFindings, setAllFindings] = useState([])
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
  const [commandCenter, setCommandCenter] = useState(null)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [rights, setRights] = useState({})
  const [events, setEvents] = useState({})
  const [realizationForm, setRealizationForm] = useState(null)
  const [realizationFields, setRealizationFields] = useState({ basis: 'cash_payment', amount: '', date: '', reference: '', actionId: '', note: '' })
  const [reverseForm, setReverseForm] = useState(null)
  const [reverseFields, setReverseFields] = useState({ amount: '', reference: '', reason: '' })
  const [confirmingCustomer, setConfirmingCustomer] = useState('')

  const isSampleMode = sessionMode === 'sample'
  const isAuthenticated = sessionMode === 'auth' && Boolean(firebaseUser)
  const apiReady = isSampleMode || isAuthenticated
  const screen = NAV_ITEMS.some((item) => item.id === route.screen) ? route.screen : 'overview'

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

  useEffect(() => {
    const onHash = () => setRoute(normalizeHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const navigate = useCallback((next, detail = null) => {
    const hash = detail ? `#/${next}/${encodeURIComponent(detail)}` : `#/${next}`
    if (window.location.hash === hash) {
      setRoute(normalizeHash())
    } else {
      window.location.hash = hash
    }
  }, [])

  const apiRequest = useCallback(async (path, options = {}) => {
    const headers = { ...(options.headers || {}) }
    if (isSampleMode) {
      headers['X-Recoup-Sample'] = '1'
    } else {
      if (!firebaseUser) throw new Error('Please sign in first')
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
      try { detail = JSON.parse(detail)?.detail || detail } catch { /* keep raw text */ }
      throw new Error(detail)
    }
    const text = await res.text()
    return text ? JSON.parse(text) : null
  }, [firebaseUser, isSampleMode])

  const authenticatedFetch = useCallback(async (path, options = {}) => {
    const headers = { ...(options.headers || {}) }
    if (isSampleMode) headers['X-Recoup-Sample'] = '1'
    else {
      if (!firebaseUser) throw new Error('Please sign in first')
      headers.Authorization = `Bearer ${await firebaseUser.getIdToken()}`
    }
    return fetch(`${API_BASE}${path}`, { ...options, headers })
  }, [firebaseUser, isSampleMode])

  const loadConnectorStatus = useCallback(async () => {
    if (!apiReady) return
    try {
      const result = await apiRequest('/connector/stripe/status')
      setConnectorConnection(result)
      setConnectorStatus(result?.message || '')
    } catch (error) { console.error(error) }
  }, [apiReady, apiRequest])

  const loadMetrics = useCallback(async () => {
    if (!apiReady) return
    try { setMetrics(await apiRequest('/metrics')) } catch (error) { console.error(error) }
  }, [apiReady, apiRequest])

  const loadCommandCenter = useCallback(async () => {
    if (!apiReady) return
    try { setCommandCenter(await apiRequest('/command-center')) } catch (error) { console.error(error) }
  }, [apiReady, apiRequest])

  const loadAssurance = useCallback(async () => {
    if (!apiReady) return
    try { setAssurance(await apiRequest('/assurance/status')) } catch (error) { console.error(error) }
  }, [apiReady, apiRequest])

  const refreshFindings = useCallback(async () => {
    if (!apiReady) return
    const [pending, all] = await Promise.all([apiRequest('/findings/pending'), apiRequest('/findings')])
    void loadMetrics(); void loadAssurance(); void loadCommandCenter()
    setFindings(Array.isArray(pending) ? pending : [])
    setAllFindings(Array.isArray(all) ? all : [])
  }, [apiReady, apiRequest, loadMetrics, loadAssurance, loadCommandCenter])

  const loadContracts = useCallback(async () => {
    if (!apiReady) return
    try {
      const result = await apiRequest('/contracts')
      const list = result?.contracts || []
      setUploadedContracts(list.map((c) => ({ ...c, confirmed: Boolean(c.confirmed), discounts: c.discounts || [] })))
    } catch (error) { console.error(error) }
  }, [apiReady, apiRequest])

  useEffect(() => {
    if (!apiReady) return
    const handle = window.setTimeout(() => {
      void refreshFindings(); void loadConnectorStatus(); void loadContracts()
      apiRequest('/renewals').then((rows) => setRenewals(Array.isArray(rows) ? rows : [])).catch(() => setRenewals([]))
    }, 0)
    return () => window.clearTimeout(handle)
  }, [apiReady, refreshFindings, loadConnectorStatus, loadContracts, apiRequest])

  useEffect(() => {
    if (!apiReady || !route.id || screen !== 'opportunities') return
    const handle = window.setTimeout(() => {
      apiRequest(`/findings/${route.id}/recovery-events`)
        .then((rows) => setEvents((current) => ({ ...current, [route.id]: Array.isArray(rows) ? rows : (rows?.events || []) })))
        .catch(() => setEvents((current) => ({ ...current, [route.id]: [] })))
    }, 0)
    return () => window.clearTimeout(handle)
  }, [apiReady, apiRequest, route.id, screen])

  useEffect(() => {
    if (!apiReady || screen !== 'agreements') return
    const missing = uploadedContracts.filter((contract) => contract.customer_id && rights[contract.customer_id] === undefined)
    if (!missing.length) return
    const handle = window.setTimeout(() => {
      missing.forEach((contract) => {
        apiRequest(`/rights/customers/${contract.customer_id}`)
          .then((graph) => setRights((current) => ({ ...current, [contract.customer_id]: graph?.rights || graph?.financial_rights || [] })))
          .catch(() => setRights((current) => ({ ...current, [contract.customer_id]: [] })))
      })
    }, 0)
    return () => window.clearTimeout(handle)
  }, [apiReady, screen, uploadedContracts, rights, apiRequest])

  const loadBillingStatus = useCallback(async () => {
    if (!apiReady) return
    try { setBilling(await apiRequest('/billing/status')) } catch (error) { console.error(error) }
  }, [apiReady, apiRequest])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const billingSetup = params.get('billing_setup')
    if (!billingSetup) return
    if (billingSetup === 'cancelled') {
      const handle = window.setTimeout(() => setStatusMessage('Card setup cancelled.'), 0)
      return () => window.clearTimeout(handle)
    }
    let cancelled = false
    const complete = async () => {
      try {
        if (isSampleMode || !firebaseUser) return
        const headers = { Authorization: `Bearer ${await firebaseUser.getIdToken()}`, 'Content-Type': 'application/json' }
        const res = await fetch(`${API_BASE}/billing/setup-complete`, { method: 'POST', headers, body: JSON.stringify({ session_id: billingSetup }) })
        const payload = await res.json()
        if (cancelled) return
        if (payload?.status === 'success') {
          setStatusMessage('Payment method saved — proof unlocked.')
          void loadBillingStatus()
        } else setStatusMessage(payload?.message || 'Payment method was not saved.')
      } catch (error) {
        console.error(error)
        if (!cancelled) setStatusMessage('Payment method was not saved.')
      }
    }
    void complete()
    return () => { cancelled = true }
  }, [firebaseUser, isSampleMode, loadBillingStatus])

  useEffect(() => {
    if (!apiReady) return
    const handle = window.setTimeout(() => void loadBillingStatus(), 0)
    return () => window.clearTimeout(handle)
  }, [apiReady, loadBillingStatus])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const stripeConnect = params.get('stripe_connect')
    if (!stripeConnect) return
    const handle = window.setTimeout(() => {
      if (stripeConnect === 'success') {
        setConnectorStatus('Stripe App connected successfully.')
        navigate('integrations')
      } else if (stripeConnect === 'error') {
        setConnectorStatus(params.get('message') || 'Stripe connection failed.')
        navigate('integrations')
      }
    }, 0)
    return () => window.clearTimeout(handle)
  }, [navigate])

  const sampleModeLogin = useCallback(async () => {
    setLoadingAuth(true)
    signOut(auth).catch(() => { /* sample mode must remain usable */ })
    setSessionMode('sample')
    setLoadingAuth(false)
  }, [])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('sample') !== '1' || sessionMode !== null) return
    const handle = window.setTimeout(() => void sampleModeLogin(), 0)
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
    try { await signOut(auth) } finally {
      setSessionMode(null)
      setFirebaseUser(null)
      setFindings([]); setAllFindings([])
      setStatusMessage('')
      setConnectorConnection(null); setMetrics(null); setConnectorStatus('')
    }
  }

  const handleContractField = (field, value) => setContractDraft((current) => ({ ...current, [field]: value }))
  const handleClauseField = (field, value) => setContractDraft((current) => ({ ...current, clauses: { ...current.clauses, [field]: value } }))

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
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Contract upload failed', error))
    } finally { setContractSubmitting(false) }
  }

  const handleBulkUpload = async (event) => {
    const fileList = Array.from(event.target.files || [])
    event.target.value = ''
    if (!fileList.length) return
    setSelectedFileName(fileList[0].name)
    setBulkUploading(true); setBulkResult(null)
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
        setUploadedContracts((current) => [{ ...contract, confirmed: false, discounts: contract.discounts || [], ...(fileName ? { file_name: fileName } : {}) }, ...current.filter((item) => item.customer_id !== contract.customer_id)])
      })
      const nr = result?.needs_review?.length
      setStatusMessage(result?.status === 'needs_review'
        ? (result.message || 'Bulk upload needs review.')
        : `Bulk upload: ${result.contracts} contracts, ${result.invoices} invoices, ${result.usage} usage rows.` + (nr ? ` ${nr} item(s) need review.` : ''))
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Bulk upload failed', error))
    } finally { setBulkUploading(false) }
  }

  const confirmContract = async (customerId) => {
    setConfirmingCustomer(customerId)
    try {
      const result = await apiRequest(`/contracts/${encodeURIComponent(customerId)}/confirm`, { method: 'POST' })
      if (result?.status === 'confirmed' || result?.contract?.confirmed) {
        setUploadedContracts((current) => current.map((item) => item.customer_id === customerId ? { ...item, confirmed: true, confirmed_by: result.contract?.confirmed_by, confirmed_at: result.contract?.confirmed_at } : item))
        setStatusMessage('Contract terms confirmed.')
      } else {
        setStatusMessage(result?.message || 'Sample mode did not persist confirmation.')
      }
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Could not confirm terms', error))
    } finally { setConfirmingCustomer('') }
  }

  const runEvaluation = async () => {
    if (!/^\d{4}-\d{2}$/.test(billingPeriod)) {
      setStatusMessage('Period must be YYYY-MM.')
      return
    }
    setRunning(true)
    try {
      const result = await apiRequest(`/reconcile?period=${billingPeriod}`, { method: 'POST' })
      setReviewQueue(result?.needs_review || [])
      setStatusMessage(`Evaluation complete: ${result.findings_found} findings.` + (result?.needs_review_count ? ` ${result.needs_review_count} item(s) need review.` : ''))
      await refreshFindings()
      navigate('opportunities')
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Evaluation failed', error))
    } finally { setRunning(false) }
  }

  const evaluateAssurance = async () => {
    const customerId = route.id || uploadedContracts[0]?.customer_id || allFindings[0]?.customer_id || ''
    if (!customerId) {
      setStatusMessage('No customer is available for manual evaluation.')
      return
    }
    try {
      const result = await apiRequest('/assurance/evaluate', { method: 'POST', body: { customer_id: customerId, period: billingPeriod } })
      setStatusMessage(result?.status === 'needs_review' ? result.message : 'Assurance evaluation recorded.')
      await loadAssurance()
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Assurance evaluation failed', error))
    }
  }

  const handleAction = async (findingId, action) => {
    try {
      const endpoint = action === 'approve' ? 'approve' : 'reject'
      const body = action === 'reject' ? { status: 'rejected', reason: 'Reviewed in dashboard' } : undefined
      const result = await apiRequest(`/findings/${findingId}/${endpoint}`, { method: 'POST', body })
      setStatusMessage(result.status !== 'approved' && result.status !== 'rejected'
        ? (result.message || 'Sample mode is read-only; approval was not recorded.')
        : (action === 'approve' ? 'Finding approved.' : 'Finding rejected.'))
      await refreshFindings()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not update the finding.')
    }
  }

  const openRealizationForm = (finding) => {
    setRealizationForm({ finding_id: finding.finding_id })
    setRealizationFields({ basis: 'cash_payment', amount: String(finding.recoverable_difference ?? finding.monthly_recoverable ?? ''), date: '', reference: '', actionId: '', note: '' })
  }

  const submitRealization = async (findingId) => {
    const amount = Number(realizationFields.amount)
    try {
      await apiRequest(`/findings/${findingId}/recovery-events`, {
        method: 'POST',
        body: {
          recovery_basis: realizationFields.basis,
          realized_value: amount,
          realized_at: realizationFields.date || null,
          external_reference: realizationFields.reference || null,
          recovery_action_id: realizationFields.actionId || null,
          note: realizationFields.note || null,
        },
      })
      setStatusMessage('Realized value recorded.')
      setRealizationForm(null)
      const rows = await apiRequest(`/findings/${findingId}/recovery-events`)
      setEvents((current) => ({ ...current, [findingId]: Array.isArray(rows) ? rows : (rows?.events || []) }))
      await refreshFindings()
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Could not record realized value', error))
    }
  }

  const recordCorrectiveInvoice = async (finding) => {
    const invoice_ref = window.prompt('Invoice reference:') || ''
    if (!invoice_ref.trim()) return
    const invoice_amount = Number(window.prompt('Invoice amount:', String(finding.recoverable_difference ?? finding.monthly_recoverable ?? '')) || 0)
    try {
      await apiRequest(`/findings/${finding.finding_id}/invoiced`, { method: 'POST', body: { invoice_ref, invoice_amount, invoice_url: null, note: '' } })
      setStatusMessage('Corrective invoice recorded.')
      await refreshFindings()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not record corrective invoice.')
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

  const submitReversal = async (findingId, eventId) => {
    try {
      await apiRequest(`/findings/${findingId}/recovery-events/${eventId}/reverse`, {
        method: 'POST',
        body: {
          reversal_amount: Number(reverseFields.amount),
          reversal_reference: reverseFields.reference || null,
          reason: reverseFields.reason || null,
        },
      })
      setStatusMessage('Realization reversed.')
      setReverseForm(null)
      const rows = await apiRequest(`/findings/${findingId}/recovery-events`)
      setEvents((current) => ({ ...current, [findingId]: Array.isArray(rows) ? rows : (rows?.events || []) }))
      await refreshFindings()
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Reversal failed', error))
    }
  }

  const exportFindings = async () => {
    try {
      const res = await authenticatedFetch('/findings/export')
      if (!res.ok) throw new Error(await res.text())
      const blob = await res.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a')
      link.href = url; link.download = 'recoup_findings.csv'; link.click(); URL.revokeObjectURL(url)
    } catch (error) {
      console.error(error); setStatusMessage(failureMessage('Export failed', error))
    }
  }

  const openAuditReport = async () => {
    try {
      const result = await apiRequest('/report/share', { method: 'POST' })
      if (!result?.url) throw new Error(result?.message || 'No share URL returned')
      window.open(result.url, '_blank', 'noopener')
    } catch (error) {
      console.error(error); setStatusMessage('Could not open the audit report.')
    }
  }

  const downloadReportPdf = async () => {
    try {
      const res = await authenticatedFetch('/report.pdf')
      if (!res.ok) throw new Error(await res.text())
      const blob = await res.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a')
      link.href = url; link.download = 'recoup_report.pdf'; link.click(); URL.revokeObjectURL(url)
    } catch (error) {
      console.error(error); setStatusMessage('Could not download the PDF report.')
    }
  }

  const startBillingSetup = async () => {
    try {
      const result = await apiRequest('/billing/setup-session', { method: 'POST' })
      if (result?.status === 'success' && result.url) { window.location.assign(result.url); return }
      setStatusMessage(result?.message || 'Could not start card setup.')
    } catch (error) {
      console.error(error); setStatusMessage('Could not start card setup.')
    }
  }

  const syncStripeRecoveries = async () => {
    setSyncingRecoveries(true)
    try {
      const result = await apiRequest('/billing/sync-recoveries', { method: 'POST' })
      if (result?.status === 'needs_connector') setStatusMessage('Connect Stripe to verify paid invoices.')
      else {
        const n = result?.recovered?.length || 0
        setStatusMessage(`Checked ${result?.checked ?? 0} invoices — ${n} newly recovered.`)
        await refreshFindings()
      }
    } catch (error) {
      console.error(error); setStatusMessage(failureMessage('Stripe sync failed', error))
    } finally { setSyncingRecoveries(false) }
  }

  const chargeSuccessFee = async () => {
    try {
      const result = await apiRequest('/billing/charge-success-fee', { method: 'POST' })
      const billing = result?.billing || {}
      setStatusMessage(billing.message || `Success fee status: ${billing.status}`)
      await loadMetrics()
    } catch (error) {
      console.error(error); setStatusMessage('Could not charge the success fee.')
    }
  }

  const [trueupSender, setTrueupSender] = useState('')
  const downloadTrueupPdf = async (customerId, customerName) => {
    try {
      const params = trueupSender ? `?sender=${encodeURIComponent(trueupSender)}` : ''
      const res = await authenticatedFetch(`/trueup/${customerId}.pdf${params}`)
      if (!res.ok) throw new Error(await res.text())
      const blob = await res.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a')
      link.href = url; link.download = `trueup_${customerId}.pdf`; link.click(); URL.revokeObjectURL(url)
      setStatusMessage(`True-up pack for ${customerName || customerId} downloaded.`)
    } catch (error) {
      console.error(error); setStatusMessage('Could not generate the true-up pack.')
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
      setFindings([]); setAllFindings([]); setUploadedContracts([]); setMetrics(null)
      setStatusMessage(result?.status === 'deleted' ? 'All account data deleted.' : (result?.message || 'Deletion did not complete.'))
    } catch (error) {
      console.error(error); setStatusMessage(failureMessage('Account data deletion failed', error))
    }
  }

  const cases = useMemo(() => {
    const minV = filters.minValue === '' ? null : Number(filters.minValue)
    const maxAge = filters.maxAge === '' ? null : Number(filters.maxAge)
    const minConf = filters.minConfidence === '' ? null : Number(filters.minConfidence)
    return (commandCenter?.cases || []).filter((c) => {
      if (filters.customer && c.counterparty?.customer_id !== filters.customer) return false
      if (filters.status && c.status !== filters.status) return false
      if (filters.type && c.financial_right?.type !== filters.type) return false
      if (filters.agreement && c.agreement?.customer_id !== filters.agreement) return false
      if (filters.period && c.period !== filters.period) return false
      if (minV !== null && Number(c.recoverable_difference || 0) < minV) return false
      if (maxAge !== null && Number(c.age_days || 0) > maxAge) return false
      if (minConf !== null && Number(c.confidence || 0) < minConf) return false
      return true
    })
  }, [commandCenter, filters])

  const selectedCase = useMemo(() => {
    if (screen !== 'opportunities' || !route.id) return null
    return (commandCenter?.cases || []).find((c) => c.finding_id === route.id)
      || allFindings.find((f) => f.finding_id === route.id)
      || null
  }, [screen, route.id, commandCenter, allFindings])

  const recoveryCases = useMemo(() => allFindings.filter((finding) => ['approved', 'invoiced', 'disputed', 'recovered', 'written_off'].includes(finding.status || 'open')), [allFindings])
  const trueupCustomers = useMemo(() => {
    const statuses = ['approved', 'invoiced', 'disputed']
    const map = {}
    allFindings.forEach((f) => {
      if (!statuses.includes(f.status || 'open')) return
      const entry = map[f.customer_id] ||= { customer_id: f.customer_id, customer_name: f.customer_name || f.customer_id, count: 0 }
      entry.count += 1
    })
    return Object.values(map).sort((a, b) => (a.customer_name || '').localeCompare(b.customer_name || ''))
  }, [allFindings])

  const reviewLabel = isSampleMode ? 'Sample data' : firebaseUser?.email || 'Authenticated'
  const proofLocked = !isSampleMode && Boolean(billing?.configured) && !billing?.card_on_file
  const lockTitle = 'Add a payment method to unlock'

  const startStripeInstall = useCallback(async () => {
    if (isSampleMode) { setConnectorStatus('Sample mode does not connect to Stripe.'); return }
    setConnectorSubmitting(true); setConnectorStatus('')
    try {
      const result = await apiRequest('/connector/stripe/oauth/start', { method: 'POST' })
      if (result?.install_url) {
        setConnectorStatus('Redirecting to Stripe App install…')
        window.location.href = result.install_url
        return
      }
      setConnectorStatus(result?.message || 'Could not start the Stripe App install.')
    } catch (error) {
      console.error(error); setConnectorStatus(error.message || 'Could not start the Stripe App install.')
    } finally { setConnectorSubmitting(false) }
  }, [apiRequest, isSampleMode])

  const renderStatus = () => statusMessage ? <p className={`status-message ${statusMessage.toLowerCase().includes('failed') || statusMessage.toLowerCase().includes('not') ? 'error' : ''}`} role="status">{statusMessage}</p> : null

  const actionButton = (action, finding) => {
    if (action === 'approve') return <button className="btn-primary" onClick={() => handleAction(finding.finding_id, 'approve')}>Approve</button>
    if (action === 'reject') return <button className="btn-secondary" onClick={() => handleAction(finding.finding_id, 'reject')}>Reject</button>
    if (action === 'invoice') return <button className="btn-primary" onClick={() => recordCorrectiveInvoice(finding)}>Record corrective invoice</button>
    if (action === 'payment') return <button className="btn-primary" onClick={() => openRealizationForm(finding)}>Record realized value</button>
    if (action === 'dispute') return <button className="btn-secondary" onClick={() => markDisputed(finding.finding_id)}>Mark disputed</button>
    if (action === 'writeoff') return <button className="btn-danger" onClick={() => markWrittenOff(finding.finding_id)}>Write off</button>
    return null
  }

  const renderRealizationForm = (finding) => {
    if (!realizationForm || realizationForm.finding_id !== finding.finding_id) return null
    return (
      <div className="recovery-form">
        <label>Recovery basis
          <select value={realizationFields.basis} onChange={(e) => setRealizationFields((f) => ({ ...f, basis: e.target.value }))}>
            {RECOVERY_BASES.map((basis) => <option key={basis} value={basis}>{basis.replace(/_/g, ' ')}</option>)}
          </select>
        </label>
        <label>Realized amount ($)
          <input type="number" min="0" step="0.01" value={realizationFields.amount} onChange={(e) => setRealizationFields((f) => ({ ...f, amount: e.target.value }))} />
        </label>
        <label>Realized date
          <input type="date" value={realizationFields.date} onChange={(e) => setRealizationFields((f) => ({ ...f, date: e.target.value }))} />
        </label>
        <label>External reference
          <input value={realizationFields.reference} onChange={(e) => setRealizationFields((f) => ({ ...f, reference: e.target.value }))} placeholder="txn / check ref" />
        </label>
        <label>Note
          <input value={realizationFields.note} onChange={(e) => setRealizationFields((f) => ({ ...f, note: e.target.value }))} />
        </label>
        <RecoveryActionSelect findingId={finding.finding_id} apiRequest={apiRequest} value={realizationFields.actionId} onChange={(v) => setRealizationFields((f) => ({ ...f, actionId: v }))} />
        <div className="review-actions">
          <button className="btn-primary" onClick={() => submitRealization(finding.finding_id)}>Save realized value</button>
          <button className="btn-secondary" onClick={() => setRealizationForm(null)}>Cancel</button>
        </div>
      </div>
    )
  }

  const renderEvents = (finding) => (
    <div className="info-group">
      <div className="info-label">Realization events</div>
      {(events[finding.finding_id] || []).length === 0 ? <p className="muted-copy">No realization events recorded.</p> : (
        <ul className="upload-history">
          {(events[finding.finding_id] || []).map((event) => (
            <li key={event.recovery_event_id} className="upload-history-item event-row">
              <span>{event.event_type?.replace(/_/g, ' ') || 'event'} · {event.recovery_basis?.replace(/_/g, ' ')} · {formatCurrency(event.event_type === 'reversal' ? event.reversal_amount : event.realized_value)}</span>
              <span className="muted-copy">{event.external_reference || event.reversal_reference || '—'} · {formatDate(event.realized_at || event.created_at)}</span>
              {event.event_type === 'realization' && (
                <button className="btn-secondary" onClick={() => { setReverseForm({ finding_id: finding.finding_id, event_id: event.recovery_event_id }); setReverseFields({ amount: '', reference: '', reason: '' }) }}>Reverse</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {reverseForm && reverseForm.finding_id === finding.finding_id && (
        <div className="recovery-form">
          <label>Reversal amount
            <input type="number" min="0" step="0.01" value={reverseFields.amount} onChange={(e) => setReverseFields((f) => ({ ...f, amount: e.target.value }))} />
          </label>
          <label>Reference
            <input value={reverseFields.reference} onChange={(e) => setReverseFields((f) => ({ ...f, reference: e.target.value }))} />
          </label>
          <label>Reason
            <input value={reverseFields.reason} onChange={(e) => setReverseFields((f) => ({ ...f, reason: e.target.value }))} />
          </label>
          <div className="review-actions">
            <button className="btn-danger" onClick={() => submitReversal(finding.finding_id, reverseForm.event_id)}>Reverse event</button>
            <button className="btn-secondary" onClick={() => setReverseForm(null)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )

  const renderOpportunityDetail = (finding) => {
    const evidence = finding.evidence || {}
    const legal = LEGAL_NEXT_ACTIONS[finding.status || 'open'] || []
    const ledger = finding.ledger || null
    const detailFinding = { ...finding, monthly_recoverable: finding.recoverable_difference ?? finding.monthly_recoverable }
    return (
      <section className="panel-card opportunity-detail">
        <div className="panel-heading">
          <div><p className="eyebrow">Opportunity detail</p><h2>{finding.financial_right?.title || finding.title || finding.finding_id}</h2></div>
          <button className="btn-secondary" onClick={() => navigate('opportunities')}>Back to queue</button>
        </div>
        <div className="detail-grid detail-grid-two">
          <div className="info-group"><div className="info-label">1. What the agreement says</div>
            {finding.locked ? <p className="muted-copy">Proof locked — add a payment method.</p> : (
              <div className="provenance-box">{evidence.clause_ref || finding.clause_ref || '—'}<br />{evidence.clause_text || finding.clause_text || 'No clause text on file.'}</div>
            )}
          </div>
          <div className="info-group"><div className="info-label">2. Expected vs actual</div>
            <div><span className="muted-copy">Expected: </span><strong className="money">{formatCurrency(finding.expected_value)}</strong></div>
            <div><span className="muted-copy">Actual: </span><strong className="money">{formatCurrency(finding.actual_value)}</strong></div>
          </div>
          <div className="info-group discrepancy-hero"><div className="info-label">3. Financial discrepancy</div>
            <div className="discrepancy-amount money">{formatCurrency(finding.recoverable_difference ?? finding.monthly_recoverable)}</div>
            <div className="muted-copy">Confidence {Math.round(Number(finding.confidence || finding.confidence_score || 0) * 100)}% · period {finding.period || '—'} · {finding.currency || 'USD'}</div>
          </div>
          <div className="info-group"><div className="info-label">4. Calculation / evidence chain</div>
            <div className="detail-copy">{evidence.math || finding.math || '—'}</div>
            <div className="muted-copy">{evidence.provenance || finding.provenance || '—'}</div>
            {(finding.evidence_refs || []).map((ref) => <span key={ref} className="file-chip">{ref}</span>)}
          </div>
        </div>
        <div className="info-group"><div className="info-label">5. Status and audit history</div>
          <span className={`status-pill status-${finding.status}`}>{String(finding.status || 'open').replace(/_/g, ' ')}</span>
          <ul className="upload-history">
            {(finding.recovery_history || []).map((h, i) => <li key={i}><span>{h.event}{h.decision ? ` · ${h.decision}` : ''}</span><span className="muted-copy">{h.ts}</span></li>)}
          </ul>
        </div>
        <div className="info-group"><div className="info-label">6. Allowed next action</div>
          {legal.length === 0 ? <p className="muted-copy">No legal actions remain for this status.</p> : <div className="review-actions">{legal.map((action) => <span key={action}>{actionButton(action, detailFinding)}</span>)}</div>}
          {renderRealizationForm(detailFinding)}
        </div>
        {['approved', 'invoiced', 'disputed'].includes(finding.status) && (
          <RecoveryActions finding={detailFinding} apiRequest={apiRequest} onChanged={refreshFindings} />
        )}
        {ledger && (
          <div className="info-group"><div className="info-label">7. Realization ledger</div>
            <div className="detail-grid">
              <div><span className="metric-label">Potential</span><div className="money">{formatCurrency(ledger.potential_value)}</div></div>
              <div><span className="metric-label">Requested</span><div className="money">{formatCurrency(ledger.approved_requested_value)}</div></div>
              <div><span className="metric-label">Realized</span><div className="money">{formatCurrency(ledger.realized_value)}</div></div>
              <div><span className="metric-label">Reversed</span><div className="money">{formatCurrency(ledger.reversed_value)}</div></div>
              <div><span className="metric-label">Outstanding</span><div className="money">{formatCurrency(ledger.outstanding_value)}</div></div>
              <div><span className="metric-label">Shortfall</span><div className="money">{formatCurrency(ledger.settlement_shortfall)}</div></div>
            </div>
          </div>
        )}
        {renderEvents(detailFinding)}
      </section>
    )
  }

  const renderOverview = () => {
    const es = commandCenter?.executive_summary || {}
    return (
      <section className="panel-card">
        <div className="panel-heading"><div><p className="eyebrow">Overview</p><h2>Recovery overview</h2></div></div>
        {!(commandCenter?.cases || []).length ? <p className="muted-copy empty-state">No agreements evaluated yet → <button className="link-button" onClick={() => navigate('agreements')}>Agreements</button></p> : null}
        <div className="metric-grid">
          {METRIC_TILES.map(([key, label]) => <div key={key} className="metric-card"><span className="metric-label">{label}</span><strong className="metric-value money">{formatCurrency(commandCenter?.metrics?.[key])}</strong></div>)}
        </div>
        <div className="panel-section"><div className="info-label">Pipeline</div>
          <div className="cc-pipeline">{(commandCenter?.pipeline || []).map((stage) => <div key={stage.stage} className="pipeline-stage"><div className="metric-label">{stage.stage}</div><strong className="money">{formatCurrency(stage.value)}</strong><div className="muted-copy">{stage.count} case{stage.count === 1 ? '' : 's'}</div></div>)}</div>
        </div>
        <div className="panel-section"><div className="info-label">Executive summary</div>
          <div className="exec-strip">
            <span><strong className="money">{formatCurrency(es.total_opportunity)}</strong> <span className="muted-copy">total opportunity</span></span>
            <span><strong className="money">{formatCurrency(es.realized_value)}</strong> <span className="muted-copy">realized</span></span>
            <span><strong>{Math.round((es.recovery_rate || 0) * 100)}%</strong> <span className="muted-copy">recovery rate</span></span>
            <span><strong>{es.avg_days_to_recovery ?? '—'}</strong> <span className="muted-copy">avg days to recovery</span></span>
            <span><strong>{es.open_cases ?? 0}</strong> <span className="muted-copy">open cases</span></span>
          </div>
        </div>
        {es.recovery_metrics && <div className="panel-section"><div className="info-label">Recovery performance</div><div className="exec-strip"><span><strong className="money">{formatCurrency(es.recovery_metrics.total_opportunity)}</strong> <span className="muted-copy">total opportunity</span></span><span><strong className="money">{formatCurrency(es.recovery_metrics.realized_value)}</strong> <span className="muted-copy">realized</span></span><span><strong>{Math.round((es.recovery_metrics.recovery_rate || 0) * 100)}%</strong> <span className="muted-copy">rate</span></span><span><strong>{es.recovery_metrics.avg_time_to_recovery ?? '—'}</strong> <span className="muted-copy">avg days</span></span><span><strong className="money">{formatCurrency(es.recovery_metrics.avg_recovery_per_case)}</strong> <span className="muted-copy">avg / case</span></span></div></div>}
        <div className="panel-section"><div className="info-label">Continuous assurance</div>
          <dl className="assurance-rows">
            <div className="assurance-row"><dt className="muted-copy">Last evaluated</dt><dd>{formatDate(assurance?.last_evaluated_at)}</dd></div>
            <div className="assurance-row"><dt className="muted-copy">Last trigger</dt><dd>{assurance?.last_trigger || '—'}</dd></div>
            <div className="assurance-row"><dt className="muted-copy">Open discrepancies</dt><dd>{assurance?.open_discrepancies ?? 0}</dd></div>
            <div className="assurance-row"><dt className="muted-copy">Needs review</dt><dd>{assurance?.needs_review ?? 0}</dd></div>
          </dl>
          <div className="chip-row">{(assurance?.sources_monitored || []).map((src) => <span key={src} className="file-chip">{src}</span>)}{(assurance?.triggers_monitored || []).map((trigger) => <span key={trigger} className="file-chip">{trigger.replace(/_/g, ' ')}</span>)}</div>
          <button className="btn-primary" onClick={evaluateAssurance}>Evaluate now</button>
        </div>
      </section>
    )
  }

  const renderOpportunities = () => {
    const set = (key) => (e) => setFilters((f) => ({ ...f, [key]: e.target.value }))
    if (route.id && selectedCase) return renderOpportunityDetail(selectedCase)
    return (
      <section className="panel-card">
        <div className="panel-heading"><div><p className="eyebrow">Opportunities</p><h2>Working queue</h2></div><span className="hint-pill">{cases.length} cases</span></div>
        <div className="cc-filters">
          <select value={filters.customer} onChange={set('customer')}><option value="">All customers</option>{(commandCenter?.filters?.customers || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <select value={filters.status} onChange={set('status')}><option value="">All statuses</option>{(commandCenter?.filters?.statuses || []).map((s) => <option key={s} value={s}>{s}</option>)}</select>
          <select value={filters.type} onChange={set('type')}><option value="">All types</option>{(commandCenter?.filters?.types || []).map((t) => <option key={t} value={t}>{t}</option>)}</select>
          <select value={filters.agreement} onChange={set('agreement')}><option value="">All agreements</option>{(commandCenter?.filters?.agreements || []).map((a) => <option key={a} value={a}>{a}</option>)}</select>
          <select value={filters.period} onChange={set('period')}><option value="">All periods</option>{(commandCenter?.filters?.periods || []).map((p) => <option key={p} value={p}>{p}</option>)}</select>
          <input type="number" placeholder="Min value" value={filters.minValue} onChange={set('minValue')} />
          <input type="number" placeholder="Max age (days)" value={filters.maxAge} onChange={set('maxAge')} />
          <input type="number" step="0.05" min="0" max="1" placeholder="Min confidence" value={filters.minConfidence} onChange={set('minConfidence')} />
        </div>
        <div className="table-scroll"><table className="cc-table"><thead><tr><th>Counterparty</th><th>Opportunity</th><th>Value</th><th>Confidence</th><th>Evidence</th><th>Age</th><th>Status</th><th>Next valid action</th></tr></thead><tbody>
          {cases.map((c) => <tr key={c.finding_id} onClick={() => navigate('opportunities', c.finding_id)} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') navigate('opportunities', c.finding_id) }}>
            <td title={c.counterparty?.customer_name} className="truncate">{c.counterparty?.customer_name}</td><td>{c.financial_right?.title || c.financial_right?.type}</td><td className="money">{formatCurrency(c.recoverable_difference)}</td><td>{Math.round(Number(c.confidence || 0) * 100)}%</td><td>{Math.round(Number(c.evidence?.completeness || 0) * 100) >= 100 ? 'Complete' : 'Partial'}</td><td>{c.age_days ?? '—'}</td><td>{c.status}</td><td>{c.recommended_next_step}</td>
          </tr>)}
          {cases.length === 0 && <tr><td colSpan="8" className="muted-copy">No cases match the current filters.</td></tr>}
        </tbody></table></div>
        <div className="panel-section"><div className="info-label">Needs review</div>
          {reviewQueue.length === 0 ? <p className="muted-copy">No needs-review items.</p> : <ul className="upload-history">{reviewQueue.map((item, i) => <li key={i} className="upload-history-item"><span>{item.customer_name || item.customer_id || 'Record'} · {item.term || item.reason}</span><span className="muted-copy">{item.reason || item.suggested_action}</span></li>)}</ul>}
        </div>
        {route.id && <p className="muted-copy">Loading selected opportunity…</p>}
      </section>
    )
  }

  const renderAgreements = () => (
    <section className="panel-card">
      <div className="panel-heading"><div><p className="eyebrow">Agreements</p><h2>Agreements and uploads</h2></div><span className="hint-pill">Files or a ZIP — we sort them out</span></div>
      <div className="upload-grid">
        <div className="dropzone">
          <Upload size={22} /><div><strong>{bulkUploading ? 'Uploading…' : 'Drop files here or click to browse'}</strong><p>Drop contracts (PDF/DOCX/scans), billing + usage CSVs, or a ZIP of everything.</p></div>
          <input type="file" multiple disabled={bulkUploading} accept=".pdf,.docx,.txt,.md,.csv,.zip,.png,.jpg,.jpeg" onChange={handleBulkUpload} />
          {selectedFileName && <span className="file-chip">{selectedFileName}</span>}
          {bulkResult?.files?.length > 0 && <ul className="upload-history">{bulkResult.files.map((f, i) => <li key={`${f.name}-${i}`} className="upload-history-item"><span className="upload-file-name">{f.name}</span><span className="file-chip">{f.kind} · {f.status}{f.message ? ` — ${f.message}` : ''}</span></li>)}</ul>}
          <p className="muted-copy">Export templates: <a href={`${API_BASE}/templates/quickbooks/invoices.csv`} download>QuickBooks</a>{' · '}<a href={`${API_BASE}/templates/xero/invoices.csv`} download>Xero</a>{' · '}<a href={`${API_BASE}/templates/stripe/invoices.csv`} download>Stripe</a></p>
        </div>
      </div>
      <details className="manual-entry">
        <summary>Enter terms manually</summary>
        <div className="contract-form-grid">
          <label>Customer name<input value={contractDraft.customer_name} onChange={(event) => handleContractField('customer_name', event.target.value)} /></label>
          <label>Customer ID<input value={contractDraft.customer_id} onChange={(event) => handleContractField('customer_id', event.target.value)} placeholder="acme" /></label>
          <label>Committed minimum monthly<input type="number" value={contractDraft.committed_minimum_monthly} onChange={(event) => handleContractField('committed_minimum_monthly', event.target.value)} /></label>
          <label>Included units<input type="number" value={contractDraft.included_units} onChange={(event) => handleContractField('included_units', event.target.value)} /></label>
          <label>Overage rate<input type="number" step="0.01" value={contractDraft.overage_rate} onChange={(event) => handleContractField('overage_rate', event.target.value)} /></label>
          <label>Annual escalator %<input type="number" step="0.01" value={contractDraft.annual_escalator_pct} onChange={(event) => handleContractField('annual_escalator_pct', event.target.value)} /></label>
          <label>Escalator effective date<input type="date" value={contractDraft.escalator_effective_date} onChange={(event) => handleContractField('escalator_effective_date', event.target.value)} /></label>
          <label>Discount name<input value={contractDraft.discount_name} onChange={(event) => handleContractField('discount_name', event.target.value)} /></label>
          <label>Discount value<input type="number" step="0.01" value={contractDraft.discount_value} onChange={(event) => handleContractField('discount_value', event.target.value)} /></label>
          <label>Discount expires<input type="date" value={contractDraft.discount_expires} onChange={(event) => handleContractField('discount_expires', event.target.value)} /></label>
          <label>Discount applies to<input value={contractDraft.discount_applies_to} onChange={(event) => handleContractField('discount_applies_to', event.target.value)} /></label>
        </div>
        <div className="clause-grid">
          <label>Committed minimum clause quote<textarea value={contractDraft.clauses.committed_minimum} onChange={(event) => handleClauseField('committed_minimum', event.target.value)} rows={3} /></label>
          <label>Overage clause quote<textarea value={contractDraft.clauses.overage} onChange={(event) => handleClauseField('overage', event.target.value)} rows={3} /></label>
          <label>Discount clause quote<textarea value={contractDraft.clauses.discount} onChange={(event) => handleClauseField('discount', event.target.value)} rows={3} /></label>
          <label>Escalator clause quote<textarea value={contractDraft.clauses.escalator} onChange={(event) => handleClauseField('escalator', event.target.value)} rows={3} /></label>
        </div>
        <div className="panel-footer"><button className="btn-primary" onClick={submitContract} disabled={contractSubmitting}>{contractSubmitting ? 'Uploading…' : 'Upload contract'}</button></div>
      </details>
      <div className="panel-section"><div className="info-label">Agreement list</div>
        {uploadedContracts.length === 0 ? <p className="muted-copy">No agreements uploaded yet.</p> : uploadedContracts.map((contract) => (
          <div key={contract.customer_id} className="agreement-card">
            <div className="panel-heading"><div><strong title={contract.customer_name} className="truncate">{contract.customer_name}</strong><span className="muted-copy"> {contract.customer_id}</span></div><span className={`status-pill ${contract.confirmed ? 'status-approved' : ''}`}>{contract.confirmed ? 'Confirmed' : 'Not confirmed'}</span></div>
            <div className="muted-copy">Term {contract.term_start || '—'} → {contract.term_end || '—'} · minimum {formatCurrency(contract.committed_minimum_monthly)} · overage {formatRate(contract.overage_rate)} · escalator {contract.annual_escalator_pct ?? '—'}% · discounts {(contract.discounts || []).length}</div>
            <div className="muted-copy">Confidence {Math.round(Number((contract.term_meta?.committed_minimum_monthly?.confidence ?? 1) * 100))}% · {contract.term_meta?.committed_minimum_monthly?.provenance || 'No provenance'}</div>
            {!contract.confirmed && <button className="btn-primary" disabled={confirmingCustomer === contract.customer_id} onClick={() => confirmContract(contract.customer_id)}>{confirmingCustomer === contract.customer_id ? 'Confirming…' : 'Confirm terms'}</button>}
            <details><summary>Financial rights</summary>
              {rights[contract.customer_id]?.length ? <ul className="upload-history">{rights[contract.customer_id].map((right) => <li key={right.right_id || right.candidate_id}><span>{right.right_type || right.type}</span><span className="muted-copy">{right.status || right.candidate_status || '—'}</span></li>)}</ul> : <p className="muted-copy">{rights[contract.customer_id] === undefined ? 'Loading rights…' : 'No additional rights discovered'}</p>}
            </details>
          </div>
        ))}
      </div>
      <div className="panel-section"><div className="info-label">Renewals</div>
        {renewals.length === 0 ? <p className="muted-copy">No renewal dates recorded.</p> : <ul className="upload-history">{renewals.map((renewal, i) => <li key={i} className="upload-history-item"><span>{renewal.customer_name || renewal.customer_id}</span><span className="muted-copy">{renewal.term_end || renewal.cancellation_deadline || '—'}</span></li>)}</ul>}
      </div>
    </section>
  )

  const renderRecoveries = () => (
    <section className="panel-card">
      <div className="panel-heading"><div><p className="eyebrow">Recoveries</p><h2>Recovered value and fees</h2></div></div>
      <div className="metric-grid">
        {[['recovered_to_date', 'Recovered to date'], ['recovered_this_month', 'Recovered this month'], ['success_fee_to_date', 'Success fee to date'], ['success_fee_this_month', 'Success fee this month'], ['invoiced_awaiting_payment', 'Invoiced awaiting payment'], ['written_off', 'Written off'], ['potential_monthly_recoverable', 'Potential monthly recoverable']].map(([key, label]) => <div key={key} className="metric-card"><span className="metric-label">{label}</span><strong className="metric-value money">{formatCurrency(metrics?.[key])}</strong></div>)}
      </div>
      <div className="panel-section"><div className="info-label">Recovery cases</div>
        <div className="table-scroll"><table className="cc-table"><thead><tr><th>Counterparty</th><th>Status</th><th>Realized</th><th>Outstanding</th><th>Action</th></tr></thead><tbody>
          {(commandCenter?.cases || []).filter((c) => ['approved', 'invoiced', 'disputed', 'recovered', 'written_off'].includes(c.status)).map((c) => <tr key={c.finding_id} onClick={() => navigate('opportunities', c.finding_id)}><td>{c.counterparty?.customer_name}</td><td>{c.status}</td><td className="money">{formatCurrency(c.ledger?.realized_value)}</td><td className="money">{formatCurrency(c.ledger?.outstanding_value)}</td><td>{c.recommended_next_step}</td></tr>)}
          {(commandCenter?.cases || []).filter((c) => ['approved', 'invoiced', 'disputed', 'recovered', 'written_off'].includes(c.status)).length === 0 && <tr><td colSpan="5" className="muted-copy">No recovery cases yet — approve an opportunity first.</td></tr>}
        </tbody></table></div>
      </div>
      <div className="panel-section"><div className="info-label">Record realized value</div>
        {recoveryCases.length === 0 ? <p className="muted-copy">No recovery cases are open.</p> : recoveryCases.map((finding) => <div key={finding.finding_id} className="agreement-card"><strong>{finding.customer_name || finding.customer_id}</strong><div className="muted-copy">{finding.status} · {finding.monthly_recoverable != null ? formatCurrency(finding.monthly_recoverable) : '—'}</div><button className="btn-primary" onClick={() => openRealizationForm(finding)}>Record realized value</button>{renderRealizationForm(finding)}{renderEvents(finding)}</div>)}
      </div>
      <div className="panel-section"><div className="info-label">True-up letters</div>
        <label className="styled-field">Sender<input value={trueupSender} onChange={(e) => setTrueupSender(e.target.value)} /></label>
        {trueupCustomers.length === 0 ? <p className="muted-copy">No true-up customers.</p> : trueupCustomers.map((customer) => <div key={customer.customer_id} className="agreement-card"><strong>{customer.customer_name}</strong><div className="muted-copy">{customer.count} finding(s)</div><button className="btn-secondary" disabled={proofLocked} title={proofLocked ? lockTitle : ''} onClick={() => downloadTrueupPdf(customer.customer_id, customer.customer_name)}>Download letter + schedule PDF</button></div>)}
      </div>
      <div className="review-actions">
        <button className="btn-secondary" disabled={proofLocked} title={proofLocked ? lockTitle : ''} onClick={exportFindings}><Download size={15} /> Export findings CSV</button>
        <button className="btn-secondary" disabled={proofLocked} title={proofLocked ? lockTitle : ''} onClick={openAuditReport}><FileText size={15} /> Audit report</button>
        <button className="btn-secondary" disabled={proofLocked} title={proofLocked ? lockTitle : ''} onClick={downloadReportPdf}><Download size={15} /> PDF report</button>
        <button className="btn-primary" onClick={chargeSuccessFee}>Bill success fee this month</button>
      </div>
    </section>
  )

  const renderIntegrations = () => (
    <section className="panel-card">
      <div className="panel-heading"><div><p className="eyebrow">Integrations</p><h2>Integrations</h2></div><span className={`connected-pill ${connectorConnection?.connected ? 'active' : ''}`}><ShieldCheck size={14} /> {connectorConnection?.connected ? 'Connected' : 'Not connected'}</span></div>
      <div className="info-box"><ShieldCheck size={18} /><div><strong>Read-only Stripe access</strong><p>The Stripe API key stays on the server. Recoup reads customer, subscription, usage and invoice data only.</p></div></div>
      <div className="info-box"><DollarSign size={18} /><div><strong>Outcome-based pricing</strong><p>Recoup charges 20% of dollars actually paid to you — tracked on Recoveries.</p></div></div>
      {isSampleMode ? <div className="info-box"><FileText size={18} /><div><strong>Sample mode</strong><p>Sample mode runs on synthetic data and does not connect to Stripe.</p></div></div> : <button className="btn-primary" onClick={startStripeInstall} disabled={connectorSubmitting}>{connectorSubmitting ? 'Connecting…' : 'Connect with Stripe'}</button>}
      {connectorStatus && <p className="muted-copy" role="status">{connectorStatus}</p>}
      <button className="btn-secondary" onClick={syncStripeRecoveries} disabled={syncingRecoveries}>{syncingRecoveries ? 'Checking…' : 'Check Stripe for paid invoices'}</button>
      <div className="panel-section"><div className="info-label">CSV fallback</div><p className="muted-copy">If Stripe is unavailable, export invoices/usage with these templates and upload on Agreements.</p><p className="muted-copy"><a href={`${API_BASE}/templates/quickbooks/invoices.csv`} download>QuickBooks</a>{' · '}<a href={`${API_BASE}/templates/xero/invoices.csv`} download>Xero</a>{' · '}<a href={`${API_BASE}/templates/stripe/invoices.csv`} download>Stripe</a></p></div>
    </section>
  )

  const renderSettings = () => (
    <section className="panel-card">
      <div className="panel-heading"><div><p className="eyebrow">Settings</p><h2>Settings</h2></div></div>
      <div className="panel-section"><div className="info-label">Billing</div>
        <p className="muted-copy">Card on file: {billing?.card_on_file ? `${billing.card_brand || ''} •••• ${billing.card_last4 || ''}` : 'None'}</p>
        <p className="muted-copy">Recoup charges 20% of net realized recovered value. <a href="/terms.html" target="_blank" rel="noreferrer">Terms of Service</a></p>
        <button className="btn-primary" onClick={startBillingSetup}>Add payment method</button>
      </div>
      <div className="panel-section"><div className="info-label">Account</div>
        <p className="muted-copy">{firebaseUser?.email || 'sample@recoup.local'}</p>
        {isSampleMode && <button className="btn-secondary" onClick={() => setSessionMode(null)}>Exit sample mode</button>}
        <button className="btn-secondary" onClick={handleLogout}>Sign out</button>
        <button className="btn-danger" onClick={deleteAccountData}>Delete all account data</button>
      </div>
    </section>
  )

  if (loadingAuth) {
    return <div className="app-container loading-shell"><RefreshCw className="spin" size={28} /><span>Loading session…</span></div>
  }

  if (!apiReady) {
    return (
      <div className="app-container auth-shell">
        <div className="panel-card auth-card">
          <div className="auth-hero"><div className="auth-mark"><Building2 size={28} /></div><div><h1 className="brand-title">Recoup</h1><p className="auth-subtitle">Revenue assurance for contract recovery workflows.</p></div></div>
          <div className="auth-note"><LockKeyhole size={16} /><span>Firebase Auth is required for real data. Sample data is available explicitly below.</span></div>
          <div className="auth-actions">
            <button className="btn-primary auth-button" onClick={handleGoogleLogin}><LogIn size={16} /> Sign in with Google</button>
            <button className="btn-secondary auth-button" onClick={sampleModeLogin}><Sparkles size={16} /> Try with sample data</button>
          </div>
          <a className="back-to-site" href="/">&larr; Recoup</a>
        </div>
      </div>
    )
  }

  return (
    <div className="app-container recoup-shell">
      <header className="header shell-header">
        <div><div className="brand-row"><h1 className="brand-title"><Building2 size={28} /> Recoup</h1><span className={`session-pill ${isSampleMode ? 'sample' : 'auth'}`}>{reviewLabel}</span></div><p className="subtitle">Revenue recovery workspace</p></div>
        <div className="header-actions">
          <div className="period-picker"><label htmlFor="billing-period">Period</label><input id="billing-period" value={billingPeriod} onChange={(event) => setBillingPeriod(event.target.value)} placeholder="YYYY-MM" /></div>
          <button className="btn-primary" onClick={runEvaluation} disabled={running}>{running ? <RefreshCw className="spin" size={16} /> : <FileText size={16} />}{running ? 'Evaluating…' : 'Run evaluation'}</button>
          <button className="btn-danger" onClick={handleLogout} title="Sign out" aria-label="Sign out"><LogOut size={16} /></button>
        </div>
      </header>
      {isSampleMode && <div className="mode-banner sample-banner"><Sparkles size={16} /> Sample data is active. Requests use synthetic data and are not tied to your account.<button className="btn-secondary" onClick={() => setSessionMode(null)}>Exit sample mode</button></div>}
      {proofLocked && <div className="mode-banner"><LockKeyhole size={16} /><div><strong>{lockTitle}</strong><p className="muted-copy">Add a payment method to unlock clause proof, audit reports and true-up packs. You are only charged 20% of dollars actually recovered — nothing upfront. By adding a card you agree to the <a href="/terms.html" target="_blank" rel="noreferrer">Terms of Service</a>.</p></div><button className="btn-primary" onClick={startBillingSetup}>Add payment method</button></div>}
      <div className="app-grid">
        <aside className="sidebar-panel">
          <nav className="stepper-list" aria-label="Primary">
            {NAV_ITEMS.map((item) => { const Icon = item.icon; const active = screen === item.id; void Icon; return <button key={item.id} type="button" className={`stepper-item ${active ? 'active' : ''}`} onClick={() => navigate(item.id)}><span className="step-icon"><Icon size={16} /></span><span className="step-copy"><span className="step-title">{item.title}</span></span><ChevronRight size={16} /></button> })}
          </nav>
        </aside>
        <main className="step-content">
          {renderStatus()}
          {screen === 'overview' && renderOverview()}
          {screen === 'opportunities' && renderOpportunities()}
          {screen === 'agreements' && renderAgreements()}
          {screen === 'recoveries' && renderRecoveries()}
          {screen === 'integrations' && renderIntegrations()}
          {screen === 'settings' && renderSettings()}
        </main>
      </div>
    </div>
  )
}

export default App
