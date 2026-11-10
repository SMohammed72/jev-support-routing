"""Metrics, threshold sweep, confusion summary and the markdown report."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from jevroute.policy import AUTO, Policy, decide

SWEEP_GRID = [0.0, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99, 0.999]


def _pct(a, b):
    return round(a / b, 4) if b else None


def _fmt(x):
    return f"{x:.0%}" if x is not None else "n/a"


def _q(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 4) if xs else None


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    ok = [r for r in rows if r["queue"] is not None]
    auto = [r for r in rows if r["route"] == AUTO]
    human = [r for r in rows if r["route"] != AUTO]
    exp_h = [r for r in rows if r["expected_human_review"]]
    exp_a = [r for r in rows if not r["expected_human_review"]]
    reasons = Counter(x for r in human for x in r["reasons"])
    conf = Counter((r["label"], r["queue"]) for r in rows)
    return {
        "cases": n,
        "api_errors": n - len(ok),
        "top1_accuracy_all": _pct(sum(r["queue"] == r["label"] for r in rows), n),
        "auto_routed": len(auto),
        "auto_rate": _pct(len(auto), n),
        "auto_routed_accuracy": _pct(sum(r["queue"] == r["label"] for r in auto), len(auto)),
        "auto_routed_errors": [r["case_id"] for r in auto if r["queue"] != r["label"]],
        "human_review": len(human),
        "human_review_rate": _pct(len(human), n),
        "human_reasons": dict(reasons.most_common()),
        "expected_human_caught": _pct(sum(r["route"] != AUTO for r in exp_h), len(exp_h)),
        "expected_human_missed": [r["case_id"] for r in exp_h if r["route"] == AUTO],
        "clear_cases_auto_routed": _pct(sum(r["route"] == AUTO for r in exp_a), len(exp_a)),
        "human_suggestion_accuracy": _pct(sum(r["queue"] == r["label"] for r in human), len(human)),
        "confusion": {f"{a} -> {b}": c for (a, b), c in sorted(conf.items(), key=lambda kv: (-kv[1], kv[0]))
                      if a != b},
        "latency_p50_s": _q([r["latency_s"] for r in rows], 0.5),
        "latency_p95_s": _q([r["latency_s"] for r in rows], 0.95),
        "cost_total_usd": round(sum(r["cost_usd"] or 0 for r in rows), 6),
        "model_versions": sorted({r["model_version"] for r in rows if r["model_version"]}),
    }


def sweep(rows: list[dict], policy: Policy, grid=SWEEP_GRID) -> list[dict]:
    """Re-apply the policy offline with ONE global threshold, keeping the hard rules. Two variants:
    with the policy's margin rule and with no margin rule. No API calls: uses the logged probabilities."""
    out = []
    for margin in (policy.min_margin, 0.0):
        for t in grid:
            p = policy.with_overrides(default_threshold=t, queue_thresholds={}, min_margin=margin)
            ds = [(decide(r["probabilities"], p, r["escalation_risk"]), r["label"]) for r in rows]
            auto = [(d, lab) for d, lab in ds if d["route"] == AUTO]
            out.append({"threshold": t, "min_margin": margin, "auto": len(auto), "coverage": _pct(len(auto), len(ds)),
                        "auto_accuracy": _pct(sum(d["queue"] == lab for d, lab in auto), len(auto)),
                        "auto_errors": sum(d["queue"] != lab for d, lab in auto)})
    return out


