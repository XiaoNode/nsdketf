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
            patch.object(daily_update, 'fetch_all_quotas', return_value={}),
            patch.object(daily_update, 'write_update') as write_update,
        ):
            exit_code = daily_update.main([])

        self.assertEqual(exit_code, 1)
        write_update.assert_not_called()


class QuotaTests(unittest.TestCase):
    def test_normalize_code(self):
        self.assertEqual(daily_update.normalize_code('sh513870'), '513870')
        self.assertEqual(daily_update.normalize_code('sz159501'), '159501')
        self.assertEqual(daily_update.normalize_code('513100'), '513100')

    def test_parse_jisilu_limit(self):
        self.assertEqual(
            daily_update.parse_jisilu_limit(None),
            {'value': None, 'status': 'open', 'desc': '不限购'},
        )
        self.assertEqual(
            daily_update.parse_jisilu_limit(0),
            {'value': None, 'status': 'open', 'desc': '不限购'},
        )
        self.assertEqual(
            daily_update.parse_jisilu_limit(1000),
            {'value': 1000, 'status': 'limit', 'desc': '单日限购1000元'},
        )
        self.assertEqual(
            daily_update.parse_jisilu_limit('暂停'),
            {'value': 0, 'status': 'suspended', 'desc': '暂停申购'},
        )
        self.assertEqual(
            daily_update.parse_jisilu_limit('单日限购1万'),
            {'value': 10000, 'status': 'limit', 'desc': '单日限购1万'},
        )
        self.assertEqual(
            daily_update.parse_jisilu_limit('不限购'),
            {'value': None, 'status': 'open', 'desc': '不限购'},
        )
        self.assertEqual(
            daily_update.parse_jisilu_limit('限大额'),
            {'value': None, 'status': 'unknown', 'desc': '限大额'},
        )

    def test_fetch_all_quotas_jisilu_then_em_fallback(self):
        sample = {'rows': [
            {'fund_id': '513870', 'limit': 1000},
            {'fund_id': '159501', 'limit': '暂停'},
        ]}

        def fake_jisilu():
            result = {}
            for row in sample['rows']:
                q = daily_update.parse_jisilu_limit(row['limit'])
                q['source'] = 'jisilu'
                result[row['fund_id'][-6:]] = q
            return result

        with (
            patch.object(daily_update, 'fetch_jisilu_quotas', fake_jisilu),
            patch.object(daily_update, 'fetch_em_quota_status', return_value=None),
        ):
            out = daily_update.fetch_all_quotas(['sh513870', 'sz159501', 'sh513390'])

        self.assertEqual(out['sh513870']['value'], 1000)
        self.assertEqual(out['sz159501']['status'], 'suspended')
        # code missing from 集思录 and EM fallback unavailable -> unknown
        self.assertEqual(out['sh513390']['status'], 'unknown')
        self.assertIn('date', out['sh513870'])

    def test_main_attaches_quota_to_all_data(self):
        ok_result = {
            'codes': ['sh513870'],
            'json_file': 'x.json',
            'all_data': {'sh513870': {'code': 'sh513870', 'name': 'X'}},
            'changed_months': set(),
            'failures': [],
        }
        fake_quota = {
            'sh513870': {
                'date': '2026-09-07', 'value': 1000, 'status': 'limit',
                'desc': '单日限购1000元', 'source': 'jisilu',
            }
        }
        with (
            patch.object(daily_update, 'load_codes', return_value=['sh513870']),
            patch.object(daily_update, 'prepare_update', return_value=ok_result),
            patch.object(daily_update, 'fetch_all_quotas', return_value=fake_quota),
            patch.object(daily_update, 'write_update') as write_update,
            patch.object(daily_update, 'update_ndx_valuation'),
        ):
            rc = daily_update.main([])

        self.assertEqual(rc, 0)
        self.assertEqual(
            ok_result['all_data']['sh513870']['quota'], fake_quota['sh513870']
        )
        write_update.assert_called()


if __name__ == '__main__':
    unittest.main()
