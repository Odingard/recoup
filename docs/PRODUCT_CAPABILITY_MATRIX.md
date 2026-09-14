# Product Truth Audit — Capability and UI Matrices

**Repository state audited:** `main` at `6647eb9` (read-only inventory; this document is not committed).

**Scope:** authenticated React application under `web/src/` only; landing pages are excluded. Backend references are to `recoup_agent/` and `recoup_agent/api.py`. “Production supported” means the deployed FastAPI/Cloud Run code path is available for a live tenant; it does not mean that a feature is surfaced in the current UI or that every external dependency is configured.

## 1. PRODUCT CAPABILITY MATRIX

| Capability | Backend module | API endpoint(s) | Persistent object/collection | Customer action | Current UI location (App.jsx screen / component) | Production supported? YES/NO | Notes/limitations |
|---|---|---|---|---|---|---|---|
| Firebase authentication | `api.py:123-145`, Firebase initialization in `api.py`; `web/src/firebase.js`; `App.jsx:167-175,402-412,414-428` | All protected `/api/*` routes through `Depends(verify_token)`; no separate login API | Firebase Auth user; account id is token `account_id` or `uid` (`api.py:311-312`) | Sign in with Google; sign out | Auth shell, `App.jsx:900-934`; header sign-out button `App.jsx:963-965` | YES | Live mode requires `Authorization: Bearer <Firebase ID token>`. Account isolation is token-derived; the UI does not let a user select an account. |
| Explicit sample mode | `api.py:123-145,331-358`; `readiness.py:11-16`; `synthetic_data.py`; `App.jsx:382-400,969-977` | Any protected route accepts `X-Recoup-Sample: 1`; `RECOUP_SAMPLE_MODE` also enables it | None; synthetic/offline book | Click “Try with sample data”; exit sample mode | Auth shell and sample banner (`App.jsx:924-927,969-975`) | YES, explicitly non-production data | Sample responses are not persisted. Several UI controls still render in sample mode but return `not_persisted`/read-only messages; the banner is the only global sample label. |
| Contract/agreement structured entry | `api.py:291-301,1838-1865`; `normalizer.py`; `db.py:174-176` | `POST /api/ingest/contract`; `GET /api/contracts` | `contracts/{customer_id}` | Fill customer, minimum, included units, overage, escalator, discount, and clause quote fields; submit | Agreements structured form `App.jsx:1107-1179`; confirmation list Agreements | YES | This UI path sends already-normalized structured values. The displayed “file” is the selected bulk file name only; structured entry itself is not a PDF upload. |
| Contract document extraction / OCR-like scanned-document handling | `ingestion_doc.py:1+`; `api.py:1541-1648,2038-2051`; `normalizer.py` | `POST /api/ingest/contract/document`; `POST /api/ingest/bulk` for contract files | `contracts`; audit/assurance events; compiled/candidate rights may also be persisted | Upload PDF, DOCX, TXT, Markdown, PNG/JPG/JPEG; review extracted terms | Agreements bulk dropzone `App.jsx:1074-1105`; Agreements confirmation `App.jsx:1272-1350` | YES, with Vertex/Gemini configuration | Accepted upload suffixes are `VALID_UPLOAD_SUFFIXES` (`api.py:329-331`). Scanned PDFs/images are passed through the document extraction path; text-layer detection and page/file limits are enforced. Extraction is model-assisted; normalized fields and money calculations are deterministic. |
| Bulk and ZIP ingestion | `ingest_bulk.py`; `ingest_csv.py`; `api.py:2038-2051`; `api.py:303-307` | `POST /api/ingest/bulk` | `contracts`, `invoices`, `usage`, `audit_log`, `assurance_events` | Select multiple files or a ZIP; inspect per-file result and needs-review list | Agreements dropzone and upload history `App.jsx:1074-1105,463-497` | YES | Limits include `MAX_BULK_FILES=50`, `MAX_BULK_FILE_BYTES=25MB`, scanned-PDF page limit 25 (`api.py:303-307`). ZIP extraction is bounded by bulk limits. |
| Extracted-term confirmation | `db.confirm_contract`; `api.py` contract confirmation route | `POST /api/contracts/{customer_id}/confirm`; `GET /api/contracts` reloads saved contract records | `contracts/{customer_id}` fields `confirmed`, `confirmed_by`, `confirmed_at`; `audit_log` entry `contract_terms_confirmed` | Review displayed values/provenance and click “Confirm terms” | Agreements agreement card | YES | Confirmation is durable, records actor/time, and survives reload. Sample mode returns `not_persisted`. |
| CSV billing ingestion and deterministic column classification | `ingest_csv.py:62-87,223-238,331-343`; `ingest_bulk.py:138-187`; `api.py:2051-2170` | `POST /api/ingest/bulk` | `invoices` | Upload billing CSV; inspect counts/needs review | Agreements bulk upload | YES | Header aliases, CSV kind, row roles and amounts are hardcoded heuristics; no LLM is used. Billing/usage CSVs are classified by aliases and line roles. |
| CSV usage ingestion | `ingest_csv.py`; `ingest_bulk.py`; `api.py:2051-2170` | `POST /api/ingest/bulk` | `usage` | Upload usage CSV | Agreements bulk upload | YES | Deterministic parsing. Identity resolution uses `identity.py` unique exact/suffix-normalized matching; ambiguous/unmatched rows become review items. |
| Customer identity resolution | `identity.py:1+`; callers in `ingest_csv.py:180-185,274-280` | Indirectly `POST /api/ingest/bulk` | Normalized `customer_id` on `contracts`, `invoices`, `usage`; review entries are returned | Provide customer labels/ids in files | Agreements upload result / Opportunities needs-review list | YES | No LLM: normalization, exact/suffix indexes, and unique-hit requirement are deterministic. Ambiguous identities fail to a needs-review path rather than guessing. |
| Stripe read connector | `billing/provider.py`; `billing/stripe_provider.py:93-294`; `billing/connector_keys.py`; `api.py:1868-1874` | `GET /api/connector/stripe/status`; indirect ingestion through connector/provider; `POST /api/billing/sync-recoveries` | Tenant connector credential in account billing/connector storage; imported data in `usage`/`invoices` | Check connection; use Stripe account data for reconciliation | Integrations `App.jsx:1184-1242`; Recoveries “Check Stripe for paid invoices” `App.jsx:1416-1419` | YES if tenant credential is configured | Provider reads customers, subscriptions, metered usage and invoices. Stripe provider is read-only for tenant data; platform billing uses a separate write key. |
| Stripe App OAuth install | `billing/stripe_oauth.py`; `api.py:1874-1893`; `App.jsx:868-889` | `POST /api/connector/stripe/oauth/start`; `GET /api/connector/stripe/oauth/callback` | Tenant connector credential / billing account fields; audit as applicable | Click “Connect with Stripe”, complete Stripe OAuth redirect | Integrations `App.jsx:1219-1226` | YES when OAuth env/secrets and manifest redirect are configured | Sample mode intentionally does not connect. OAuth start requires auth and configured client/secret/state. |
| Reconciliation orchestration | `pipeline.py:1+`; `api.py:598-632`; `book_loader.py` | `POST /api/reconcile?period=YYYY-MM` | `findings`, `audit_log`; assurance may upsert findings on ingest | Choose period and run reconciliation | Header Run evaluation `App.jsx:959-962`; header Run evaluation `App.jsx:1245-1269` | YES | The normal path is deterministic Python reconciliation. The UI routes to Opportunities after completion. |
| Rule 1 — committed minimum not enforced | `reconciliation.py:206-233` | Included in `POST /api/reconcile` and assurance re-evaluation | `findings` type `unenforced_minimum` | Review/approve finding | Opportunities queue/detail | YES | Computes minimum minus billed base; amendment schedules resolved by `minimum_for_period`. Clause quote is required or item becomes needs review. |
| Rule 2 — unbilled overage | `reconciliation.py:235-309` | Included in `POST /api/reconcile` | `findings` type `unbilled_overage` | Review/approve finding | Opportunities | YES | Supports flat `overage_rate` and tiered `overage_tiers`; expected/actual are persisted. |
| Rule 3 — expired discount | `reconciliation.py:311-339`; `book_loader.match_discount` | Included in `POST /api/reconcile` | `findings` type `expired_discount` | Review/approve finding | Opportunities | YES | Matches applied discount to contract discount and detects post-expiry application. |
| Rule 4 — missed annual escalator | `reconciliation.py:341-401` | Included in `POST /api/reconcile` | `findings` type `missed_escalator` | Review/approve finding | Opportunities | YES | Compounds by anniversary. Prorated invoices skip minimum/escalator checks. |
| Rule 5 — post-term billing review | `reconciliation.py:403+` | Included in `POST /api/reconcile` | Needs-review item, not a dollar finding | Confirm term/renewal manually | Opportunities needs-review column | YES | Review-only; no recovery dollars are created. |
| Rule 6 — missing base charge / underbilled seats | `reconciliation.py:403-478` | Included in `POST /api/reconcile` | `findings` types `missing_base_charge` and `underbilled_seats` | Review/approve finding | Opportunities | YES | These are the sixth finding rule family in addition to the four documented at module header. |
| Novel rights discovery and compilation | `rights_discovery/discovery.py`, `verifier.py`, `compiler.py`; upload orchestration `api.py:420-477,1648-1740` | Contract document upload invokes discovery/verification; `POST /api/rights/evaluate` evaluates supplied compiled/right inputs | `candidate_rights`, `compiled_rights`, `observations`, `findings` with `novel:*` types | Upload contract document; optionally evaluate novel right inputs | No dedicated UI; bulk/contract upload indirectly invokes it | YES for live document upload; NO as a first-class UI workflow | Gemini proposes/verifies candidate rights. Compiler fails closed to RightSpec; runtime is deterministic. No frontend control for candidate review/compilation/evaluation. |
| Novel rights evaluate in normal reconcile | `pipeline.py:46-105`; `assurance.py:157-217`; `rights_discovery/runtime.py`; `api.py:1696-1749` | `POST /api/rights/evaluate`; assurance re-evaluation can call `build_novel_findings` for persisted compiled rights | `compiled_rights`, `observations`, `findings` | No current customer UI action | No UI caller | NO as part of ordinary `/api/reconcile` | `/api/reconcile` runs `compute_findings_and_review`/legacy `reconcile`; it does not invoke compiled-right evaluation. Novel evaluation is separate endpoint/eval/assurance path. LLM is absent from runtime calculation; model influence is upstream in candidate spec. |
| Continuous assurance | `assurance.py:22-334`; hooks in `api.py:384-393,1825-1865,2083-2113` | `GET /api/assurance/status` (includes `triggers_monitored` excluding `new_payment`); `GET /api/assurance/events`; `POST /api/assurance/evaluate` | `assurance_events`; account-root `assurance`; `audit_log`; findings upserted | Ingest data; inspect assurance panel; optionally manually evaluate | Overview assurance card (`App.jsx renderOverview`) | YES | Actually emitted triggers: contract create/update via structured and document paths: `new_agreement`, `agreement_amendment`, `contract_renewal`, `term_expiration`, or `pricing_change`; new invoice: `new_invoice` + `new_billing_period`; invoice credits change: `new_credit_refund`; usage ingest: `new_usage`; bulk emits corresponding contract/invoice/usage triggers. **`new_payment` is declared but not emitted** because invoice payload has no payment field. |
| Findings list and pending queue | `api.py:582-596`; `db.py:79-99` | `GET /api/findings/pending`; `GET /api/findings` | `findings` | Open review queue and all findings | Opportunities `App.jsx:1572-1616`; Recoveries lists derived from all findings | YES | Finding data is account-scoped; sample uses synthetic book. |
| Finding lifecycle | `recovery.py:1-27`; `db.py:106-134`; `api.py:634-685,946-1038` | `POST /findings/{id}/approve`; `/reject`; `/invoiced`; `/recovered`; `/disputed`; `/written-off` | `findings`; `audit_log`; `recovery_events` for recovered | Approve/reject, record invoice/payment, dispute, write off | Opportunities and Recoveries | YES | Legal finding transitions: `open→approved|rejected`; `approved→invoiced|recovered|written_off|rejected`; `invoiced→recovered|disputed|written_off`; `disputed→recovered|written_off`. Terminal: rejected/recovered/written_off. `recovered` path creates realization event; `invoiced` records corrective invoice evidence. |
| Finding evidence lock | `api.py:_proof_unlocked`, `_redact_if_locked` and report routes | Finding/report/true-up endpoints | Billing fields on account root + findings | Add payment method to unlock clause proof/reports | Global banner `App.jsx:979-993`; Opportunities evidence view | YES | Locked account keeps case numbers but blanks proof/evidence in relevant responses. UI disables export/report buttons. |
| Recovery action creation | `recovery_actions/models.py`; `api.py:1097-1135` | `POST /api/findings/{finding_id}/recovery-actions` | `recovery_actions`; action audit entries | Choose one of 7 action types and template/model draft mode | `RecoveryActions.jsx:225-237`, in Opportunities and Recovery overview drawer | YES | Allowed types: corrective_invoice, credit_request, rebate_request, reimbursement_request, contractual_notice, counterparty_inquiry, manual_recovery_action. Requires finding status approved/invoiced/disputed. Requested amount is copied from authoritative `monthly_recoverable`; cannot be edited. |
| Recovery action drafting | `recovery_actions/drafting.py`; `rights_discovery.discovery.generate` for optional model wording | Create action with `draft_mode=template|model`; `POST /api/recovery-actions/{id}/draft` | `recovery_actions` draft fields/history | Edit draft wording; save | `RecoveryActions.jsx:112-132` | YES; model mode requires Gemini | Template is deterministic. Model output can only fill text; exact authoritative amount must appear and unexpected dollar figures cause fallback to template. |
| Recovery action approval state machine | `recovery_actions/models.py`, `service.py` | `/submit`, `/approve`, `/reject` | `recovery_actions`, `audit_log` | Submit, approve, reject | `RecoveryActions.jsx:135-163` | YES | States: draft, pending_approval, approved, sent, awaiting_response, disputed, resolved, rejected, written_off. Every transition is checked; approval records actor/time. |
| Recovery action channels | `recovery_actions/adapters.py`; `api.py:1232-1275` | `GET /api/recovery-actions/{id}/preview?channel=`; `POST /.../{id}/execute` | `recovery_actions`; artifacts are response data | Prepare/execute document, email draft, or manual record | `RecoveryActions.jsx:166-181` | YES, with important limitation | Channels: `document` renders PDF only; `email_draft` creates subject/body/draft id and never sends; `manual` requires external reference. Nothing is externally sent by Recoup. Execute requires approved state and unchanged amount. |
| Recovery action outcomes | `recovery_actions/service.py`; `api.py:1277-1280` | `POST /api/recovery-actions/{id}/outcome` | `recovery_actions`, `audit_log`; finding may transition disputed | Record resolved/disputed/written_off and note/reference | `RecoveryActions.jsx:186-220` | YES | Resolved does not create realized money; response points operator to recovery-events. Disputed can cascade an invoiced finding to disputed. |
| Realization events | `billing/realized_value.py`; `api.py:808-868,946-1004` | `POST /api/findings/{id}/recovery-events`; `POST /api/findings/{id}/recovered`; `GET /api/findings/{id}/recovery-events` | `recovery_events`; finding recovery fields; audit log | Record cash/credit/offset/etc. realized value | Recoveries payment form `App.jsx:577-588`; Opportunities detail; linked action select in `App.jsx:654-661` | YES | Immutable event model; realization is the source of realized money. Optional recovery action id is validated against the same account/finding; linked sent/awaiting/disputed actions get history only and are not auto-resolved. |
| Realization reversals | `billing/realized_value.py:new_reversal`; `api.py:870-934` | `POST /api/findings/{id}/recovery-events/{event_id}/reverse` | `recovery_events`; audit/fee adjustment fields | No current UI control; API caller can reverse | No current UI caller | YES backend / NO current UI | Reversal is a new event, bounded by remaining original value; it copies lineage/action id and subtracts from net realized. |
| Realization ledger | `realization_ledger.py`; `command_center.py` | `GET /api/recovery-cases/{id}/ledger`; embedded in `/api/command-center` | Pure projection; no ledger collection | Inspect case economics | Opportunity detail / Recoveries ledger | YES | Numbers are derived from stored finding/actions/events/audit. Integrity reports duplicate IDs or reversal overages rather than raising to UI. |
| Outcome records | `realization_ledger.py:outcome_record`; `db.py:save_outcome_record`; API `_record_outcome` | `GET /api/recovery-cases/{id}/outcome`; writes occur after realization/reversal/key finding transitions/action outcome | `outcome_records/{finding_id}` | No direct customer action; generated by lifecycle writes | No current UI caller | YES backend / NO current UI | Structured, no free text/clause/math/draft. Read route is account-scoped. |
| Success fee / recovery billing gate | `billing/realized_value.py`; `success_fee.py`; `billing/recoup_billing.py`; `api.py:1355-1435` | `GET /api/metrics`; `GET /api/billing/status`; `POST /api/billing/setup-session`; `/setup-complete`; `/sync-recoveries`; `/charge-success-fee` | Account-root billing fields; `recovery_events` fee fields; Stripe invoices/charges | Add card; sync Stripe paid invoices; charge success fee | Recoveries `App.jsx:1390-1429,741-783` | YES if Stripe platform key/card configured | Fee remains deterministic 20% of net realized. Card-on-file/proof lock gates evidence/report access; platform billing uses Connect/Stripe customer/payment method. Connector read key is separate from platform write key. |
| Audit report and PDF | `report.py`; `api.py:1566-1589` | `GET /api/report`; `GET /api/report.pdf`; `POST /api/report/share`; sample report routes | Findings/audit log; signed/share token is transient or stored by implementation | View/share/download report | Recoveries buttons `App.jsx:1407-1414` | YES, proof lock applies | Templated deterministic report/PDF; no LLM-written financial numbers. |
| True-up pack / customer letter | `trueup.py`; `api.py:2003-2021` | `GET /api/trueup/{customer_id}`; `.pdf` | Findings/contracts; generated response/PDF | Choose sender and download letter + schedule | Recoveries `App.jsx:1519-1548` | YES, proof lock applies | Deterministic letter and ReportLab PDF. UI computes customer totals locally for listing; authoritative pack is server-generated. |
| Recovery overview | `command_center.py`; `api.py:1072-1095` | `GET /api/command-center`; `GET /api/command-center/cases/{finding_id}` | Pure projection over findings/events/audit/contracts/actions | Filter, inspect ranked case, open in Opportunities, prepare action | Overview metrics/pipeline/summary; Opportunities queue/detail; rail nav `App.jsx:NAV_ITEMS` | YES | Eight metric tiles, pipeline, executive summary, recovery performance, client-side filters, ranked cases, ledger/action drawer. No client-side money calculation beyond formatting/display conversions. |
| General metrics | `success_fee.py`; `api.py:1040-1048` | `GET /api/metrics` | Findings/recovery events | Inspect recovery/potential metrics | Recoveries metric cards `App.jsx:1380-1403` | YES | Includes recovered-to-date, current month, success fee, potential, invoiced awaiting payment, written off. |
| Recovery metrics | `realization_ledger.py`; `api.py:1342-1353`; command center embeds result | `GET /api/metrics/recovery` | Pure projection | No current direct UI call; embedded in Recovery overview | Recovery overview performance section consumes embedded `executive_summary.recovery_metrics` | YES backend / YES indirectly | Metrics include rate, average time/per-case, right type/customer/action strategy and resolution mix. |
| Rights graph | `rights_graph/service.py`, `adapter.py`, `models.py`; endpoints `api.py:1591-1785` | `GET /api/rights/customers/{customer_id}`; `GET /api/rights/candidates`; `POST /api/observations`; `POST /api/rights/evaluate`; discrepancy case/strategy routes | `candidate_rights`, `compiled_rights`, `observations`; graph rebuilt on read | No current UI action | No web caller | YES backend / NO current UI | Graph is rebuilt from tenant-scoped records and does not store its own collection or calculate dollars independently. Investigation/strategy routes are opt-in and may use Gemini. |
| Account data deletion | `db.delete_account_data`; `api.py:2023-2036` | `DELETE /api/account/data` | Deletes all account subcollections/root, connector/billing data per implementation | Type DELETE and submit | Recoveries danger zone `App.jsx:1551-1567,814-832` | YES | Destructive, explicit confirmation. Must include all current subcollections when adding new persisted objects. |
| Health/version | `api.py:196-204` | `GET /api/health` | None; env `RECOUP_GIT_SHA` | Operator/deployment check | No app UI caller | YES | Public liveness/version output. |
| Readiness/dependencies | `readiness.py`; `api.py:205-210` | `GET /api/ready` | Firestore/Firebase dependency probes; no business collection | Operator/deployment check | No app UI caller | YES | Reports booleans and non-secret details for project, billing key, connector credential, Vertex config, version, Firestore and Firebase Auth. |

