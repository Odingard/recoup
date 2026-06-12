import { useState, useEffect } from 'react'
import { CheckCircle2, XCircle, FileText, AlertCircle, RefreshCw, LogIn, LogOut } from 'lucide-react'
const API_BASE = "http://127.0.0.1:8001/api"

export default function App() {
  const [findings, setFindings] = useState([])
  const [activeFinding, setActiveFinding] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [user, setUser] = useState(null)

  useEffect(() => {
    // Check local storage for session
    const savedUser = localStorage.getItem('recoup_user')
    if (savedUser) {
      const parsed = JSON.parse(savedUser)
      setUser(parsed)
      fetchFindings(parsed)
    } else {
      setLoading(false)
    }
  }, [])

  const getToken = async () => {
    return user ? user.token : null;
  }

  const fetchFindings = async (currentUser = user) => {
    if (!currentUser) return;
    try {
      const token = currentUser.token;
      const res = await fetch(`${API_BASE}/findings/pending`, {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      const data = await res.json()
      setFindings(data)
      if (data.length > 0 && !activeFinding) {
        setActiveFinding(data[0])
      }
      setLoading(false)
    } catch (e) {
      console.error("Failed to fetch", e)
      setLoading(false)
    }
  }

  const triggerReconciliation = async () => {
    setRunning(true)
    try {
      const token = await getToken()
      await fetch(`${API_BASE}/reconcile?period=2026-06`, { 
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
      await fetchFindings()
    } catch(e) {
      console.error(e)
    }
    setRunning(false)
  }

  const handleAction = async (findingId, action) => {
    try {
      const token = await getToken()
      const endpoint = action === 'approve' ? 'approve' : 'reject'
      await fetch(`${API_BASE}/findings/${findingId}/${endpoint}`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ status: action, reason: '' })
      })
      
      const newFindings = findings.filter(f => f.finding_id !== findingId)
      setFindings(newFindings)
      setActiveFinding(newFindings.length > 0 ? newFindings[0] : null)
    } catch(e) {
      console.error(e)
    }
  }

  const handleLogin = async () => {
    try {
      // Simulate enterprise SSO
      await new Promise(r => setTimeout(r, 600));
      const mockUser = { email: 'admin@enterprise.com', token: 'mock-enterprise-token-123' };
      localStorage.setItem('recoup_user', JSON.stringify(mockUser));
      setUser(mockUser);
      fetchFindings(mockUser);
    } catch (error) {
      console.error("Login failed:", error);
    }
  }

  const handleLogout = () => {
    localStorage.removeItem('recoup_user');
    setUser(null);
  }

  if (loading) return <div className="app-container">Loading...</div>

  if (!user) {
    return (
      <div className="app-container" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="glass-panel" style={{ padding: '48px', textAlign: 'center', maxWidth: '400px' }}>
          <RefreshCw size={48} color="var(--accent-primary)" style={{ margin: '0 auto 24px' }} />
          <h1 className="title-glow" style={{ marginBottom: '16px' }}>Recoup</h1>
          <p style={{ color: 'var(--text-muted)', marginBottom: '32px' }}>Enterprise Revenue Assurance. Please authenticate to access your dashboard.</p>
          <button className="btn-primary" onClick={handleLogin} style={{ width: '100%', padding: '12px', fontSize: '16px', display: 'flex', justifyContent: 'center', gap: '8px' }}>
            <LogIn size={20} />
            Enter Secure Dashboard
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="app-container">
      <header className="header">
        <div>
          <h1 className="title-glow" style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <RefreshCw size={28} color="var(--accent-primary)" />
            Recoup
          </h1>
          <p style={{ color: 'var(--text-muted)' }}>Revenue Assurance Dashboard</p>
        </div>
        <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
          <span style={{ color: 'var(--text-muted)', fontSize: '14px' }}>
            Secure Session Active
          </span>
          <button 
            className="btn-primary" 
            onClick={triggerReconciliation}
            disabled={running}
            style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
          >
            {running ? <RefreshCw className="spin" size={16} /> : <FileText size={16} />}
            {running ? 'Reconciling...' : 'Run Reconciliation'}
          </button>
          <button className="btn-danger" onClick={handleLogout} title="Sign Out">
            <LogOut size={16} />
          </button>
        </div>
      </header>

      <div className="dashboard-grid">
        {/* Sidebar Queue */}
        <div className="queue-list">
          <h3 style={{ marginBottom: '12px', fontSize: '14px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
            Pending Review ({findings.length})
          </h3>
          {findings.length === 0 ? (
            <div className="glass-panel" style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)' }}>
              <CheckCircle2 size={32} style={{ margin: '0 auto 12px', opacity: 0.5 }} />
              <p>No pending findings.</p>
            </div>
          ) : (
            findings.map(f => (
              <div 
                key={f.finding_id} 
                className={`glass-panel queue-item ${activeFinding?.finding_id === f.finding_id ? 'active' : ''}`}
                onClick={() => setActiveFinding(f)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                  <span className="badge badge-pending">Action Required</span>
                  <span className="amount">${f.monthly_recoverable.toLocaleString()}</span>
                </div>
                <div style={{ fontWeight: 500, marginBottom: '4px' }}>{f.customer_name}</div>
                <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>{f.title}</div>
              </div>
            ))
          )}
        </div>

        {/* Main Detail View */}
        {activeFinding ? (
          <div className="glass-panel detail-view">
            <div className="detail-header">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <h2 style={{ marginBottom: '8px' }}>{activeFinding.customer_name}</h2>
                  <div style={{ color: 'var(--text-muted)', display: 'flex', gap: '16px' }}>
                    <span>ID: {activeFinding.finding_id}</span>
                    <span>Period: {activeFinding.period}</span>
                  </div>
                </div>
                <div className="amount" style={{ fontSize: '28px', color: 'var(--accent-primary)' }}>
                  ${activeFinding.monthly_recoverable.toLocaleString()} / mo
                </div>
              </div>
            </div>

            <div className="detail-content">
              {/* Left Column: The Logic */}
              <div>
                <h3 style={{ marginBottom: '16px', borderBottom: '1px solid var(--border-color)', paddingBottom: '8px' }}>Discrepancy Details</h3>
                <div className="info-group">
                  <div className="info-label">Title</div>
                  <div>{activeFinding.title}</div>
                </div>
                <div className="info-group">
                  <div className="info-label">Engine Reasoning</div>
                  <div style={{ lineHeight: '1.6', color: '#c9d1d9' }}>{activeFinding.detail}</div>
                </div>
              </div>

              {/* Right Column: The Ground Truth */}
              <div>
                <h3 style={{ marginBottom: '16px', borderBottom: '1px solid var(--border-color)', paddingBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <AlertCircle size={18} color="var(--text-muted)" />
                  Contract Grounding
                </h3>
                <div className="info-group">
                  <div className="info-label">Confidence Score</div>
                  <div>{(activeFinding.confidence_score * 100).toFixed(0)}%</div>
                </div>
                <div className="info-group">
                  <div className="info-label">Exact Clause Quote (Provenance)</div>
                  <div className="provenance-box">
                    "{activeFinding.provenance || "No direct quote available in legacy data."}"
                  </div>
                </div>
              </div>
            </div>

            <div className="action-bar">
              <button 
                className="btn-danger" 
                style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
                onClick={() => handleAction(activeFinding.finding_id, 'reject')}
              >
                <XCircle size={16} /> Reject Finding
              </button>
              <button 
                className="btn-success"
                style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
                onClick={() => handleAction(activeFinding.finding_id, 'approve')}
              >
                <CheckCircle2 size={16} /> Approve & Draft Invoice
              </button>
            </div>
          </div>
        ) : (
          <div className="glass-panel" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)' }}>
            Select a finding from the queue to review
          </div>
        )}
      </div>
      
      <style dangerouslySetInnerHTML={{__html: `
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { 100% { transform: rotate(360deg); } }
      `}} />
    </div>
  )
}
