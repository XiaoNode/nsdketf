import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
        self.write_funds({'etf_all.json': (4.9999, '2026-09-23', '2026-09-23')})

    def write_funds(self, overrides=None):
        overrides = overrides or {}
        for i, (_, filename) in enumerate(premium_alert.GROUPS):
            value, price_date, nav_date = overrides.get(
                filename, (6.0, '2026-09-23', '2026-09-23'))
            data = {f'sh50000{i}': {
                'name': f'基金{i}',
                'price': [{'date': price_date, 'value': 1 + value / 100}],
                'nav': [{'date': nav_date, 'value': 1.0}],
                'premium': [{'date': price_date, 'nav_date': nav_date, 'value': value}],
            }}
            (self.root / filename).write_text(json.dumps(data), encoding='utf-8')

    def send_with_mock(self):
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
        return SMTP.sent

    # ---------- 数据校验：沿用原有的严格口径 ----------

    def test_cross_date_nav_is_not_verified_even_when_formula_matches(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-22')})
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])

    def test_below_threshold_is_flagged_as_alert(self):
        items = premium_alert.select_candidates(self.root, '2026-09-23')
        self.assertEqual(len(items), 1)
        board = premium_alert.select_board(self.root, '2026-09-23')
        self.assertEqual(board['alerts'], 1)
        self.assertEqual([item['premium'] for item in board['lowest']],
                         [4.9999, 6.0, 6.0, 6.0])
        self.assertTrue(board['lowest'][0]['alert'])
        self.assertFalse(board['lowest'][1]['alert'])

    def test_board_sorts_lowest_ascending_and_highest_descending(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-23'),
                          'sp500_all.json': (12.0, '2026-09-23', '2026-09-23'),
                          'us50_all.json': (8.0, '2026-09-23', '2026-09-23'),
                          'djia_all.json': (5.0, '2026-09-23', '2026-09-23')})
        board = premium_alert.select_board(self.root, '2026-09-23')
        self.assertEqual([item['premium'] for item in board['lowest']], [4.0, 5.0, 8.0, 12.0])
        self.assertEqual([item['premium'] for item in board['highest']], [12.0, 8.0, 5.0, 4.0])
        self.assertEqual(board['total'], 4)

    def test_board_limits_to_five_rows(self):
        data = {}
        for i in range(8):
            code = f'sh60000{i}'
            data[code] = {
                'name': f'基金{i}',
                'price': [{'date': '2026-09-23', 'value': 1 + (i + 1) / 100}],
                'nav': [{'date': '2026-09-23', 'value': 1.0}],
                'premium': [{'date': '2026-09-23', 'nav_date': '2026-09-23',
                             'value': float(i + 1)}],
            }
        (self.root / 'etf_all.json').write_text(json.dumps(data), encoding='utf-8')
        board = premium_alert.select_board(self.root, '2026-09-23')
        self.assertEqual([item['premium'] for item in board['lowest']], [1.0, 2.0, 3.0, 4.0, 5.0])
        # 其余三组默认 6.0，因此最高榜第五位是 6.0 而非 4.0
        self.assertEqual([item['premium'] for item in board['highest']], [8.0, 7.0, 6.0, 6.0, 6.0])
        self.assertEqual(board['total'], 11)

    def test_ranking_excludes_stale_and_unverified_funds(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-23'),
                          'sp500_all.json': (12.0, '2026-09-22', '2026-09-21'),
                          'us50_all.json': (8.0, '2026-09-23', '2026-09-23')})
        file = self.root / 'us50_all.json'
        data = json.loads(file.read_text(encoding='utf-8'))
        next(iter(data.values()))['price'][0]['value'] = 1.09
        file.write_text(json.dumps(data), encoding='utf-8')
        ranked = premium_alert.select_ranked_funds(self.root, '2026-09-23')
        self.assertEqual([item['code'] for item in ranked], ['sh500003', 'sh500000'])

    def test_old_price_or_nav_is_rejected(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-22', '2026-09-21')})
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-20')})
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])

    def test_unverified_premium_is_rejected(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-23')})
        data_file = self.root / 'etf_all.json'
        data = json.loads(data_file.read_text(encoding='utf-8'))
        first = next(iter(data.values()))
        first['price'][0]['value'] = 1.06
        data_file.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])

    def test_exactly_five_is_not_selected_as_alert(self):
        self.write_funds({'etf_all.json': (5.0, '2026-09-23', '2026-09-23')})
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])
        self.assertEqual(premium_alert.select_board(self.root, '2026-09-23')['alerts'], 0)

    # ---------- 数据日锚点：净值延迟/假期补发 ----------

    def test_latest_verified_date_uses_newest_matching_session(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-23'),
                          'sp500_all.json': (12.0, '2026-09-22', '2026-09-21')})
        self.assertEqual(premium_alert.latest_verified_date(self.root), '2026-09-23')

    def test_latest_verified_date_ignores_unpublished_session(self):
        data = json.loads((self.root / 'etf_all.json').read_text(encoding='utf-8'))
        info = next(iter(data.values()))
        # 有 9-30 收盘价但没有 9-30 净值（长假停更）时，锚点仍应是 9-23
        info['price'].append({'date': '2026-09-30', 'value': 1.05})
        (self.root / 'etf_all.json').write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(premium_alert.latest_verified_date(self.root), '2026-09-23')

    def test_catch_up_run_reports_delayed_session(self):
        """净值延迟披露后，该数据日仍会被补发一次，而不是被永久跳过。"""
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        self.assertTrue(premium_alert.claim(self.root, self.state, self.now, env=ENV))
        # 长假后的第一次运行：同一数据日不再重发
        later = datetime(2026, 10, 9, 20, 30, tzinfo=BJ)
        self.assertFalse(premium_alert.prepare(self.root, self.state, later, env=ENV))

    # ---------- 每个交易日都要发 ----------

    def test_digest_is_sent_even_without_low_premium(self):
        """没有低于 5% 的标的也要发每日播报（此前的行为是不发）。"""
        self.write_funds()
        self.assertEqual(premium_alert.select_candidates(self.root, '2026-09-23'), [])
        self.assertEqual(len(premium_alert.select_ranked_funds(self.root, '2026-09-23')), 4)
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        state = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertEqual(state['status'], 'reserved')
        self.assertEqual(state['price_date'], '2026-09-23')

    def test_no_verified_data_does_not_reserve(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-22'),
                          'sp500_all.json': (6.0, '2026-09-23', '2026-09-22'),
                          'us50_all.json': (6.0, '2026-09-23', '2026-09-22'),
                          'djia_all.json': (6.0, '2026-09-23', '2026-09-22')})
        self.assertFalse(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        self.assertFalse(self.state.exists())

    def test_missing_secrets_do_not_reserve(self):
        self.assertFalse(premium_alert.prepare(self.root, self.state, self.now, env={}))
        self.assertFalse(self.state.exists())

    def test_reservation_is_once_per_session_and_private(self):
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        self.assertFalse(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        state = self.state.read_text(encoding='utf-8')
        self.assertNotIn(ENV['ALERT_TO_EMAIL'], state)
        self.assertNotIn(ENV['ALERT_SMTP_PASSWORD'], state)
        self.assertEqual(json.loads(state)['date'], '2026-09-23')

    def test_next_morning_does_not_resend_yesterdays_price(self):
        premium_alert.prepare(self.root, self.state, self.now, env=ENV)
        json.loads(self.state.read_text(encoding='utf-8'))
        morning = datetime(2026, 9, 24, 8, 20, tzinfo=BJ)
        self.assertFalse(premium_alert.prepare(self.root, self.state, morning, env=ENV))

    def test_changed_data_refuses_claim(self):
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-23')})
        self.assertFalse(premium_alert.claim(self.root, self.state, self.now, env=ENV))
        self.assertEqual(premium_alert.read_state(self.state)['status'], 'reserved')

    def test_send_requires_committed_claim(self):
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        with self.assertRaisesRegex(ValueError, 'claimed'):
            premium_alert.send(self.root, self.state, self.now, env=ENV)

    # ---------- 邮件正文 ----------

    def test_send_produces_low_and_high_tables(self):
        self.assertTrue(premium_alert.prepare(self.root, self.state, self.now, env=ENV))
        self.assertTrue(premium_alert.claim(self.root, self.state, self.now, env=ENV))
        sent = self.send_with_mock()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['To'], ENV['ALERT_TO_EMAIL'])
        self.assertIn('数据日 2026-09-23', sent[0]['Subject'])
        self.assertIn('其中1只低于5%', sent[0]['Subject'].replace(' ', ''))
        plain = sent[0].get_body(preferencelist=('plain',)).get_content()
        self.assertIn('溢价最低', plain)
        self.assertIn('溢价最高', plain)
        self.assertIn('⚠ 低于5%，重点关注', plain)
        self.assertIn('4.9999%', plain)
        self.assertIn('上一有效交易日：数据不足', plain)
        html_body = sent[0].get_body(preferencelist=('html',)).get_content()
        self.assertEqual(html_body.count('<table'), 2)
        self.assertIn('background:#fff2cc', html_body)

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
                'nav_date': '2026-09-23', 'comparison': comparison, 'alert': True}
        board = {'price_date': '2026-09-23', 'total': 1, 'alerts': 1,
                 'lowest': [item], 'highest': [item]}
        body = premium_alert.build_message(
            board, '2026-09-23', ENV['ALERT_TO_EMAIL'],
            ENV['ALERT_SMTP_USER']).get_body(preferencelist=('plain',)).get_content()
        self.assertIn('上一有效交易日：+3.0000% (2026-09-22)', body)
        self.assertIn('变化：-2.0000 个百分点', body)
        self.assertIn('近1个月平均：+2.5000%（2个有效交易日）', body)
        self.assertIn('近3个月平均：+3.0000%（3个有效交易日）', body)
        self.assertIn('近1年平均：+3.7500%（4个有效交易日）', body)

    def test_invalid_history_and_insufficient_periods(self):
        self.write_funds({'etf_all.json': (4.0, '2026-09-23', '2026-09-23')})
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
        item = premium_alert.select_board(self.root, '2026-09-23')['lowest'][0]
        self.assertIsNone(item['comparison']['previous_date'])
        self.assertIsNone(item['comparison']['averages'][1])
        board = premium_alert.select_board(self.root, '2026-09-23')
        body = premium_alert.build_message(
            board, '2026-09-23', ENV['ALERT_TO_EMAIL'],
            ENV['ALERT_SMTP_USER']).get_body(preferencelist=('plain',)).get_content()
        self.assertIn('近1个月平均：数据不足', body)
        self.assertNotIn('2026-09-21)', body)

    # ---------- IOPV 口径：当日可得，与券商 App 一致 ----------

    def add_iopv_session(self, day='2026-09-24', price=2.230, iopv=1.9796, value=12.649):
        file = self.root / 'etf_all.json'
        data = json.loads(file.read_text(encoding='utf-8'))
        info = next(iter(data.values()))
        info['price'].append({'date': day, 'value': price})
        info['iopv_premium'] = [{'date': day, 'value': value, 'iopv': iopv}]
        file.write_text(json.dumps(data), encoding='utf-8')

    def test_iopv_series_takes_precedence_over_nav_series(self):
        self.write_funds()
        self.add_iopv_session()
        ranked = premium_alert.select_ranked_funds(self.root, '2026-09-24')
        item = [row for row in ranked if row['code'] == 'sh500000'][0]
        self.assertEqual(item['series'], 'iopv_premium')
        self.assertEqual(item['premium'], 12.649)
        # 该日尚无单位净值可核对，NAV 口径应诚实留空而不是套用旧净值
        self.assertIsNone(item['nav_premium'])
        self.assertEqual(item['nav_date'], '2026-09-24')

    def test_verified_history_falls_back_to_nav_when_iopv_missing(self):
        self.write_funds()
        ranked = premium_alert.select_ranked_funds(self.root, '2026-09-23')
        self.assertTrue(all(row['series'] == 'premium' for row in ranked))

    def test_latest_verified_date_advances_with_iopv_only_session(self):
        self.write_funds()
        self.assertEqual(premium_alert.latest_verified_date(self.root), '2026-09-23')
        self.add_iopv_session()
        self.assertEqual(premium_alert.latest_verified_date(self.root), '2026-09-24')

    def test_email_shows_both_gauges(self):
        self.write_funds()
        self.add_iopv_session()
        board = premium_alert.select_board(self.root, '2026-09-24')
        body = premium_alert.build_message(
            board, '2026-09-24', ENV['ALERT_TO_EMAIL'],
            ENV['ALERT_SMTP_USER']).get_body(preferencelist=('plain',)).get_content()
        self.assertIn('单位净值口径', body)
        self.assertIn('净值未披露', body)
        self.assertIn('12.649', body)
        self.assertIn('IOPV', premium_alert.series_label(board))

    def test_calendar_month_end_clamps(self):
        self.assertEqual(premium_alert.months_ago(datetime(2026, 3, 31).date(), 1).isoformat(),
                         '2026-02-28')


if __name__ == '__main__':
    unittest.main()
