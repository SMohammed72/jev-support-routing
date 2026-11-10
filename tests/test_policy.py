"""Threshold-policy unit tests. Pure functions; no network, no key."""
import unittest

from jevroute.policy import AUTO, HUMAN, Policy, decide, suggest_thresholds

P = Policy(default_threshold=0.80, queue_thresholds={"billing_refunds": 0.90, "product_questions": 0.70},
           min_margin=0.30, always_human=["fraud_security", "legal_privacy"], no_auto_route=["other"],
           escalation_risk_threshold=0.50, high_risk_floor=0.20)


def probs(**kw):
    base = {"billing_refunds": 0.0, "order_shipping": 0.0, "fraud_security": 0.0, "legal_privacy": 0.0,
            "product_questions": 0.0, "other": 0.0}
    base.update(kw)
    return base


class DecideTests(unittest.TestCase):
    def test_confident_low_risk_is_auto(self):
        d = decide(probs(order_shipping=0.97, billing_refunds=0.03), P, escalation_risk=0.01)
        self.assertEqual((d["route"], d["queue"], d["reasons"]), (AUTO, "order_shipping", []))

    def test_per_queue_threshold_is_used(self):
        # 0.85 clears the 0.80 default but not billing's stricter 0.90
        d = decide(probs(billing_refunds=0.85, order_shipping=0.15), P)
        self.assertEqual(d["route"], HUMAN)
        self.assertIn("below_threshold", d["reasons"])
        # and a looser queue auto-routes at 0.72
        d = decide(probs(product_questions=0.72, order_shipping=0.28), P)
        self.assertEqual(d["route"], AUTO)

    def test_threshold_boundary_is_inclusive(self):
        d = decide(probs(order_shipping=0.80, billing_refunds=0.20), P)
        self.assertEqual(d["route"], AUTO)

    def test_low_margin_goes_to_human_even_above_threshold(self):
        p = P.with_overrides(queue_thresholds={}, default_threshold=0.5)
        d = decide(probs(order_shipping=0.60, billing_refunds=0.40), p)
        self.assertEqual(d["route"], HUMAN)
        self.assertEqual(d["reasons"], ["low_margin"])
        self.assertEqual(d["runner_up"], "billing_refunds")

    def test_always_human_queue_never_auto_routes_even_at_p1(self):
        for q in ("fraud_security", "legal_privacy"):
            d = decide(probs(**{q: 1.0}), P, escalation_risk=0.0)
            self.assertEqual(d["route"], HUMAN)
            self.assertIn("always_human", d["reasons"])

    def test_high_risk_shadow(self):
        d = decide(probs(order_shipping=0.78, fraud_security=0.22), P)
        self.assertIn("high_risk_shadow", d["reasons"])
        d = decide(probs(order_shipping=0.81, fraud_security=0.19), P)
        self.assertNotIn("high_risk_shadow", d["reasons"])

    def test_no_auto_route_queue(self):
        d = decide(probs(other=0.99), P)
        self.assertEqual(d["route"], HUMAN)
        self.assertIn("no_auto_route", d["reasons"])

    def test_escalation_flag_overrides_confident_queue(self):
        d = decide(probs(billing_refunds=0.99), P, escalation_risk=0.91)
        self.assertEqual(d["route"], HUMAN)
        self.assertEqual(d["reasons"], ["escalation_risk"])

    def test_missing_probabilities_is_model_error(self):
        for x in (None, {}):
            d = decide(x, P)
            self.assertEqual((d["route"], d["reasons"]), (HUMAN, ["model_error"]))

    def test_tie_keeps_model_choice(self):
        p = P.with_overrides(min_margin=0.0, default_threshold=0.4, queue_thresholds={})
        d = decide({"order_shipping": 0.5, "billing_refunds": 0.5}, p, model_choice="billing_refunds")
        self.assertEqual(d["queue"], "billing_refunds")

    def test_raising_threshold_never_increases_auto_routes(self):
        cases = [probs(order_shipping=x / 20, billing_refunds=1 - x / 20) for x in range(21)]
        prev = None
        for t in (0.5, 0.6, 0.7, 0.8, 0.9, 0.99):
            p = P.with_overrides(default_threshold=t, queue_thresholds={}, min_margin=0.0)
            n = sum(decide(c, p)["route"] == AUTO for c in cases)
            if prev is not None:
                self.assertLessEqual(n, prev)
            prev = n


class TuneTests(unittest.TestCase):
    def test_suggests_lowest_threshold_meeting_target(self):
        recs = ([{"queue": "order_shipping", "top_p": 0.99, "label": "order_shipping"}] * 8
                + [{"queue": "order_shipping", "top_p": 0.70, "label": "returns_exchanges"}]
                + [{"queue": "order_shipping", "top_p": 0.90, "label": "order_shipping"}])
        s = suggest_thresholds(recs, target_accuracy=0.95, min_support=3)
        self.assertEqual(s["order_shipping"]["threshold"], 0.9)
        self.assertEqual(s["order_shipping"]["accuracy"], 1.0)

    def test_thin_history_keeps_default(self):
        s = suggest_thresholds([{"queue": "cancellations", "top_p": 0.99, "label": "cancellations"}], default=0.8)
        self.assertEqual(s["cancellations"]["threshold"], 0.8)

    def test_unreachable_target_disables_auto(self):
        recs = [{"queue": "tech_login", "top_p": 0.99, "label": "fraud_security"}] * 4
        s = suggest_thresholds(recs, target_accuracy=0.95)
        self.assertGreater(s["tech_login"]["threshold"], 1.0)


if __name__ == "__main__":
    unittest.main()
