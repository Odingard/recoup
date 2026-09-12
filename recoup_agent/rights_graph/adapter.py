"""Domain adapters: project a domain's raw records into the Rights Graph.

The B2B adapter works on the existing contract/usage/invoice dicts that
feed `reconciliation.reconcile`. It never computes a dollar amount itself:
expected/actual/recoverable amounts are copied from reconcile() findings and
invoice/usage fields; the reconciliation engine stays the sole calculator.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Protocol

from ..book_loader import match_discount
from ..reconciliation import CONFIDENCE_THRESHOLD, minimum_for_period, reconcile
from .ids import stable_id
from .models import (
    AuthoritySource, Discrepancy, EvaluationMode, EvidenceReference,
    ExpectedState, FinancialRight, Observation, RecoveryAction,
    RecoveryOutcome, RightsGraph, RightStatus, ReviewStatus,
)

FINDING_TYPE_TO_RIGHT = {
    "unenforced_minimum": "committed_minimum",
    "missing_base_charge": "committed_minimum",
    "unbilled_overage": "usage_overage",
    "expired_discount": "discount_expiration",
    "missed_escalator": "annual_escalator",
    "underbilled_seats": "committed_seat_charge",
}

FINDING_TYPE_TO_OBSERVATION = {
    "unenforced_minimum": "base_amount_billed",
    "missing_base_charge": "base_amount_billed",
    "unbilled_overage": "overage_billed",
    "expired_discount": "discount_applied",
    "missed_escalator": "base_amount_billed",
    "underbilled_seats": "seat_count_billed",
}

_TRIGGER = {
    "committed_minimum": "billing period invoice issued",
    "usage_overage": "usage above included tier",
    "discount_expiration": "discount applied past its expiry date",
    "annual_escalator": "contract escalator anniversary reached",
    "committed_seat_charge": "billing period invoice issued",
}

_DESCRIPTION = {
    "committed_minimum": "Customer is committed to a monthly minimum charge",
    "usage_overage": "Usage above the included tier is billable at the contract rate",
    "discount_expiration": "A contractual discount stops applying after its expiry date",
    "annual_escalator": "Contract price escalates annually from its effective date",
    "committed_seat_charge": "Customer is committed to a minimum seat count",
}

# right_type -> (contract fields that must all be present,
#                term_meta fields whose confidences are min()'d,
#                clause key for evidence fallback)
_RIGHT_SPEC = {
    "committed_minimum": (("committed_minimum_monthly", "minimum_schedule"),
                          ("committed_minimum_monthly",), "committed_minimum"),
    "usage_overage": (("included_units", "overage_rate", "overage_tiers"),
                      ("included_units", "overage_rate", "overage_tiers"), "overage"),
    "discount_expiration": (("discounts",), ("discounts",), "discount"),
    "annual_escalator": (("annual_escalator_pct", "escalator_effective_date"),
                         ("annual_escalator_pct", "escalator_effective_date"), "escalator"),
    "committed_seat_charge": (("committed_seats", "seat_price"),
                              ("committed_seats", "seat_price"), "seats"),
}

# Observation type a right needs before it can be evaluated for a period.
# Absence is an observability gap (not_evaluable), never a contractual problem.
REQUIRED_OBSERVATION = {
    "committed_minimum": "base_amount_billed",
    "usage_overage": "usage_measured",
    "discount_expiration": "invoice_issued",
    "annual_escalator": "base_amount_billed",
    "committed_seat_charge": "seat_count_billed",
}


def _proj_committed_minimum(finding, contract, invoice, period, obs):
    """expected = the engine's own resolved minimum for this period."""
    return minimum_for_period(contract, period)[0], invoice.get("base_charge"), {}


def _proj_unbilled_overage(finding, contract, invoice, period, obs):
    """Engine identity: amount = expected_overage - billed_overage."""
    actual = invoice.get("overage_charge") or 0.0
    expected = round(actual + finding["monthly_recoverable"], 2)
    return expected, actual, {
        "relation": "expected_overage = billed_overage + recoverable (engine identity)"}


