# Recoup — Production Acceptance Report

Final phase: Product Truth + Production Acceptance. No new product features were added in this phase; every change was a correction to make the deployed product match its real, production-backed capability (see `docs/PRODUCT_CAPABILITY_MATRIX.md`).

## 1. Release candidates

| Candidate | Main SHA | Cloud Run revision | Purpose |
|---|---|---|---|
| RC1 (frozen) | `8bc85791739c0a97fe8641760618524a3507c85f` | `recoup-00054-bjn` | Full browser acceptance walk (tests 1–12 + time-separated always-on run) |
| RC2 | `79911f013db56083440c933409105306b69afe1c` | `recoup-00056-sxf` | Fixes D01–D17 (PR #37); targeted browser re-walk |
| RC3 | `58fff0211f9c3d63ca02d21a726aa6db9d268ea1` | `recoup-00058-nqk` | Fix D09b partial-realization aggregate metrics (PR #38); targeted API/browser verification |
| D18 copy | `fde9176f10d5ce9f60648318fba45853121c13cf` | `recoup-00062-b9c` | Landing copy no longer claims a live payment source (PR #40) |
| Closeout (current production, **frozen**) | `d40604d29c0801ffde1b6daf894de14947a6a679` | auto-deployed from main | Fixes D19–D22 found in the card-gated exercise (PR #41); share-secret provisioning added to deploy |

Production URL: https://recoup.odingard.com. `GET /api/health` returned `{"status":"ok","version":"<SHA>","mode":"live"}` for each candidate at deploy time; `/api/ready` 200; `/api/command-center`, `/api/metrics/recovery`, `POST /api/contracts/{id}/confirm` return 401 unauthenticated. No `*_TEST_*` env vars on the production service; `recoup-validation` and `recoup-staging` untouched.

Release verification: frontend bundle is built into the same image as the backend (single image tag = SHA), so bundle/backend correspondence is by construction; 100% traffic on the named revision; no leftover customer data (all synthetic tenants deleted, see §6).

## 2. Method

- Real Chromium browser against the public production URL (desktop 1440×1080 and mobile viewport in RC1; desktop in the RC2 re-walk). Browser-context authenticated API calls were used only where the acceptance criteria required an exact status/body.
- Auth: the one available Google identity (UID `BNggBcRfcRRTnUoxHcwzG8zzuXD3`) validated popup sign-in / sign-out / re-login / reload only. All lifecycle and isolation work used fresh production Firebase custom-token tenants, one per run:
  - RC1: `recoup-acceptance-8bc8579-{a,b,c}-20260914`
  - RC2: `recoup-acceptance-79911f0-{a,b,c}-20260914`
- Synthetic fixtures only (`/home/ubuntu/acceptance/fixtures/`, manifest `manifest.json`): Aster PDF, Birch DOCX, Cedar+Delta ZIP, baseline/new billing and usage CSVs, Aster amendment, corrupt PDF, $12,345,678.90 large-value agreement.
- Boundaries respected: no card added, no Stripe charge, no external communication sent (document/email-draft channels produce artifacts only), no writes on the Google identity.
- Evidence: `/home/ubuntu/acceptance/RESULTS.md` (RC1, 18 findings), `/home/ubuntu/acceptance/RESULTS_RC2.md` (RC2), screenshots under `acceptance/screenshots/` and `acceptance/rc2/screenshots/`, sanitized request/response logs `evidence/browser-events.jsonl` and `evidence/api-checks.jsonl`, recordings `screencasts/recoup-acceptance-desktop/*-edited.mp4` and `screencasts/recoup-rc2-targeted/*-edited.mp4`. All captured API `request_id` values were null (the service does not emit a correlation header); timestamps are server UTC from response bodies.

## 3. Expected arithmetic (billing period 2026-06, USD)

| Customer | Term | Actual | Expected finding |
|---|---|---|---|
| Aster | $1,000 minimum | $600 billed | $400 (→ $300 after $700 invoice; → $500 after $1,200 amendment) |
| Birch | $2,000 minimum | $1,500 | $500 |
| Cedar | $3,000 minimum | $2,000 | $1,000 |
| Delta | 100 units incl., $2/unit overage | 150 units | $100 (→ $200 at 200 units) |
| Baseline total | | | **$2,000, 4 findings** — observed exactly, no duplicates on re-evaluation |

Closed loop (RC2, Birch $500 requested): cash $100 → settlement $200 → reverse $40 ⇒ realized $300, reversed $40, **net $260**, fee 20% = **$52.00**, shortfall $240. All observed exactly. RC1 sequence (cash 100 + credit 50 − reversal 40 + settlement 200 = net $310 / fee $62) also exact; duplicate reference → 409; over-reversal → 422.

## 4. Journey results (final state, after RC3)

| # | Journey | RC1 | Final | Notes |
|---|---|---|---|---|
| 1 | Entry / auth / session persistence | PASS | PASS | 401 on missing, garbage and expired bearer. Google popup logs COOP console messages (browser-side, benign). |
| 2 | Onboarding (PDF/DOCX/ZIP/CSV, corrupt file, term review, durable confirm) | FAIL (D14) | PASS | Corrupt PDF now returns a finance-readable message with no provider text and no 500. Single-doc endpoint answers `200 {status:"needs_review"}` per the API's fail-closed convention rather than 422; the UI treats it as an error. Scanned-image OCR **BLOCKED** (no scanned fixture). |
| 3 | Initial evaluation & exact calculations | PASS | PASS | $2,000 / 4 findings; minimum-only customers require explicit zero-usage rows, otherwise fail to review. Report/PDF proof **BLOCKED** (card-gated). |
| 4 | Opportunity lifecycle & illegal transitions | partial (D16, D17) | PASS | rejected→approved 409; audit actor now uid when no email; no realize control on terminal rows. |
| 5 | Continuous Assurance | FAIL (D01, D02, D06) | PASS | Changed invoice in existing period emits `new_invoice` and updates the finding (Aster $400→$300); identical re-upload no event; identical amendment no event; UI refreshes without reload; approved/recovered/written-off states preserved across re-evaluation. `new_payment` not emitted (no payment source) — documented limitation. |
| 6 | Recovery actions (7 types, approval gate, adapters, no external send) | FAIL (D03–D05) | PASS | Native create/save/submit/approve/execute persist; manual execute without reference → 409 shown as error, not success. Seven types/three channels exercised in RC1 via API after the UI defect; document/email-draft never sent anything externally. |
| 7 | Closed-loop realization | FAIL (D09, D11) | PASS* | Partial leaves case open with correct outstanding; settlement closes as settled; reversal allowed after recovery; history hydrates after re-login; net vs gross labelled. *Aggregate metrics after a partial (D09b) fixed in RC3 — see §5. |
| 8 | Success fee | partial (D13) | PASS | Deterministic 20% of net; no-card → button disabled + API 402 before any Stripe call. Actual charge **BLOCKED** by policy (no real card). |
| 9 | Tenant isolation | partial (D07, D08) | PASS | No cross-tenant disclosure in either run; child routes uniform 404; foreign deep link renders not-found. Share-id/report isolation **BLOCKED** (card-gated). |
| 10 | Failure modes / fail-closed | partial (D10, D14) | PASS | No 500s captured in either run; structured errors readable. Provider outage simulation **not performed**. |
| 11 | UI acceptance (desktop/mobile, keyboard, deep links, reload, states) | partial (D12, D15, D17) | PASS | Two-decimal money; net realized column. Mobile verified on RC1 only; RC2/RC3 changes were desktop-verified. |
| 12 | Public surface | partial (D18) | PASS w/ note | Landing, CTA, Terms, Privacy, sign-in, SAMPLE labelling all correct. D18: hero says "billed, paid, credited, or delivered"; payments enter only via recorded realization events — wording left to product decision. |
| E | Time-separated always-on lifecycle | partial (interval 9m54s) | PASS | RC2: changed-billing event 03:32:50Z → re-login → explicit re-evaluation 03:45:37Z (12m47s); all states, events and ledger identical. Redeploy-in-flight persistence **not tested**. |

## 5. Defect register

| ID | Sev | Found on | Description | Fix | Verified |
|---|---|---|---|---|---|
| D01 | S1 | RC1 | Changed invoice in existing period produced no assurance event | `classify_invoice_event` hashes money-bearing fields (#37) | RC2 browser PASS |
| D02 | S2 | RC1 | UI stale after ingest until reload | `refreshAll()` after every ingest/mutation (#37) | RC2 PASS |
| D03 | S1 | RC1 | "Draft created" shown, nothing persisted (double JSON-stringify → 200 needs_review) | Object bodies; `apiRequest` throws on needs_review for mutations (#37) | RC2 PASS |
| D04 | S1 | RC1 | Draft edits lost silently | same root cause (#37) | RC2 PASS |
| D05 | S1 | RC1 | Execute showed success, action stayed approved | same root cause (#37) | RC2 PASS |
| D06 | S2 | RC1 | Identical amendment re-upload emitted a second event | Contract events classified/hashed on canonical money projection (#37) | RC2 PASS |
| D07 | S2 | RC1 | Foreign child routes returned `200 []` | `_get_finding_or_404` on all finding child routes (#37) | RC2 PASS |
| D08 | S2 | RC1 | Foreign deep link loaded forever | Not-found state (#37) | RC2 PASS |
| D09 | S1 | RC1 | First partial realization closed the case, outstanding $0 | Transition to recovered only when net ≥ requested or settlement (#37) | RC2 PASS |
| D09b | S1 | RC2 | After a partial, `/api/metrics` and Recoveries cards showed $0 recovered / $0 fee while ledger showed $100 / $20 | `success_fee.summarize` and command-center pipeline count events, not status (#38) | RC3 — see §7 |
| D10 | S2 | RC1 | `[object Object]` on structured 409 | `apiErrorMessage()` (#37) | RC2 PASS |
| D11 | S1 | RC1 | Realization history empty after re-login | Fetch events on case/detail render (#37) | RC2 PASS |
| D12 | S2 | RC1 | Case row showed gross, cards showed net | "Net realized" column from ledger (#37) | RC2 PASS |
| D13 | S2 | RC1 | No-card fee button reached Stripe, "Missing email" | 402 before Stripe; button disabled with hint (#37) | RC2 PASS |
| D14 | S2 | RC1 | Raw Gemini `INVALID_ARGUMENT` shown to customer | `UnreadableDocumentError` → finance-readable message, raw error logged server-side (#37) | RC2 PASS (message); status is 200/needs_review by API convention, accepted |
| D15 | S3 | RC1 | `$12,345,678.9` | Two-decimal `Intl.NumberFormat` (#37) | RC2 PASS |
| D16 | S2 | RC1 | Audit actor `ui_approval_by_None` | `_actor(user)` email→uid fallback (#37) | RC2 PASS |
| D17 | S2 | RC1 | Realize control offered on written-off row | Controls only for actionable statuses; Reverse still allowed on recovered (#37) | RC2 PASS |
| D18 | S2 | RC1 | Hero claims comparison against "paid" with no live payment source | Hero/support copy → "billing, usage, credits, and operational records" (#40) | PASS (prod HTML verified) |
| D19 | S1 | Closeout | Fee credit note on reversal rejected by Stripe (paid invoice needs full allocation) | `CreditNote.create(..., refund_amount=fee)` refunds to card (#41) | Closeout rerun PASS (`cn_1UFUMsGdRXoU9c1NhKQCRkK6`, $20 refund) |
| D20 | S1 | Closeout | Date-only realized date → 500 on command-center/case-ledger (naive vs aware datetime), blanking Overview | `_dt()` normalises naive timestamps to UTC (#41) | Closeout rerun PASS |
| D21 | S1 | Closeout | Phantom $1,000 Delta minimum: partial uploaded period filled from connector with empty data, and stale open finding survived re-evaluation | Connector only when neither uploaded record exists; complete re-evaluation withdraws unreproduced `open` findings (`rejected`, `assurance_withdrawn_stale`) — approved+ states and novel rights never touched (#41) | Closeout rerun PASS (4 findings / $2,000) |
| D22 | S1 | Closeout | `POST /api/report/share` 503 — `RECOUP_REPORT_SHARE_SECRET` never provisioned | deploy generates `recoup-report-share-secret` once per project and mounts it (#41) | Closeout rerun PASS (share 200, tampered token 403) |
| D23 | S3 | Closeout rerun | Same-day date-only realization shows −0.3 days to recovery | Not fixed (product frozen); cosmetic | open — documented |
| D24 | S3 | Closeout rerun | Proof stayed visually locked until the next refresh after saving a card | Not fixed (product frozen); resolves on refresh | open — documented |

Open S1/S2 defects: **0**. Open S3 (cosmetic, documented): D23, D24.

## 6. Data hygiene

All synthetic tenants were deleted through Settings → Delete all account data after evidence capture (RC1: A at 02:52:31Z, B and C likewise; RC2: C 03:49:00Z, B 03:50:46Z, A 03:51:06Z), each followed by `GET /api/findings → []` and `GET /api/contracts → {"contracts":[]}`. Application-data deletion does not remove the Firebase Auth users or Stripe objects; none were created for the synthetic tenants (no card, no charge). The single Stripe request from RC1's no-card attempt (`req_6lw8zcYFDxUBJ7`) failed before creating a charge.

## 7. RC3 verification

Deployed 2026-09-14T04:01:57Z. Fresh tenant `recoup-acceptance-58fff02-a-20260914`, Birch fixture ($500 finding), native approve → cash $100 (`RC3-PAY-1`):

- Case: approved, net $100, outstanding $400, `partially_realized`.
- `GET /api/metrics`: `recovered_to_date` 100, `success_fee_to_date` 20, `recovered_count` 1; Recoveries cards match.
- Overview pipeline: Realized $100, In Recovery $400 / 1 case, Approved $0 / 0 cases; persists after reload. (Realized stage *count* shows closed cases only, so it reads 0 while value is $100 — by design, value is the authoritative figure.)
- Settlement $200 (`RC3-SETTLE-1`): recovered, net $300, fee $60, outstanding $0; Overview Realized $300, In Recovery $0.
- Both events report unbilled — "No payment method on file"; no card, no charge. Tenant data deleted, findings/contracts verified empty.

Evidence: `/home/ubuntu/acceptance/RESULTS_RC3.md`, `acceptance/rc3/screenshots/`, `acceptance/rc3/evidence/*.jsonl`, recording `screencasts/recoup-rc3-partial/recoup-rc3-partial-edited.mp4`. No 500s or page exceptions captured.

This report is committed as a docs-only change on top of `58fff02`; the resulting main SHA redeploys identical application code.

## 7a. Launch closeout — card-gated surfaces (validation environment, Stripe TEST mode)

Run on the isolated `recoup-validation` project (revisions `recoup-validation-00007-tff` @ `a897a24`, then `recoup-validation-00008-jqk` @ `d40604d`), Stripe restricted `rk_test_` keys only, card `4242 4242 4242 4242`; no live card, no production data.

| Surface | Result |
|---|---|
| No card: report/PDF/share/true-up → 402, clause proof redacted, fee control gated, status transitions still available | PASS |
| Hosted Stripe setup; Settings shows Visa ····4242; persists across logout/login | PASS |
| Report page, PDF, findings CSV, true-up pack unlock; amounts equal authoritative findings (4 findings, $2,000) | PASS |
| Public share link: create 200, renders unauthenticated, tampered token 403 | PASS (after D22) |
| $500 realization → one paid $100 TEST invoice (`in_1UFULQGdRXoU9c1Ngr4f9DJH`, `pi_3UFULRGdRXoU9c1N0e6TtCF6`); repeated monthly billing → no duplicate | PASS |
| $100 reversal → $400 net / $80 fee; $20 credit note with $20 refund on the fee invoice | PASS (after D19); refund settlement status not readable with the restricted key (`charge_read` missing) — verify once in the Stripe dashboard |
| Tenant B cannot read tenant A private data | PASS |
| Cleanup: tenant A/B app data deleted; Stripe TEST customers deleted | PASS |

Evidence: `acceptance/closeout/RESULTS_CLOSEOUT.md`, `acceptance/closeout/RESULTS_CLOSEOUT_RERUN.md`, recordings `acceptance/closeout/recording.mp4` and `screencasts/recoup-closeout-rerun/recoup-closeout-rerun-edited.mp4`.

## 8. Known limitations (not defects)

- `new_payment` assurance trigger is not emitted: invoices carry no payment field; payments enter via realization events. A payment/AR source is a future connector.
- The 20% billing path was exercised end to end in Stripe TEST mode only; the first live charge will be the first paying customer's.
- Stale-finding withdrawal (D21) is covered by unit tests; the rerun's ingest order produced no transient finding, so the withdrawal branch was not observed in the browser.
- Scanned-image OCR path, Gemini/Firestore/Stripe outage behaviour, and persistence across an in-flight redeploy were not tested.
- The service emits no request-correlation ID; evidence is keyed on server timestamps and tenant IDs.
- Mobile layout was verified on RC1; RC2/RC3 UI changes were verified on desktop only.

## 9. Recommendation

**GO — for a supervised pilot.**

Gate check: 0 blockers; 0 open critical (S1) defects; all 12 core journeys pass on the current production code; financial calculations exact in every scenario ($2,000 baseline, $260/$52 and $300/$60 closed loops, 20% fee, no double counting); lifecycle states persisted across re-login and a 12m47s time-separated re-evaluation; Continuous Assurance demonstrated on invoice, usage and amendment events with idempotent re-uploads; billing correctness (deterministic fee, no-card gate 402); tenant isolation with uniform 404; public UI reflects only real capability with SAMPLE labelling.

Closeout status: all three launch-closeout items are done — D18 copy shipped (#40); card-gated surfaces exercised in Stripe TEST mode (§7a) with the four defects they exposed fixed and re-verified (#41); product frozen at `d40604d`.

**Product freeze.** No further feature work. Changes after this point are limited to security patches, outage fixes, and defects raised by pilot customers. Supervised pilots may begin: every recovery action remains human-approved and no channel sends externally.

One operator follow-up: confirm refund `re_3UFULRGdRXoU9c1N0LS77fyT` settled in the Stripe TEST dashboard (the validation key cannot read refund status).
