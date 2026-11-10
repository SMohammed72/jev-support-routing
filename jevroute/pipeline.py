"""intake -> normalize + redact -> jev classification -> confidence policy -> route -> feedback log."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from jevroute.policy import Policy, decide
from jevroute.redact import normalize, redact

ROOT = Path(__file__).resolve().parent.parent
ESCALATION_Q = {
    "type": "noul",
    "instructions": "Does the customer threaten legal action, a lawyer, a chargeback or bank dispute, a regulatory "
                    "complaint, or mention a physical injury or a safety hazard such as fire or smoke?",
}


def load_cases(path: Path = ROOT / "data" / "cases.jsonl") -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_queues(path: Path = ROOT / "config" / "queues.json") -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


def intake(case: dict) -> dict:
    """Normalize and redact. Only the redacted text is used from here on."""
    subject, c1 = redact(normalize(case.get("subject", "")))
    message, c2 = redact(normalize(case.get("message", "")))
    counts = {k: c1.get(k, 0) + c2.get(k, 0) for k in set(c1) | set(c2)}
    return {"channel": case.get("channel", "email"), "subject": subject or "(no subject)", "message": message,
            "redactions": counts}


def build_payload(ticket: dict, queues: dict[str, str], model: str, case_id: str) -> dict:
    return {
        "model": model,
        "state": {"channel": ticket["channel"], "subject": ticket["subject"], "message": ticket["message"]},
        "questions": {
            "queue": {"type": "choice",
                      "instructions": "Which customer-support queue should handle this message? Pick the queue "
                                      "for the customer's main request.",
                      "criteria": queues},
            "escalation_risk": ESCALATION_Q,
        },
        "session_id": f"jev-support-routing-{case_id}",
    }


def parse_answer(rec: dict, queues: dict[str, str]) -> tuple[dict | None, float | None, str | None]:
    body = rec.get("body") if rec.get("status") == 200 else None
    ans = ((body or {}).get("answers") or {}) if isinstance(body, dict) else {}
    q = ans.get("queue") or {}
    probs = {k: float(v) for k, v in (q.get("probabilities") or {}).items() if k in queues}
    risk = (ans.get("escalation_risk") or {}).get("noul")
    return (probs or None), (float(risk) if risk is not None else None), q.get("choice")


def case_ref(case_id: str) -> str:
    """Stable pseudonymous reference for the log; in production hash the ticket id with a secret salt."""
    return hashlib.sha256(case_id.encode()).hexdigest()[:12]


def run_case(case: dict, client, policy: Policy, queues: dict[str, str]) -> dict:
    ticket = intake(case)
    payload = build_payload(ticket, queues, policy.model, case["id"])
    rec = client.decide(case["id"], payload)
    probs, risk, choice = parse_answer(rec, queues)
    d = decide(probs, policy, escalation_risk=risk, model_choice=choice)
    body = rec.get("body") if isinstance(rec.get("body"), dict) else {}
    # Feedback-log row: no message text, no PII, no headers. `label` is the reviewer's queue; in this demo it is
    # the dataset's expected queue, in production it is written when a reviewer confirms or corrects the route.
    return {
        "case_ref": case_ref(case["id"]), "case_id": case["id"], "mode": getattr(client, "mode", "?"),
        "model_version": body.get("model"), "status": rec.get("status"), "stale_recording": rec.get("stale", False),
        "queue": d["queue"], "top_p": d["top_p"], "margin": d["margin"], "runner_up": d["runner_up"],
        "escalation_risk": risk, "route": d["route"], "reasons": d["reasons"],
        "probabilities": probs, "redactions": ticket["redactions"],
        "latency_s": rec.get("latency_s"), "cost_usd": float((body.get("usage") or {}).get("cost") or 0),
        "label": case.get("expected_queue"), "expected_human_review": case.get("expected_human_review"),
        "logged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def run_all(cases, client, policy, queues, log_path: Path | None = None) -> list[dict]:
    rows = [run_case(c, client, policy, queues) for c in cases]
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return rows
