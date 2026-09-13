import { useMemo, useState } from 'react'
import { AlertCircle, LockKeyhole } from 'lucide-react'
import RecoveryActions from './RecoveryActions'

void [AlertCircle, LockKeyhole, RecoveryActions]

function formatCurrency(value) {
  return `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

const METRIC_TILES = [
  ['potential_recoverable_value', 'Potential recoverable'],
  ['verified_value', 'Verified'],
  ['needs_review', 'Needs review'],
  ['approved', 'Approved'],
  ['in_recovery', 'In recovery'],
  ['disputed', 'Disputed'],
  ['realized_value', 'Realized value'],
  ['written_off', 'Written off'],
]

const EMPTY_FILTERS = {
  customer: '', status: '', type: '', minValue: '',
  maxAge: '', minConfidence: '', agreement: '', period: '',
}

export default function CommandCenter({ data, onOpenInReview, apiRequest,
                                        onChanged }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [selected, setSelected] = useState(null)

  const cases = useMemo(() => {
    const minV = filters.minValue === '' ? null : Number(filters.minValue)
    const maxAge = filters.maxAge === '' ? null : Number(filters.maxAge)
    const minConf = filters.minConfidence === '' ? null : Number(filters.minConfidence)
    return (data?.cases || []).filter((c) => {
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
  }, [data, filters])

  if (!data) {
    return (
      <section className="glass-panel panel-card">
        <div className="panel-heading"><h2>Command Center</h2></div>
        <p className="muted-copy">Loading recovery pipeline…</p>
      </section>
    )
  }

  const set = (key) => (e) => setFilters((f) => ({ ...f, [key]: e.target.value }))
  const es = data.executive_summary || {}

  return (
    <section className="glass-panel panel-card command-center">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Step 7</p>
          <h2>Recovery Command Center</h2>
        </div>
        <span className="hint-pill">{cases.length} cases</span>
      </div>

      <div className="metric-grid">
        {METRIC_TILES.map(([key, label]) => (
          <div key={key} className="glass-panel metric-card">
            <span className="metric-label">{label}</span>
            <strong className="metric-value">{formatCurrency(data.metrics?.[key])}</strong>
          </div>
        ))}
      </div>

      <div className="glass-panel" style={{ padding: '0.9rem', marginTop: '1rem' }}>
        <div className="cc-pipeline" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {(data.pipeline || []).map((stage) => (
            <div key={stage.stage} className="glass-panel" style={{ flex: 1, minWidth: '8rem', padding: '0.6rem' }}>
              <div className="metric-label">{stage.stage}</div>
              <strong>{formatCurrency(stage.value)}</strong>
              <div className="muted-copy">{stage.count} case{stage.count === 1 ? '' : 's'}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="glass-panel" style={{ padding: '0.9rem', marginTop: '1rem', display: 'flex', gap: '1.4rem', flexWrap: 'wrap' }}>
        <span><strong>{formatCurrency(es.total_opportunity)}</strong> <span className="muted-copy">total opportunity</span></span>
        <span><strong>{formatCurrency(es.realized_value)}</strong> <span className="muted-copy">realized</span></span>
        <span><strong>{Math.round((es.recovery_rate || 0) * 100)}%</strong> <span className="muted-copy">recovery rate</span></span>
        <span><strong>{es.avg_days_to_recovery ?? '—'}</strong> <span className="muted-copy">avg days to recovery</span></span>
        <span><strong>{es.open_cases ?? 0}</strong> <span className="muted-copy">open cases</span></span>
        {(es.top_recovery_sources || []).length > 0 && (
          <span className="muted-copy">
            Top sources: {es.top_recovery_sources.map((s) => `${s.type} (${formatCurrency(s.value)})`).join(', ')}
          </span>
        )}
      </div>

      <div className="cc-filters" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '1rem' }}>
        <select value={filters.customer} onChange={set('customer')}>
          <option value="">All customers</option>
          {(data.filters?.customers || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select value={filters.status} onChange={set('status')}>
          <option value="">All statuses</option>
          {(data.filters?.statuses || []).map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={filters.type} onChange={set('type')}>
          <option value="">All types</option>
          {(data.filters?.types || []).map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={filters.agreement} onChange={set('agreement')}>
          <option value="">All agreements</option>
          {(data.filters?.agreements || []).map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select value={filters.period} onChange={set('period')}>
          <option value="">All periods</option>
          {(data.filters?.periods || []).map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <input type="number" placeholder="Min value" value={filters.minValue} onChange={set('minValue')} style={{ width: '7rem' }} />
        <input type="number" placeholder="Max age (days)" value={filters.maxAge} onChange={set('maxAge')} style={{ width: '8rem' }} />
        <input type="number" step="0.05" min="0" max="1" placeholder="Min confidence" value={filters.minConfidence} onChange={set('minConfidence')} style={{ width: '8rem' }} />
      </div>

      <div style={{ overflowX: 'auto', marginTop: '1rem' }}>
        <table className="cc-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
          <thead>
            <tr style={{ textAlign: 'left' }}>
              <th>Counterparty</th><th>Financial right</th><th>Period</th>
              <th>Expected</th><th>Actual</th><th>Recoverable</th>
              <th>Confidence</th><th>Evidence</th><th>Status</th><th>Next step</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.finding_id} onClick={() => setSelected(c)}
                  style={{ cursor: 'pointer', borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                <td>{c.counterparty?.customer_name}</td>
                <td>{c.financial_right?.type}</td>
                <td>{c.period}</td>
                <td>{formatCurrency(c.expected_value)}</td>
                <td>{formatCurrency(c.actual_value)}</td>
                <td>{formatCurrency(c.recoverable_difference)}</td>
                <td>{Math.round(Number(c.confidence || 0) * 100)}%</td>
                <td>{Math.round(Number(c.evidence?.completeness || 0) * 100)}%</td>
                <td>{c.status}{c.locked ? ' 🔒' : ''}</td>
                <td>{c.recommended_next_step}</td>
              </tr>
            ))}
            {cases.length === 0 && (
              <tr><td colSpan="10" className="muted-copy" style={{ padding: '1rem' }}>No cases match the current filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="glass-panel" style={{ marginTop: '1rem', padding: '1rem' }}>
          <div className="panel-heading">
            <div>
              <p className="eyebrow">{selected.finding_id}</p>
              <h3>{selected.financial_right?.title}</h3>
            </div>
            <button className="btn-secondary" onClick={() => setSelected(null)}>Close</button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div>
              <div className="info-group"><div className="info-label">Agreement</div>
                <div>{selected.agreement?.customer_name} ({selected.agreement?.customer_id})</div>
                <div className="muted-copy">
                  {selected.agreement?.term_start || '—'} → {selected.agreement?.term_end || '—'}
                </div>
              </div>
              <div className="info-group"><div className="info-label">Approval</div>
                <div>{selected.approval?.state}{selected.approval?.approved_at ? ` at ${selected.approval.approved_at}` : ''}</div>
              </div>
              <div className="info-group"><div className="info-label">Net realized</div>
                <div>{formatCurrency(selected.net_realized)}</div>
              </div>
              {onOpenInReview && (
                <button className="btn-primary" onClick={() => onOpenInReview(selected.finding_id)}>
                  Open in review
                </button>
              )}
            </div>
            <div>
              <div className="info-group"><div className="info-label">Source clause</div>
                <div className="provenance-box">
                  {selected.locked && <LockKeyhole size={14} />}
                  {selected.evidence?.clause_text || selected.evidence?.provenance || 'No clause text on file.'}
                </div>
              </div>
              <div className="info-group"><div className="info-label">Math</div>
                <div className="detail-copy">{selected.evidence?.math || '—'}</div>
              </div>
            </div>
          </div>
          {(selected.recovery_history || []).length > 0 && (
            <div className="info-group"><div className="info-label">Recovery history</div>
              <ul className="upload-history">
                {selected.recovery_history.map((h, i) => (
                  <li key={i} className="upload-history-item">
                    <span>{h.event}{h.decision ? ` · ${h.decision}` : ''}</span>
                    <span className="muted-copy">{h.ts}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(selected.realization_history || []).length > 0 && (
            <div className="info-group"><div className="info-label">Realization history</div>
              <ul className="upload-history">
                {selected.realization_history.map((e, i) => (
                  <li key={i} className="upload-history-item">
                    <span>{e.event_type} · {e.recovery_basis} · {formatCurrency(e.event_type === 'reversal' ? e.reversal_amount : e.realized_value)}</span>
                    <span className="muted-copy">{e.realized_at || e.created_at}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(selected.recovery_actions || []).length > 0 && (
            <div className="info-group"><div className="info-label">Recovery actions</div>
              <ul className="upload-history">
                {selected.recovery_actions.map((a) => (
                  <li key={a.id} className="upload-history-item">
                    <span>{a.action_type?.replace(/_/g, ' ')} · {a.status} · {formatCurrency(a.requested_value)}</span>
                    <span className="muted-copy">{a.channel || ''}{a.executed_at ? ` · ${a.executed_at}` : ''}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {apiRequest && (
            <RecoveryActions
              finding={{ finding_id: selected.finding_id, status: selected.status,
                         customer_name: selected.counterparty?.customer_name,
                         monthly_recoverable: selected.recoverable_difference }}
              apiRequest={apiRequest}
              onChanged={onChanged}
            />
          )}
          {selected.locked && (
            <p className="muted-copy"><AlertCircle size={13} /> Proof is locked until a payment method is on file.</p>
          )}
        </div>
      )}
    </section>
  )
}
