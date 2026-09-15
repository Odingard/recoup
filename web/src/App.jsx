import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  ChevronDown,
  DollarSign,
  Download,
  FileText,
  LogOut,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Upload,
  X,
  XCircle,
} from 'lucide-react'
import { GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut } from 'firebase/auth'
import { auth } from './firebase'
import RecoveryActions, { RecoveryActionSelect } from './RecoveryActions'
import './App.css'

const API_BASE = (import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8001/api').replace(/\/$/, '')
const DEFAULT_PERIOD = '2026-06'
void [AlertCircle, ChevronDown, DollarSign, Download, FileText, LogOut, LockKeyhole, RefreshCw, ShieldCheck, SlidersHorizontal, Upload, X, XCircle, RecoveryActions, RecoveryActionSelect]

const SHELL_LINKS = [
  { id: 'agreements', title: 'Agreements' },
  { id: 'integrations', title: 'Integrations' },
  { id: 'settings', title: 'Settings' },
]

const SPINE_TITLES = ['Watch', 'Detect', 'Prove', 'Prioritize', 'Act', 'Approve', 'Recover', 'Verify']

const SPINE_DESCRIPTIONS = [
  'Re-evaluates the affected agreements whenever new documents are uploaded or an evaluation is run.',
  'Identify when financial reality diverges from what the agreement required.',
  'Tie the discrepancy to the source term, evidence, actual activity and calculation.',
  'Rank recovery opportunities by value, confidence, age and status.',
  'Prepare the appropriate recovery action.',
  'Your team retains authority over consequential recovery actions.',
  'Move the approved case through the recovery workflow.',
  'Confirm whether value was actually realized.',
]

const TRIGGER_LABELS = {
  new_invoice: 'New invoice', new_billing_period: 'New billing period', new_usage: 'New usage',
  new_credit: 'Credit/refund', new_agreement: 'New agreement', agreement_amendment: 'Agreement amended',
  contract_renewal: 'Renewal', term_expiration: 'Term expired', pricing_change: 'Pricing change',
}

const TERM_LABELS = {
  committed_minimum_monthly: 'Minimum', included_units: 'Included units', overage_rate: 'Overage',
  annual_escalator_pct: 'Escalator', escalator_effective_date: 'Escalator date', term_start: 'Term',
  term_end: 'Term', auto_renew_months: 'Auto-renew', renewal_notice_days: 'Notice', committed_seats: 'Seats',
}

const CLAUSE_REF_LABELS = {
  committed_minimum: 'Committed minimum', overage: 'Overage', discount: 'Discount', escalator: 'Escalator',
}

const DOC_ROLE_LABELS = { master: 'Master agreement', order_form: 'Order form', exhibit: 'Exhibit', sow: 'SOW', other: 'Document' }
const docRoleLabel = (doc) => doc?.role === 'amendment' ? `Amendment${doc.amendment_number ? ` ${doc.amendment_number}` : ''}` : (DOC_ROLE_LABELS[doc?.role] || 'Document')
const TERM_HISTORY_KEYS = { committed_minimum_monthly: 'committed_minimum', annual_escalator_pct: 'escalator', escalator_effective_date: 'escalator', auto_renew_months: 'auto_renewal', term_start: 'term_start', term_end: 'term_end', included_units: 'included_units', overage_rate: 'overage_rate', renewal_notice_days: 'renewal_notice_days', committed_seats: 'committed_seats', seat_price: 'seat_price' }

function termHistoryNote(contract, metaKey) {
  const history = contract?.term_history?.[TERM_HISTORY_KEYS[metaKey] || metaKey]
  if (!history || history.length < 2) return null
  const last = history[history.length - 1]
  const doc = (contract?.documents || []).find((d) => d.file_name === last.source_file)
  const label = doc ? docRoleLabel(doc) : (last.source_file || 'a later document')
  return `changed by ${label}${last.effective_date ? ` (${last.effective_date})` : ''}`
}

function caseStageLabel(caseItem) {
  const status = caseItem?.status || 'open'
  if (status === 'open') return caseItem?.verified ? 'Approve' : 'Prove'
  if (status === 'approved') return 'Act'
  if (status === 'invoiced' || status === 'disputed') return 'Recover'
  if (status === 'recovered') return 'Verify'
  return status
}

function shortActor(value) {
  if (!value) return 'your team'
  return String(value).split('@')[0]
}

function triggerLabel(trigger) {
  if (!trigger) return 'Evaluation'
  return TRIGGER_LABELS[trigger] || trigger.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function relativeTime(value) {
  if (!value) return null
  const ms = Date.now() - new Date(value).getTime()
  if (!Number.isFinite(ms)) return null
  const minutes = Math.max(0, Math.round(ms / 60000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

const TEMPLATE_LINKS = (
  <>
    <a href={`${API_BASE}/templates/quickbooks/invoices.csv`} download>QuickBooks</a>
    {' · '}
    <a href={`${API_BASE}/templates/xero/invoices.csv`} download>Xero</a>
    {' · '}
    <a href={`${API_BASE}/templates/stripe/invoices.csv`} download>Stripe</a>
  </>
)

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

const ACTIONABLE_REALIZATION_STATUSES = ['approved', 'invoiced', 'disputed']
const REVERSIBLE_FINDING_STATUSES = ['approved', 'invoiced', 'disputed', 'recovered']

const LEGAL_NEXT_ACTIONS = {
  open: ['approve', 'reject'],
  approved: ['invoice', 'payment', 'writeoff', 'reject'],
  invoiced: ['payment', 'dispute', 'writeoff'],
  disputed: ['payment', 'writeoff'],
  rejected: [], recovered: [], written_off: [],
}

function apiErrorMessage(text) {
  let detail = text
  try {
    const parsed = JSON.parse(text)
    detail = parsed?.detail ?? parsed?.message ?? text
  } catch { /* keep raw text */ }
  if (detail && typeof detail === 'object') {
    return detail.message || detail.detail ||
      (detail.status === 'duplicate'
        ? 'This external reference was already recorded for this case.'
        : JSON.stringify(detail))
  }
  return String(detail || 'Request failed')
}

const MONEY_FORMATTER = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

function formatCurrency(value) {
  return MONEY_FORMATTER.format(Number(value || 0))
}

function formatRate(value) {
  return formatCurrency(value)
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

function normalizeHash() {
  const raw = (window.location.hash || '#/workspace').replace(/^#\/?/, '')
  const [screen, id] = raw.split('/')
  return { screen: screen || 'workspace', id: id || null }
}

function App() {
  const [firebaseUser, setFirebaseUser] = useState(null)
  const [sessionMode, setSessionMode] = useState(null)
  const [loadingAuth, setLoadingAuth] = useState(true)
  const [route, setRoute] = useState(normalizeHash())
  const [billingPeriod, setBillingPeriod] = useState(DEFAULT_PERIOD)
  const [, setFindings] = useState([])
  const [allFindings, setAllFindings] = useState([])
  const [findingsLoaded, setFindingsLoaded] = useState(false)
  const [running, setRunning] = useState(false)
  const [statusMessage, setStatusMessage] = useState('')
  const [uploadedContracts, setUploadedContracts] = useState([])
  const [selectedFileName, setSelectedFileName] = useState('')
  const [bulkResult, setBulkResult] = useState(null)
  const [bulkUploading, setBulkUploading] = useState(false)
  const [metrics, setMetrics] = useState(null)
  const [connectorSubmitting, setConnectorSubmitting] = useState(false)
  const [connectorStatus, setConnectorStatus] = useState('')
  const [connectorConnection, setConnectorConnection] = useState(null)
  const [renewals, setRenewals] = useState([])
  const [billing, setBilling] = useState(null)
  const [termsAccepted, setTermsAccepted] = useState(false)
  const [syncingRecoveries, setSyncingRecoveries] = useState(false)
  const [reviewQueue, setReviewQueue] = useState([])
  const [assurance, setAssurance] = useState(null)
  const [commandCenter, setCommandCenter] = useState(null)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [rights, setRights] = useState({})
  const [events, setEvents] = useState({})
  const [eventsLoading, setEventsLoading] = useState({})
  const [realizationForm, setRealizationForm] = useState(null)
  const [realizationFields, setRealizationFields] = useState({ basis: 'cash_payment', amount: '', date: '', reference: '', actionId: '', note: '' })
  const [reverseForm, setReverseForm] = useState(null)
  const [reverseFields, setReverseFields] = useState({ amount: '', reference: '', reason: '' })
  const [confirmingCustomer, setConfirmingCustomer] = useState('')
  const [isOperator, setIsOperator] = useState(false)
  const [adminSettings, setAdminSettings] = useState(null)
  const [adminInviteText, setAdminInviteText] = useState('')
  const [adminTenants, setAdminTenants] = useState([])
  const [adminAudit, setAdminAudit] = useState([])
  const [adminSelected, setAdminSelected] = useState(null)
  const [adminLoading, setAdminLoading] = useState(false)
  const [adminSaving, setAdminSaving] = useState(false)
  const [adminResetConfirm, setAdminResetConfirm] = useState('')

  const isSampleMode = sessionMode === 'sample'
  const isAuthenticated = sessionMode === 'auth' && Boolean(firebaseUser)
  const apiReady = isSampleMode || isAuthenticated
  const [uploadFileCount, setUploadFileCount] = useState(0)
  const [dragging, setDragging] = useState(false)
  const [stageFilter, setStageFilter] = useState(null)
  const [pulseStep, setPulseStep] = useState(0)
  const [activity, setActivity] = useState([])
  const sampleSeeded = useRef(false)
  const [showFilters, setShowFilters] = useState(false)
  const [showPerformance, setShowPerformance] = useState(() => {
    try { return window.localStorage.getItem('recoup.performance') === '1' } catch { return false }
  })
  const navItems = useMemo(() => (
    isOperator
      ? [...SHELL_LINKS, { id: 'admin', title: 'Platform Admin' }]
      : SHELL_LINKS
  ), [isOperator])
  const isDrawerScreen = navItems.some((item) => item.id === route.screen)
  const drawerScreen = isDrawerScreen ? route.screen : null

  const togglePerformance = () => {
    setShowPerformance((current) => {
      const next = !current
      try { window.localStorage.setItem('recoup.performance', next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }

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

  useEffect(() => {
    if (!drawerScreen) return undefined
    const onKey = (event) => { if (event.key === 'Escape') navigate('workspace') }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [drawerScreen]) // eslint-disable-line react-hooks/exhaustive-deps

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
      throw new Error(apiErrorMessage(await res.text()))
    }
    const text = await res.text()
    const parsed = text ? JSON.parse(text) : null
    const method = (options.method || 'GET').toUpperCase()
    if (method !== 'GET' && parsed?.status === 'needs_review' && Array.isArray(parsed.fields)) {
      const fieldMessages = parsed.fields.map((field) => field?.message || field?.field).filter(Boolean).join('; ')
      throw new Error([parsed.message, fieldMessages].filter(Boolean).join(' '))
    }
    return parsed
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

  const loadAdmin = useCallback(async () => {
    if (!isOperator) return
    setAdminLoading(true)
    try {
      const [settings, tenants, audit] = await Promise.all([
        apiRequest('/admin/settings'),
        apiRequest('/admin/tenants'),
        apiRequest('/admin/audit'),
      ])
      setAdminSettings(settings)
      setAdminInviteText((settings?.invited_emails || []).join('\n'))
      setAdminTenants(Array.isArray(tenants) ? tenants : [])
      setAdminAudit(Array.isArray(audit) ? audit : [])
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Platform admin load failed', error))
    } finally {
      setAdminLoading(false)
    }
  }, [apiRequest, isOperator])

  useEffect(() => {
    if (!isAuthenticated) {
      const handle = window.setTimeout(() => setIsOperator(false), 0)
      return () => window.clearTimeout(handle)
    }
    let cancelled = false
    apiRequest('/admin/me')
      .then((result) => { if (!cancelled) setIsOperator(Boolean(result?.operator)) })
      .catch(() => { if (!cancelled) setIsOperator(false) })
    return () => { cancelled = true }
  }, [apiRequest, isAuthenticated])

  useEffect(() => {
    if (!isOperator || route.screen !== 'admin') return
    const handle = window.setTimeout(() => void loadAdmin(), 0)
    return () => window.clearTimeout(handle)
  }, [isOperator, route.screen, loadAdmin])

  const loadAdminTenant = async (accountId) => {
    if (!accountId) return
    try {
      setAdminSelected(await apiRequest(`/admin/tenants/${encodeURIComponent(accountId)}`))
      setAdminResetConfirm('')
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Could not load tenant', error))
    }
  }

  const saveAdminSettings = async (next = {}) => {
    setAdminSaving(true)
    try {
      const invited = (next.invitedText ?? adminInviteText)
        .split(/\n+/).map((email) => email.trim()).filter(Boolean)
      const settings = await apiRequest('/admin/settings', {
        method: 'PUT',
        body: {
          signup_enabled: next.signupEnabled ?? adminSettings?.signup_enabled ?? true,
          invited_emails: invited,
        },
      })
      setAdminSettings(settings)
      setAdminInviteText((settings.invited_emails || []).join('\n'))
      setAdminAudit(await apiRequest('/admin/audit'))
      setStatusMessage('Platform settings saved.')
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Could not save platform settings', error))
    } finally { setAdminSaving(false) }
  }

  const setAdminDemo = async (tenant, demo) => {
    try {
      const updated = await apiRequest(`/admin/tenants/${encodeURIComponent(tenant.account_id)}/demo`, { method: 'POST', body: { demo } })
      setAdminTenants((current) => current.map((item) => item.account_id === tenant.account_id ? { ...item, ...updated } : item))
      setAdminSelected((current) => current?.account_id === tenant.account_id ? { ...current, ...updated } : current)
      setAdminAudit(await apiRequest('/admin/audit'))
      setStatusMessage(demo ? 'Tenant marked as demo.' : 'Tenant demo flag removed.')
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Could not update demo flag', error))
    }
  }

  const retryAdminFee = async (event) => {
    const accountId = adminSelected?.account_id
    if (!accountId) return
    try {
      await apiRequest(`/admin/tenants/${encodeURIComponent(accountId)}/retry-fee`, {
        method: 'POST', body: { recovery_event_id: event.recovery_event_id },
      })
      setAdminSelected(await apiRequest(`/admin/tenants/${encodeURIComponent(accountId)}`))
    } catch (error) { setStatusMessage(failureMessage('Fee retry failed', error)) }
  }

  const resetAdminTenant = async () => {
    const accountId = adminSelected?.account_id
    if (!accountId) return
    setAdminSaving(true)
    try {
      const result = await apiRequest(`/admin/tenants/${encodeURIComponent(accountId)}/reset`, {
        method: 'POST',
        body: { confirm_account_id: adminResetConfirm },
      })
      const deleted = Object.entries(result?.deleted || {}).map(([name, count]) => `${name}: ${count}`).join(', ')
      setStatusMessage(`Demo tenant reset.${deleted ? ` Deleted ${deleted}.` : ''}`)
      setAdminSelected(null)
      setAdminResetConfirm('')
      await loadAdmin()
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Demo tenant reset failed', error))
    } finally { setAdminSaving(false) }
  }

  const appendActivity = useCallback((lines) => {
    const entries = (Array.isArray(lines) ? lines : [lines]).filter(Boolean).map((message) => ({
      id: `${Date.now()}-${Math.random()}`,
      at: new Date().toISOString(),
      message,
    }))
    if (!entries.length) return
    setActivity((current) => [...current, ...entries].slice(-50))
  }, [])

  const formatAssuranceActivity = useCallback((event) => {
    if (!event || event.status === 'duplicate') return null
    const customer = uploadedContracts.find((contract) => contract.customer_id === event.customer_id)?.customer_name
      || commandCenter?.filters?.customers?.find((item) => item.id === event.customer_id)?.name
      || event.customer_id
      || 'counterparty'
    const period = event.period || 'all periods'
    const label = triggerLabel(event.trigger)
    if (event.status === 'needs_review') return `${label} · could not resolve counterparty → sent to review`
    if (event.status === 'error') return `${label} · evaluation failed → sent to review`
    const updated = Number(event.findings_upserted || 0)
    const withdrawn = Array.isArray(event.findings_withdrawn) ? event.findings_withdrawn.length : Number(event.findings_withdrawn || 0)
    const review = Array.isArray(event.needs_review) ? event.needs_review.length : Number(event.needs_review_count || 0)
    return `${label} · ${customer} · ${period} → ${updated} discrepanc${updated === 1 ? 'y' : 'ies'} updated${withdrawn ? `, ${withdrawn} withdrawn` : ''}${review ? `, ${review} sent to review` : ''}`
  }, [uploadedContracts, commandCenter])

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
    try {
      const result = await apiRequest('/assurance/status')
      setAssurance(result)
      const recentEvents = (result?.recent_events || []).slice().reverse()
      const seeded = recentEvents.map((event) => ({ message: formatAssuranceActivity(event), at: event.evaluated_at || new Date().toISOString() })).filter((entry) => entry.message)
      if (seeded.length) setActivity((current) => current.length ? current : seeded.map((entry, index) => ({ id: `seed-${index}-${entry.message}`, ...entry })))
    } catch (error) { console.error(error) }
  }, [apiReady, apiRequest, formatAssuranceActivity])

  const refreshFindings = useCallback(async () => {
    if (!apiReady) return
    try {
      const [pending, all] = await Promise.all([apiRequest('/findings/pending'), apiRequest('/findings')])
      setFindings(Array.isArray(pending) ? pending : [])
      setAllFindings(Array.isArray(all) ? all : [])
    } finally {
      setFindingsLoaded(true)
      void loadMetrics(); void loadAssurance(); void loadCommandCenter()
    }
  }, [apiReady, apiRequest, loadMetrics, loadAssurance, loadCommandCenter])

  const loadContracts = useCallback(async () => {
    if (!apiReady) return
    try {
      const result = await apiRequest('/contracts')
      const list = result?.contracts || []
      setUploadedContracts(list.map((c) => ({ ...c, confirmed: Boolean(c.confirmed), discounts: c.discounts || [] })))
    } catch (error) { console.error(error) }
  }, [apiReady, apiRequest])

  const loadRenewals = useCallback(async () => {
    if (!apiReady) return
    try {
      const rows = await apiRequest('/renewals')
      setRenewals(Array.isArray(rows) ? rows : [])
    } catch (error) {
      console.error(error)
      setRenewals([])
    }
  }, [apiReady, apiRequest])

  const loadRecoveryEvents = useCallback(async (findingId) => {
    if (!apiReady || !findingId) return
    setEventsLoading((current) => ({ ...current, [findingId]: true }))
    try {
      const rows = await apiRequest(`/findings/${findingId}/recovery-events`)
      setEvents((current) => ({ ...current, [findingId]: Array.isArray(rows) ? rows : (rows?.events || []) }))
    } catch (error) {
      console.error(error)
      setEvents((current) => ({ ...current, [findingId]: [] }))
    } finally {
      setEventsLoading((current) => ({ ...current, [findingId]: false }))
    }
  }, [apiReady, apiRequest])

  useEffect(() => {
    if (!apiReady || route.screen !== 'agreements') return
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
  }, [apiReady, route.screen, uploadedContracts, rights, apiRequest])

  const loadBillingStatus = useCallback(async () => {
    if (!apiReady) return
    try { setBilling(await apiRequest('/billing/status')) } catch (error) { console.error(error) }
  }, [apiReady, apiRequest])

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([
      refreshFindings(),
      loadContracts(),
      loadRenewals(),
      loadBillingStatus(),
    ])
  }, [refreshFindings, loadContracts, loadRenewals, loadBillingStatus])

  useEffect(() => {
    if (!apiReady) return
    const handle = window.setTimeout(() => {
      void refreshAll(); void loadConnectorStatus()
    }, 0)
    return () => window.clearTimeout(handle)
  }, [apiReady, refreshAll, loadConnectorStatus])

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
    if (!isSampleMode || sampleSeeded.current) return undefined
    sampleSeeded.current = true
    const seed = async () => {
      try {
        const result = await apiRequest(`/reconcile?period=${billingPeriod}`, { method: 'POST' })
        appendActivity(`Evaluated ${result?.agreements_evaluated ?? result?.contracts_evaluated ?? result?.findings_found ?? 0} findings for ${billingPeriod}`)
        const latestFindings = await apiRequest('/findings')
        appendActivity((Array.isArray(latestFindings) ? latestFindings : []).map((finding) => {
          const customer = finding.customer_name || finding.customer_id || 'counterparty'
          const title = finding.financial_right?.title || finding.title || finding.finding_id
          return `Found ${title} · ${customer} · ${formatCurrency(finding.recoverable_difference ?? finding.monthly_recoverable)}`
        }))
        await refreshAll()
      } catch (error) { console.error(error) }
    }
    void seed()
    return undefined
  }, [apiRequest, appendActivity, billingPeriod, isSampleMode, refreshAll, sampleSeeded])

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
      setFindings([]); setAllFindings([]); setFindingsLoaded(false)
      setEvents({}); setEventsLoading({}); setActivity([]); sampleSeeded.current = false; setStageFilter(null)
      setStatusMessage('')
      setConnectorConnection(null); setMetrics(null); setConnectorStatus('')
      setIsOperator(false); setAdminSettings(null); setAdminInviteText('')
      setAdminTenants([]); setAdminAudit([]); setAdminSelected(null)
      setAdminResetConfirm('')
    }
  }

  const handleBulkUpload = async (filesOrEvent) => {
    const isInputEvent = Boolean(filesOrEvent?.target?.files)
    const fileList = Array.from(isInputEvent ? filesOrEvent.target.files : filesOrEvent || [])
    if (isInputEvent) filesOrEvent.target.value = ''
    if (!fileList.length) return
    setSelectedFileName(fileList[0].name)
    setUploadFileCount(fileList.length)
    setBulkUploading(true); setBulkResult(null)
    try {
      const form = new FormData()
      fileList.forEach((f) => form.append('files', f))
      const result = await apiRequest('/ingest/bulk', { method: 'POST', body: form })
      setBulkResult(result)
      appendActivity((result?.files || []).map((file) => {
        const kind = file.kind || file.type || 'file'
        const customer = file.customer || file.customer_name || file.customer_id
        const review = file.status === 'error' || file.status === 'needs_review' ? ' · needs review' : ''
        return `Read ${file.name} → ${kind}${customer ? ` · matched ${customer}` : ''}${review}`
      }))
      appendActivity((result?.assurance?.events || []).map(formatAssuranceActivity).filter(Boolean))
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
      await refreshAll()
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Bulk upload failed', error))
    } finally { setBulkUploading(false) }
  }

  const handleEmptyDragOver = (event) => {
    if (isSampleMode) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    setDragging(true)
  }

  const handleEmptyDragLeave = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false)
  }

  const handleEmptyDrop = (event) => {
    if (isSampleMode) return
    event.preventDefault()
    setDragging(false)
    void handleBulkUpload(event.dataTransfer.files)
  }

  const confirmContract = async (customerId) => {
    setConfirmingCustomer(customerId)
    try {
      const result = await apiRequest(`/contracts/${encodeURIComponent(customerId)}/confirm`, { method: 'POST' })
      if (result?.status === 'confirmed' || result?.contract?.confirmed) {
        setUploadedContracts((current) => current.map((item) => item.customer_id === customerId ? { ...item, confirmed: true, confirmed_by: result.contract?.confirmed_by, confirmed_at: result.contract?.confirmed_at } : item))
        setStatusMessage('Contract terms confirmed.')
        await refreshAll()
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
      appendActivity(`Evaluated ${result?.agreements_evaluated ?? result?.contracts_evaluated ?? result?.findings_found ?? 0} findings for ${billingPeriod}`)
      await refreshAll()
      const latestFindings = await apiRequest('/findings')
      appendActivity((Array.isArray(latestFindings) ? latestFindings : []).map((finding) => {
        const customer = finding.customer_name || finding.customer_id || 'counterparty'
        const title = finding.financial_right?.title || finding.title || finding.finding_id
        return `Found ${title} · ${customer} · ${formatCurrency(finding.recoverable_difference ?? finding.monthly_recoverable)}`
      }))
      navigate('opportunities')
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Evaluation failed', error))
    } finally { setRunning(false) }
  }

  const evaluateAssurance = async () => {
    const customerId = (route.screen === 'opportunities' ? null : route.id)
      || uploadedContracts[0]?.customer_id || allFindings[0]?.customer_id || ''
    if (!customerId) {
      setStatusMessage('No customer is available for manual evaluation.')
      return
    }
    try {
      const result = await apiRequest('/assurance/evaluate', { method: 'POST', body: { customer_id: customerId, period: billingPeriod } })
      setStatusMessage(result?.status === 'needs_review' ? result.message : 'Assurance evaluation recorded.')
      await refreshAll()
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
      const finding = allFindings.find((item) => item.finding_id === findingId)
      const customer = finding?.customer_name || finding?.customer_id || 'counterparty'
      const amount = formatCurrency(finding?.recoverable_difference ?? finding?.monthly_recoverable)
      appendActivity(`You ${action === 'approve' ? 'approved' : 'rejected'} ${customer} · ${amount}`)
      setStatusMessage(result.status !== 'approved' && result.status !== 'rejected'
        ? (result.message || 'Sample mode is read-only; approval was not recorded.')
        : (action === 'approve' ? 'Finding approved.' : 'Finding rejected.'))
      await refreshAll()
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
      await loadRecoveryEvents(findingId)
      await refreshAll()
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
      await refreshAll()
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
      await refreshAll()
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
      await refreshAll()
    } catch (error) {
      console.error(error)
      setStatusMessage('Could not write off the finding.')
    }
  }

  const submitReversal = async (findingId, eventId) => {
    try {
      const reversal = await apiRequest(`/findings/${findingId}/recovery-events/${eventId}/reverse`, {
        method: 'POST',
        body: {
          reversal_amount: Number(reverseFields.amount),
          reversal_reference: reverseFields.reference || null,
          reason: reverseFields.reason || null,
        },
      })
      const feeNote = reversal?.fee_status === 'adjusted'
        ? 'Success fee credited.'
        : reversal?.fee_status === 'adjustment_pending'
          ? `Success fee adjustment pending: ${reversal?.fee_charge?.message || 'review the fee invoice manually.'}`
          : ''
      setStatusMessage(`Realization reversed. ${feeNote}`.trim())
      setReverseForm(null)
      await loadRecoveryEvents(findingId)
      await refreshAll()
    } catch (error) {
      console.error(error)
      setStatusMessage(failureMessage('Reversal failed', error))
    }
  }

  const exportFindings = async () => {
    try {
      const res = await authenticatedFetch('/findings/export')
      if (!res.ok) throw new Error(apiErrorMessage(await res.text()))
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
      if (!res.ok) throw new Error(apiErrorMessage(await res.text()))
      const blob = await res.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a')
      link.href = url; link.download = 'recoup_report.pdf'; link.click(); URL.revokeObjectURL(url)
    } catch (error) {
      console.error(error); setStatusMessage('Could not download the PDF report.')
    }
  }

  const startBillingSetup = async () => {
    try {
      const result = await apiRequest('/billing/setup-session', { method: 'POST', body: {
        accept_terms: Boolean(termsAccepted || billing?.terms_accepted),
        terms_version: '2026-09',
      } })
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
        await refreshAll()
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
      await refreshAll()
    } catch (error) {
      console.error(error); setStatusMessage('Could not charge the success fee.')
    }
  }

  const [trueupSender, setTrueupSender] = useState('')
  const downloadTrueupPdf = async (customerId, customerName) => {
    try {
      const params = trueupSender ? `?sender=${encodeURIComponent(trueupSender)}` : ''
      const res = await authenticatedFetch(`/trueup/${customerId}.pdf${params}`)
      if (!res.ok) throw new Error(apiErrorMessage(await res.text()))
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
      setFindings([]); setAllFindings([]); setFindingsLoaded(false); setUploadedContracts([]); setMetrics(null)
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
      const status = c.status || 'open'
      const pendingApproval = (c.recovery_actions || []).some((action) => action.status === 'pending_approval')
      if (stageFilter === 'Watch') return false
      if (stageFilter === 'Prove' && !(status === 'open' && !c.verified)) return false
      if (stageFilter === 'Act' && status !== 'approved') return false
      if (stageFilter === 'Approve' && !((status === 'open' && c.verified) || pendingApproval)) return false
      if (stageFilter === 'Recover' && !['invoiced', 'disputed'].includes(status)) return false
      if (stageFilter === 'Verify' && status !== 'recovered') return false
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
  }, [commandCenter, filters, stageFilter])

  const selectedCase = useMemo(() => {
    if (route.screen !== 'opportunities' || !route.id) return null
    return (commandCenter?.cases || []).find((c) => c.finding_id === route.id)
      || allFindings.find((f) => f.finding_id === route.id)
      || null
  }, [route.screen, route.id, commandCenter, allFindings])

  const recoveryCases = useMemo(() => allFindings.filter((finding) => ['approved', 'invoiced', 'disputed', 'recovered', 'written_off'].includes(finding.status || 'open')), [allFindings])
  const eventCaseIds = useMemo(() => {
    const ids = new Set(recoveryCases.map((finding) => finding.finding_id).filter(Boolean))
    if (selectedCase?.finding_id) ids.add(selectedCase.finding_id)
    return Array.from(ids)
  }, [recoveryCases, selectedCase])

  useEffect(() => {
    eventCaseIds.forEach((findingId) => {
      if (events[findingId] === undefined && !eventsLoading[findingId]) {
        void loadRecoveryEvents(findingId)
      }
    })
  }, [eventCaseIds, events, eventsLoading, loadRecoveryEvents])

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

  const reviewLabel = isSampleMode ? 'Sample data · read-only' : firebaseUser?.email || 'Authenticated'
  const proofLocked = !isSampleMode && Boolean(billing?.configured) && !billing?.card_on_file
  const lockTitle = 'Add a payment method to unlock'

  const ccCases = useMemo(() => commandCenter?.cases || [], [commandCenter])
  const tenantEmpty = findingsLoaded && ccCases.length === 0
    && allFindings.length === 0 && uploadedContracts.length === 0
  const isWorking = bulkUploading || running
  const workingText = bulkUploading
    ? `Reading ${uploadFileCount || 'your'} file${uploadFileCount === 1 ? '' : 's'}…`
    : 'Evaluating…'

  useEffect(() => {
    if (!isWorking) return undefined
    const reset = window.setTimeout(() => setPulseStep(0), 0)
    const timer = window.setInterval(() => setPulseStep((current) => Math.min(3, current + 1)), 700)
    return () => { window.clearTimeout(reset); window.clearInterval(timer) }
  }, [isWorking])

  const spineSteps = useMemo(() => {
    const allActions = ccCases.flatMap((c) => c.recovery_actions || [])
    const drafted = allActions.filter((a) => ['draft', 'pending_approval'].includes(a.status)).length
    const actionPending = allActions.filter((a) => a.status === 'pending_approval').length
    const openCount = ccCases.filter((c) => (c.status || 'open') === 'open').length
    const pipeline = commandCenter?.pipeline || []
    const verifiedStage = pipeline.find((s) => s.stage === 'Verified')
    const recoveryStage = pipeline.find((s) => s.stage === 'In Recovery')
    const verifiedCount = verifiedStage?.count
      ?? ccCases.filter((c) => c.verified || !['needs_review', 'potential'].includes(c.status || 'open')).length
    const mm = commandCenter?.metrics || {}
    const subs = [
      `${uploadedContracts.length} agreements · ${(assurance?.sources_monitored || []).length} sources monitored`,
      `${ccCases.length} discrepancies`,
      `${verifiedCount} verified`,
      `${ccCases.length} ranked · ${formatCurrency(mm.potential_recoverable_value)}`,
      `${drafted} actions drafted`,
      `${openCount + actionPending} awaiting your approval`,
      `${recoveryStage?.count ?? ccCases.filter((c) => ['invoiced', 'disputed'].includes(c.status)).length} in recovery · ${formatCurrency(mm.in_recovery)}`,
      `${formatCurrency(mm.realized_value)} realized`,
    ]
    return SPINE_TITLES.map((title, i) => ({
      n: `0${i + 1}`, title, sub: subs[i], human: i === 5,
    }))
  }, [ccCases, commandCenter, assurance, uploadedContracts])

  const nextUp = useMemo(() => {
    const allActions = ccCases.flatMap((c) => (c.recovery_actions || [])
      .map((a) => ({ ...a, finding_id: c.finding_id })))
    const review = ccCases.filter((c) => (c.status || 'open') === 'open' && !c.verified)
    const readyApprove = ccCases.filter((c) => (c.status || 'open') === 'open' && c.verified)
    const actionsPending = allActions.filter((a) => a.status === 'pending_approval')
    const approvedNoAction = ccCases.filter((c) => c.status === 'approved' && !(c.recovery_actions || []).length)
    const awaiting = allActions.filter((a) => ['sent', 'awaiting_response'].includes(a.status))
    const inRecovery = ccCases.filter((c) => ['invoiced', 'disputed'].includes(c.status))
    const realized = Number(commandCenter?.metrics?.realized_value || 0)
    const plural = (n) => (n === 1 ? '' : 's')
    if (review.length || reviewQueue.length) {
      const n = review.length || reviewQueue.length
      return {
        text: `${n} item${plural(n)} need${n === 1 ? 's' : ''} your review`,
        label: 'Review',
        onClick: () => review[0] && navigate('opportunities', review[0].finding_id),
      }
    }
    const awaitingApproval = readyApprove.length + actionsPending.length
    if (awaitingApproval) {
      const target = readyApprove[0] || actionsPending[0]
      return {
        text: `${awaitingApproval} case${plural(awaitingApproval)} ready to approve`,
        label: 'Approve',
        onClick: () => navigate('opportunities', target.finding_id),
      }
    }
    if (approvedNoAction.length) {
      return {
        text: `${approvedNoAction.length} approved case${plural(approvedNoAction.length)} ready for a recovery action`,
        label: 'Prepare action',
        onClick: () => navigate('opportunities', approvedNoAction[0].finding_id),
      }
    }
    if (awaiting.length || inRecovery.length) {
      const n = awaiting.length + inRecovery.length
      const target = inRecovery[0] || ccCases.find((c) => c.finding_id === awaiting[0]?.finding_id)
      return {
        text: `${n} recover${n === 1 ? 'y' : 'ies'} awaiting outcome`,
        label: 'Record outcome',
        onClick: () => target && navigate('opportunities', target.finding_id),
      }
    }
    if (realized > 0) return { text: `All caught up — ${formatCurrency(realized)} realized` }
    return { text: 'No discrepancies found yet — add more billing periods or documents' }
  }, [ccCases, commandCenter, reviewQueue, navigate])

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
      {events[finding.finding_id] === undefined || eventsLoading[finding.finding_id] ? (
        <p className="muted-copy">Loading realization events…</p>
      ) : events[finding.finding_id].length === 0 ? <p className="muted-copy">No realization events recorded.</p> : (
        <ul className="upload-history">
          {(events[finding.finding_id] || []).map((event) => (
            <li key={event.recovery_event_id} className="upload-history-item event-row">
              <span>{event.event_type?.replace(/_/g, ' ') || 'event'} · {event.recovery_basis?.replace(/_/g, ' ')} · {formatCurrency(event.event_type === 'reversal' ? event.reversal_amount : event.realized_value)}</span>
              <span className="muted-copy">{event.external_reference || event.reversal_reference || '—'} · {formatDate(event.realized_at || event.created_at)}</span>
              {event.event_type === 'realization' && REVERSIBLE_FINDING_STATUSES.includes(finding.status) && (
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
    const joined = { ...(allFindings.find((f) => f.finding_id === finding.finding_id) || {}), ...finding }
    const evidence = joined.evidence || {}
    const legal = LEGAL_NEXT_ACTIONS[joined.status || 'open'] || []
    const ledger = joined.ledger || null
    const detailFinding = { ...joined, monthly_recoverable: joined.recoverable_difference ?? joined.monthly_recoverable }
    const contract = uploadedContracts.find((c) => c.customer_id === joined.customer_id)
    const fileName = contract?.file_name
    const clauseRef = evidence.clause_ref || joined.clause_ref
    const clauseLabel = CLAUSE_REF_LABELS[clauseRef] || clauseRef
    const clauseField = ({ committed_minimum: 'committed_minimum_monthly', overage: 'overage_rate', discount: 'discounts', escalator: 'annual_escalator_pct' })[clauseRef] || clauseRef
    const clauseMeta = contract?.term_meta?.[clauseField] || {}
    const clauseSection = clauseMeta.section_ref || joined.section_ref
    const clausePage = clauseMeta.page || joined.page
    const sourceFileName = clauseMeta.source_file || fileName
    const verifiedQuote = clauseMeta.verification?.quote_found && clauseMeta.verification?.model_check === 'supports'
    const gated = joined.locked || proofLocked
    return (
      <section className="panel-card opportunity-detail">
        <div className="panel-heading">
          <div><p className="eyebrow">Opportunity detail</p><h2>{joined.financial_right?.title || joined.title || joined.finding_id}</h2><div className="detail-counterparty">{joined.counterparty?.customer_name || joined.customer_name || 'Unknown counterparty'} · {joined.counterparty?.customer_id || joined.customer_id || '—'}</div></div>
          <div className="header-actions">
            {legal.map((action) => <span key={action}>{actionButton(action, detailFinding)}</span>)}
            <button className="btn-secondary" onClick={() => navigate('opportunities')}>Back to queue</button>
          </div>
        </div>
        {renderRealizationForm(detailFinding)}
        <div className="info-group"><p className="eyebrow">Where the money was found</p>
          {gated ? renderGate() : (
            <>
              <p className="story-paragraph">
                {joined.detail || joined.financial_right?.title || joined.title || joined.finding_id}{' '}
                Recoup read this in {sourceFileName ? `“${sourceFileName}”` : 'the agreement'}{clauseSection ? `, ${clauseSection}` : clauseLabel ? `, ${clauseLabel}` : ''}{clausePage ? `, page ${clausePage}` : ''}, and checked it against the {joined.period || '—'} billing period.
              </p>
              {joined.assumption && <p className="muted-copy">Assumption: {joined.assumption}</p>}
            </>
          )}
        </div>
        <div className="info-group discrepancy-hero"><div className="info-label">Financial discrepancy</div>
          <div className="discrepancy-amount money">{formatCurrency(joined.recoverable_difference ?? joined.monthly_recoverable)}</div>
          <div className="discrepancy-cols">
            <div><span className="metric-label">Expected</span><div className="money">{formatCurrency(joined.expected_value)}</div></div>
            <div><span className="metric-label">Actual</span><div className="money">{formatCurrency(joined.actual_value)}</div></div>
            <div><span className="metric-label">Difference</span><div className="money">{formatCurrency(joined.recoverable_difference ?? joined.monthly_recoverable)}</div></div>
          </div>
          <div className="muted-copy">Confidence {Math.round(Number(joined.confidence || joined.confidence_score || 0) * 100)}% · period {joined.period || '—'} · {joined.currency || 'USD'}</div>
        </div>
        <div className="info-group"><div className="info-label">Proof</div>
          {gated ? renderGate() : (
            <div className="detail-grid detail-grid-two">
              <div><div className="info-label">Agreement clause</div>
                <div className="provenance-box">{clauseSection || clauseLabel || '—'}{clausePage ? ` · p. ${clausePage}` : ''}<br />{evidence.clause_text || joined.clause_text || 'No clause text on file.'}{verifiedQuote ? <span className="file-chip">Verified quote</span> : <span className="file-chip muted-chip">Quote not found</span>}</div>
              </div>
              <div><div className="info-label">Calculation</div>
                <div className="detail-copy math-copy">{evidence.math || joined.math || '—'}</div>
                <div className="muted-copy">{evidence.provenance || joined.provenance || '—'}</div>
              </div>
            </div>
          )}
          {(joined.evidence_refs || []).map((ref) => <span key={ref} className="file-chip">{ref}</span>)}
        </div>
        <div className="info-group"><div className="info-label">Status and history</div>
          <span className={`status-pill status-${joined.status}`}>{String(joined.status || 'open').replace(/_/g, ' ')}</span>
          <ul className="upload-history">
            {(joined.recovery_history || []).map((h, i) => <li key={i}><span>{h.event}{h.decision ? ` · ${h.decision}` : ''}</span><span className="muted-copy">{h.ts}</span></li>)}
          </ul>
        </div>
        {['approved', 'invoiced', 'disputed'].includes(joined.status) && (
          <RecoveryActions finding={detailFinding} apiRequest={apiRequest} onChanged={refreshAll} />
        )}
        {ledger && (
          <div className="info-group"><div className="info-label">Realization ledger</div>
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

  const renderGate = () => (
    <div className="pay-gate">
      <LockKeyhole size={16} />
      <div>
        <strong>{lockTitle}</strong>
        <p className="muted-copy">Add a payment method to unlock clause proof, audit reports and true-up packs. You are only charged 20% of dollars actually recovered — nothing upfront. By adding a card you agree to the <a href="/terms.html" target="_blank" rel="noreferrer">Terms of Service</a>.</p>
        {!billing?.terms_accepted && <label className="terms-check"><input type="checkbox" checked={termsAccepted} onChange={(event) => setTermsAccepted(event.target.checked)} /> I agree to the Terms of Service (v2026-09)</label>}
        <button className="btn-primary" disabled={!billing?.terms_accepted && !termsAccepted} onClick={startBillingSetup}>Add payment method</button>
      </div>
    </div>
  )

  const caseRowAction = (c) => {
    const status = c.status || 'open'
    if (status === 'open') {
      return c.verified
        ? <button className="btn-primary row-action" onClick={(e) => { e.stopPropagation(); navigate('opportunities', c.finding_id) }}>Approve</button>
        : <button className="btn-primary row-action" onClick={(e) => { e.stopPropagation(); navigate('opportunities', c.finding_id) }}>Review</button>
    }
    const labels = {
      approved: 'Prepare action', invoiced: 'Record outcome',
      disputed: 'Record outcome', recovered: 'Record outcome',
    }
    const label = labels[status]
    if (!label) return null
    return <button className="btn-primary row-action" onClick={(e) => { e.stopPropagation(); navigate('opportunities', c.finding_id) }}>{label}</button>
  }

  const renderSpine = () => (
    <section className="spine-block" aria-label="How Recoup works">
      <div className="spine-head">
        <ol className="spine">
          {spineSteps.map((step, i) => {
            const selected = stageFilter === step.title
            const onStep = () => {
              if (step.title === 'Watch') {
                navigate('agreements')
                return
              }
              setStageFilter((current) => current === step.title ? null : step.title)
            }
            return (
              <li key={step.title} className={`spine-step ${isWorking && i === pulseStep ? 'working' : ''} ${selected ? 'selected' : ''}`}>
                <button type="button" onClick={onStep} aria-pressed={selected} title={SPINE_DESCRIPTIONS[i]}>
                  <span className="spine-num">{step.n}</span>
                  <span className="spine-title">{step.title}<span className="spine-help" aria-hidden="true">?</span>{step.human && <span className="human-badge">human</span>}</span>
                  <span className="spine-sub">{step.sub}</span>
                </button>
              </li>
            )
          })}
        </ol>
      </div>
      <p className="spine-caption">Recoup's operating loop — every case moves left to right; nothing leaves your team without approval at step 06.</p>
      {isWorking && <p className="spine-status" role="status">{workingText}</p>}
    </section>
  )

  const renderActivity = () => {
    const lastEvaluated = relativeTime(assurance?.last_evaluated_at)
    return (
      <section className="activity panel-card" aria-label="Activity">
        <div className="activity-head">
          <h2><span className="live-dot" />Activity</h2>
          <span className="activity-status">{isWorking ? <><span className="pulse-dot" />Working…</> : lastEvaluated ? `Monitoring · last evaluated ${lastEvaluated}` : 'Monitoring'}</span>
        </div>
        {activity.length ? <ul className="activity-list">{activity.slice(-8).reverse().map((entry) => <li key={entry.id}><time>{new Date(entry.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time><span>{entry.message}</span></li>)}</ul> : <p className="muted-copy">Monitoring for new agreement and billing activity.</p>}
      </section>
    )
  }

  const renderQueue = () => {
    const set = (key) => (e) => setFilters((f) => ({ ...f, [key]: e.target.value }))
    return (
      <section className="panel-card queue-card">
        <div className="panel-heading">
          <div><p className="eyebrow">Work queue</p><h2>Ranked cases · {stageFilter || 'All'}</h2></div>
          <div className="review-actions">
            {stageFilter && <button className="link-button" onClick={() => setStageFilter(null)}>Clear</button>}
            <span className="hint-pill">{cases.length} cases</span>
            <button className="btn-secondary" onClick={() => setShowFilters((v) => !v)} aria-expanded={showFilters}>
              <SlidersHorizontal size={14} /> Filter
            </button>
          </div>
        </div>
        {showFilters && (
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
        )}
        <div className="table-scroll"><table className="cc-table"><thead><tr><th>Counterparty</th><th>Financial right</th><th>Period</th><th>Recoverable</th><th>Confidence</th><th>Stage</th><th>Next action</th></tr></thead><tbody>
          {cases.map((c) => <tr key={c.finding_id} onClick={() => navigate('opportunities', c.finding_id)} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') navigate('opportunities', c.finding_id) }}>
            <td title={c.counterparty?.customer_name} className="truncate">{c.counterparty?.customer_name}</td>
            <td>{c.financial_right?.title || c.financial_right?.type}</td>
            <td>{c.period || '—'}</td>
            <td className="money">{formatCurrency(c.recoverable_difference)}</td>
            <td>{Math.round(Number(c.confidence || 0) * 100)}%</td>
            <td>{caseStageLabel(c)}</td>
            <td>{caseRowAction(c)}</td>
          </tr>)}
          {cases.length === 0 && <tr><td colSpan="7" className="muted-copy">No cases match the current filters.</td></tr>}
        </tbody></table></div>
        {reviewQueue.length > 0 && (
          <div className="panel-section"><div className="info-label">Needs review</div>
            <ul className="upload-history">{reviewQueue.map((item, i) => <li key={i} className="upload-history-item"><span>{item.customer_name || item.customer_id || 'Record'} · {item.term || item.reason || 'Needs review'}</span><span className="muted-copy">{[item.reason, item.suggested_action, item.next_step].filter((value, index, values) => value && values.indexOf(value) === index).join(' · ')}</span></li>)}</ul>
          </div>
        )}
      </section>
    )
  }

  const renderEmptyState = () => (
    <section
      className={`panel-card empty-hero ${dragging ? 'dragging' : ''}`}
      onDragOver={handleEmptyDragOver}
      onDragLeave={handleEmptyDragLeave}
      onDrop={handleEmptyDrop}
    >
      <Upload size={28} />
      <h2>Drop your agreements and billing data</h2>
      <p className="muted-copy">Contracts (PDF, DOCX, scans), invoices and usage exports (CSV), or a ZIP of everything. Recoup reads them, matches customers, and finds what you’re owed.</p>
      {isSampleMode ? (
        <button className="btn-primary empty-cta" onClick={runEvaluation} disabled={running}>
          {running ? 'Running…' : 'Run the sample book'}
        </button>
      ) : (
        <label className="btn-primary empty-cta">
          {bulkUploading ? 'Uploading…' : 'Choose files'}
          <input type="file" multiple hidden disabled={bulkUploading} accept=".pdf,.docx,.txt,.md,.csv,.zip,.png,.jpg,.jpeg" onChange={handleBulkUpload} />
        </label>
      )}
      <p className="muted-copy">Need an export template? {TEMPLATE_LINKS}</p>
    </section>
  )

  const renderWorkspace = () => {
    if (route.screen === 'opportunities' && route.id) {
      if (selectedCase) return renderOpportunityDetail(selectedCase)
      if (findingsLoaded) {
        return (
          <section className="panel-card opportunity-detail">
            <div className="panel-heading"><div><p className="eyebrow">Case detail</p><h2>Case not found</h2></div><button className="btn-secondary" onClick={() => navigate('workspace')}>Back to workspace</button></div>
            <p className="muted-copy">No case exists for this link in the current account.</p>
          </section>
        )
      }
      return <section className="panel-card"><p className="muted-copy">Loading selected case…</p></section>
    }
    if (tenantEmpty && !isWorking) return renderEmptyState()
    return (
      <>
        {renderSpine()}
        {renderActivity()}
        <div className="nextup">
          <span>{nextUp.text}</span>
          {nextUp.label && <button className="btn-primary" onClick={nextUp.onClick}>{nextUp.label}</button>}
        </div>
        {renderQueue()}
        <section className="perf-collapse">
          <button className="perf-toggle" onClick={togglePerformance} aria-expanded={showPerformance}>
            <span>Performance &amp; assurance</span><ChevronDown size={15} className={showPerformance ? 'chev open' : 'chev'} />
          </button>
          {showPerformance && <>{renderOverview()}{renderRecoveries()}</>}
        </section>
      </>
    )
  }

  const renderDrawer = () => {
    const bodies = {
      agreements: renderAgreements,
      integrations: renderIntegrations,
      settings: renderSettings,
      admin: renderAdmin,
    }
    const Body = bodies[drawerScreen]
    const title = navItems.find((item) => item.id === drawerScreen)?.title || ''
    if (!Body) return null
    return (
      <div className="drawer-backdrop" onClick={() => navigate('workspace')}>
        <aside className="drawer" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
          <div className="drawer-head">
            <button className="link-quiet" onClick={() => navigate('workspace')}>← Back to workspace</button>
            <button className="icon-btn" onClick={() => navigate('workspace')} aria-label="Close"><X size={16} /></button>
          </div>
          <div className="drawer-body"><Body /></div>
        </aside>
      </div>
    )
  }

  const renderAgreements = () => (
    <section className="panel-card">
      <div className="panel-heading"><div><p className="eyebrow">Agreements</p><h2>Agreements and uploads</h2></div><span className="hint-pill">Recoup reads them — nothing to key in</span></div>
      <div className="upload-grid">
        <div
          className={`dropzone ${dragging ? 'dragging' : ''}`}
          onDragOver={handleEmptyDragOver}
          onDragLeave={handleEmptyDragLeave}
          onDrop={handleEmptyDrop}
        >
          <Upload size={22} /><div><strong>{bulkUploading ? 'Uploading…' : 'Drop files here or click to browse'}</strong><p>Drop contracts (PDF/DOCX/scans), billing + usage CSVs, or a ZIP of everything.</p></div>
          <input type="file" multiple disabled={bulkUploading} accept=".pdf,.docx,.txt,.md,.csv,.zip,.png,.jpg,.jpeg" onChange={handleBulkUpload} />
          {selectedFileName && <span className="file-chip">{selectedFileName}</span>}
          {bulkResult?.files?.length > 0 && <ul className="upload-history">{bulkResult.files.map((f, i) => <li key={`${f.name}-${i}`} className="upload-history-item"><span className="upload-file-name">{f.name}</span><span className="file-chip">{f.kind} · {f.status}{f.message ? ` — ${f.message}` : ''}</span></li>)}</ul>}
          <p className="muted-copy">Export templates: <a href={`${API_BASE}/templates/quickbooks/invoices.csv`} download>QuickBooks</a>{' · '}<a href={`${API_BASE}/templates/xero/invoices.csv`} download>Xero</a>{' · '}<a href={`${API_BASE}/templates/stripe/invoices.csv`} download>Stripe</a></p>
        </div>
      </div>
      <div className="panel-section"><div className="info-label">Agreement list</div>
        {uploadedContracts.length === 0 ? <p className="muted-copy">No agreements uploaded yet.</p> : uploadedContracts.map((contract) => {
          const termMeta = contract.term_meta || {}
          const termEntries = Object.entries(termMeta).filter(([key, meta]) => key !== 'discounts' && TERM_LABELS[key] && meta && typeof meta === 'object' && typeof meta.confidence === 'number')
          const needsReview = Object.values(termMeta).some((meta) => meta && typeof meta.confidence === 'number' && meta.confidence < 0.85) || !contract.term_start || !contract.term_end
          const provenances = [...new Set(termEntries.map(([, meta]) => meta.provenance).filter(Boolean))]
          return (
          <div key={contract.customer_id} className="agreement-card">
            <div className="panel-heading"><div><strong title={contract.customer_name} className="truncate">{contract.customer_name}</strong><span className="muted-copy"> {contract.customer_id}</span></div><span className={`status-pill ${contract.confirmed ? 'status-approved' : needsReview ? 'status-review' : 'status-read'}`}>{contract.confirmed ? `Confirmed by ${shortActor(contract.confirmed_by)}` : needsReview ? 'Needs your review' : 'Read by Recoup'}</span></div>
            {(contract.documents || []).length > 0 && <div className="term-chips doc-chips">{contract.documents.map((doc, i) => <span key={`${doc.file_name}-${i}`} className="term-chip">{docRoleLabel(doc)} · {doc.file_name}{doc.effective_date ? ` · eff. ${doc.effective_date}` : ''}</span>)}</div>}
            <div className="muted-copy">Term {contract.term_start || '—'} → {contract.term_end || '—'} · minimum {formatCurrency(contract.committed_minimum_monthly)} · overage {formatRate(contract.overage_rate)} · escalator {contract.annual_escalator_pct ?? '—'}% · discounts {(contract.discounts || []).length}</div>
            {termEntries.length > 0 && <div className="term-chips">{termEntries.map(([key, meta]) => { const verified = meta.verification?.quote_found && meta.verification?.model_check === 'supports'; return <span key={key} className={`term-chip ${meta.confidence < 0.85 ? 'review' : ''}`} title={meta.provenance || undefined}>{TERM_LABELS[key]} · {Math.round(meta.confidence * 100)}%{meta.section_ref ? ` · ${meta.section_ref}` : ''}{meta.page ? ` · p. ${meta.page}` : ''}{termHistoryNote(contract, key) ? ` · ${termHistoryNote(contract, key)}` : ''}{verified ? <span className="quote-chip">Verified quote</span> : <span className="quote-chip muted-chip">Quote not found</span>}</span>})}</div>}
            {provenances.length === 1 && <div className="muted-copy source-line">Source: {provenances[0]}</div>}
            {needsReview && !contract.confirmed && <><button className="btn-primary" disabled={confirmingCustomer === contract.customer_id} onClick={() => confirmContract(contract.customer_id)}>{confirmingCustomer === contract.customer_id ? 'Confirming…' : 'Confirm as read'}</button><span className="muted-copy review-note">Recoup wasn't sure about the amber terms — confirm or re-upload a clearer copy.</span></>}
            <details><summary>Financial rights</summary>
              {rights[contract.customer_id]?.length ? <ul className="upload-history">{rights[contract.customer_id].map((right) => <li key={right.right_id || right.candidate_id}><span>{right.right_type || right.type}</span><span className="muted-copy">{right.status || right.candidate_status || '—'}</span></li>)}</ul> : <p className="muted-copy">{rights[contract.customer_id] === undefined ? 'Loading rights…' : 'No additional rights discovered'}</p>}
            </details>
          </div>
        )})}
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
        <div className="table-scroll"><table className="cc-table"><thead><tr><th>Counterparty</th><th>Status</th><th>Net realized</th><th>Outstanding</th><th>Action</th></tr></thead><tbody>
          {(commandCenter?.cases || []).filter((c) => ['approved', 'invoiced', 'disputed', 'recovered', 'written_off'].includes(c.status)).map((c) => <tr key={c.finding_id} onClick={() => navigate('opportunities', c.finding_id)}><td>{c.counterparty?.customer_name || c.customer_name || '—'}{c.counterparty?.customer_id || c.customer_id ? ` · ${c.counterparty?.customer_id || c.customer_id}` : ''}</td><td>{c.status}</td><td className="money">{formatCurrency(c.ledger?.net_realized)}</td><td className="money">{formatCurrency(c.ledger?.outstanding_value)}</td><td>{c.recommended_next_step}</td></tr>)}
          {(commandCenter?.cases || []).filter((c) => ['approved', 'invoiced', 'disputed', 'recovered', 'written_off'].includes(c.status)).length === 0 && <tr><td colSpan="5" className="muted-copy">No recovery cases yet — approve an opportunity first.</td></tr>}
        </tbody></table></div>
      </div>
      <div className="panel-section"><div className="info-label">Record realized value</div>
        {recoveryCases.length === 0 ? <p className="muted-copy">No recovery cases are open.</p> : recoveryCases.map((finding) => <div key={finding.finding_id} className="agreement-card"><strong>{finding.customer_name || finding.customer_id}</strong><div className="muted-copy">{finding.status} · {finding.monthly_recoverable != null ? formatCurrency(finding.monthly_recoverable) : '—'}</div>{ACTIONABLE_REALIZATION_STATUSES.includes(finding.status) && (
            <>
              <button className="btn-primary" onClick={() => openRealizationForm(finding)}>Record realized value</button>
              {renderRealizationForm(finding)}
            </>
          )}{renderEvents(finding)}</div>)}
      </div>
      <div className="panel-section"><div className="info-label">True-up letters</div>
        <label className="styled-field">Sender<input value={trueupSender} onChange={(e) => setTrueupSender(e.target.value)} /></label>
        {trueupCustomers.length === 0 ? <p className="muted-copy">No true-up customers.</p> : trueupCustomers.map((customer) => <div key={customer.customer_id} className="agreement-card"><strong>{customer.customer_name}</strong><div className="muted-copy">{customer.count} finding(s)</div><button className="btn-secondary" disabled={proofLocked} title={proofLocked ? lockTitle : ''} onClick={() => downloadTrueupPdf(customer.customer_id, customer.customer_name)}>Download letter + schedule PDF</button></div>)}
      </div>
      {proofLocked && renderGate()}
      <div className="review-actions">
        <button className="btn-secondary" disabled={proofLocked} title={proofLocked ? lockTitle : ''} onClick={exportFindings}><Download size={15} /> Export findings CSV</button>
        <button className="btn-secondary" disabled={proofLocked} title={proofLocked ? lockTitle : ''} onClick={openAuditReport}><FileText size={15} /> Audit report</button>
        <button className="btn-secondary" disabled={proofLocked} title={proofLocked ? lockTitle : ''} onClick={downloadReportPdf}><Download size={15} /> PDF report</button>
        <button
          className="btn-primary"
          onClick={chargeSuccessFee}
          disabled={billing?.card_on_file === false}
          title={billing?.card_on_file === false ? 'Add a payment method in Settings before billing the success fee.' : undefined}
        >Bill success fee this month</button>
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

  const renderAdmin = () => (
    <section className="panel-card">
      <div className="panel-heading">
        <div><p className="eyebrow">Platform Admin</p><h2>Operator console</h2></div>
        <button className="btn-secondary" onClick={loadAdmin} disabled={adminLoading}>{adminLoading ? 'Refreshing…' : 'Refresh'}</button>
      </div>
      <div className="metric-grid admin-grid">
        <div className="metric-card"><span className="metric-label">Tenants</span><strong className="metric-value">{adminTenants.length}</strong></div>
        <div className="metric-card"><span className="metric-label">Signups</span>
          <label className="admin-toggle"><input type="checkbox" checked={Boolean(adminSettings?.signup_enabled)} disabled={!adminSettings || adminSaving} onChange={(event) => saveAdminSettings({ signupEnabled: event.target.checked })} /> Pilot signups enabled</label>
        </div>
      </div>
      <div className="panel-section">
        <div className="info-label">Invited emails</div>
        <textarea className="admin-invites" rows={5} value={adminInviteText} onChange={(event) => setAdminInviteText(event.target.value)} placeholder={'one email per line'} />
        <div className="panel-footer"><button className="btn-primary" onClick={() => saveAdminSettings()} disabled={!adminSettings || adminSaving}>{adminSaving ? 'Saving…' : 'Save invitations'}</button></div>
      </div>
      <div className="panel-section">
        <div className="info-label">Tenants</div>
        <div className="table-scroll"><table className="cc-table"><thead><tr><th>Email</th><th>Account</th><th>First seen</th><th>Last seen</th><th>Demo</th><th>Open</th><th>Approved</th><th>Recovered</th><th>Potential</th><th>Realized</th><th>Fee billed</th><th>Card</th><th>Evaluated</th></tr></thead><tbody>
          {adminTenants.map((tenant) => {
            const counts = tenant.findings_by_status || {}
            return <tr key={tenant.account_id} onClick={() => loadAdminTenant(tenant.account_id)} tabIndex={0} onKeyDown={(event) => { if (event.key === 'Enter') loadAdminTenant(tenant.account_id) }}>
              <td className="truncate" title={tenant.email}>{tenant.email || '—'}</td>
              <td><button className="link-button" title={tenant.account_id} onClick={(event) => { event.stopPropagation(); navigator.clipboard?.writeText(tenant.account_id || '') }}>{(tenant.account_id || '').slice(0, 10)}…</button></td>
              <td>{formatDate(tenant.first_seen)}</td><td>{formatDate(tenant.last_seen)}</td>
              <td>{tenant.demo ? <span className="status-pill status-approved">demo</span> : '—'}</td>
              <td>{counts.open || 0}</td><td>{counts.approved || 0}</td><td>{counts.recovered || 0}</td>
              <td className="money">{formatCurrency(tenant.potential_value)}</td><td className="money">{formatCurrency(tenant.realized_value)}</td><td className="money">{formatCurrency(tenant.fee_billed)}</td>
              <td>{tenant.billing?.card_on_file ? 'Yes' : 'No'}</td><td>{tenant.error ? <span className="muted-copy">{tenant.error}</span> : formatDate(tenant.assurance_last_evaluated)}</td>
            </tr>
          })}
          {adminTenants.length === 0 && <tr><td colSpan="13" className="muted-copy">No tenants have signed in yet.</td></tr>}
        </tbody></table></div>
      </div>
      {adminSelected && (
        <div className="panel-section admin-detail">
          <div className="panel-heading"><div><p className="eyebrow">Tenant detail</p><h3>{adminSelected.email || adminSelected.account_id}</h3><p className="muted-copy">{adminSelected.account_id}</p></div><button className="btn-secondary" onClick={() => setAdminSelected(null)}>Close</button></div>
          {adminSelected.error && <p className="status-message error">{adminSelected.error}</p>}
          <div className="detail-grid detail-grid-two">
            <div className="info-group"><div className="info-label">Recent tenant audit</div>
              {(adminSelected.audit_log || []).length === 0 ? <p className="muted-copy">No tenant audit entries.</p> : <ul className="upload-history">{adminSelected.audit_log.map((entry, index) => <li key={index} className="upload-history-item"><span>{entry.event || 'event'}{entry.finding_id ? ` · ${entry.finding_id}` : ''}</span><span className="muted-copy">{formatDate(entry.ts)}</span></li>)}</ul>}
            </div>
            <div className="info-group"><div className="info-label">Recent assurance events</div>
              {(adminSelected.assurance_events || []).length === 0 ? <p className="muted-copy">No assurance events.</p> : <ul className="upload-history">{adminSelected.assurance_events.map((entry, index) => <li key={entry.event_id || index} className="upload-history-item"><span>{entry.trigger || entry.event_type || 'event'}{entry.customer_id ? ` · ${entry.customer_id}` : ''}</span><span className="muted-copy">{formatDate(entry.received_at || entry.ts)}</span></li>)}</ul>}
            </div>
          </div>
          <div className="panel-section">
            <div className="info-label">Fee events needing attention</div>
            {(adminSelected.fee_events || []).length === 0 ? <p className="muted-copy">No unpaid or failed fee events.</p> : <ul className="upload-history">{adminSelected.fee_events.map((event) => <li key={event.recovery_event_id} className="upload-history-item"><span>{event.recovery_event_id} · {event.fee_status || 'unbilled'}</span>{['payment_failed', 'error', 'pending', 'unbilled'].includes(event.fee_status) && <button className="btn-secondary" onClick={() => retryAdminFee(event)}>Retry</button>}</li>)}</ul>}
          </div>
          <div className="panel-section review-actions">
            <button className="btn-secondary" onClick={() => setAdminDemo(adminSelected, !adminSelected.demo)}>{adminSelected.demo ? 'Remove demo flag' : 'Mark as demo'}</button>
          </div>
          <div className="panel-section">
            <div className="info-label">Reset demo tenant</div>
            <p className="muted-copy">Type the full account id to confirm. Only demo tenants can be reset from this console.</p>
            <div className="review-actions">
              <input className="admin-confirm" value={adminResetConfirm} onChange={(event) => setAdminResetConfirm(event.target.value)} placeholder={adminSelected.account_id} disabled={!adminSelected.demo} />
              <button className="btn-danger" disabled={!adminSelected.demo || adminResetConfirm !== adminSelected.account_id || adminSaving} onClick={resetAdminTenant}>{adminSaving ? 'Resetting…' : 'Reset demo tenant'}</button>
            </div>
          </div>
        </div>
      )}
      <div className="panel-section">
        <div className="info-label">Admin audit</div>
        {adminAudit.length === 0 ? <p className="muted-copy">No admin actions recorded.</p> : <ul className="upload-history">{adminAudit.map((entry, index) => <li key={index} className="upload-history-item"><span>{entry.action}{entry.target_account_id ? ` · ${entry.target_account_id}` : ''}</span><span className="muted-copy">{entry.operator_email || '—'} · {formatDate(entry.at)}</span></li>)}</ul>}
      </div>
    </section>
  )

  const renderSettings = () => (
    <section className="panel-card">
      <div className="panel-heading"><div><p className="eyebrow">Settings</p><h2>Settings</h2></div></div>
      <div className="panel-section"><div className="info-label">Billing</div>
        <p className="muted-copy">Card on file: {billing?.card_on_file ? `${billing.card_brand || ''} •••• ${billing.card_last4 || ''}` : 'None'}</p>
        {billing?.card_status === 'failed' && <p className="status-message error">Your last fee payment failed — update your card</p>}
        <p className="muted-copy">Recoup charges 20% of net realized recovered value. <a href="/terms.html" target="_blank" rel="noreferrer">Terms of Service</a></p>
        {billing?.terms_accepted && <p className="muted-copy">Terms accepted {formatDate(billing.terms_accepted.accepted_at)}</p>}
        {!billing?.terms_accepted && <label className="terms-check"><input type="checkbox" checked={termsAccepted} onChange={(event) => setTermsAccepted(event.target.checked)} /> I agree to the Terms of Service (v2026-09)</label>}
        <button className="btn-primary" disabled={!billing?.terms_accepted && !termsAccepted} onClick={startBillingSetup}>Add payment method</button>
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
        <header className="auth-top">
          <span className="wordmark"><span className="glyph">R</span>Recoup</span>
          <span className="muted-copy">An Odingard Security application</span>
        </header>
        <div className="panel-card auth-card">
          <h1 className="auth-headline">Find the revenue you’re already owed.</h1>
          <p className="auth-sub">Sign in to upload your agreements and billing data and let Recoup find what was missed.</p>
          <button className="btn-primary auth-button" onClick={handleGoogleLogin}>Continue with Google</button>
          <button className="link-quiet auth-sample" onClick={sampleModeLogin}>Explore with sample data →</button>
          <p className="auth-fine">No upfront fee · 20% of realized recovered value · Every recovery action is approved by your team.</p>
        </div>
        <a className="auth-back" href="/">← Back to recoup.odingard.com</a>
      </div>
    )
  }

  return (
    <div className="app-container recoup-shell">
      <header className="topbar">
        <button className="wordmark link-quiet" onClick={() => navigate('workspace')} aria-label="Recoup workspace">
          <span className="glyph">R</span><span>Recoup</span>
        </button>
        <nav className="topbar-links" aria-label="Sections">
          <span className="period-picker"><label htmlFor="billing-period">Period</label><input id="billing-period" value={billingPeriod} onChange={(event) => setBillingPeriod(event.target.value)} placeholder="YYYY-MM" /></span>
          {navItems.map((item) => (
            <button key={item.id} type="button" className={`link-quiet topbar-link ${drawerScreen === item.id ? 'active' : ''}`} onClick={() => navigate(item.id)}>{item.title}</button>
          ))}
          <label className="topbar-link topbar-action" aria-label="Add documents">
            <span className="topbar-icon" aria-hidden="true">＋</span><span className="topbar-link-label">Add documents</span>
            <input type="file" multiple hidden disabled={bulkUploading} accept=".pdf,.docx,.txt,.md,.csv,.zip,.png,.jpg,.jpeg" onChange={handleBulkUpload} />
          </label>
          <button type="button" className="link-quiet topbar-link topbar-action" onClick={runEvaluation} disabled={running} aria-label="Run evaluation">
            <span className="topbar-icon" aria-hidden="true">{running ? <RefreshCw className="spin" size={13} /> : '↻'}</span><span className="topbar-link-label">{running ? 'Evaluating…' : 'Run evaluation'}</span>
          </button>
          <span className={`session-pill ${isSampleMode ? 'sample' : 'auth'}`}>{reviewLabel}</span>
          <button className="icon-btn" onClick={handleLogout} title="Sign out" aria-label="Sign out"><LogOut size={16} /></button>
        </nav>
      </header>
      <main className="workspace">
        {renderStatus()}
        {renderWorkspace()}
      </main>
      {renderDrawer()}
    </div>
  )
}

export default App