def _proj_expired_discount(finding, contract, invoice, period, obs):
    """Nothing is owed as a deduction after expiry: expected = 0."""
    return 0.0, (obs.amount if obs is not None else None), {}


def _proj_missed_escalator(finding, contract, invoice, period, obs):
    """expected_base/baseline are engine internals; do not recompute."""
    return None, None, {
        "escalator_steps": finding.get("escalator_steps"),
        "base_charge_billed": invoice.get("base_charge"),
        "lossless": False,
        "see": "finding.math",
    }


def _proj_underbilled_seats(finding, contract, invoice, period, obs):
    """billed seats x seat price would be a formula; carry the quantity."""
    return None, None, {
        "actual_seat_count_billed": obs.quantity if obs is not None
        else invoice.get("seat_units"),
    }


_PROJECTIONS = {
    "unenforced_minimum": _proj_committed_minimum,
    "missing_base_charge": _proj_committed_minimum,
    "unbilled_overage": _proj_unbilled_overage,
    "expired_discount": _proj_expired_discount,
    "missed_escalator": _proj_missed_escalator,
    "underbilled_seats": _proj_underbilled_seats,
}


def _term_conf(contract: dict, field: str) -> float:
    return float(contract.get("term_meta", {}).get(field, {}).get("confidence", 1.0))


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


class DomainAdapter(Protocol):
    """Contract every domain (B2B SaaS today; healthcare/royalties/etc. later)
    must implement to participate in the Rights Graph."""

    def extract_rights(self, source_record: dict, account_id: str | None):
        """-> (AuthoritySource, [EvidenceReference], [FinancialRight], [needs_review dict])"""
        ...

    def normalize_observations(self, source_record: dict, usage: dict, invoice: dict,
                               period: str, account_id: str | None) -> list[Observation]:
        ...

    def evaluate(self, source_record: dict, usage: dict, invoice: dict, period: str,
                 account_id: str | None, mode: EvaluationMode = EvaluationMode.audit,
                 needs_review: list | None = None):
        """-> (findings, RightsGraph). Money math is delegated to the domain's
        authoritative calculator (reconcile for B2B); adapters only link it."""
        ...

    def build_recovery_context(self, finding: dict, discrepancy_id: str):
        """-> ([RecoveryAction], [RecoveryOutcome]) projected from the finding's
        legacy lifecycle fields."""
        ...


