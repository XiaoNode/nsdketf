import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import daily_update


class MergeEtfDataTests(unittest.TestCase):
    def test_recent_source_nav_replaces_forward_filled_rows(self):
        info = {
            'name': 'Example ETF',
            'price': [
                {'date': '2026-07-15', 'value': 2.188},
                {'date': '2026-07-16', 'value': 2.167},
                {'date': '2026-07-17', 'value': 2.107},
                {'date': '2026-07-20', 'value': 2.096},
            ],
            # The old updater incorrectly copied the July 15 NAV forward.
            'nav': [
                {'date': '2026-07-15', 'value': 1.9981},
                {'date': '2026-07-16', 'value': 1.9981},
                {'date': '2026-07-17', 'value': 1.9981},
                {'date': '2026-07-20', 'value': 1.9981},
            ],
            'premium': [],
        }
        actual_navs = {
            '2026-07-15': 1.9981,
            '2026-07-16': 1.9659,
        }

        updated, _, rejected = daily_update.merge_etf_data(
            info, {}, actual_navs
        )

        self.assertEqual(
            updated['nav'],
            [
                {'date': '2026-07-15', 'value': 1.9981},
                {'date': '2026-07-16', 'value': 1.9659},
            ],
        )
        latest = updated['premium'][-1]
        self.assertEqual(latest['date'], '2026-07-16')
        self.assertEqual(latest['nav_date'], '2026-07-16')
        self.assertNotIn('2026-07-20', [row['date'] for row in updated['premium']])
        self.assertEqual(rejected, [])

    def test_implausible_split_premium_is_not_published(self):
        info = {'price': [], 'nav': [], 'premium': []}

        updated, _, rejected = daily_update.merge_etf_data(
            info,
            {'2022-07-04': 2.384},
            {'2022-07-04': 0.5992},
            replace_all_nav=True,
        )

        self.assertEqual(updated['premium'], [])
        self.assertEqual(rejected, [('2022-07-04', 297.8638)])


class FetchNavTests(unittest.TestCase):
    def test_full_nav_fetch_continues_after_a_twenty_record_page(self):
        first_page = [
            {
                'FSRQ': f'2026-06-{day:02d}',
                'DWJZ': '1.0',
            }
            for day in range(1, 21)
        ]
        final_page = [{'FSRQ': '2026-05-31', 'DWJZ': '0.9'}]

        with (
            patch.object(
                daily_update,
                'request_with_retry',
                side_effect=[first_page, final_page],
            ) as request,
            patch.object(daily_update.time, 'sleep'),
        ):
            navs = daily_update.fetch_nav('sh513100', start_date='2026-05-01')

        self.assertEqual(len(navs), 21)
        self.assertEqual(request.call_count, 2)


class TrackingErrorTests(unittest.TestCase):
    @staticmethod
    def _series(days, daily_growth, end='2026-09-17'):
        """Build a date->value series of ``days`` consecutive days ending at ``end``."""
        end_date = datetime.strptime(end, '%Y-%m-%d')
        series = {}
        value = 1.0
        for offset in range(days, 0, -1):
            date = (end_date - timedelta(days=offset - 1)).strftime('%Y-%m-%d')
            value *= 1 + daily_growth
            series[date] = value
        return series

    def test_zero_tracking_error_when_fund_matches_index(self):
        series = self._series(90, 0.001)
        # 基金与指数完全同步 -> 跟踪误差为 0
        error = daily_update.compute_tracking_error(series, dict(series), years=1)
        self.assertEqual(error, 0.0)

    def test_tracking_error_is_positive_when_fund_diverges(self):
        # 跟踪误差是日收益差值的标准差：需让差值本身有波动，
        # 恒定收益差（如始终 1% vs 2%）的标准差为 0，不构成跟踪误差。
        fund = self._series(90, 0.01)
        index = self._series(90, 0.01)
        dates = sorted(index)
        for position, date in enumerate(dates):
            # 指数交替多涨/少涨，制造真实的跟踪偏离波动
            index[date] *= 1 + (0.02 if position % 2 else -0.02)
        error = daily_update.compute_tracking_error(fund, index, years=1)
        self.assertIsNotNone(error)
        self.assertGreater(error, 0)

    def test_tracking_error_is_none_when_overlap_too_short(self):
        fund = self._series(20, 0.01)
        index = self._series(20, 0.01)
        self.assertIsNone(daily_update.compute_tracking_error(fund, index, years=1))

    def test_period_return_matches_manual_calculation(self):
        series = {'2025-09-17': 100.0, '2026-09-17': 150.0}
        # 100 -> 150 即 +50%
        self.assertEqual(daily_update.compute_period_return(series, 1), 50.0)

    def test_period_return_is_none_when_history_too_short(self):
        series = {'2026-01-01': 1.0, '2026-09-17': 1.2}
        self.assertIsNone(daily_update.compute_period_return(series, 5))


