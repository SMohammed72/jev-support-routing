"""Clients that answer a jev Decisions request.

- JevClient: live call to OpenRouter's Decisions API (POST /api/alpha/decisions). The key is read from the
  OPENROUTER_API_KEY environment variable at call time and is only ever placed in the Authorization header.
  It is never printed, logged, recorded or written to disk.
- ReplayClient: offline mode. Returns responses recorded by a previous live run (recordings/<model>/<case>.json),
  so the repo works without a key. Recordings hold the redacted request, status, latency and response body only.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api")
DECISIONS_URL = BASE + "/alpha/decisions"
RETRY = {0, 408, 409, 425, 429, 500, 502, 503, 504}
UA = {"User-Agent": "jev-support-routing/0.1", "X-Title": "jev-support-routing"}


class MissingKey(SystemExit):
    pass


def request_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise MissingKey("OPENROUTER_API_KEY is not set. Export it in your shell (see .env.example) "
                         "or run with --mode replay to use recorded responses.")
    return key


class JevClient:
    mode = "live"

    def __init__(self, timeout: float = 60, retries: int = 3, record_dir: Path | None = None):
        self.timeout, self.retries, self.record_dir = timeout, retries, record_dir
        _key()  # fail fast, before any case is processed

    def _post_once(self, payload: dict):
        headers = {**UA, "Content-Type": "application/json", "Authorization": "Bearer " + _key()}
        req = urllib.request.Request(DECISIONS_URL, data=json.dumps(payload).encode(), method="POST", headers=headers)
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                status, raw = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - reported as status 0, never raised with request details
            return 0, {"error": type(e).__name__}, time.perf_counter() - t
        dt = time.perf_counter() - t
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"error": "non-json response", "text": raw[:500]}
        return status, body, dt

    def decide(self, case_id: str, payload: dict) -> dict:
        for attempt in range(self.retries + 1):
            status, body, dt = self._post_once(payload)
            if status not in RETRY or attempt == self.retries:
                break
            time.sleep(min(1.5 * 2 ** attempt + random.uniform(0, 0.5), 30))
        if isinstance(body, dict):
            body.pop("id", None)  # per-request id; not needed for replay
        rec = {"case_id": case_id, "request_sha": request_hash(payload), "request": payload,
               "status": status, "latency_s": round(dt, 4), "body": body}
        if self.record_dir is not None:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / f"{case_id}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n",
                                                             encoding="utf-8")
        return rec


class ReplayClient:
    mode = "replay"

    def __init__(self, record_dir: Path):
        self.record_dir = record_dir
        if not record_dir.is_dir():
            raise SystemExit(f"no recordings at {record_dir}; run once with --mode live to create them")

    def decide(self, case_id: str, payload: dict) -> dict:
        p = self.record_dir / f"{case_id}.json"
        if not p.exists():
            return {"case_id": case_id, "status": -1, "latency_s": 0.0, "body": {"error": "no recording"}}
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["stale"] = rec.get("request_sha") != request_hash(payload)
        return rec