class B2BContractAdapter:
    """Rights Graph projection for the B2B SaaS contract book."""

    # ---- extraction -------------------------------------------------------

    def _evidence(self, contract: dict, source_id: str, field_name: str,
                  clause_key: str | None, quoted_text: str,
                  confidence: float | None) -> EvidenceReference | None:
        if not (quoted_text or "").strip():
            return None
        has_meta = field_name in (contract.get("term_meta") or {})
        return EvidenceReference(
            evidence_id=stable_id("ev", source_id, field_name, quoted_text),
            source_id=source_id,
            locator=f"contract.{field_name}",
            quoted_text=quoted_text,
            confidence=confidence,
            extraction_method="llm_extraction" if has_meta else "structured",
            content_hash=sha256(quoted_text.encode()).hexdigest(),
        )

    def _quote(self, contract: dict, field_name: str,
               clause_key: str | None, item_provenance: str = "") -> str:
        return (
            (contract.get("term_meta", {}).get(field_name, {}) or {}).get("provenance")
            or (contract.get("clauses", {}) or {}).get(clause_key)
            or item_provenance
            or ""
        )

    def _make_right(self, contract, account_id, source_id, right_type,
                    required_present: bool, evidence: list, conf_fields,
                    dates_ok: bool, inputs: dict, id_part: str,
                    needs_review: list[dict], metadata=None) -> FinancialRight:
        refs = [e.evidence_id for e in evidence]
        conf = min((_term_conf(contract, f) for f in conf_fields), default=1.0)
        active = (required_present and any(
                    e is not None and e.quoted_text.strip() for e in evidence)
                  and conf >= CONFIDENCE_THRESHOLD and dates_ok)
        if not active:
            missing = []
            if not required_present:
                missing.append("required contract term missing")
            if not any(e is not None and e.quoted_text.strip() for e in evidence):
                missing.append("no clause quote could be cited to ground it")
            if conf < CONFIDENCE_THRESHOLD:
                missing.append(f"term confidence {conf:.2f} below {CONFIDENCE_THRESHOLD}")
            if not dates_ok:
                missing.append("required effective/expiry date missing or unparseable")
            needs_review.append({
                "customer_id": contract["customer_id"],
                "customer_name": contract.get("customer_name"),
                "term": right_type,
                "reason": f"right '{right_type}' not enforceable: {'; '.join(missing)}",
                "suggested_action": f"Confirm the {right_type} terms in the signed "
                                    "contract, correct them in Step 4, and re-run "
                                    "reconciliation.",
            })
        return FinancialRight(
            right_id=stable_id("rt", account_id, source_id, right_type, id_part),
            account_id=account_id,
            source_id=source_id,
            holder_party_id=account_id,
            obligor_party_id=contract["customer_id"],
            right_type=right_type,
            description=_DESCRIPTION[right_type],
            effective_from=contract.get("term_start") or contract.get("effective_date"),
            effective_until=contract.get("term_end"),
            trigger_definition=_TRIGGER[right_type],
            calculation_rule=right_type,
            calculation_inputs=inputs,
            evidence_refs=[r for r in refs],
            extraction_confidence=conf,
            review_status=ReviewStatus.confirmed.value if active
            else ReviewStatus.needs_review.value,
            status=RightStatus.active.value if active else RightStatus.inactive.value,
            metadata=metadata or {},
        )

    def extract_rights(self, contract: dict, account_id: str | None):
        cid = contract["customer_id"]
        source_id = stable_id("src", account_id, cid,
                              contract.get("contract_id") or "contract")
        now = datetime.now(timezone.utc).isoformat()
        source = AuthoritySource(
            source_id=source_id,
            account_id=account_id,
            source_type="contract",
            external_reference=contract.get("contract_id"),
            counterparty_id=cid,
            effective_date=contract.get("effective_date") or contract.get("term_start"),
            expiration_date=contract.get("term_end"),
            document_hash=contract.get("document_hash"),
            ingestion_timestamp=now,
            status="active",
        )

        evidence: list[EvidenceReference] = []
        rights: list[FinancialRight] = []
        needs_review: list[dict] = []

        def add_evidence(field_name, clause_key, quoted, conf):
            ev = self._evidence(contract, source_id, field_name, clause_key, quoted, conf)
            if ev is not None:
                evidence.append(ev)
            return [ev] if ev is not None else []

        # committed_minimum
        has_minimum = (contract.get("committed_minimum_monthly") is not None
                       or bool(contract.get("minimum_schedule")))
        if has_minimum:
            ev = add_evidence(
                "committed_minimum_monthly", "committed_minimum",
                self._quote(contract, "committed_minimum_monthly", "committed_minimum"),
                _term_conf(contract, "committed_minimum_monthly"))
            rights.append(self._make_right(
                contract, account_id, source_id, "committed_minimum",
                required_present=has_minimum, evidence=ev,
                conf_fields=("committed_minimum_monthly",), dates_ok=True,
                inputs={
                    "committed_minimum_monthly": contract.get("committed_minimum_monthly"),
                    "minimum_schedule": contract.get("minimum_schedule"),
                },
                id_part="committed_minimum_monthly", needs_review=needs_review,
                metadata={"minimum_schedule": contract.get("minimum_schedule")}
                if contract.get("minimum_schedule") else {}))

        # usage_overage
        has_overage = (contract.get("included_units") is not None
                       and (contract.get("overage_rate") is not None
                            or bool(contract.get("overage_tiers"))))
        if contract.get("included_units") is not None or has_overage:
            tiers = contract.get("overage_tiers")
            rate_field = "overage_tiers" if tiers else "overage_rate"
            item_prov = ""
            if tiers:
                item_prov = next((t.get("provenance") for t in tiers if t.get("provenance")), "")
            ev = add_evidence(
                rate_field, "overage",
                self._quote(contract, rate_field, "overage", item_prov),
                min(_term_conf(contract, "included_units"),
                    _term_conf(contract, rate_field)))
            rights.append(self._make_right(
                contract, account_id, source_id, "usage_overage",
                required_present=has_overage, evidence=ev,
                conf_fields=("included_units", rate_field), dates_ok=True,
                inputs={
                    "included_units": contract.get("included_units"),
                    "overage_rate": contract.get("overage_rate"),
                    "overage_tiers": contract.get("overage_tiers"),
                },
                id_part=rate_field, needs_review=needs_review))

        # discount_expiration: one right per discount with an expiry
        for d in contract.get("discounts") or []:
            if not d.get("expires"):
                continue
            name = d.get("name") or "discount"
            ev = add_evidence(
                "discounts", "discount",
                d.get("provenance") or self._quote(contract, "discounts", "discount"),
                _term_conf(contract, "discounts"))
            dates_ok = _parse_date(d.get("expires")) is not None
            rights.append(self._make_right(
                contract, account_id, source_id, "discount_expiration",
                required_present=True, evidence=ev,
                conf_fields=("discounts",), dates_ok=dates_ok,
                inputs={"discount": dict(d)},
                id_part=name, needs_review=needs_review,
                metadata={"discount_name": name, "expires": d.get("expires")}))

        # annual_escalator
        if contract.get("annual_escalator_pct"):
            ev = add_evidence(
                "annual_escalator_pct", "escalator",
                self._quote(contract, "annual_escalator_pct", "escalator"),
                min(_term_conf(contract, "annual_escalator_pct"),
                    _term_conf(contract, "escalator_effective_date")))
            has_date = bool(contract.get("escalator_effective_date"))
            rights.append(self._make_right(
                contract, account_id, source_id, "annual_escalator",
                required_present=has_date, evidence=ev,
                conf_fields=("annual_escalator_pct", "escalator_effective_date"),
                dates_ok=_parse_date(contract.get("escalator_effective_date")) is not None,
                inputs={
                    "annual_escalator_pct": contract.get("annual_escalator_pct"),
                    "escalator_effective_date": contract.get("escalator_effective_date"),
                },
                id_part="annual_escalator_pct", needs_review=needs_review))

        # committed_seat_charge
        if contract.get("committed_seats") is not None or contract.get("seat_price") is not None:
            ev = add_evidence(
                "committed_seats", "seats",
                self._quote(contract, "committed_seats", "seats"),
                min(_term_conf(contract, "committed_seats"),
                    _term_conf(contract, "seat_price")))
            rights.append(self._make_right(
                contract, account_id, source_id, "committed_seat_charge",
                required_present=bool(contract.get("committed_seats")) and
                                 contract.get("seat_price") is not None,
                evidence=ev,
                conf_fields=("committed_seats", "seat_price"), dates_ok=True,
                inputs={
                    "committed_seats": contract.get("committed_seats"),
                    "seat_price": contract.get("seat_price"),
                },
                id_part="committed_seats", needs_review=needs_review))

        return source, evidence, rights, needs_review

    # ---- observations -----------------------------------------------------

    def normalize_observations(self, contract: dict, usage: dict, invoice: dict,
                               period: str, account_id: str | None) -> list[Observation]:
        cid = contract["customer_id"]
        out: list[Observation] = []

        def add(obs_type, record, field_name, amount=None, quantity=None,
                unit=None, ext_ref=None):
            out.append(Observation(
                observation_id=stable_id("obs", account_id, cid, period,
                                         obs_type, ext_ref or ""),
                account_id=account_id,
                observation_type=obs_type,
                party_id=cid,
                counterparty_id=account_id,
                period=period,
                amount=amount,
                quantity=quantity,
                unit=unit,
                source_system=invoice.get("source") or "upload",
                external_reference=ext_ref,
                evidence={"record": record, "field": field_name},
            ))

        if invoice.get("base_charge") is not None:
            add("base_amount_billed", "invoice", "base_charge",
                amount=invoice["base_charge"])
        if invoice.get("overage_charge") is not None:
            add("overage_billed", "invoice", "overage_charge",
                amount=invoice["overage_charge"])
        for d in invoice.get("discounts_applied") or []:
            add("discount_applied", "invoice", "discounts_applied",
                amount=d.get("amount"), ext_ref=d.get("name"))
        for c in invoice.get("credits_applied") or []:
            add("credit_applied", "invoice", "credits_applied",
                amount=c.get("amount"), ext_ref=c.get("description"))
        if usage.get("units") is not None:
            add("usage_measured", "usage", "units",
                quantity=usage["units"], unit=usage.get("metric"))
        if invoice.get("seat_units") is not None:
            add("seat_count_billed", "invoice", "seat_units",
                quantity=invoice["seat_units"], unit="seats")
        if invoice:
            add("invoice_issued", "invoice", "total",
                amount=invoice.get("total"),
                ext_ref=invoice.get("invoice_id") or invoice.get("ref"))
        return out

    # ---- evaluation -------------------------------------------------------

    def _find_right(self, rights, right_type, name=None):
        candidates = [r for r in rights if r.right_type == right_type]
        if name is not None:
            for r in candidates:
                if r.metadata.get("discount_name") == name:
                    return r
        return candidates[0] if candidates else None

    def link_findings(self, contract: dict, usage: dict, invoice: dict,
                      period: str, account_id: str | None,
                      findings: list[dict],
                      needs_review: list | None = None) -> RightsGraph:
        """Steps 2-7 of evaluate(): build graph entities for ALREADY-COMPUTED
        findings. Never calls reconcile and never invents amounts."""
        graph = RightsGraph()
        source, evidence, rights, right_reviews = self.extract_rights(contract, account_id)
        graph.sources = [source]
        graph.evidence = evidence
        graph.rights = rights
        graph.needs_review.extend(right_reviews)
        graph.observations = self.normalize_observations(
            contract, usage, invoice, period, account_id)

        # Valid but not evaluable this period: required observation absent.
        observed_types = {o.observation_type for o in graph.observations}
        for right in rights:
            if right.status != RightStatus.active.value:
                continue
            required = REQUIRED_OBSERVATION.get(right.right_type)
            if required and required not in observed_types:
                graph.not_evaluable.append({
                    "right_id": right.right_id,
                    "right_type": right.right_type,
                    "customer_id": contract["customer_id"],
                    "period": period,
                    "missing_observation": required,
                    "reason": f"right is valid but not evaluable for this period: "
                              f"no {required} observation",
                })

        # Pair each expired_discount finding with the applied discount that
        # produced it, mirroring reconcile()'s iteration order.
        discounts = contract.get("discounts") or []
        by_name = {d.get("name"): d for d in discounts}
        period_d = _parse_date(period + "-01")
        expired_applied = []
        for applied in invoice.get("discounts_applied") or []:
            d = by_name.get(match_discount(discounts, applied["name"]))
            exp = _parse_date(d.get("expires")) if d else None
            if d and exp and period_d and period_d > exp:
                expired_applied.append((applied, d))
        expired_iter = iter(expired_applied)

        now = datetime.now(timezone.utc).isoformat()
        for finding in findings:
            if finding.get("customer_id") != contract["customer_id"] or \
                    finding.get("period", period) != period:
                continue
            right_type = FINDING_TYPE_TO_RIGHT.get(finding["type"])
            if right_type is None:
                continue
            project = _PROJECTIONS.get(finding["type"])
            if project is None:
                continue
            obs_type = FINDING_TYPE_TO_OBSERVATION[finding["type"]]
            discount_name = None
            obs = None
            if right_type == "discount_expiration":
                applied, d = next(expired_iter, (None, None))
                if d is not None:
                    discount_name = d.get("name")
                obs = next((o for o in graph.observations
                            if o.observation_type == "discount_applied"
                            and o.external_reference == (applied or {}).get("name")), None)
            else:
                obs = next((o for o in graph.observations
                            if o.observation_type == obs_type), None)
            right = self._find_right(rights, right_type, discount_name)
            if right is None or right.status != RightStatus.active.value:
                if needs_review is not None:
                    needs_review.append({
                        "customer_id": contract["customer_id"],
                        "customer_name": contract.get("customer_name"),
                        "term": right_type,
                        "reason": "finding computed but supporting right failed gating",
                    })
                continue

            expected, actual, extra_inputs = project(finding, contract, invoice,
                                                     period, obs)
            inputs = dict(right.calculation_inputs)
            inputs.update(extra_inputs)
            inputs["period"] = period
            if finding.get("escalator_steps") is not None:
                inputs["escalator_steps"] = finding["escalator_steps"]
            if finding.get("overage_tiers") is not None:
                inputs["overage_tiers"] = finding["overage_tiers"]
            trace = {
                "rule": finding["type"],
                "right_type": right_type,
                "inputs": inputs,
                "formula": finding["math"],
                "result": finding["monthly_recoverable"],
                "currency": "USD",
                "engine": "recoup_agent.reconciliation.reconcile",
                "confidence": finding["confidence_score"],
            }

            actual_obs_ids = [obs.observation_id] if obs is not None else []
            expected_state = ExpectedState(
                expected_state_id=stable_id("exp", right.right_id, period),
                right_id=right.right_id,
                period=period,
                expected_amount=expected,
                currency="USD",
                deterministic_rule=right.calculation_rule,
                calculation_trace=trace,
                input_observation_ids=actual_obs_ids,
                generated_at=now,
            )
            graph.expected_states.append(expected_state)
            discrepancy = Discrepancy(
                discrepancy_id=stable_id("dsc", right.right_id, period,
                                         ",".join(sorted(actual_obs_ids)),
                                         finding["type"]),
                right_id=right.right_id,
                expected_state_id=expected_state.expected_state_id,
                actual_observation_ids=actual_obs_ids,
                discrepancy_type=finding["type"],
                expected_amount=expected,
                actual_amount=actual,
                recoverable_amount=finding["monthly_recoverable"],
                calculation_trace=trace,
                confidence=finding.get("confidence_score"),
                status="open",
                finding_id=finding["finding_id"],
            )
            graph.discrepancies.append(discrepancy)
            finding["right_id"] = right.right_id
            finding["expected_state_id"] = expected_state.expected_state_id
            finding["discrepancy_id"] = discrepancy.discrepancy_id
        return graph

    def evaluate(self, contract: dict, usage: dict, invoice: dict, period: str,
                 account_id: str | None, mode: EvaluationMode = EvaluationMode.audit,
                 needs_review: list | None = None):
        """audit and preflight are currently identical: reconcile() computes the
        findings, link_findings() projects them into the graph."""
        nr = needs_review if needs_review is not None else []
        findings = reconcile(contract, usage, invoice, period, needs_review=nr)
        graph = self.link_findings(contract, usage, invoice, period,
                                   account_id, findings, needs_review=nr)
        return findings, graph

    # ---- recovery projection ----------------------------------------------

    _ACTION_STATUS = {
        "open": "proposed", "approved": "approved", "invoiced": "executed",
        "recovered": "executed", "disputed": "executed",
        "written_off": "closed", "rejected": "rejected",
    }

    def build_recovery_context(self, finding: dict, discrepancy_id: str):
        """One corrective-invoice RecoveryAction per discrepancy regardless of
        finding status; a RecoveryOutcome only once the lifecycle resolves."""
        status = finding.get("status", "open")
        corrective = finding.get("corrective_invoice") or {}
        action = RecoveryAction(
            action_id=stable_id("act", discrepancy_id, "corrective_invoice"),
            discrepancy_id=discrepancy_id,
            action_type="corrective_invoice",
            proposed_at=finding.get("created_at"),
            approved_at=finding.get("approved_at"),
            executed_at=corrective.get("date"),
            status=self._ACTION_STATUS.get(status, "proposed"),
            human_approval_required=True,
            external_reference=corrective.get("ref"),
            metadata={"finding_status": status},
        )
        outcomes = []
        outcome_type = resolution = None
        if status == "recovered":
            outcome_type = "recovered"
            recovered = finding.get("recovered_amount")
            expected = finding.get("monthly_recoverable")
            if (recovered is not None and expected is not None
                    and recovered < expected - 0.005):
                outcome_type = "partially_recovered"
        elif status == "disputed":
            outcome_type, resolution = "disputed", "pending"
        elif status == "written_off":
            outcome_type, resolution = "written_off", "written_off"
        elif status == "rejected":
            outcome_type, resolution = "rejected", "rejected_by_reviewer"
        if outcome_type:
            outcomes.append(RecoveryOutcome(
                outcome_id=stable_id("out", action.action_id, outcome_type),
                action_id=action.action_id,
                discrepancy_id=discrepancy_id,
                outcome_type=outcome_type,
                amount_recovered=_net_recovered(finding),
                resolved_at=None if status == "disputed"
                else finding.get("recovered_at"),
                resolution=resolution,
                evidence=finding.get("payment") or {},
            ))
        return [action], outcomes


