"""Command line.

  python -m jevroute run                       # offline: replay recorded jev responses (no key needed)
  python -m jevroute run --mode live           # live: call jev on OpenRouter (needs OPENROUTER_API_KEY)
  python -m jevroute run --mode live --record  # live, and overwrite recordings/ with the new responses
  python -m jevroute review                    # print the human-review queue from the last run
  python -m jevroute tune --target 0.95        # suggest per-queue thresholds from the decision log
  python -m jevroute classify "text..."        # route one ad-hoc message (live)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jevroute.client import JevClient, ReplayClient
from jevroute.pipeline import ROOT, load_cases, load_queues, run_all, run_case
from jevroute.policy import Policy, suggest_thresholds
from jevroute.report import write_report


def _slug(model: str) -> str:
    return model.replace("/", "__")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jevroute", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--policy", default=str(ROOT / "config" / "policy.json"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run all cases through the pipeline and write a report")
    r.add_argument("--mode", choices=["replay", "live"], default="replay")
    r.add_argument("--record", action="store_true", help="live mode: save responses to recordings/")
    r.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
    r.add_argument("--out", default=str(ROOT / "runs" / "latest"))
    r.add_argument("--title", default=None)
    v = sub.add_parser("review", help="show the human-review queue from a decision log")
    v.add_argument("--log", default=str(ROOT / "runs" / "latest" / "decisions.jsonl"))
    t = sub.add_parser("tune", help="suggest per-queue thresholds from labeled decisions")
    t.add_argument("--log", default=str(ROOT / "runs" / "latest" / "decisions.jsonl"))
    t.add_argument("--target", type=float, default=0.95)
    t.add_argument("--min-support", type=int, default=30, help="labeled tickets per queue before a threshold may move")
    c = sub.add_parser("classify", help="route one message live")
    c.add_argument("text")
    c.add_argument("--subject", default="")
    a = ap.parse_args(argv)
    policy = Policy.load(a.policy)
    queues = load_queues()
    rec_dir = ROOT / "recordings" / _slug(policy.model)

    if a.cmd == "run":
        cases = load_cases(Path(a.cases))
        client = JevClient(record_dir=rec_dir if a.record else None) if a.mode == "live" else ReplayClient(rec_dir)
        out = Path(a.out)
        rows = run_all(cases, client, policy, queues, log_path=out / "decisions.jsonl")
        stale = sum(r["stale_recording"] for r in rows)
        if stale:
            print(f"warning: {stale} recordings were made with a different request (prompt/config changed); "
                  f"re-record with --mode live --record", file=sys.stderr)
        m = write_report(rows, policy, cases, out, a.title or f"Routing report ({a.mode})")
        print(f"{m['cases']} cases | auto-routed {m['auto_routed']} ({m['auto_rate']:.0%}) at "
              f"{(m['auto_routed_accuracy'] or 0):.0%} accuracy | human review {m['human_review']} "
              f"({m['human_review_rate']:.0%}) | expected-human caught {m['expected_human_caught']:.0%} | "
              f"cost ${m['cost_total_usd']:.6f}")
        print(f"report: {out / 'report.md'}")
        return 0
    if a.cmd == "review":
        rows = [json.loads(l) for l in Path(a.log).read_text(encoding="utf-8").splitlines() if l.strip()]
        hq = [r for r in rows if r["route"] != "auto"]
        print(f"{len(hq)} tickets waiting for human review (suggested queue pre-filled for the reviewer):")
        for r in sorted(hq, key=lambda r: ("always_human" not in r["reasons"], r["top_p"])):
            print(f"  {r['case_id']}  suggest={r['queue']:<22} p={r['top_p']:.2f} runner-up={r['runner_up']:<22} "
                  f"reasons={','.join(r['reasons'])}")
        return 0
    if a.cmd == "tune":
        rows = [json.loads(l) for l in Path(a.log).read_text(encoding="utf-8").splitlines() if l.strip()]
        rows = [r for r in rows if r["queue"] not in policy.always_human + policy.no_auto_route]
        s = suggest_thresholds(rows, a.target, a.min_support, default=policy.default_threshold,
                               current={q: policy.threshold(q) for q in {r['queue'] for r in rows}})
        print(f"suggested thresholds for >= {a.target:.0%} auto-route accuracy (current in parentheses):")
        for q, x in s.items():
            print(f"  {q:<22} {x['threshold']:<6} ({policy.threshold(q)})  support={x['support']}  {x['why']}")
        return 0
    if a.cmd == "classify":
        row = run_case({"id": "adhoc", "subject": a.subject, "message": a.text}, JevClient(), policy, queues)
        print(json.dumps({k: row[k] for k in ("queue", "top_p", "margin", "runner_up", "escalation_risk", "route",
                                              "reasons", "redactions", "latency_s")}, indent=1))
        return 0
    return 1
