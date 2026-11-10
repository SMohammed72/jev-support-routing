import unittest

from jevroute.redact import luhn_ok, normalize, redact


class RedactTests(unittest.TestCase):
    def test_email_phone_card(self):
        t, c = redact("mail a.b+x@example.co.uk or call +1 415-555-0182, card 4111 1111 1111 1111")
        self.assertNotIn("example", t)
        self.assertNotIn("4111", t)
        self.assertNotIn("555", t)
        self.assertEqual(c, {"EMAIL": 1, "PHONE": 1, "CARD": 1})

    def test_order_numbers_and_prices_survive(self):
        t, c = redact("Order #A10234 for $54.20 placed on Oct 28")
        self.assertEqual(t, "Order #A10234 for $54.20 placed on Oct 28")
        self.assertEqual(c, {})

    def test_non_luhn_long_number_is_not_a_card(self):
        self.assertFalse(luhn_ok("1234567812345678"))
        t, c = redact("tracking 1234567812345678")
        self.assertNotIn("CARD", c)

    def test_ssn_and_ip(self):
        t, c = redact("ssn 123-45-6789 from 192.168.10.4")
        self.assertEqual(t, "ssn [SSN] from [IP]")

    def test_normalize(self):
        self.assertEqual(normalize("  hi\u200b   there\x07\n\n\n\nok "), "hi there\n\nok")


if __name__ == "__main__":
    unittest.main()