def _net_recovered(finding: dict):
    """Net realized value when recovery events are attached to the finding;
    otherwise the legacy recovered_amount field (which the event endpoints
    already maintain as net)."""
    events = finding.get("recovery_events")
    if events:
        from ..billing.realized_value import net_realized
        return net_realized(events)
    return finding.get("recovered_amount")


class NovelRightsAdapter:
    """Projects AI-discovered compiled rights and runtime evaluations into the
    Rights Graph. Everything it emits is already compiled/evaluated
    deterministically by recoup_agent.rights_discovery — this adapter only
    shapes entities."""

    def extract_rights(self, compiled, account_id: str | None):
        """compiled: CompiledRight or its dict form + candidate quote/context."""
        from ..rights_discovery.models import CompiledRight, RightSpec
        if isinstance(compiled, dict):
            compiled = CompiledRight.from_dict(compiled)
        spec = compiled.spec if isinstance(compiled.spec, RightSpec) \
            else RightSpec.from_dict(compiled.spec)
        meta = compiled.to_dict().get("metadata", {}) or {}
        source_id = meta.get("source_id") or stable_id(
            "src", account_id, "compiled", spec.right_id)
        now = datetime.now(timezone.utc).isoformat()
        source = AuthoritySource(
            source_id=source_id,
            account_id=account_id,
            source_type="contract",
            external_reference=meta.get("external_reference"),
            counterparty_id=spec.obligor_party_id,
            effective_date=spec.effective_from,
            expiration_date=spec.effective_until,
            ingestion_timestamp=now,
            status="active",
        )
        quote = meta.get("source_quote", "")
        evidence = [EvidenceReference(
            evidence_id=spec.evidence_refs[0] if spec.evidence_refs
            else stable_id("ev", source_id, quote),
            source_id=source_id,
            locator="contract.source_quote",
            quoted_text=quote,
            extraction_method="ai_discovery",
            content_hash=sha256(quote.encode()).hexdigest() if quote else None,
        )] if quote or spec.evidence_refs else []
        right = FinancialRight(
            right_id=spec.right_id,
            account_id=account_id,
            source_id=source_id,
            holder_party_id=spec.holder_party_id or account_id,
            obligor_party_id=spec.obligor_party_id,
            right_type=spec.right_family,
            description=meta.get("description", ""),
            effective_from=spec.effective_from,
            effective_until=spec.effective_until,
            trigger_definition="per RightSpec trigger",
            calculation_rule=spec.right_family,
            calculation_inputs={
                "required_observations": spec.required_observations,
                "constants": {c["name"]: c["value"]
                              for c in spec.contractual_constants or []},
            },
            evidence_refs=[e.evidence_id for e in evidence],
            review_status=ReviewStatus.confirmed.value,
            status=RightStatus.active.value,
            metadata={
                "discovery_origin": "ai",
                "compiler_version": compiled.compiler_version,
                "spec_version": spec.spec_version,
                "discovery_model": compiled.discovery_model,
                "verification_model": compiled.verification_model,
                "candidate_id": compiled.candidate_id,
            },
        )
        return source, evidence, [right], []

    def normalize_observations(self, observations: list[dict],
                               account_id: str | None) -> list[Observation]:
        out = []
        for o in observations:
            out.append(Observation(
                observation_id=o.get("observation_id") or stable_id(
                    "obs", account_id, o.get("customer_id"), o.get("period"),
                    o.get("type") or o.get("observation_type"),
                    o.get("external_reference") or ""),
                account_id=account_id,
                observation_type=o.get("type") or o.get("observation_type"),
                party_id=o.get("customer_id"),
                period=o.get("period"),
                amount=o.get("amount"),
                quantity=o.get("quantity"),
                value=o.get("value"),
                unit=o.get("unit"),
                source_system=o.get("source_system"),
                external_reference=o.get("external_reference"),
                evidence=o.get("evidence") or {},
            ))
        return out

    def project_evaluation(self, spec, result, account_id, obs_ids):
        """EvaluationResult -> ExpectedState + Discrepancy (only when
        evaluated with recoverable > 0)."""
        now = datetime.now(timezone.utc).isoformat()
        period = getattr(result, "period", None) or \
            result.calculation_trace.get("period")
        expected_state = ExpectedState(
            expected_state_id=stable_id("exp", spec.right_id, period),
            right_id=spec.right_id,
            period=period,
            expected_amount=result.expected_amount,
            currency=result.currency,
            deterministic_rule=spec.right_family,
            calculation_trace=result.calculation_trace,
            input_observation_ids=list(result.input_observation_ids or obs_ids),
            generated_at=now,
        )
        discrepancy = Discrepancy(
            discrepancy_id=stable_id("dsc", spec.right_id, period,
                                     ",".join(sorted(
                                         result.input_observation_ids or obs_ids)),
                                     spec.right_family),
            right_id=spec.right_id,
            expected_state_id=expected_state.expected_state_id,
            actual_observation_ids=list(result.input_observation_ids or obs_ids),
            discrepancy_type=spec.right_family,
            expected_amount=result.expected_amount,
            actual_amount=result.actual_amount,
            recoverable_amount=result.recoverable_amount,
            calculation_trace=result.calculation_trace,
            status="open",
        )
        return expected_state, discrepancy

    def build_recovery_context(self, finding: dict, discrepancy_id: str):
        return B2BContractAdapter().build_recovery_context(finding, discrepancy_id)