### Assurance trigger precision

The declared trigger set is `new_invoice`, `new_billing_period`, `new_usage`, `new_payment`, `new_credit_refund`, `new_agreement`, `agreement_amendment`, `contract_renewal`, `term_expiration`, and `pricing_change` (`assurance.py:22-24`). The live emitters are:

- `POST /api/ingest/usage` → `new_usage` (`api.py:1813-1830`).
- `POST /api/ingest/invoice` → `new_invoice` + `new_billing_period` for a new period, and `new_credit_refund` when `credits_applied` changes (`api.py:1831-1848`; classifier `assurance.py:105-116`). It does **not** emit `new_payment`.
- `POST /api/ingest/contract` and document upload → classifier result among `new_agreement`, `agreement_amendment`, `contract_renewal`, `term_expiration`, `pricing_change` (`api.py:384-393,1850-1865,2038-2051`).
- `POST /api/ingest/bulk` → same classifier-driven contract/invoice/usage events for each persisted record (`api.py:2083-2113`).
- `POST /api/assurance/evaluate` is a manual trigger, not a new source event.

## 2. UI ELEMENT MATRIX

All UI source references below are to the current non-landing app. Values should be treated as source-of-truth only where the listed endpoint/field is real; local display filters and form state are not backend truth.

| UI element | Screen | Exact source of truth (endpoint + field) | Action performed (endpoint) | Allowed states | Supported in production? YES/NO | Verdict |
|---|---|---|---|---|---|---|
| “Sign in with Google” | Auth shell | Firebase Auth session (`onAuthStateChanged`, `App.jsx:167-175`) | Firebase popup sign-in (`App.jsx:402-412`) | signed out / Firebase-authenticated | YES | KEEP |
| “Try with sample data” | Auth shell | `sessionMode=sample`; backend synthetic data | Sets client mode; subsequent calls carry `X-Recoup-Sample: 1` | sample / exited | YES (sample-only) | RELABEL — button is clear, but every mutating sample response should remain visibly labelled |
| “Sign out” | Header | Firebase Auth session | `signOut(auth)`; clears local state | authenticated / signed out | YES | KEEP |
| Session pill | Header | `sessionMode`, Firebase user email | None | Sample data / authenticated email | YES | KEEP |
| Period input | Header / header Run evaluation | Local `billingPeriod`; used as `?period=` | `POST /api/reconcile?period=...` | Free text; backend expects `YYYY-MM` | YES | RELABEL — validate/display “YYYY-MM” and reject malformed input before misleading run state |
| Header “Run reconciliation” | Header | Reconcile response `findings_found`, `needs_review_count` | `POST /api/reconcile` | idle / reconciling / success / error | YES | KEEP |
| Sample-mode banner + “Exit sample mode” | Global | `sessionMode=sample` | Clears local sample session | sample / exited | YES | KEEP |
| Payment-method lock banner | Global | `GET /api/billing/status`: `configured`, `card_on_file` | `POST /api/billing/setup-session` | configured/no card / unlocked | YES | KEEP |
| “Add payment method” | Global | Billing setup response | `POST /api/billing/setup-session`; redirect; callback calls `/billing/setup-complete` | idle / redirecting / saved / cancelled | YES when Stripe billing configured | KEEP |
| Nav: Overview | Left rail | `/command-center`, `/assurance/status` | `#/overview` | active/inactive | YES | KEEP |
| Nav: Opportunities | Left rail | `/command-center`, `/findings/pending` | `#/opportunities` and `#/opportunities/{finding_id}` | active/inactive/detail | YES | KEEP |
| Nav: Agreements | Left rail | `/contracts`, `/renewals`, `/rights/customers/{customer_id}` | `#/agreements` | active/inactive | YES | KEEP |
| Nav: Recoveries | Left rail | `/metrics`, findings, recovery-events, command-center ledgers | `#/recoveries` | active/inactive | YES | KEEP |
| Nav: Integrations | Left rail | `/connector/stripe/status`, OAuth and billing sync endpoints | `#/integrations` | active/inactive | YES | KEEP |
| Nav: Settings | Left rail | `/billing/status`, `/account/data` | `#/settings` | active/inactive | YES | KEEP |
| Continuous Assurance panel: last evaluated | Left rail | `GET /api/assurance/status`: `last_evaluated_at` | None | timestamp / — | YES | KEEP |
| Continuous Assurance panel: next evaluation | Left rail | Hard-coded text “On next billing, usage or agreement event” (`App.jsx:1031-1034`) | None | static text | YES | RELABEL — it omits credit-refund and does not expose manual evaluation; derive from actual trigger list or label as informational |
| Continuous Assurance: open discrepancies | Left rail | `/api/assurance/status`: `open_discrepancies` | None | integer | YES | KEEP |
| Continuous Assurance: needs review | Left rail | `/api/assurance/status`: `needs_review` | None | integer | YES | KEEP |
| Continuous Assurance: source chips | Left rail | `/api/assurance/status`: `sources_monitored` | None | list | YES | KEEP |
| Continuous Assurance: recent event list | Left rail | `/api/assurance/status`: `recent_events[]` fields trigger/customer/period/status | None | event statuses | YES | KEEP |
| Agreements dropzone file chooser | Agreements Upload contracts | Browser selected file(s), response `bulkResult.files` | `POST /api/ingest/bulk` multipart | accepted formats `.pdf,.docx,.txt,.md,.csv,.zip,.png,.jpg,.jpeg`; upload states | YES | KEEP |
| Selected filename chip | Agreements | Local `selectedFileName` | None | selected/empty | YES | KEEP |
| Per-file upload status chip | Agreements | `POST /api/ingest/bulk` response `files[].kind/status/message` | None | backend result status | YES | KEEP |
| Export template links: QuickBooks/Xero/Stripe | Agreements | Static route URLs `GET /api/templates/{system}/invoices.csv` | Browser download; no auth header | available/download | YES | RELABEL — direct links do not carry Firebase auth and may be public/sample-like; verify production access policy |
| Structured customer name / id fields | Agreements | Local `contractDraft`; submitted payload | `POST /api/ingest/contract` | editable text | YES | KEEP |
| Structured minimum/included/overage/escalator/discount fields | Agreements | Local `contractDraft`; submitted payload | `POST /api/ingest/contract` | editable numeric/date/text | YES | KEEP |
| Structured clause quote textareas | Agreements | Local clause fields; persisted contract clauses | `POST /api/ingest/contract` | editable quote text | YES | KEEP |
| “Upload contract” structured button | Agreements | Response and `GET /api/contracts` | `POST /api/ingest/contract` | idle/uploading/success/error | YES | KEEP |
| Integrations Connected/Not connected badge | Integrations | `GET /api/connector/stripe/status`: `connected` | None | connected/not connected | YES | KEEP |
| Read-only Stripe access / outcome-based pricing info boxes | Integrations | Static explanatory copy; pricing references backend fee behavior | None | static | YES | RELABEL — pricing is true, but “tracked on Recovered & billing” is accurate only after event data exists |
| Sample mode info box | Integrations | `isSampleMode` | None | sample-only | YES | KEEP |
| “Connect with Stripe” | Integrations | OAuth response `install_url`/message | `POST /api/connector/stripe/oauth/start` | idle/starting/redirect/error | YES when configured | KEEP |
| Stripe connection status text | Integrations | `/connector/stripe/status`: `connected`, `stripe_account_id`, `message` | None | connected/awaiting/error | YES | KEEP |
| header Run evaluation period hint | header Run evaluation | Local `billingPeriod` | None | current period | YES | KEEP |
| header Run evaluation deterministic pass info box | header Run evaluation | Static copy + actual reconcile endpoint | None | static | YES | KEEP |
| header Run evaluation “Run reconciliation” | header Run evaluation | Reconcile response | `POST /api/reconcile` | idle/running/success/error | YES | KEEP |
| Contract review “Confirmed” badge | Agreements | Local `uploadedContracts[].confirmed` | None; `confirmContract` only changes React state (`App.jsx:499-504`) | confirmed/unconfirmed | NO (not durable) | REMOVE — implies persisted confirmation that backend does not perform |
| Contract review “Needs human review” badge | Agreements | Local confirmed flag and contract list | None | pending/confirmed | YES as local state only | RELABEL — “Not yet confirmed in this session” |
| Extracted contract values/provenance | Agreements | `GET /api/contracts`: normalized fields, `term_meta` | None | value/provenance/confidence | YES | KEEP |
| Opportunities “Needs human review” callout | Opportunities | Local `needsHumanReview` from findings + unconfirmed contracts | None | visible/hidden | YES | KEEP, but includes non-persistent confirmation state |
| Pending Review queue / Action required badge | Opportunities | `GET /api/findings/pending`; each finding fields | Selects local `selectedFinding` | open findings / empty | YES | KEEP |
| Needs-review list | Opportunities | `/reconcile` or bulk response `needs_review[]`: customer/term/reason/suggested_action | None | list/empty | YES | KEEP |
| Finding detail title/customer/id/period/amount | Opportunities | `GET /api/findings` finding fields | None | selected finding | YES | KEEP |
| Finding engine reasoning | Opportunities | finding `detail` | None | text/empty | YES | KEEP |
| Confidence score | Opportunities | finding `confidence_score` | None | numeric percentage | YES | KEEP |
| Exact clause quote/provenance | Opportunities | finding `provenance`/`clause_text`; locked redaction | None | quote/locked/no quote | YES | KEEP |
| “Reject finding” | Opportunities | Response `status` | `POST /api/findings/{id}/reject` | open→rejected; sample not persisted | YES | KEEP |
|| “Approve” | Opportunity detail | Response `status` | `POST /api/findings/{id}/approve` | open→approved; sample not persisted | YES | KEEP |
| “Record payment” in Opportunities | Opportunities | Finding and payment response | `POST /api/findings/{id}/recovered` | eligible finding statuses; sample read-only | YES | KEEP |
| Recoveries metrics: recovered count | Recoveries | `GET /api/metrics` → `recovered_count` | None | integer | YES | KEEP |
| Recoveries metrics: recovered to date | Recoveries | `GET /api/metrics` → `recovered_to_date` | None | currency | YES | KEEP |
| Recoveries metrics: recovered this month | Recoveries | `GET /api/metrics` → `recovered_this_month` | None | currency | YES | KEEP |
| Recoveries metrics: success fee to date | Recoveries | `GET /api/metrics` → `success_fee_to_date` | None | currency | YES | KEEP |
| Recoveries metrics: success fee this month | Recoveries | `GET /api/metrics` → `success_fee_this_month` | None | currency | YES | KEEP |
| Recoveries metrics: invoiced awaiting payment | Recoveries | `GET /api/metrics` → `invoiced_awaiting_payment` | None | currency | YES | KEEP |
| Recoveries metrics: written off | Recoveries | `GET /api/metrics` → `written_off` | None | currency | YES | KEEP |
| Recoveries metrics: potential monthly recoverable | Recoveries | `GET /api/metrics` → `potential_monthly_recoverable` | None | currency | YES | KEEP |
| Export findings CSV | Recoveries | `/findings/export` response | `GET /api/findings/export` | enabled/disabled by proof lock | YES | KEEP |
| Audit report button | Recoveries | Share response URL | `POST /api/report/share` | enabled/disabled; opens URL | YES | KEEP |
| Download PDF button | Recoveries | Report PDF response | `GET /api/report.pdf` | enabled/disabled | YES | KEEP |
| Check Stripe for paid invoices | Recoveries | Sync result `checked`, `recovered`, `needs_connector` | `POST /api/billing/sync-recoveries` | live only; syncing/result | YES | KEEP |
| Bill success fee this month | Recoveries | Billing response `billing.status/message` | `POST /api/billing/charge-success-fee` | idle/status/error | YES when configured | KEEP |
| Card-on-file display | Recoveries | `/billing/status.card_on_file/card_brand/card_last4` | None | hidden/no card/card | YES | KEEP |
| Approved awaiting recovery badge/list | Recoveries | `allFindings` filtered status approved | Record invoice/payment endpoints | approved/empty | YES | KEEP |
| Record invoice | Recoveries | Invoice response and finding status | `POST /api/findings/{id}/invoiced` | approved→invoiced | YES | KEEP |
| Record payment | Recoveries | Payment/realization response | `POST /api/findings/{id}/recovered` | approved/invoiced/disputed→recovered | YES | KEEP |
| Invoiced/disputed badge | Recoveries | finding `status`, corrective invoice fields | None | invoiced/disputed | YES | KEEP |
| Mark disputed | Recoveries | Finding status | `POST /api/findings/{id}/disputed` | invoiced→disputed | YES | KEEP |
| Write off | Recoveries | Finding status | `POST /api/findings/{id}/written-off` | approved/invoiced/disputed→written_off | YES | KEEP |
| Recovered badge/amount/payment ref | Recoveries | finding status/recovered_amount/payment/fee_charge | None | recovered | YES | KEEP |
| Fee charge status line | Recoveries | finding `fee_charge.status/amount/invoice_id` | None | billed/unbilled/error/etc. | YES | KEEP |
| True-up sender input | Recoveries | Local `trueupSender` | Used in true-up URL query | editable/empty | YES | KEEP |
| Download letter + schedule PDF | Recoveries | True-up PDF response | `GET /api/trueup/{customer_id}.pdf?sender=` | proof unlocked/disabled | YES | KEEP |
| Delete all account data | Recoveries danger zone | Delete response `status/message` | `DELETE /api/account/data` after exact `DELETE` prompt | authenticated live only | YES | KEEP |
| Recovery overview case count | Overview | `/command-center.cases.length` | None | integer | YES | KEEP |
| Recovery overview eight metric tiles | Overview | `/command-center.metrics`: potential, verified, needs_review, approved, in_recovery, disputed, realized, written_off | None | currency | YES | KEEP |
| Recovery overview five pipeline stages | Overview | `/command-center.pipeline[]` stage/value/count | None | Potential/Verified/Approved/In Recovery/Realized | YES | KEEP |
| Recovery overview executive summary | Overview | `/command-center.executive_summary` | None | opportunity/realized/rate/avg days/open cases/top sources | YES | KEEP |
| Recovery performance headline metrics | Overview | `/command-center.executive_summary.recovery_metrics` | None | opportunity/realized/rate/avg days/avg per case | YES | KEEP |
| Recovery performance by-right/customer/action tables | Overview | recovery metrics arrays | None | rows/empty | YES | KEEP |
| Recovery overview customer/status/type/agreement/period filters | Overview | `/command-center.filters` | Client-side only | selected/all | YES | KEEP |
| Recovery overview min-value/max-age/min-confidence filters | Overview | Case fields `recoverable_difference`, `age_days`, `confidence` | Client-side only | numeric/empty | YES | KEEP |
| Ranked case table | Overview | `/command-center.cases` and `rank` | Click opens drawer | ranked rows/empty | YES | KEEP |
| Case status display and lock glyph | Overview | case `status`, `locked` | None | finding statuses/locked | YES | RELABEL — uses a literal lock glyph while the rest of the app uses icon components; not a functional issue |
| Opportunity detail Close | Overview | Local selected case | Clears local selection | open/closed | YES | KEEP |
| “Open in review” | Overview | Case `finding_id`; local allFindings | Navigates Opportunities; no endpoint | selected/available | YES | KEEP |
| Recovery-action summary list | Overview drawer | case `recovery_actions[]` | None | action summaries | YES | KEEP |
| RecoveryActions draft editor | Opportunities/7 drawer | `GET /findings/{id}/recovery-actions` action fields | `POST /recovery-actions/{id}/draft` | draft/pending approval editable | YES | KEEP |
| Create recovery action | Opportunities/7 drawer | Created action object | `POST /findings/{id}/recovery-actions` | approved/invoiced/disputed findings only | YES | KEEP |
| Submit for approval | Opportunities/7 drawer | action status/history | `POST /recovery-actions/{id}/submit` | draft→pending_approval | YES | KEEP |
| Approve/reject action | Opportunities/7 drawer | action status/history | `POST /.../{id}/approve` or `/reject` | pending→approved/rejected | YES | KEEP |
| Execute action | Opportunities/7 drawer | action status/channel/external ref | `POST /recovery-actions/{id}/execute` | approved→sent; manual ref required | YES | KEEP — wording correctly says execute, but channels never send email externally |
| Action outcome controls | Opportunities/7 drawer | action outcome/status/history | `POST /recovery-actions/{id}/outcome` | sent/awaiting/disputed→resolved/disputed/written_off | YES | KEEP |
| Action history timeline | Opportunities/7 drawer | action `history[]` | None | chronological events | YES | KEEP |
| Linked recovery action select | Opportunities/6 payment form | `GET /findings/{id}/recovery-actions` | Sends `recovery_action_id` in `/recovered` payload | optional action | YES | KEEP |
| Realization ledger figures | Overview drawer | case `ledger`: potential/requested/realized/reversed/outstanding/shortfall | None | numeric | YES | KEEP |
| Ledger resolution/dispute badges | Overview drawer | case `ledger.resolution_status/dispute_status` | None | open/partially_realized/settled/resolved_full/resolved_unverified/rejected/written_off/disputed | YES | KEEP |
| Ledger dates/bases/reference table/lineage | Overview drawer | case `ledger` fields | None | empty or event rows | YES | KEEP |

