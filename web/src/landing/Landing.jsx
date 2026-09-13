import { useState } from 'react'
import './landing.css'

const PILOT_TO = 'andre.byrd@odingard.com'

const ENTITLEMENTS = ['Minimum commitments', 'Usage charges', 'Price increases', 'Credits', 'Rebates', 'Refund rights', 'Service-level remedies', 'Reimbursements', 'Contractual adjustments']

const LOOP = [
  ['Watch', 'Continuously monitor agreements, amendments, billing, usage and recovery evidence.'],
  ['Detect', 'Identify when financial reality diverges from what the agreement required.'],
  ['Prove', 'Tie the discrepancy to the source term, evidence, actual activity and calculation.'],
  ['Prioritize', 'Rank recovery opportunities by value, confidence, age and status.'],
  ['Act', 'Prepare the appropriate recovery action.'],
  ['Approve', 'Your team retains authority over consequential recovery actions.'],
  ['Recover', 'Move the approved case through the recovery workflow.'],
  ['Verify', 'Confirm whether value was actually realized.'],
]

const FINDS = [
  ['Revenue gaps', ['Missed minimum commitments', 'Unbilled usage', 'Underbilled seats', 'Missed price increases', 'Expired discounts']],
  ['Contractual value', ['Service credits', 'Rebates', 'Refunds', 'Reimbursements', 'Contractual offsets', 'Commercial adjustments']],
  ['Commercial drift', ['Amendments not reflected downstream', 'Incorrect terms continuing after expiration', 'Billing not updated after pricing changes', 'Agreement-to-invoice discrepancies']],
]

const METRICS = [
  ['Potential recoverable value', '$427,380', 'pot'],
  ['Verified', '$184,200', 'ver'],
  ['Approved', '$92,700', 'apr'],
  ['In recovery', '$61,400', 'rec'],
  ['Realized', '$38,900', 'rlz'],
]

const CASES = [
  ['Northwind Logistics', 'Missed minimum commitment', '$48,600', 'High', 'MSA §4.1 · INV-2041', 'Approved', 'Issue true-up invoice'],
  ['Meridian Health', 'SLA service credit', '$15,000', 'High', 'MSA §7.2 · Uptime report', 'Verified', 'Send for approval'],
  ['Cascade Analytics', 'Expired discount still applied', '$12,000', 'High', 'Order form §2 · 6 invoices', 'In recovery', 'Await counterparty'],
  ['Harbor Freight Systems', 'Missed price increase', '$9,840', 'Medium', 'Amendment 2 · §3.3', 'Verified', 'Confirm effective date'],
  ['Sterling Manufacturing', 'Unbilled usage', '$7,215', 'Medium', 'Usage export · 3 periods', 'Detected', 'Review evidence'],
  ['Volt Energy', 'Rebate not applied', '$4,300', 'Low', 'Rebate schedule · Q2', 'Needs review', 'Ambiguous term'],
]

const PROOF = ['Agreement', 'Source term', 'Actual activity', 'Discrepancy', 'Calculation', 'Recovery case', 'Realized value']

const BASES = ['Cash recovery', 'Refund', 'Rebate', 'Reimbursement', 'Contractual credit', 'Settlement', 'Verified offset']

function Brand({ small }) {
  return (
    <a href="/" className={'mark' + (small ? ' small' : '')}>
      <span className="glyph">R</span>
      <span className="mark-t">Recoup<small>An Odingard Security application</small></span>
    </a>
  )
}

function HeroCase() {
  return (
    <div className="hcase" aria-label="Illustrative recovery case">
      <div className="hcase-top">
        <span className="pill example">Illustrative example</span>
        <span className="pill status">Recovery case ready</span>
      </div>
      <div className="hrows">
        <div className="hrow">
          <span className="hk">Agreement</span>
          <span className="hv">Committed minimum <b>$12,000</b>/mo · MSA §4.1</span>
          <span className="hs">entitled</span>
        </div>
        <div className="hrow">
          <span className="hk">Activity</span>
          <span className="hv">June consumption <b>$9,340</b></span>
          <span className="hs">actual</span>
        </div>
        <div className="hrow">
          <span className="hk">Billed</span>
          <span className="hv">Invoice INV-2041 <b>$9,340</b></span>
          <span className="hs bad">billed</span>
        </div>
        <div className="hrow miss">
          <span className="hk">Missed</span>
          <span className="hv">$12,000 − $9,340 = <b>$2,660</b></span>
          <span className="hs miss">shortfall</span>
        </div>
      </div>
      <div className="hcase-bottom">
        <div className="value"><span className="l">Recoverable</span><span className="n">$2,660.00</span></div>
        <div className="approve">
          <button type="button" disabled>Approve</button>
          <button type="button" disabled className="ghost">Needs review</button>
        </div>
      </div>
    </div>
  )
}

