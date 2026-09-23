import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import premium_alert

BJ = timezone(timedelta(hours=8))
ENV = {
    'ALERT_TO_EMAIL': 'recipient@example.com',
    'ALERT_SMTP_HOST': 'smtp.example.com',
    'ALERT_SMTP_USER': 'sender@example.com',
    'ALERT_SMTP_PASSWORD': 'test-only-not-a-real-password',
}


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / '.github' / 'premium-alert-state.json'
        self.now = datetime(2026, 9, 23, 22, 30, tzinfo=BJ)
        self.write_funds({'etf_all.json': (4.9999, '2026-09-23', '2026-09-22')})

    def write_funds(self, overrides=None):
        overrides = overrides or {}
        for i, (_, filename) in enumerate(premium_alert.GROUPS):
            value, price_date, nav_date = overrides.get(
                filename, (6.0, '2026-09-23', '2026-09-22'))
            data = {f'sh50000{i}': {
                'name': f'基金{i}',
                'price': [{'date': price_date, 'value': 1 + value / 100}],
                'nav': [{'date': nav_date, 'value': 1.0}],
                'premium': [{'date': price_date, 'nav_date': nav_date, 'value': value}],
            }}
            (self.root / filename).write_text(json.dumps(data), encoding='utf-8')

    def test_any_one_below_five_is_selected(self):
        items = premium_alert.select_candidates(self.root, '2026-09-23')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['premium'], 4.9999)

    def test_exactly_five_is_not_selected(self):
        self.write_funds({'etf_all.json': (5.0, '2026-09-23', '2026-09-22')})
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])

    def test_old_price_or_nav_is_rejected(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-22', '2026-09-21')})
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-20')})
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])

    def test_unverified_premium_is_rejected(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-22')})
        data_file = self.root / 'etf_all.json'
        data = json.loads(data_file.read_text(encoding='utf-8'))
        first = next(iter(data.values()))
        first['price'][0]['value'] = 1.06
        data_file.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])

    def test_missing_secrets_do_not_reserve(self):
        self.assertFalse(premium_alert.prepare(self.root, self.state, self.now, env={}))
        self.assertFalse(self.state.exists())

    def test_reservation_is_only_once_per_beijing_day_and_private(self):
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        self.assertFalse(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        state = self.state.read_text(encoding='utf-8')
        self.assertNotIn(ENV['ALERT_TO_EMAIL'], state)
        self.assertNotIn(ENV['ALERT_SMTP_PASSWORD'], state)
        self.assertEqual(json.loads(state)['date'], '2026-09-23')

    def test_next_morning_does_not_resend_yesterdays_price(self):
        morning = datetime(2026, 9, 24, 8, 20, tzinfo=BJ)
        self.assertFalse(premium_alert.prepare(self.root, self.state, morning, env=ENV))
        self.assertFalse(self.state.exists())

    def test_send_uses_reservation_and_mock_smtp(self):
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        self.assertTrue(premium_alert.claim(self.root, self.state, self.now, env=ENV))
        self.assertFalse(premium_alert.claim(self.root, self.state, self.now, env=ENV))
        class SMTP:
            sent = []
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def login(self, *args):
                pass
            def send_message(self, message):
                self.sent.append(message)
        premium_alert.send(self.root, self.state, self.now, env=ENV, smtp_factory=SMTP)
        self.assertEqual(len(SMTP.sent), 1)
        self.assertEqual(SMTP.sent[0]['To'], ENV['ALERT_TO_EMAIL'])
        self.assertIn('4.9999%', SMTP.sent[0].get_content())

    def test_one_off_email_does_not_touch_daily_state(self):
        class SMTP:
            sent = []
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def login(self, *args):
                pass
            def send_message(self, message):
                self.sent.append(message)
        premium_alert.send_test_email(env=ENV, smtp_factory=SMTP)
        self.assertEqual(len(SMTP.sent), 1)
        self.assertIn('邮件通道测试', SMTP.sent[0]['Subject'])
        self.assertFalse(self.state.exists())

    def test_changed_data_refuses_claim(self):
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-22')})
        self.assertFalse(premium_alert.claim(self.root, self.state, self.now, env=ENV))
        self.assertEqual(premium_alert.read_state(self.state)['status'], 'reserved')

    def test_send_requires_committed_claim(self):
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        with self.assertRaisesRegex(ValueError, 'claimed'):
            premium_alert.send(self.root, self.state, self.now, env=ENV)


if __name__ == '__main__':
    unittest.main()