### UI truth gaps and hard-coded behavior

- `POST /api/contracts/{customer_id}/confirm` now persists confirmation and writes `contract_terms_confirmed`; the badge is durable.
- The approval action is now labelled “Approve”; `/approve` only changes finding status, and the UI no longer claims invoice drafting.
- The hard-coded “Next evaluation” sentence is removed; the UI displays `triggers_monitored` and offers “Evaluate now”.
- The browser no longer sums true-up totals; each customer row displays only the backend-visible finding count and the backend-generated PDF.
- Template links remain unauthenticated CSV downloads on Agreements/Integrations; verify the intended public/private policy for production.
- The literal “Sample data” UI is present and clear. Sample-only mutations are not always disabled, but backend responses state read-only/not-persisted.

## 3. DEAD / UNUSED FRONTEND CODE AND BACKEND ENDPOINTS WITH NO UI CALLER

### Frontend code with no current backend action or likely dead behavior

- `confirmContract` now calls `POST /api/contracts/{customer_id}/confirm` and persists `confirmed`, `confirmed_by`, and `confirmed_at`.
- `uploadedContracts[].confirmed` is now persisted on `contracts/{customer_id}` and returned by `GET /api/contracts`.
- `recoveryForm.kind === 'invoice'` is useful, but there is no separate invoice list endpoint in the UI; invoice evidence is only visible through finding fields after refresh.
- Agreements now renders `/renewals`; no dead fetch remains.
- The Overview assurance card uses `/assurance/status`; `GET /assurance/events` remains a backend-only history route.
- `RecoveryActions.jsx` still does not call `GET /recovery-actions/{id}/preview`; the backend route remains an API-only preview.
- The Recovery overview uses `onOpenInReview` but does not use its own case-detail endpoint; it uses the bulk `/command-center` response.
- `RecoveryActionSelect` hides itself when no actions exist; there is no explicit “none found” state, which is acceptable but obscures linkage availability.

