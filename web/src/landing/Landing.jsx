import './landing.css'

const CHECK = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1F8A5B" strokeWidth="2" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
)

export default function Landing() {
  return (
    <div className="landing">
      <header>
        <div className="wrap bar">
          <div className="mark"><span className="glyph">R</span>Recoup</div>
          <nav>
            <a href="#how">How it works</a>
            <a href="#pricing">Pricing</a>
            <a href="/app/" className="signin">Sign in</a>
            <a href="/app/" className="btn">Run a free audit</a>
          </nav>
        </div>
      </header>

      <main>
        <div className="wrap">
          <section className="hero">
            <div className="kicker">Revenue assurance for B2B contracts</div>
            <h1>Find the revenue you're already owed — pay only if we recover it</h1>
            <p className="lede">Recoup reads your signed contracts, checks them against what you actually billed, and finds the money that slipped through — unenforced minimums, unbilled usage, expired discounts, forgotten price increases.</p>
            <div className="hero-cta">
              <a href="/app/" className="btn">Run a free audit</a>
              <a href="/app/?sample=1" className="btn ghost">See it on sample data</a>
            </div>
            <p className="paynote">No integration to start. <strong>You pay 20% of what we actually recover</strong> — nothing if we find nothing.</p>

            <div className="ledger" role="img" aria-label="Reconciliation example: a contract minimum of 8,000 dollars per month billed at only 4,800, surfacing 3,200 per month, 6,400 over 2 months.">
              <div className="ledger-head">
                <span className="who">Meridian Foods — Supply Chain Cloud</span>
                <span className="tag">Period 2026-06 · 2026-07</span>
              </div>
              <div className="rows">
                <div className="row contract">
                  <div className="label"><b>Contract minimum</b> — clause 3.3, monthly floor</div>
                  <div className="val">$8,000 / mo</div>
                </div>
                <div className="row billed">
                  <div className="label">Actually billed — 6 active facilities, prorated</div>
                  <div className="val">$4,800 / mo</div>
                </div>
                <div className="row gap divider">
                  <div className="label"><b>Recoverable</b> — floor not enforced, 2 months</div>
                  <div className="val">+ $6,400</div>
                </div>
              </div>
              <div className="clause">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#1F8A5B" strokeWidth="2" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
                <div>Grounded in contract: <span className="q">"Customer commits to a minimum monthly charge of $8,000 regardless of the number of active facilities."</span></div>
              </div>
            </div>
          </section>
        </div>

        <div className="band">
          <div className="wrap">
            <div className="stat"><div className="n">3–5%</div><div className="l">of ARR that B2B SaaS companies lose to billing leakage every year</div></div>
            <div className="stat"><div className="n">$0</div><div className="l">upfront — the audit is free, you pay only on what's recovered</div></div>
            <div className="stat"><div className="n">Read-only</div><div className="l">Recoup never writes to your systems — it shows you the money, you send the invoice</div></div>
          </div>
        </div>

        <div className="wrap">
          <section className="how" id="how">
            <div className="h2">Three steps to found money.</div>
            <p className="sub">No implementation project. No engineering time. Give it your contracts and a billing export, and the audit runs.</p>
            <div className="steps">
              <div className="step">
                <div className="s">Step 1</div>
                <h3>Bring your contracts</h3>
                <p>Upload signed agreements and a billing export — or connect Stripe read-only. Nothing to integrate, nothing to install.</p>
              </div>
              <div className="step">
                <div className="s">Step 2</div>
                <h3>Recoup reconciles</h3>
                <p>It reads every contract clause, compares it to what was billed, and computes each gap in exact dollars — grounded in the language that proves it's owed.</p>
              </div>
              <div className="step">
                <div className="s">Step 3</div>
                <h3>You approve, then recover</h3>
                <p>Review each finding with its cited clause and math. Approve the ones you want; Recoup drafts the corrective invoice for you to send.</p>
              </div>
            </div>
          </section>

          <section className="why">
            <div className="why-inner">
              <div className="lead">
                <h3>Why not the billing platform you already have?</h3>
                <p>Billing suites and reconciliation tools find leakage by becoming your infrastructure — a months-long rollout, seat licenses, and a finance team to run them. Recoup takes the opposite path.</p>
              </div>
              <div className="compare">
                <div className="item">
                  {CHECK}
                  <div className="k"><b>No integration to start</b>Paste contracts and a billing export, or connect Stripe read-only. No implementation project.</div>
                </div>
                <div className="item">
                  {CHECK}
                  <div className="k"><b>Nothing to pay until it works</b>No seats, no platform fee. You pay 20% of what's actually recovered — or nothing.</div>
                </div>
                <div className="item">
                  {CHECK}
                  <div className="k"><b>Built for your size</b>For the companies too small for a six-month rollout and too busy to leave money on the table.</div>
                </div>
              </div>
            </div>
          </section>

          <section className="leaks">
            <div className="h2">What slips through</div>
            <div className="leak-grid">
              <div className="leak"><span className="dot"></span><div><h4>Unenforced minimums</h4><p>A contract floor that quietly gets billed below when usage dips.</p></div></div>
              <div className="leak"><span className="dot"></span><div><h4>Unbilled usage overage</h4><p>Consumption above the committed tier that never gets rated or charged.</p></div></div>
              <div className="leak"><span className="dot"></span><div><h4>Expired discounts</h4><p>A launch promo that was supposed to end months ago, still applied.</p></div></div>
              <div className="leak"><span className="dot"></span><div><h4>Forgotten escalators</h4><p>An annual price increase written into the deal, never applied at renewal.</p></div></div>
            </div>
          </section>
        </div>

        <section className="pricing" id="pricing">
          <div className="wrap">
            <div className="h2" style={{ marginBottom: '36px' }}>Pricing that only wins when you do.</div>
            <div className="price-card">
              <div className="big">20%<span> of what we recover</span></div>
              <ul className="terms">
                <li>{CHECK}The audit is free. See exactly what's recoverable before you pay anything.</li>
                <li>{CHECK}You only pay on dollars actually recovered — proposed, approved, collected.</li>
                <li>{CHECK}Find nothing, owe nothing. No seats, no platform fee, no contract to sign.</li>
              </ul>
              <a href="/app/" className="btn">Run a free audit</a>
              <p className="fine">Built for finance teams and the fractional CFOs who serve them.</p>
            </div>
          </div>
        </section>

        <div className="wrap">
          <section className="close">
            <h2>Your contracts already earned this money. Go get it.</h2>
            <a href="/app/" className="btn">Run a free audit</a>
          </section>
        </div>
      </main>

      <footer>
        <div className="wrap foot">
          <div className="mark" style={{ fontSize: '18px' }}><span className="glyph" style={{ width: '24px', height: '24px', fontSize: '13px' }}>R</span>Recoup</div>
          <div>An Odingard product · <a href="/app/">Sign in</a></div>
        </div>
      </footer>
    </div>
  )
}
