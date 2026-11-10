"""End-to-end over all 50 cases in replay mode: no network, no key."""
import json
import os
import unittest
from unittest import mock

from jevroute.client import MissingKey, ReplayClient
from jevroute.pipeline import ROOT, build_payload, intake, load_cases, load_queues, run_all
from jevroute.policy import Policy
from jevroute.report import metrics

POLICY = Policy.load(ROOT / "config" / "policy.json")
REC = ROOT / "recordings" / POLICY.model.replace("/", "__")


class DatasetTests(unittest.TestCase):
    def test_fifty_labeled_example_cases(self):
        cases = load_cases()
        queues = load_queues()
        self.assertEqual(len(cases), 50)
        self.assertEqual(len({c["id"] for c in cases}), 50)
        for c in cases:
            self.assertTrue(c["example"])
            self.assertIn(c["expected_queue"], queues)
            self.assertTrue(c["note"])
        self.assertEqual({c["expected_queue"] for c in cases}, set(queues))


class OfflinePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.queues = load_cases(), load_queues()
        cls.rows = run_all(cls.cases, ReplayClient(REC), POLICY, cls.queues)

    def test_recordings_match_current_requests(self):
        self.assertFalse([r["case_id"] for r in self.rows if r["stale_recording"]])
        self.assertTrue(all(r["status"] == 200 for r in self.rows))

    def test_high_risk_queues_never_auto_route(self):
        for r in self.rows:
            if r["queue"] in POLICY.always_human + POLICY.no_auto_route:
                self.assertEqual(r["route"], "human", r["case_id"])

    def test_every_expected_human_case_reaches_a_human(self):
        missed = [r["case_id"] for r in self.rows if r["expected_human_review"] and r["route"] == "auto"]
        self.assertEqual(missed, [])

    def test_metrics_shape(self):
        m = metrics(self.rows)
        self.assertEqual(m["auto_routed"] + m["human_review"], 50)

    def test_decision_log_has_no_raw_text_or_pii(self):
        blob = json.dumps(self.rows)
        for c in self.cases:
            self.assertNotIn(c["message"][:40], blob)
        for needle in ("@example.com", "4111", "555-0182"):
            self.assertNotIn(needle, blob)

    def test_recordings_hold_only_redacted_requests_and_no_auth(self):
        for p in REC.glob("*.json"):
            s = p.read_text(encoding="utf-8")
            self.assertNotIn("Authorization", s)
            self.assertNotIn("Bearer", s)
            self.assertNotIn("sk-or-", s)
            self.assertNotIn("@example.com", s)
            self.assertNotIn("4111 1111", s)


class KeyHandlingTests(unittest.TestCase):
    def test_live_client_refuses_without_key(self):
        from jevroute.client import JevClient
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            with self.assertRaises(MissingKey):
                JevClient()

    def test_payload_never_contains_key(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-FAKEFAKEFAKE"}):
            t = intake(load_cases()[0])
            p = build_payload(t, load_queues(), POLICY.model, "case_01")
            self.assertNotIn("sk-or-", json.dumps(p))


if __name__ == "__main__":
    unittest.main()