### Backend endpoints with no current UI caller (directly or indirectly)

The current web caller set is the paths in `App.jsx:211-310,449-473,509,528,566-614,691-821,876`, plus `App.jsx`/`RecoveryActions.jsx`. The following deployed routes have no current caller in `web/src`:

- `GET /api/health`, `GET /api/ready` — operator/deployment probes.
- `GET /api/findings/{finding_id}/recovery-events` — UI posts events but does not list the endpoint directly.
- `POST /api/findings/{finding_id}/recovery-events` and `POST .../reverse` — current UI uses `/recovered` for payment; no generic realization/reversal form.
- `GET /api/recovery-actions` (all account), `GET /api/recovery-actions/{id}`, `GET /api/recovery-actions/{id}/preview` — UI lists by finding and does not preview/get by id.
- `GET /api/recovery-cases/{id}/ledger`, `GET /api/recovery-cases/{id}/outcome`, `GET /api/metrics/recovery` — ledger and metrics are embedded in `/command-center`; outcome is not read by UI.
- `POST /api/billing/setup-complete` is called by the URL callback code (`App.jsx:346`) rather than `apiRequest`, so it is a direct caller but not a normal button caller.
- `GET /api/billing/status`, `/api/billing/setup-session`, `/api/billing/sync-recoveries`, `/api/billing/charge-success-fee` are called by Recoveries/global UI and are not dead.
- `GET /api/report`, `GET /api/report/{account_id}/{token}`, `GET /report/sample`, `GET /report/sample.pdf` — report/share/download UI uses `/report/share` and `/report.pdf`; public/sample report variants have no app caller.
- `GET /api/rights/customers/{customer_id}`, `GET /api/rights/candidates`, `POST /api/observations`, `POST /api/rights/evaluate`, `GET /api/rights/discrepancies/{id}/case`, `GET /api/rights/discrepancies/{id}/strategy` — rights graph/discovery APIs have no caller in current web app.
- `GET /api/assurance/events`, `POST /api/assurance/evaluate` — sidebar uses only `/assurance/status`.
- `GET /api/contracts` is called by Agreements; `GET /api/renewals` is called but result is not rendered.
- `POST /api/ingest/usage`, `POST /api/ingest/invoice`, `POST /api/ingest/contract/document` — bulk/structured upload UI does not call these individually; they are used by integrations, API clients, or internal orchestration.
- `GET /api/templates/{system}/{kind}.csv` has direct unauthenticated anchor callers, but not `apiRequest`.
- `GET /api/trueup/{customer_id}` HTML route has no caller; UI downloads only `.pdf`.
- `POST /api/rights/evaluate` and ADK/agent tools are separate from the browser application.

