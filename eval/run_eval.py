#!/usr/bin/env python3
"""
Vertex migration eval harness.

Replays real creditor email replies through each LLM call-site under BOTH
providers (claude baseline vs vertex candidate) and scores the candidate
against the acceptance thresholds from
docs/EMAIL-MATCHER-VERTEX-MIGRATION-PLAN.md (section 6.3):

    intent_classifier    >= 95% class agreement
    entity_extractor     >= 90% of fields match
    settlement_extractor >= 95% amount exact match, 0% hallucinated amounts

Both SDKs (anthropic + google-genai) and both sets of credentials
(ANTHROPIC_API_KEY + GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT)
must be configured in the environment this runs in (staging).

Usage:
    python eval/run_eval.py --fixtures eval/fixtures/email_replies.json
    python eval/run_eval.py --call-sites intent,settlement

Fixtures are a JSON array of objects:
    {
      "id": "reply-001",
      "subject": "AW: Forderung Az. 123/26",
      "body": "Sehr geehrte Damen und Herren, ...",
      "from_email": "inkasso@example.de"
    }

Vision call-sites (pdf_extractor / image_extractor) are NOT covered here:
they need binary attachment fixtures and are evaluated separately. See README.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Acceptance thresholds (fraction 0..1) per call-site.
THRESHOLDS = {
    "intent": 0.95,
    "entity": 0.90,
    "settlement": 0.95,
}

BASELINE_PROVIDER = "claude"
CANDIDATE_PROVIDER = "vertex"


# --------------------------------------------------------------------------- #
# Provider switching
# --------------------------------------------------------------------------- #
def _run_under_provider(provider: str, fn: Callable[[], Any]) -> Any:
    """Run ``fn`` with settings.llm_provider temporarily set to ``provider``."""
    from app.config import settings

    previous = settings.llm_provider
    settings.llm_provider = provider
    try:
        return fn()
    finally:
        settings.llm_provider = previous


# --------------------------------------------------------------------------- #
# Call-site runners — return a plain dict of the comparable output
# --------------------------------------------------------------------------- #
def run_intent(fx: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.intent_classifier import classify_intent_with_llm

    result = classify_intent_with_llm(fx.get("body", ""), fx.get("subject", ""))
    intent = result.intent.value if hasattr(result.intent, "value") else result.intent
    return {"intent": intent, "confidence": result.confidence}


def run_entity(fx: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.entity_extractor_claude import entity_extractor_claude

    e = entity_extractor_claude.extract_entities(
        email_body=fx.get("body", ""),
        from_email=fx.get("from_email", ""),
        subject=fx.get("subject"),
        attachment_texts=fx.get("attachment_texts"),
    )
    return {
        "is_creditor_reply": e.is_creditor_reply,
        "client_name": e.client_name,
        "creditor_name": e.creditor_name,
        "debt_amount": e.debt_amount,
        "reference_numbers": sorted(e.reference_numbers or []),
    }


def run_settlement(fx: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.settlement_extractor import settlement_extractor

    s = settlement_extractor.extract(
        email_body=fx.get("body", ""),
        from_email=fx.get("from_email", ""),
        subject=fx.get("subject"),
        attachment_texts=fx.get("attachment_texts"),
    )
    decision = (
        s.settlement_decision.value
        if hasattr(s.settlement_decision, "value")
        else s.settlement_decision
    )
    return {"settlement_decision": decision, "counter_offer_amount": s.counter_offer_amount}


RUNNERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "intent": run_intent,
    "entity": run_entity,
    "settlement": run_settlement,
}


# --------------------------------------------------------------------------- #
# Comparison logic per call-site
# --------------------------------------------------------------------------- #
def _norm_str(v: Optional[str]) -> Optional[str]:
    return v.strip().lower() if isinstance(v, str) else v


def _amounts_equal(a: Optional[float], b: Optional[float]) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= 0.01


@dataclass
class CallSiteScore:
    name: str
    fixtures: int = 0
    # generic match counters
    matches: int = 0
    total: int = 0
    # settlement-specific
    hallucinated_amounts: int = 0
    errors: int = 0
    mismatches: List[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.matches / self.total if self.total else 0.0


def score_intent(score: CallSiteScore, fx_id: str, base: Dict, cand: Dict) -> None:
    score.total += 1
    if base.get("intent") == cand.get("intent"):
        score.matches += 1
    else:
        score.mismatches.append(
            f"[{fx_id}] intent: baseline={base.get('intent')} candidate={cand.get('intent')}"
        )


def score_entity(score: CallSiteScore, fx_id: str, base: Dict, cand: Dict) -> None:
    fields = ["is_creditor_reply", "client_name", "creditor_name", "debt_amount", "reference_numbers"]
    for f in fields:
        score.total += 1
        bv, cv = base.get(f), cand.get(f)
        if f == "debt_amount":
            ok = _amounts_equal(bv, cv)
        elif f in ("client_name", "creditor_name"):
            ok = _norm_str(bv) == _norm_str(cv)
        else:
            ok = bv == cv
        if ok:
            score.matches += 1
        else:
            score.mismatches.append(f"[{fx_id}] {f}: baseline={bv!r} candidate={cv!r}")


def score_settlement(score: CallSiteScore, fx_id: str, base: Dict, cand: Dict) -> None:
    # Amount agreement is the headline metric.
    score.total += 1
    if _amounts_equal(base.get("counter_offer_amount"), cand.get("counter_offer_amount")):
        score.matches += 1
    else:
        score.mismatches.append(
            f"[{fx_id}] counter_offer_amount: baseline={base.get('counter_offer_amount')} "
            f"candidate={cand.get('counter_offer_amount')}"
        )
    # Hallucinated amount: candidate invents a figure where baseline had none.
    if base.get("counter_offer_amount") is None and cand.get("counter_offer_amount") is not None:
        score.hallucinated_amounts += 1
        score.mismatches.append(
            f"[{fx_id}] HALLUCINATED amount: {cand.get('counter_offer_amount')} (baseline None)"
        )


SCORERS = {
    "intent": score_intent,
    "entity": score_entity,
    "settlement": score_settlement,
}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Vertex migration eval harness")
    ap.add_argument("--fixtures", default="eval/fixtures/email_replies.json")
    ap.add_argument(
        "--call-sites",
        default="intent,entity,settlement",
        help="comma-separated subset of: intent,entity,settlement",
    )
    ap.add_argument("--show-mismatches", type=int, default=15, help="max mismatch lines per call-site")
    args = ap.parse_args()

    try:
        with open(args.fixtures, encoding="utf-8") as fh:
            fixtures = json.load(fh)
    except FileNotFoundError:
        print(f"ERROR: fixtures file not found: {args.fixtures}", file=sys.stderr)
        print("Pull ~100 real replies from prod (incoming_email) into that path.", file=sys.stderr)
        return 2

    call_sites = [c.strip() for c in args.call_sites.split(",") if c.strip()]
    scores = {cs: CallSiteScore(name=cs) for cs in call_sites}

    for fx in fixtures:
        fx_id = str(fx.get("id", fx.get("subject", "?")))[:48]
        for cs in call_sites:
            runner, scorer = RUNNERS[cs], SCORERS[cs]
            scores[cs].fixtures += 1
            try:
                base = _run_under_provider(BASELINE_PROVIDER, lambda: runner(fx))
                cand = _run_under_provider(CANDIDATE_PROVIDER, lambda: runner(fx))
            except Exception as exc:  # noqa: BLE001 - eval keeps going on per-row error
                scores[cs].errors += 1
                scores[cs].mismatches.append(f"[{fx_id}] ERROR: {type(exc).__name__}: {exc}")
                continue
            scorer(scores[cs], fx_id, base, cand)

    # ---- Report ----
    print("\n" + "=" * 72)
    print(f"VERTEX EVAL  ({len(fixtures)} fixtures)  baseline={BASELINE_PROVIDER} candidate={CANDIDATE_PROVIDER}")
    print("=" * 72)
    all_passed = True
    for cs in call_sites:
        s = scores[cs]
        threshold = THRESHOLDS[cs]
        passed = s.rate >= threshold and (cs != "settlement" or s.hallucinated_amounts == 0)
        all_passed = all_passed and passed
        flag = "PASS" if passed else "FAIL"
        print(f"\n[{flag}] {cs}: {s.rate:.1%} match ({s.matches}/{s.total}) — threshold {threshold:.0%}")
        if cs == "settlement":
            hflag = "OK" if s.hallucinated_amounts == 0 else "VIOLATION"
            print(f"        hallucinated amounts: {s.hallucinated_amounts} [{hflag}] (threshold: 0)")
        if s.errors:
            print(f"        errors: {s.errors}")
        for line in s.mismatches[: args.show_mismatches]:
            print(f"        - {line}")
        if len(s.mismatches) > args.show_mismatches:
            print(f"        ... +{len(s.mismatches) - args.show_mismatches} more")

    print("\n" + "=" * 72)
    print("RESULT:", "ALL GREEN — safe to greenlight" if all_passed else "BELOW THRESHOLD — do not cut over")
    print("=" * 72)
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