function ProblemVisual() {
  return (
    <div className="problem-vis" aria-hidden="true">
      <div className="src"><span className="src-k">Agreement</span><p>What should have happened</p></div>
      <div className="vs">vs.</div>
      <div className="src"><span className="src-k">Billing / Operations</span><p>What actually happened</p></div>
      <div className="vs">=</div>
      <div className="resolve miss"><span className="glyph">R</span>Missed value</div>
    </div>
  )
}

function Loop() {
  return (
    <ol className="loop" aria-label="Always-on recovery loop">
      {LOOP.map(([h, p], i) => (
        <li key={h} className="loop-step" style={{ '--i': i }}>
          <div className="loop-n">{String(i + 1).padStart(2, '0')}</div>
          <div className="loop-body"><h3>{h}</h3><p>{p}</p></div>
          {i < LOOP.length - 1 && <span className="loop-arrow" aria-hidden="true">→</span>}
        </li>
      ))}
    </ol>
  )
}

function Tangible() {
  return (
    <div className="tang" role="group" aria-label="Illustrative example: SLA credit">
      <div className="tang-side">
        <div className="card clause">
          <div className="card-h"><span className="k">Agreement</span>Master Services Agreement · §7.2 Service Levels</div>
          <p className="quote">
            "Provider shall maintain Monthly Uptime of at least <mark>99.95%</mark>. If Monthly Uptime falls below this threshold, Customer is entitled to a <mark>service credit of $15,000</mark> for the affected month."
          </p>
        </div>
        <div className="card obs">
          <div className="card-h"><span className="k">Observed</span>Uptime report · June</div>
          <div className="meter">
            <div className="meter-bar"><i style={{ width: '72%' }} /></div>
            <div className="meter-l"><span>Observed <b>99.72%</b></span><span>Required <b>99.95%</b></span></div>
          </div>
        </div>
        <div className="card remedy">
          <div className="card-h"><span className="k">Contractual remedy</span>Service credit</div>
          <div className="remedy-n">$15,000</div>
        </div>
      </div>
      <div className="tang-main">
        <div className="tang-h"><span className="pill example">Illustrative example</span><span className="tang-t">Recoup</span></div>
        <ul className="checks">
          {['Clause identified', 'Requirement verified', 'Observed result matched', 'Recovery amount calculated', 'Evidence assembled', 'Recovery case prepared'].map((t, i) => (
            <li key={t} style={{ '--i': i }}><i>✓</i>{t}</li>
          ))}
        </ul>
        <div className="result">
          <span className="l">Result</span>
          <span className="n">$15,000</span>
          <span className="d">potential contractual recovery</span>
        </div>
      </div>
    </div>
  )
}