“Dead” here means “no current browser caller,” not necessarily unreachable: integration clients, operations, tests, Stripe callbacks, ADK, and external customers may use these routes.

## 4. APP UI TEXT USING “AI” / “LLM”

No matching `AI`, `LLM`, or `Gemini` product wording was found in the rendered non-landing React components (`App.jsx`, `RecoveryActions.jsx`) at this revision. The app uses “Suggested wording” for model-generated recovery drafts (`RecoveryActions.jsx:226-230`) without labelling the source as AI/model. Backend/docs still contain technical AI/LLM references, including `rights_discovery/__init__.py:1-5`, but those are not current app UI text.

## 5. FIRESTORE COLLECTIONS PER ACCOUNT

`db.py` uses `_account_root(db, account_id) = accounts/{account_id}` (`db.py:22-30`). The currently enumerated account-scoped collections are:

| Collection under `accounts/{account_id}` | Evidence | Contents |
|---|---|---|
| `findings` | `db.py:36-95` | Reconciliation findings, statuses, evidence, expected/actual, recovery fields |
| `usage` | `db.py:166-180` | Normalized usage records keyed customer/period |
| `invoices` | `db.py:170-184` | Normalized billing/invoice records keyed customer/period |
| `contracts` | `db.py:174-187` | Normalized agreements/terms |
| `audit_log` | `db.py:106-159,206-213,318-322,369-374,416-422` | Finding/action/assurance/recovery audit entries |
| `candidate_rights` | `db.py:240-262` | LLM-proposed/verified candidate rights and provenance |
| `compiled_rights` | `db.py:264-279` | Deterministically compiled RightSpec records |
| `observations` | `db.py:281-297` | Novel-right runtime observations |
| `recovery_events` | `db.py:301-347` | Immutable realization and reversal events, fees, lineage |
| `assurance_events` | `db.py:348-364` | Idempotent continuous-assurance change events |
| `recovery_actions` | `db.py:385-427` | Governed recovery action documents, drafts, statuses, history |
| `outcome_records` | `db.py:430-436` | Structured closed-loop outcome projections keyed by finding |

