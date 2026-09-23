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

    def test_high_premium_ranking_in_same_email(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-22'),
                          'sp500_all.json': (12.0, '2026-09-23', '2026-09-22'),
                          'us50_all.json': (8.0, '2026-09-23', '2026-09-22'),
                          'djia_all.json': (5.0, '2026-09-23', '2026-09-22')})
        ranked = premium_alert.select_ranked_funds(self.root, '2026-09-23')
        self.assertEqual([item['premium'] for item in ranked], [12.0, 8.0, 5.0, 4.0])
        candidates = premium_alert.select_candidates(self.root, '2026-09-23')
        self.assertEqual([item['premium'] for item in candidates], [4.0])
        message = premium_alert.build_message(candidates, '2026-09-23',
                                              ENV['ALERT_TO_EMAIL'], ENV['ALERT_SMTP_USER'], ranked)
        text = message.get_body(preferencelist=('plain',)).get_content()
        self.assertIn('以下 1 只场内ETF的有效溢价率低于 5%', text)
        self.assertIn('当日场内ETF溢价从高到低（4只', text)
        self.assertLess(text.index('12.0000%'), text.index('8.0000%'))
        self.assertLess(text.index('8.0000%'), text.index('5.0000%'))
        self.assertLess(text.index('5.0000%'), text.index('4.0000%', text.index('当日场内ETF溢价从高到低')))
        html_body = message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('<table', html_body)
        self.assertEqual(html_body.count('<tr>'), 5)
        self.assertIn('近1个月均值', html_body)

    def test_ranking_excludes_stale_and_unverified_funds(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-22'),
                          'sp500_all.json': (12.0, '2026-09-22', '2026-09-21'),
                          'us50_all.json': (8.0, '2026-09-23', '2026-09-22')})
        file = self.root / 'us50_all.json'
        data = json.loads(file.read_text(encoding='utf-8'))
        next(iter(data.values()))['price'][0]['value'] = 1.09
        file.write_text(json.dumps(data), encoding='utf-8')
        ranked = premium_alert.select_ranked_funds(self.root, '2026-09-23')
        self.assertEqual([item['code'] for item in ranked], ['sh500003', 'sh500000'])

    def test_no_low_premium_does_not_reserve_even_when_ranking_exists(self):
        self.write_funds()
        self.assertEqual(len(premium_alert.select_ranked_funds(self.root, '2026-09-23')), 4)
        self.assertFalse(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        self.assertFalse(self.state.exists())

    def test_historical_comparison_excludes_today_and_uses_calendar_windows(self):
        history = {
            '2025-09-22': 8.0,
            '2026-06-22': 6.0,
            '2026-08-22': 4.0,
            '2026-09-21': 2.0,
            '2026-09-22': 3.0,
            '2026-09-23': 1.0,
        }
        comparison = premium_alert.premium_comparison(history, '2026-09-23')
        self.assertEqual(comparison['previous_date'], '2026-09-22')
        self.assertEqual(comparison['previous_premium'], 3.0)
        self.assertEqual(comparison['averages'][1], (2.5, 2))
        self.assertEqual(comparison['averages'][3], (3.0, 3))
        self.assertEqual(comparison['averages'][12], (3.75, 4))
        item = {'group': '纳斯达克100', 'name': '示例', 'code': 'sz000001',
                'premium': 1.0, 'price_date': '2026-09-23',
                'nav_date': '2026-09-22', 'comparison': comparison}
        body = premium_alert.build_message([item], '2026-09-23',
                                           ENV['ALERT_TO_EMAIL'], ENV['ALERT_SMTP_USER']).get_body(preferencelist=('plain',)).get_content()
        self.assertIn('上一有效交易日：+3.0000% (2026-09-22)', body)
        self.assertIn('变化：-2.0000 个百分点', body)
        self.assertIn('近1个月平均：+2.5000%（2个有效交易日）', body)
        self.assertIn('近3个月平均：+3.0000%（3个有效交易日）', body)
        self.assertIn('近1年平均：+3.7500%（4个有效交易日）', body)

    def test_invalid_history_and_insufficient_periods(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-22')})
        data_file = self.root / 'etf_all.json'
        data = json.loads(data_file.read_text(encoding='utf-8'))
        info = next(iter(data.values()))
        info['premium'] = [
            {'date': '2026-09-18', 'nav_date': '2026-09-17', 'value': 3.0},
            {'date': '2026-09-21', 'nav_date': '2026-09-18', 'value': 2.0},
            info['premium'][0],
        ]
        info['price'].extend([{'date': '2026-09-18', 'value': 1.03},
                              {'date': '2026-09-21', 'value': 1.09}])
        info['nav'].extend([{'date': '2026-09-17', 'value': 1.0},
                            {'date': '2026-09-18', 'value': 1.0}])
        data_file.write_text(json.dumps(data), encoding='utf-8')
        candidate = premium_alert.select_candidates(self.root, '2026-09-23')[0]
        self.assertEqual(candidate['comparison']['previous_date'], '2026-09-18')
        self.assertIsNone(candidate['comparison']['averages'][1])
        body = premium_alert.build_message([candidate], '2026-09-23',
                                           ENV['ALERT_TO_EMAIL'], ENV['ALERT_SMTP_USER']).get_body(preferencelist=('plain',)).get_content()
        self.assertIn('近1个月平均：数据不足', body)
        self.assertNotIn('2026-09-21)', body)

    def test_calendar_month_end_clamps(self):
        self.assertEqual(premium_alert.months_ago(datetime(2026, 3, 31).date(), 1).isoformat(),
                         '2026-02-28')

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
        plain = SMTP.sent[0].get_body(preferencelist=('plain',)).get_content()
        self.assertIn('4.9999%', plain)
        self.assertIn('上一有效交易日：数据不足', plain)
        self.assertIn('当日场内ETF溢价从高到低', plain)
        self.assertEqual(SMTP.sent[0].get_body(preferencelist=('html',)).get_content().count('<tr>'), 5)

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
