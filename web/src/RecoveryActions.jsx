import { useEffect, useState } from 'react'

const ACTION_TYPES = [
  'corrective_invoice', 'credit_request', 'rebate_request',
  'reimbursement_request', 'contractual_notice', 'counterparty_inquiry',
  'manual_recovery_action',
]

const CHANNELS = [
  { id: 'document', label: 'Prepare document' },
  { id: 'email_draft', label: 'Prepare email draft' },
  { id: 'manual', label: 'Record manual action' },
]

function formatCurrency(value) {
  return `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

function pretty(v) {
  return String(v || '').replace(/_/g, ' ')
}

export function RecoveryActionSelect({ findingId, apiRequest, value, onChange }) {
  const [actions, setActions] = useState([])
  useEffect(() => {
    let on = true
    if (!findingId) return undefined
    apiRequest(`/findings/${findingId}/recovery-actions`)
      .then((list) => { if (on) setActions(list) })
      .catch(() => {})
    return () => { on = false }
  }, [findingId]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!actions.length) return null
  return (
    <label>
      Linked recovery action (optional)
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">— none —</option>
        {actions.map((a) => (
          <option key={a.recovery_action_id} value={a.recovery_action_id}>
            {pretty(a.action_type)} · {pretty(a.status)}
          </option>
        ))}
      </select>
    </label>
  )
}

export default function RecoveryActions({ finding, apiRequest, onChanged }) {
  const [actions, setActions] = useState([])
  const [actionType, setActionType] = useState(ACTION_TYPES[0])
  const [draftMode, setDraftMode] = useState('template')
  const [draftText, setDraftText] = useState({})
  const [channel, setChannel] = useState('document')
  const [externalRef, setExternalRef] = useState('')
  const [outcomeResult, setOutcomeResult] = useState('resolved')
  const [outcomeNote, setOutcomeNote] = useState('')
  const [outcomeRef, setOutcomeRef] = useState('')
  const [rejectNote, setRejectNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const findingId = finding?.finding_id

  const load = async () => {
    if (!findingId) return
    try {
      setActions(await apiRequest(`/findings/${findingId}/recovery-actions`))
    } catch (e) {
      console.error(e)
    }
  }

  useEffect(() => { void load() }, [findingId]) // eslint-disable-line react-hooks/exhaustive-deps, react-hooks/set-state-in-effect

  const run = async (fn, ok) => {
    setBusy(true)
    setMessage('')
    try {
      await fn()
      await load()
      if (onChanged) onChanged()
      if (ok) setMessage(ok)
    } catch (e) {
      setMessage(e.message || 'Failed')
    } finally {
      setBusy(false)
    }
  }

  const post = (id, path, body) => apiRequest(`/recovery-actions/${id}${path}`, {
    method: 'POST',
    body: JSON.stringify(body || {}),
  })

  const actionable = ['approved', 'invoiced', 'disputed'].includes(finding?.status)

  return (
    <div className="recovery-actions">
      <h4>Recovery actions</h4>
      {message && <p className="muted-note">{message}</p>}

      {actions.map((a) => (
        <div key={a.recovery_action_id} className="action-card glass-panel">
          <div className="action-head">
            <strong>{pretty(a.action_type)}</strong>
            <span className={`status-pill status-${a.status}`}>{pretty(a.status)}</span>
            <span className="amount-badge">{formatCurrency(a.requested_value)}</span>
            {a.channel && <span className="muted-note">via {pretty(a.channel)}</span>}
          </div>

          <label className="field-label">Draft communication</label>
          <textarea
            rows={6}
            disabled={!['draft', 'pending_approval'].includes(a.status)}
            value={draftText[a.recovery_action_id] ?? a.draft_communication}
            onChange={(e) => setDraftText({
              ...draftText, [a.recovery_action_id]: e.target.value })}
          />
          {['draft', 'pending_approval'].includes(a.status) && (
            <button
              className="ghost-btn"
              disabled={busy}
              onClick={() => run(async () => {
                await post(a.recovery_action_id, '/draft', {
                  draft_communication:
                    draftText[a.recovery_action_id] ?? a.draft_communication })
              }, 'Draft saved')}
            >Save draft</button>
          )}

          <div className="action-buttons">
            {a.status === 'draft' && (
              <>
                <button disabled={busy} className="primary-btn"
                  onClick={() => run(() => post(a.recovery_action_id, '/submit'),
                                     'Submitted for approval')}>
                  Submit for approval</button>
                <input placeholder="Rejection note" value={rejectNote}
                  onChange={(e) => setRejectNote(e.target.value)} />
                <button disabled={busy} className="ghost-btn"
                  onClick={() => run(() => post(a.recovery_action_id, '/reject',
                                                { note: rejectNote }),
                                     'Rejected')}>Reject</button>
              </>
            )}
            {a.status === 'pending_approval' && (
              <>
                <button disabled={busy} className="primary-btn"
                  onClick={() => {
                    if (window.confirm('Approve this recovery action?')) {
                      void run(() => post(a.recovery_action_id, '/approve'),
                               'Approved')
                    }
                  }}>Approve</button>
                <input placeholder="Rejection note" value={rejectNote}
                  onChange={(e) => setRejectNote(e.target.value)} />
                <button disabled={busy} className="ghost-btn"
                  onClick={() => run(() => post(a.recovery_action_id, '/reject',
                                                { note: rejectNote }),
                                     'Rejected')}>Reject</button>
              </>
            )}
            {a.status === 'approved' && (
              <>
                <select value={channel} onChange={(e) => setChannel(e.target.value)}>
                  {CHANNELS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
                {channel === 'manual' && (
                  <input placeholder="External reference" value={externalRef}
                    onChange={(e) => setExternalRef(e.target.value)} />
                )}
                <button disabled={busy} className="primary-btn"
                  onClick={() => {
                    if (window.confirm('Execute the approved action?')) {
                      void run(() => post(a.recovery_action_id, '/execute', {
                        channel,
                        external_reference: channel === 'manual' ? externalRef : null }),
                               'Executed')
                    }
                  }}>Execute</button>
              </>
            )}
            {['sent', 'awaiting_response', 'disputed'].includes(a.status) && (
              <>
                <select value={outcomeResult}
                  onChange={(e) => setOutcomeResult(e.target.value)}>
                  <option value="resolved">Resolved</option>
                  <option value="disputed">Disputed</option>
                  <option value="written_off">Written off</option>
                </select>
                <input placeholder="Note" value={outcomeNote}
                  onChange={(e) => setOutcomeNote(e.target.value)} />
                <input placeholder="Response reference" value={outcomeRef}
                  onChange={(e) => setOutcomeRef(e.target.value)} />
                <button disabled={busy} className="primary-btn"
                  onClick={() => run(() => post(a.recovery_action_id, '/outcome', {
                    result: outcomeResult, note: outcomeNote,
                    response_reference: outcomeRef }), 'Outcome recorded')}>
                  Record outcome</button>
              </>
            )}
          </div>

          {a.outcome && (
            <p className="muted-note">
              Outcome: {a.outcome.result} {a.outcome.note ? `— ${a.outcome.note}` : ''}
            </p>
          )}

          <details className="history">
            <summary>History</summary>
            <ul>
              {(a.history || []).map((h, i) => (
                <li key={i}>{h.ts} — {h.event}: {h.from || '—'} → {h.to}
                  {h.actor ? ` (${h.actor})` : ''}</li>
              ))}
            </ul>
          </details>
        </div>
      ))}

      {actionable && (
        <div className="new-action">
          <h5>Prepare recovery action</h5>
          <select value={actionType} onChange={(e) => setActionType(e.target.value)}>
            {ACTION_TYPES.map((t) => <option key={t} value={t}>{pretty(t)}</option>)}
          </select>
          <select value={draftMode} onChange={(e) => setDraftMode(e.target.value)}>
            <option value="template">Template</option>
            <option value="model">Suggested wording</option>
          </select>
          <button className="primary-btn" disabled={busy}
            onClick={() => run(async () => {
              await apiRequest(`/findings/${findingId}/recovery-actions`, {
                method: 'POST',
                body: JSON.stringify({ action_type: actionType,
                                       draft_mode: draftMode }),
              })
            }, 'Draft created')}>
            Create draft</button>
        </div>
      )}
      {!actionable && !actions.length && (
        <p className="muted-note">
          Recovery actions can be prepared once the finding is approved.
        </p>
      )}
    </div>
  )
}