The account root document also stores fields such as `billing` and `assurance` (`db.py:195-203,369-383`); these are **not collections**. Firestore’s top-level `_readiness/probe` used by readiness probing (`readiness.py:34-46`) is outside tenant account roots. `delete_account_data` iterates all subcollections beneath the account root (`db.py:216-239`), so new collections are included in deletion by traversal.

## Audit conclusion — REMOVE verdicts and gaps

**Post-restructure REMOVE verdict:** the former local-only confirmation UI and hard-coded assurance sentence were removed/replaced with real backend-backed surfaces. The approval label no longer claims invoice drafting. `/renewals` is now rendered. Remaining gaps are API-only surfaces rather than fabricated UI.

**Primary remaining product-truth gaps:**

1. Standalone outcome records and `/api/metrics/recovery` remain backend-only; Overview consumes embedded projections.
2. Rights graph investigation/strategy/observation and compiled-right evaluation remain backend/API-only; Agreements only displays read-only rights.
3. Recovery-action `email_draft` and `document` channels prepare artifacts; they do not send externally.
4. `/api/assurance/events` and command-center case detail remain backend-only; the UI uses status and embedded case data.
5. Net customer value is omitted because `/api/metrics` does not return it.
6. Browser UI remains hash-routed and does not use server-side routes beyond `/app`.