class NormalizeGroupPremiumsTests(unittest.TestCase):
    def test_holiday_does_not_justify_using_old_nav(self):
        # 9-25 休市，但 9-28 有交易且无 9-28 单位净值；不能沿用 9-24。
        all_data = {
            'sz159501': {
                'price': [
                    {'date': '2026-09-23', 'value': 2.219},
                    {'date': '2026-09-24', 'value': 2.212},
                    {'date': '2026-09-28', 'value': 2.214},
                ],
                'nav': [
                    {'date': '2026-09-23', 'value': 1.9247},
                    {'date': '2026-09-24', 'value': 1.9257},
                ],
                'premium': [],
            },
        }

        daily_update.normalize_group_premiums(all_data, ['sz159501'])

        self.assertNotIn('2026-09-28', [row['date'] for row in all_data['sz159501']['premium']])
        self.assertEqual(all_data['sz159501']['premium'][-1]['date'], '2026-09-24')

        # 日后 NAV 回填后，无需改价序列即可自动生成 9-28 的同日值。
        all_data['sz159501']['nav'].append({'date': '2026-09-28', 'value': 1.9300})
        daily_update.normalize_group_premiums(all_data, ['sz159501'])
        latest = all_data['sz159501']['premium'][-1]
        self.assertEqual(latest['date'], '2026-09-28')
        self.assertEqual(latest['nav_date'], '2026-09-28')
        self.assertEqual(latest['value'], round((2.214 / 1.9300 - 1) * 100, 4))

    def test_laggard_nav_does_not_drag_group_to_older_nav(self):
        # 一只基金净值披露滞后（只有 9-23），不应拖累其他基金用更旧的净值。
        all_data = {
            'sz159501': {
                'price': [{'date': '2026-09-24', 'value': 2.212}],
                'nav': [
                    {'date': '2026-09-23', 'value': 1.9247},
                    {'date': '2026-09-24', 'value': 1.9257},
                ],
                'premium': [],
            },
            'sh513110': {
                'price': [{'date': '2026-09-24', 'value': 2.5}],
                'nav': [{'date': '2026-09-23', 'value': 2.3528}],
                'premium': [],
            },
        }

        daily_update.normalize_group_premiums(all_data, ['sz159501', 'sh513110'])

        premium = all_data['sz159501']['premium'][-1]
        self.assertEqual(premium['nav_date'], '2026-09-24')
        self.assertEqual(premium['value'], round((2.212 / 1.9257 - 1) * 100, 4))
        self.assertEqual(all_data['sh513110']['premium'], [])

    def test_truly_stale_latest_premium_is_dropped(self):
        # 最新价 9-28 但净值只到 9-23（早于前一真实交易日 9-24）-> 仍应丢弃。
        all_data = {
            'sz159501': {
                'price': [
                    {'date': '2026-09-24', 'value': 2.212},
                    {'date': '2026-09-28', 'value': 2.214},
                ],
                'nav': [{'date': '2026-09-23', 'value': 1.9247}],
                'premium': [],
            },
        }

        daily_update.normalize_group_premiums(all_data, ['sz159501'])

        dates = [p['date'] for p in all_data['sz159501']['premium']]
        self.assertNotIn('2026-09-28', dates)
        self.assertNotIn('2026-09-24', dates)


class UpdateFailureTests(unittest.TestCase):
    def test_main_does_not_write_when_any_fund_fails(self):
        failed_result = {
            'codes': ['sh000001'],
            'json_file': 'unused.json',
            'all_data': {},
            'changed_months': set(),
            'failures': ['sh000001: source unavailable'],
        }

        with (
            patch.object(daily_update, 'load_codes', return_value=['sh000001']),
            patch.object(daily_update, 'prepare_update', return_value=failed_result),
            patch.object(daily_update, 'update_otc_quota_status'),
            patch.object(daily_update, 'write_update') as write_update,
        ):
            exit_code = daily_update.main([])

        self.assertEqual(exit_code, 1)
        write_update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
