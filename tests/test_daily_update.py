import unittest
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
        self.assertEqual(latest['date'], '2026-07-20')
        self.assertEqual(latest['nav_date'], '2026-07-16')
        self.assertEqual(latest['value'], 6.6178)
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
            patch.object(daily_update, 'write_update') as write_update,
        ):
            exit_code = daily_update.main([])

        self.assertEqual(exit_code, 1)
        write_update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