def chart(sw: list[dict], default_point: dict, out: Path) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    fig, ax = plt.subplots(figsize=(7.5, 4.6), dpi=150)
    styles = ((0.0, "#9aa5b1", "--", "no margin rule"), (sw[0]["min_margin"], "#1f5fa8", "-",
              f"margin rule {sw[0]['min_margin']:g}"))
    for margin, color, ls, name in styles:
        pts = [s for s in sw if s["min_margin"] == margin and s["auto"]]
        ax.plot([s["coverage"] * 100 for s in pts], [s["auto_accuracy"] * 100 for s in pts], "o", ls=ls,
                color=color, lw=2, ms=5, label=f"one global threshold, {name}")
        groups: dict = {}
        for s in pts:
            groups.setdefault((s["coverage"], s["auto_accuracy"]), []).append(s["threshold"])
        for (cov, acc), ts in groups.items():
            lab = f"t={min(ts):g}" if len(ts) == 1 else f"t={min(ts):g}–{max(ts):g}"
            ax.annotate(lab, (cov * 100, acc * 100), textcoords="offset points",
                        xytext=(6, 8) if margin else (6, -14), fontsize=8, color=color)
    ax.plot([default_point["auto_rate"] * 100], [default_point["auto_routed_accuracy"] * 100], "*", ms=14,
            color="#d9822b", label="default policy (per-queue thresholds)")
    ax.set_xlabel("coverage: % of all 50 cases auto-routed")
    ax.set_ylabel("accuracy on auto-routed cases (%)")
    ax.set_title("Coverage vs accuracy as the confidence threshold moves", loc="left", fontsize=11)
    ax.margins(x=0.08, y=0.08)
    ax.grid(alpha=0.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def write_report(rows: list[dict], policy: Policy, cases: list[dict], out: Path, title: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    m = metrics(rows)
    sw = sweep(rows, policy)
    png = chart(sw, m, out / "sweep.png")
    (out / "metrics.json").write_text(json.dumps({"metrics": m, "sweep": sw}, indent=1) + "\n", encoding="utf-8")
    byid = {c["id"]: c for c in cases}
    L = [f"# {title}", "",
         f"Model `{policy.model}` via OpenRouter ({', '.join(m['model_versions']) or 'n/a'}). "
         f"{m['cases']} EXAMPLE cases, default policy in `config/policy.json`.", "",
         "## Headline", "",
         "| metric | value |", "|---|---|",
         f"| top-1 queue accuracy, all cases | {m['top1_accuracy_all']:.0%} |",
         f"| auto-routed | {m['auto_routed']} / {m['cases']} ({m['auto_rate']:.0%}) |",
         f"| accuracy on auto-routed | {m['auto_routed_accuracy']:.0%} ({len(m['auto_routed_errors'])} wrong) |"
         if m["auto_routed_accuracy"] is not None else "| accuracy on auto-routed | n/a |",
         f"| sent to human review | {m['human_review']} / {m['cases']} ({m['human_review_rate']:.0%}) |",
         f"| expected-human cases caught | {m['expected_human_caught']:.0%} |",
         f"| clear cases auto-routed | {m['clear_cases_auto_routed']:.0%} |",
         f"| reviewer pre-fill (suggested queue) accuracy | {m['human_suggestion_accuracy']:.0%} |"
         if m["human_suggestion_accuracy"] is not None else "| reviewer pre-fill accuracy | n/a |",
         f"| latency p50 / p95 | {m['latency_p50_s']} s / {m['latency_p95_s']} s |",
         f"| total API cost | ${m['cost_total_usd']:.6f} |", "",
         "## Why cases went to human review", "",
         "| reason | cases |", "|---|---|", *[f"| `{k}` | {v} |" for k, v in m["human_reasons"].items()], "",
         "## Confusion summary (expected -> predicted, mistakes only)", "",
         *([f"- `{k}`: {v}" for k, v in m["confusion"].items()] or ["- none"]), "",
         "## Threshold sweep (one global threshold, hard rules kept)", "",
         "| threshold | margin rule | auto-routed | coverage | accuracy on auto | wrong auto-routes |",
         "|---|---|---|---|---|---|",
         *[f"| {s['threshold']:g} | {s['min_margin']:g} | {s['auto']} | {s['coverage']:.0%} | "
           f"{_fmt(s['auto_accuracy'])} | {s['auto_errors']} |"
           for s in sw], ""]
    if png:
        L += ["![coverage vs accuracy](sweep.png)", ""]
    L += ["## Every case", "",
          "| case | expected | predicted | p | margin | esc. | route | reasons | note |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = "" if r["queue"] == r["label"] else " ✗"
        esc = f"{r['escalation_risk']:.2f}" if r["escalation_risk"] is not None else "-"
        L.append(f"| {r['case_id']} | {r['label']} | {r['queue']}{mark} | {r['top_p']:.2f} | {r['margin']:.2f} | "
                 f"{esc} | {r['route']} | {', '.join(r['reasons']) or '-'} | {byid[r['case_id']]['note']} |")
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return m