function CommandCenter() {
  return (
    <div className="cc" role="group" aria-label="Recovery command center, sample data">
      <div className="cc-top">
        <span className="cc-title">Recovery command center</span>
        <span className="pill example">Sample data</span>
      </div>
      <div className="metrics">
        {METRICS.map(([l, v, cls], i) => (
          <div key={l} className={'metric ' + cls} style={{ '--i': i }}>
            <span className="ml">{l}</span>
            <span className="mv">{v}</span>
          </div>
        ))}
      </div>
      <div className="cases-wrap">
        <table className="cases">
          <thead>
            <tr>{['Counterparty', 'Recovery type', 'Potential value', 'Confidence', 'Evidence', 'Status', 'Next action'].map((h) => <th key={h}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {CASES.map(([c, t, v, conf, ev, st, next]) => (
              <tr key={c}>
                <td className="cp">{c}</td>
                <td>{t}</td>
                <td className="num">{v}</td>
                <td><span className={'conf ' + conf.toLowerCase()}>{conf}</span></td>
                <td className="ev">{ev}</td>
                <td><span className={'st ' + st.toLowerCase().replace(/\s+/g, '-')}>{st}</span></td>
                <td className="next">{next}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function PilotForm() {
  const [f, setF] = useState({ company: '', name: '', email: '' })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const body = [
    `Company: ${f.company}`,
    `Name: ${f.name}`,
    `Work email: ${f.email}`,
  ].join('\n')
  const href = `mailto:${PILOT_TO}?subject=${encodeURIComponent('Recoup pilot request — ' + (f.company || 'company'))}&body=${encodeURIComponent(body)}`
  return (
    <form className="pilot-form" onSubmit={(e) => { e.preventDefault(); window.location.href = href }}>
      <label>Company<input required value={f.company} onChange={set('company')} autoComplete="organization" /></label>
      <label>Name<input required value={f.name} onChange={set('name')} autoComplete="name" /></label>
      <label>Work email<input required type="email" value={f.email} onChange={set('email')} autoComplete="email" /></label>
      <button type="submit" className="btn">Run a Recoup Pilot</button>
      <p className="fine">For CFOs, Controllers, Finance, Revenue Operations and Billing Operations at contract-heavy B2B companies.</p>
    </form>
  )
}

void Brand
void HeroCase
void ProblemVisual
void Loop
void Tangible
void CommandCenter
void PilotForm

export default function Landing() {
  return (
    <div className="landing">
      <header>
        <div className="wrap bar">
          <Brand />
          <nav>
            <a href="#how">How it works</a>
            <a href="#trust">Proof</a>
            <a href="#pricing">Pricing</a>
            <a href="/app/" className="signin">Sign in</a>
            <a href="#pilot" className="btn sm">Run a Pilot</a>
          </nav>
        </div>
      </header>

      <main>
        {/* 1 — Hero */}
        <section className="hero wrap">
          <div className="hero-copy">
            <div className="kicker">Revenue recovery</div>
            <h1>Find the revenue you're already owed.</h1>
            <p className="lede">Recoup continuously checks what your agreements say you should receive against what was actually billed, paid, credited, or delivered — then shows your team what was missed and what can be recovered.</p>
            <div className="hero-cta">
              <a href="#pilot" className="btn">Run a Pilot</a>
              <a href="#how" className="btn ghost">See How Recoup Works</a>
            </div>
            <p className="paynote">No upfront audit fee. 20% of realized recovered value.</p>
            <p className="brandline">Recoup — an Odingard Security application</p>
          </div>
          <div className="hero-visual"><HeroCase /></div>
        </section>

        {/* 2 — Problem */}
        <section className="problem wrap" id="problem">
          <div className="two">
            <div>
              <div className="h2">Companies track what they billed.<br />Few continuously track what they were entitled to.</div>
              <p className="body">Commercial agreements create financial obligations and entitlements:</p>
              <ul className="ents">{ENTITLEMENTS.map((e) => <li key={e}>{e}</li>)}</ul>
              <p className="body">But the systems that record invoices and payments do not continuously determine whether every financial right in the agreement was actually realized.</p>
              <p className="closing">The difference becomes missed revenue.</p>
            </div>
            <ProblemVisual />
          </div>
        </section>

        {/* 3 — Insight */}
        <section className="insight" id="insight">
          <div className="wrap">
            <div className="h2 center">Your systems know what you charged.<br />Recoup finds what you should have realized.</div>
            <div className="stack" aria-hidden="true">
              <div className="layer rec"><span className="glyph">R</span>Recoup — the recovery layer</div>
              <div className="layer-row">
                {['Billing', 'Usage', 'Payments', 'Credits', 'Operational evidence'].map((s) => <div key={s} className="layer">{s}</div>)}
              </div>
              <div className="layer base">Agreements &amp; amendments</div>
            </div>
            <p className="sub center">Recoup sits on top of the systems you already use. It continuously compares agreement terms against billing, usage, payments, credits and supporting operational evidence to identify financial value that was missed.</p>
          </div>
        </section>

        {/* 4 — Loop */}
        <section className="how wrap" id="how">
          <div className="h2">From agreement to realized recovery.</div>
          <p className="sub">An always-on loop, not a one-time audit.</p>
          <Loop />
        </section>

        {/* 5 — Tangible */}
        <section className="tangible" id="example">
          <div className="wrap">
            <div className="h2">A contract clause becomes a recovery case.</div>
            <Tangible />
          </div>
        </section>

        {/* 6 — What Recoup recovers */}
        <section className="finds wrap" id="finds">
          <div className="h2">Revenue doesn't only leak from invoices.</div>
          <div className="find-cols">
            {FINDS.map(([h, items]) => (
              <div className="find-col" key={h}>
                <h4>{h}</h4>
                <ul>{items.map((t) => <li key={t}>{t}</li>)}</ul>
              </div>
            ))}
          </div>
        </section>

        {/* 7 — Command center */}
        <section className="command" id="command">
          <div className="wrap">
            <div className="h2">Know exactly where recoverable value stands.</div>
            <p className="sub">Recoup is an operating function, not a report generator. Every opportunity carries its value, confidence, evidence, status and next action.</p>
            <CommandCenter />
          </div>
        </section>

        {/* 8 — Trust */}
        <section className="trust" id="trust">
          <div className="wrap">
            <div className="h2 center">Every dollar has to be proven.</div>
            <ol className="pipe" aria-label="Proof chain">
              {PROOF.map((s, i) => (
                <li key={s} style={{ '--i': i }}>
                  <span className="node">{s}</span>
                  {i < PROOF.length - 1 && <span className="link" aria-hidden="true"><i /></span>}
                </li>
              ))}
            </ol>
            <div className="rules">
              <div><b>No source evidence?</b> No recovery claim.</div>
              <div><b>Ambiguous term?</b> Flagged for review.</div>
              <div><b>No authorized action?</b> Nothing moves forward.</div>
            </div>
            <p className="control">Your team stays in control.</p>
          </div>
        </section>

        {/* 9 — Why Recoup exists */}
        <section className="vision wrap">
          <div className="vision-inner">
            <div className="h2">Billing reconciliation is only part of the problem.</div>
            <p className="body">Businesses operate through agreements.</p>
            <p className="body">Those agreements continuously create financial rights and obligations.</p>
            <p className="body">But today's financial systems are designed primarily to record transactions — not continuously determine whether every contractual entitlement was actually realized.</p>
            <p className="closing">Recoup becomes the recovery layer between what the agreement promised and what the business actually received.</p>
          </div>
        </section>

        {/* 10 — Business model */}
        <section className="pricing" id="pricing">
          <div className="wrap">
            <div className="h2">We win when you recover value.</div>
            <div className="price-card">
              <div className="big">20%<span> of realized recovered value</span></div>
              <p className="no-upfront">No upfront audit fee. Recoup earns when value is actually realized through outcomes such as:</p>
              <div className="bases">
                <ul>{BASES.map((b) => <li key={b}>{b}</li>)}</ul>
              </div>
              <p className="closing">No realized value. No success fee.</p>
            </div>
          </div>
        </section>

        {/* 11 — Pilot */}
        <section className="pilot wrap" id="pilot">
          <div className="pilot-copy">
            <div className="h2">Find out what your agreements say you're leaving behind.</div>
            <p className="sub">Give Recoup your agreements and billing data and let us identify where financial value may have been missed. Upload your documents and watch Recoup work — no integrations required to start.</p>
          </div>
          <PilotForm />
        </section>

        {/* 12 — Close */}
        <section className="close wrap">
          <h2>You already earned it.</h2>
          <p className="close-sub">Recoup helps you recover it.</p>
          <a href="#pilot" className="btn">Run a Pilot</a>
        </section>
      </main>

      <footer>
        <div className="wrap foot">
          <Brand small />
          <div><a href="/terms.html">Terms</a> · <a href="/privacy.html">Privacy</a> · <a href="/app/">Sign in</a></div>
        </div>
      </footer>
    </div>
  )
}
