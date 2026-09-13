import { useState } from 'react'
import './landing.css'

const PILOT_TO = 'andre.byrd@odingard.com'

const INSIGHT = [
  ['Agreement', 'What should have happened', 'agr'],
  ['Usage / Operations', 'What actually happened', 'ops'],
  ['Billing', 'What was charged', 'bill'],
  ['Recoup', 'What is missing', 'rec'],
]

const STEPS = [
  ['Discover', 'Recoup identifies the financial obligations and entitlements contained in your agreements.'],
  ['Compare', 'Recoup compares those terms against billing, usage and supporting records.'],
  ['Prove', 'Every discrepancy is tied back to the source clause, supporting evidence and calculation.'],
  ['Recover', 'Your team receives an evidence-backed recovery case and decides what action to take.'],
]

const FINDS = [
  ['Billing gaps', ['Missed minimum commitments', 'Unbilled usage', 'Underbilled seats', 'Expired discounts', 'Missed price increases']],
  ['Contractual value', ['Service credits', 'Rebates', 'Reimbursements', 'Contractual adjustments', 'Offsets']],
  ['Commercial drift', ['Amendments not reflected in billing', 'Terms applied after expiration', 'Pricing changes not implemented', 'Agreement and invoice discrepancies']],
]

const PROOF = ['Agreement', 'Source clause', 'Financial term', 'Actual activity', 'Discrepancy', 'Calculation', 'Recovery']

const BASES = ['Payments', 'Credits', 'Refunds', 'Rebates', 'Reimbursements', 'Settlements', 'Verified offsets']

function Brand({ small }) {
  return (
    <a href="/" className={'mark' + (small ? ' small' : '')}>
      <span className="glyph">R</span>
      <span className="mark-t">Recoup<small>An Odingard Security application</small></span>
    </a>
  )
}

