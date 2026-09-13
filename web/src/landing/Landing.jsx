import { useState } from 'react'
import './landing.css'

const PILOT_TO = 'andre.byrd@odingard.com'

const PIPELINE = [
  'Agreement',
  'AI Rights Discovery',
  'Evidence Verification',
  'Deterministic Calculation',
  'Recovery Case',
  'Human Approval',
  'Realized Recovery',
]

const FINDS = [
  ['Missed contractual minimums', 'A committed floor billed below when usage dips.'],
  ['Unbilled usage', 'Consumption above the committed tier that was never rated.'],
  ['Expired discounts', 'A launch promo still applied months after its end date.'],
  ['Missed escalators', 'An annual increase written into the deal, never applied.'],
  ['Underbilled seats', 'Provisioned seats above what the invoice charged for.'],
  ['SLA / service credits', 'Credits you are owed when a vendor misses its commitment.'],
  ['Rebates', 'Volume or loyalty rebates earned but never claimed.'],
  ['Reimbursements', 'Pass-through costs the agreement says the other side pays.'],
  ['Offsets', 'Amounts one clause lets you net against another.'],
  ['Other evidence-backed rights', 'Anything the agreement grants that the ledger never reflected.'],
]

const STEPS = [
  ['Discover', 'AI reads agreements and discovers financial rights — including rights not hardcoded into a predefined rule list.'],
  ['Verify', 'Every candidate right must trace to authoritative source language and supporting evidence. No quote, no right.'],
  ['Calculate', 'Deterministic code calculates money. The LLM does not decide the amount owed.'],
  ['Recover', 'Recoup prepares the evidence-backed recovery case and recommended action for human approval.'],
]

const BASES = ['Cash recovery', 'Refund', 'Rebate', 'Reimbursement', 'Contractual credit', 'Settlement', 'Offset']

function Pipeline({ compact }) {
  return (
    <ol className={'pipe' + (compact ? ' compact' : '')} aria-label="Recoup pipeline">
      {PIPELINE.map((s, i) => (
        <li key={s} style={{ '--i': i }}>
          <span className="node">{s}</span>
          {i < PIPELINE.length - 1 && <span className="link" aria-hidden="true"><i /></span>}
        </li>
      ))}
    </ol>
  )
}

function ExampleCase() {
  return (
    <div className="case" role="group" aria-label="Synthetic example: SLA credit">
      <div className="case-top">
        <span className="pill example">Synthetic example — not a customer claim</span>
        <span className="pill status">Recovery case prepared</span>
      </div>

      <div className="case-grid">
        <div className="card clause">
          <div className="card-h"><span className="k">Source</span>Master Services Agreement · §7.2 Service Levels</div>
          <p className="quote">
            "Provider shall maintain Monthly Uptime of at least <mark>99.95%</mark>. If Monthly Uptime falls below this threshold, Customer is entitled to a <mark>service credit of $15,000</mark> for the affected month."
          </p>
          <div className="card-f">Clause verified · quote grounded in source document</div>
        </div>

        <div className="card right">
          <div className="card-h"><span className="k">Verified right</span>SLA service credit</div>
          <dl>
            <div><dt>Trigger</dt><dd>observed_uptime &lt; 99.95%</dd></div>
            <div><dt>Amount</dt><dd>constant · $15,000.00</dd></div>
            <div><dt>Basis</dt><dd>contractual credit</dd></div>
          </dl>
          <div className="card-f">Right id · sha256:9f3a…c41e</div>
        </div>

        <div className="card obs">
          <div className="card-h"><span className="k">Observation</span>Uptime report · 2026-06</div>
          <div className="meter">
            <div className="meter-bar"><i style={{ width: '72%' }} /></div>
            <div className="meter-l"><span>Observed <b>99.72%</b></span><span>Threshold <b>99.95%</b></span></div>
          </div>
          <div className="card-f">Trigger proven · 99.72% &lt; 99.95%</div>
        </div>

        <div className="card calc">
          <div className="card-h"><span className="k">Deterministic calculation</span>runtime · Decimal, ROUND_HALF_UP</div>
          <pre>{`trigger  99.72 < 99.95  → true
amount   constant       → 15000.00
currency USD
result   credit         → $15,000.00`}</pre>
          <div className="card-f">Computed by code, not by the model</div>
        </div>
      </div>

      <div className="case-bottom">
        <div className="value">
          <span className="l">Recoverable</span>
          <span className="n">$15,000.00</span>
        </div>
        <div className="trail">
          {['Right discovered', 'Clause verified', 'Trigger proven', 'Deterministic amount', 'Recovery case prepared'].map((t) => (
            <span key={t}><i />{t}</span>
          ))}
        </div>
        <div className="approve">
          <button type="button" disabled>Approve</button>
          <button type="button" disabled className="ghost">Needs review</button>
          <span className="who">Awaiting your team</span>
        </div>
      </div>
    </div>
  )
}

function PilotForm() {
  const [f, setF] = useState({ company: '', name: '', email: '', acv: '', billing: '', contracts: '' })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const body = [
    `Company: ${f.company}`,
    `Name: ${f.name}`,
    `Work email: ${f.email}`,
    `Approx. annual contract revenue: ${f.acv}`,
    `Billing system: ${f.billing}`,
    `Approx. active contracts: ${f.contracts}`,
  ].join('\n')
  const href = `mailto:${PILOT_TO}?subject=${encodeURIComponent('Recoup pilot request — ' + (f.company || 'company'))}&body=${encodeURIComponent(body)}`
  return (
    <form className="pilot-form" onSubmit={(e) => { e.preventDefault(); window.location.href = href }}>
      <label>Company<input required value={f.company} onChange={set('company')} autoComplete="organization" /></label>
      <label>Name<input required value={f.name} onChange={set('name')} autoComplete="name" /></label>
      <label>Work email<input required type="email" value={f.email} onChange={set('email')} autoComplete="email" /></label>
      <label>Approx. annual contract revenue<input value={f.acv} onChange={set('acv')} placeholder="$" /></label>
      <label>Billing system<input value={f.billing} onChange={set('billing')} placeholder="Stripe, QuickBooks, Xero, spreadsheets…" /></label>
      <label>Approx. active contracts<input value={f.contracts} onChange={set('contracts')} inputMode="numeric" /></label>
      <button type="submit" className="btn">Run a Pilot</button>
      <p className="fine">For CFOs, Controllers, Finance, RevOps, and Billing Operations at contract-heavy B2B companies.</p>
    </form>
  )
}