def project_novel_rights(compiled_rights, observations, evaluations,
                         account_id) -> RightsGraph:
    """Merge AI-discovered rights + runtime evaluations into a RightsGraph."""
    from ..rights_discovery.models import EvaluationResult, RightSpec
    adapter = NovelRightsAdapter()
    graph = RightsGraph()
    specs = {}
    for compiled in compiled_rights or []:
        try:
            source, evidence, rights, _ = adapter.extract_rights(
                compiled, account_id)
        except Exception:
            graph.needs_review.append({
                "right_id": "unknown",
                "reason": "stored compiled right is malformed; skipped "
                          "(fail-closed)",
            })
            continue
        graph.sources.append(source)
        graph.evidence.extend(evidence)
        graph.rights.extend(rights)
        spec_dict = compiled["spec"] if isinstance(compiled, dict) \
            else compiled.spec
        specs[right_id_of(spec_dict)] = spec_dict
    graph.observations.extend(
        adapter.normalize_observations(observations or [], account_id))
    obs_ids = [o.observation_id for o in graph.observations]
    for ev in evaluations or []:
        result = ev if isinstance(ev, EvaluationResult) \
            else EvaluationResult.from_dict(ev)
        spec = specs.get(result.right_id)
        if spec is None:
            continue
        spec = spec if isinstance(spec, RightSpec) else RightSpec.from_dict(spec)
        if result.status == "not_evaluable":
            graph.not_evaluable.append({
                "right_id": result.right_id,
                "right_type": spec.right_family,
                "period": getattr(result, "period", None)
                or result.calculation_trace.get("period"),
                "missing_observation": ", ".join(result.missing_observations),
                "reason": "novel right valid but not evaluable: missing "
                          + ", ".join(result.missing_observations),
            })
            continue
        if result.status == "evaluated" and (result.recoverable_amount or 0) > 0:
            state, disc = adapter.project_evaluation(spec, result, account_id,
                                                     obs_ids)
            graph.expected_states.append(state)
            graph.discrepancies.append(disc)
    return graph


def right_id_of(spec_dict) -> str:
    return spec_dict.get("right_id") if isinstance(spec_dict, dict) \
        else spec_dict.right_id
