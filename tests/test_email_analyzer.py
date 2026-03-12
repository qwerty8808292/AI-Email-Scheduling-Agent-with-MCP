import unittest
import email_analyzer as EMAIL_ANALYZER


class EmailAnalyzerUtilityTests(unittest.TestCase):
    def test_is_important_sender_matches_domain_and_exact_email(self):
        important_senders = {
            "important_domains": ["example.com"],
            "important_senders": ["ceo@vip.com"],
        }

        self.assertTrue(
            EMAIL_ANALYZER.is_important_sender("boss@example.com", important_senders)
        )
        self.assertTrue(
            EMAIL_ANALYZER.is_important_sender("CEO@VIP.COM", important_senders)
        )
        self.assertFalse(
            EMAIL_ANALYZER.is_important_sender("person@other.com", important_senders)
        )

    def test_normalize_dt_converts_timezone_aware_input_to_taipei(self):
        normalized = EMAIL_ANALYZER.normalize_dt("2026-03-12T09:00:00+00:00")
        self.assertEqual(normalized, "2026-03-12T17:00:00")

    def test_normalize_dt_keeps_naive_input_and_none(self):
        self.assertEqual(
            EMAIL_ANALYZER.normalize_dt("2026-03-12T09:00:00"),
            "2026-03-12T09:00:00",
        )
        self.assertIsNone(EMAIL_ANALYZER.normalize_dt(None))


if __name__ == "__main__":
    unittest.main()
