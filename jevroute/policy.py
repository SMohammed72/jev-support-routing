"""Confidence policy: decides auto-route vs human review from jev's calibrated probabilities.

Pure functions, no I/O, so every rule is unit-tested without a network (tests/test_policy.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

AUTO, HUMAN = "auto", "human"


@dataclass
class Policy:
    model: str = "typesafe/jev-1.14"
    default_threshold: float = 0.80
    queue_thresholds: dict[str, float] = field(default_factory=dict)
    min_margin: float = 0.30
    always_human: list[str] = field(default_factory=lambda: ["fraud_security", "legal_privacy"])
    no_auto_route: list[str] = field(default_factory=lambda: ["other"])
    escalation_risk_threshold: float = 0.50
    high_risk_floor: float = 0.20

    def threshold(self, queue: str) -> float:
        return float(self.queue_thresholds.get(queue, self.default_threshold))

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def with_overrides(self, **kw) -> "Policy":
        d = {**self.__dict__, **kw}
        return Policy(**d)


def ranked(probs: dict[str, float]) -> list[tuple[str, float]]:
    return sorted(((k, float(v)) for k, v in (probs or {}).items()), key=lambda kv: -kv[1])


def decide(probs: dict[str, float] | None, policy: Policy, escalation_risk: float | None = None,
           model_choice: str | None = None) -> dict:
    """Return {"route", "queue", "top_p", "margin", "runner_up", "reasons"}.

    Rules (every one that fires is recorded in `reasons`; any reason means human review):
      model_error        no usable probabilities (API error, unknown queue)
      always_human       top queue is high-risk (fraud/security, legal/privacy): a person always handles it
      high_risk_shadow   a high-risk queue is not on top but still has p >= high_risk_floor
      no_auto_route      top queue has no automated destination (e.g. "other": off-topic, greeting, cut off)
      escalation_risk    the separate yes/no question (legal threat, chargeback, injury) is >= its threshold
      below_threshold    top probability < that queue's threshold
      low_margin         top-1 minus top-2 probability < min_margin (two intents compete)
    """
    r = ranked(probs or {})
    if not r:
        return {"route": HUMAN, "queue": None, "top_p": 0.0, "margin": 0.0, "runner_up": None,
                "reasons": ["model_error"]}
    (q, p), (q2, p2) = r[0], (r[1] if len(r) > 1 else (None, 0.0))
    if model_choice and model_choice != q and probs.get(model_choice) == p:
        q = model_choice  # tie: keep the model's own pick
    reasons = []
    if q in policy.always_human:
        reasons.append("always_human")
    elif any(probs.get(h, 0.0) >= policy.high_risk_floor for h in policy.always_human):
        reasons.append("high_risk_shadow")
    if q in policy.no_auto_route:
        reasons.append("no_auto_route")
    if escalation_risk is not None and escalation_risk >= policy.escalation_risk_threshold:
        reasons.append("escalation_risk")
    if p < policy.threshold(q):
        reasons.append("below_threshold")
    if p - p2 < policy.min_margin:
        reasons.append("low_margin")
    return {"route": HUMAN if reasons else AUTO, "queue": q, "top_p": round(p, 4), "margin": round(p - p2, 4),
            "runner_up": q2, "reasons": reasons}


def suggest_thresholds(records: list[dict], target_accuracy: float = 0.95, min_support: int = 3,
                       floor: float = 0.50, default: float = 0.80,
                       current: dict[str, float] | None = None) -> dict[str, dict]:
    """Feedback loop: from labeled decisions (model queue, top_p, reviewer label), pick per queue the LOWEST
    threshold whose auto-routed accuracy on the history is >= target_accuracy. Lower threshold = more coverage.

    records: [{"queue": predicted, "top_p": float, "label": reviewer/ground-truth queue}, ...]
    Queues with fewer than min_support predictions keep their current threshold (or `default`)."""
    by_q: dict[str, list[tuple[float, bool]]] = {}
    for x in records:
        if x.get("queue") is None or x.get("label") is None:
            continue
        by_q.setdefault(x["queue"], []).append((float(x["top_p"]), x["queue"] == x["label"]))
    out = {}
    for q, xs in sorted(by_q.items()):
        if len(xs) < min_support:
            out[q] = {"threshold": (current or {}).get(q, default), "support": len(xs), "accuracy": None, "why": "not enough history"}
            continue
        best = None
        for t in sorted({p for p, _ in xs} | {floor}):
            if t < floor:
                continue
            sel = [ok for p, ok in xs if p >= t]
            if sel and sum(sel) / len(sel) >= target_accuracy:
                best = (t, len(sel), sum(sel) / len(sel))
                break
        if best is None:
            out[q] = {"threshold": 1.01, "support": len(xs), "accuracy": None,
                      "why": f"no threshold reaches {target_accuracy:.0%}; keep this queue human-only"}
        else:
            out[q] = {"threshold": round(best[0], 4), "support": best[1], "accuracy": round(best[2], 4),
                      "why": f"lowest threshold with >= {target_accuracy:.0%} accuracy"}
    return out
