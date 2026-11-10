"""Normalization and PII redaction. Runs before anything leaves the process or is logged.

Redacts emails, phone numbers, payment card numbers (Luhn-checked), US SSN-shaped numbers and IP addresses.
Order numbers are kept because routing needs them and they are not personal data on their own.
Names and street addresses are NOT detected by these regexes; see docs/ARCHITECTURE.md for the limitation.
"""
from __future__ import annotations

import re
import unicodedata

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE = re.compile(r"(?<![\w#])(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def normalize(text: str) -> str:
    """NFKC, strip control characters, collapse runs of whitespace, cap length."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:4000]


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Return (redacted_text, counts_by_kind). Order matters: emails, cards, SSNs, IPs, then phones."""
    counts: dict[str, int] = {}

    def sub(pattern, label, s, check=None):
        def repl(m):
            if check and not check(m.group(0)):
                return m.group(0)
            counts[label] = counts.get(label, 0) + 1
            return f"[{label}]"
        return pattern.sub(repl, s)

    text = sub(EMAIL, "EMAIL", text)
    text = sub(CARD, "CARD", text, check=lambda s: luhn_ok(re.sub(r"\D", "", s)))
    text = sub(SSN, "SSN", text)
    text = sub(IPV4, "IP", text)
    text = sub(PHONE, "PHONE", text)
    return text, counts