/* Hero visual: the agreement → discrepancy → recovery story as a product surface */
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
          <span className="hs">should</span>
        </div>
        <div className="hrow">
          <span className="hk">Usage</span>
          <span className="hv">June consumption <b>$9,340</b></span>
          <span className="hs">actual</span>
        </div>
        <div className="hrow">
          <span className="hk">Billed</span>
          <span className="hv">Invoice INV-2041 <b>$9,340</b></span>
          <span className="hs bad">billed</span>
        </div>
        <div className="hrow miss">
          <span className="hk">Missing</span>
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
      <div className="src"><span className="src-k">Contracts</span><p>Minimums · escalators · discounts · credits · rebates</p></div>
      <div className="vs">vs.</div>
      <div className="src"><span className="src-k">Usage / Operations</span><p>Seats · consumption · uptime · deliveries</p></div>
      <div className="vs">vs.</div>
      <div className="src"><span className="src-k">Billing</span><p>Invoices · line items · credits issued</p></div>
      <div className="resolve"><span className="glyph">R</span>Recoup resolves the mismatch</div>
    </div>
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
          {['Clause identified', 'Requirement verified', 'Observed performance matched', 'Recovery amount calculated', 'Evidence package prepared'].map((t, i) => (
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
      <label>Approximate annual contract revenue<input value={f.acv} onChange={set('acv')} placeholder="$" /></label>
      <label>Billing system<input value={f.billing} onChange={set('billing')} placeholder="Stripe, QuickBooks, Xero…" /></label>
      <label>Approximate number of active contracts<input value={f.contracts} onChange={set('contracts')} inputMode="numeric" /></label>
      <button type="submit" className="btn">Run a Recoup Pilot</button>
      <p className="fine">For CFOs, Controllers, Finance, RevOps and Billing Operations at contract-heavy B2B companies.</p>
    </form>
  )
}

void Brand
void HeroCase
void ProblemVisual
void Tangible
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
            <h1>Find the revenue you're already owed.</h1>
            <p className="lede">Contracts, pricing terms, usage and billing don't always agree. Recoup finds the difference, shows you exactly why money was missed, and gives your team what it needs to recover it.</p>
            <div className="hero-cta">
              <a href="#pilot" className="btn">Run a Pilot</a>
              <a href="#how" className="btn ghost">See How It Works</a>
            </div>
            <p className="paynote">No upfront audit fee. We only get paid when value is recovered.</p>
            <p className="brandline">Recoup — an Odingard Security application</p>
          </div>
          <div className="hero-visual"><HeroCase /></div>
        </section>

        {/* 2 — Problem */}
        <section className="problem wrap" id="problem">
          <div className="two">
            <div>
              <div className="h2">Your contracts and your billing system don't speak the same language.</div>
              <p className="body">Commercial agreements contain minimum commitments, pricing changes, discounts, credits, rebates, usage terms and other financial obligations.</p>
              <p className="body">Billing systems record transactions.</p>
              <p className="body">They do not continuously prove whether those transactions match what the agreement actually required.</p>
              <p className="closing">That gap becomes missed revenue.</p>
            </div>
            <ProblemVisual />
          </div>
        </section>

        {/* 3 — Insight */}
        <section className="insight" id="insight">
          <div className="wrap">
            <div className="h2 center">The source of truth isn't the invoice. It's the agreement.</div>
            <ol className="flow" aria-label="Agreement to Recoup flow">
              {INSIGHT.map(([h, s, cls], i) => (
                <li key={h} className={cls} style={{ '--i': i }}>
                  <div className="flow-card"><span className="fh">{h}</span><span className="fs">{s}</span></div>
                  {i < INSIGHT.length - 1 && <span className="down" aria-hidden="true">↓</span>}
                </li>
              ))}
            </ol>
            <p className="sub center">Recoup compares the financial terms of the agreement against what actually happened and what was actually billed.</p>
          </div>
        </section>

        {/* 4 — What Recoup does */}
        <section className="how wrap" id="how">
          <div className="h2">Recoup turns agreements into recoverable financial truth.</div>
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

        {/* 5 — Tangible */}
        <section className="tangible" id="example">
          <div className="wrap">
            <div className="h2">From contract clause to recovered value.</div>
            <Tangible />
          </div>
        </section>

        {/* 6 — What Recoup can find */}
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

        {/* 7 — Trust */}
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
              <div><b>Your team</b> approves every recovery action.</div>
            </div>
          </div>
        </section>

        {/* 8 — Vision */}
        <section className="vision wrap">
          <div className="vision-inner">
            <div className="h2">Billing reconciliation is only the beginning.</div>
            <p className="body">Businesses operate through agreements.</p>
            <p className="body">Those agreements create financial obligations and entitlements.</p>
            <p className="body">But today's systems are built around transactions — not continuously determining whether every financial right in an agreement was actually realized.</p>
            <p className="closing">Recoup closes that gap.</p>
          </div>
        </section>

        {/* 9 — Business model */}
        <section className="pricing" id="pricing">
          <div className="wrap">
            <div className="h2">Aligned with the recovery.</div>
            <div className="price-card">
              <div className="big">20%<span> of realized recovered value</span></div>
              <p className="no-upfront">No upfront audit fee. Recoup earns only when the customer actually realizes value.</p>
              <div className="bases">
                <div className="bl">Realized value includes</div>
                <ul>{BASES.map((b) => <li key={b}>{b}</li>)}</ul>
              </div>
              <p className="closing">No realized value. No success fee.</p>
            </div>
          </div>
        </section>

        {/* 10 — Pilot */}
        <section className="pilot wrap" id="pilot">
          <div className="pilot-copy">
            <div className="h2">See what your agreements say you're leaving behind.</div>
            <p className="sub">Let Recoup review where your contracts and billing diverge.</p>
          </div>
          <PilotForm />
        </section>

        {/* 11 — Close */}
        <section className="close wrap">
          <h2>You already earned it.</h2>
          <p className="close-sub">Recoup helps you find it.</p>
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