void Pipeline
void ExampleCase
void PilotForm

export default function Landing() {
  return (
    <div className="landing">
      <header>
        <div className="wrap bar">
          <a href="/" className="mark"><span className="glyph">R</span>Recoup</a>
          <nav>
            <a href="#how">How it works</a>
            <a href="#trust">Trust</a>
            <a href="#pricing">Pricing</a>
            <a href="/app/" className="signin">Sign in</a>
            <a href="#pilot" className="btn sm">Run a Pilot</a>
          </nav>
        </div>
      </header>

      <main>
        <section className="hero wrap">
          <div className="hero-copy">
            <div className="kicker">AI-native financial rights recovery</div>
            <h1>Find the money you're already entitled to.</h1>
            <p className="lede">Recoup uses AI to discover financial rights hidden across contracts and billing data, verifies each right against source evidence, calculates the value deterministically, and helps your team recover it.</p>
            <p className="drift">Contracts, amendments, usage, and billing systems drift apart. The money that falls between them gets missed — not stolen, just never noticed.</p>
            <div className="hero-cta">
              <a href="#pilot" className="btn">Run a Pilot</a>
              <a href="#how" className="btn ghost">See How Recoup Works</a>
            </div>
            <p className="paynote"><strong>20% of realized recovered value.</strong> No recovery, no success fee.</p>
            <p className="trustline">AI discovers. Evidence verifies. Deterministic systems calculate. Humans approve.</p>
          </div>
          <div className="hero-visual">
            <Pipeline />
            <ExampleCase />
          </div>
        </section>

        <section className="finds wrap" id="finds">
          <div className="h2">What Recoup finds</div>
          <p className="sub">Any evidence-backed financial right your agreements grant and your ledger never reflected.</p>
          <div className="find-grid">
            {FINDS.map(([h, p]) => (
              <div className="find" key={h}><span className="dot" /><div><h4>{h}</h4><p>{p}</p></div></div>
            ))}
          </div>
        </section>

        <section className="how wrap" id="how">
          <div className="h2">How it works</div>
          <div className="steps">
            {STEPS.map(([h, p], i) => (
              <div className="step" key={h}>
                <div className="s">0{i + 1}</div>
                <h3>{h}</h3>
                <p>{p}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="trust" id="trust">
          <div className="wrap">
            <div className="h2">The AI never gets to invent the money.</div>
            <p className="sub">Every recoverable dollar traces end to end. Discovery is open-ended; calculation is closed, deterministic, and reviewable.</p>
            <Pipeline compact />
            <div className="trace">
              {[
                ['source', 'The signed agreement, amendment, or order form — the authority for the right.'],
                ['evidence', 'A verbatim quote from that source. Candidates that cannot quote their source are rejected.'],
                ['verified right', 'A constrained, typed specification of the right: trigger, operands, amount, currency.'],
                ['observation', 'What actually happened — usage, invoices, uptime, seats — from your exports or connectors.'],
                ['deterministic calculation', 'Code applies the verified right to the observation. Exact decimal money, one truth path.'],
              ].map(([k, v], i, arr) => (
                <div className="trace-step" key={k}>
                  <div className="tk">{k}</div>
                  <p>{v}</p>
                  {i < arr.length - 1 && <span className="arrow" aria-hidden="true">→</span>}
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="control wrap">
          <div className="control-inner">
            <h2>Recoup discovers. Recoup proves. Recoup recommends. <em>Your team decides.</em></h2>
            <p>Nothing is billed, credited, or sent without a human approving the finding. Anything uncertain lands in needs-review — never in the total.</p>
          </div>
        </section>

        <section className="pricing" id="pricing">
          <div className="wrap">
            <div className="h2">Pricing</div>
            <div className="price-card">
              <div className="big">20%<span> of actual realized recovered value</span></div>
              <p className="no-upfront">No upfront audit fee. No realized value = no success fee.</p>
              <div className="bases">
                <div className="bl">Realized value may include verified</div>
                <ul>{BASES.map((b) => <li key={b}>{b}</li>)}</ul>
              </div>
              <a href="#pilot" className="btn">Run a Pilot</a>
            </div>
          </div>
        </section>

        <section className="pilot wrap" id="pilot">
          <div className="pilot-copy">
            <div className="h2">Let Recoup audit where your agreements and your revenue diverge.</div>
            <p className="sub">Bring your agreements and a billing export. We return a grounded audit: each right, its evidence, its math, and what it's worth.</p>
          </div>
          <PilotForm />
        </section>

        <section className="close wrap">
          <h2>You already earned it. Recoup helps you find it.</h2>
          <a href="#pilot" className="btn">Run a Pilot</a>
        </section>
      </main>

      <footer>
        <div className="wrap foot">
          <div className="mark small"><span className="glyph">R</span>Recoup</div>
          <div>An Odingard product · <a href="/terms.html">Terms</a> · <a href="/privacy.html">Privacy</a> · <a href="/app/">Sign in</a></div>
        </div>
      </footer>
    </div>
  )
}
