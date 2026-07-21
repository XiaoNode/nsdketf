import json
import os
import re
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GeneratedDataTests(unittest.TestCase):
    def test_complete_data_has_valid_nav_provenance_and_premiums(self):
        for filename in ('etf_all.json', 'sp500_all.json'):
            with self.subTest(filename=filename):
                with open(os.path.join(ROOT, filename), encoding='utf-8') as f:
                    data = json.load(f)

                for code, info in data.items():
                    with self.subTest(filename=filename, code=code):
                        price_dates = [item['date'] for item in info['price']]
                        nav_dates = [item['date'] for item in info['nav']]
                        self.assertEqual(price_dates, sorted(set(price_dates)))
                        self.assertEqual(nav_dates, sorted(set(nav_dates)))

                        prices = {item['date']: item['value'] for item in info['price']}
                        navs = {item['date']: item['value'] for item in info['nav']}
                        premium_dates = []
                        for item in info['premium']:
                            premium_dates.append(item['date'])
                            self.assertIn(item['date'], prices)
                            self.assertIn(item['nav_date'], navs)
                            self.assertLessEqual(item['nav_date'], item['date'])
                            expected = round(
                                (prices[item['date']] / navs[item['nav_date']] - 1) * 100,
                                4,
                            )
                            self.assertEqual(item['value'], expected)
                            self.assertLessEqual(
                                abs(item['value']), daily_premium_limit()
                            )
                        self.assertEqual(premium_dates, sorted(set(premium_dates)))

    def test_monthly_javascript_matches_complete_json(self):
        for prefix, filename in (
            ('etf', 'etf_all.json'),
            ('sp500', 'sp500_all.json'),
        ):
            with open(os.path.join(ROOT, filename), encoding='utf-8') as f:
                complete = json.load(f)

            pattern = re.compile(rf'^{prefix}_data_(\d{{4}}-\d{{2}})\.js$')
            for data_filename in os.listdir(os.path.join(ROOT, 'data')):
                match = pattern.fullmatch(data_filename)
                if not match:
                    continue
                month = match.group(1)
                path = os.path.join(ROOT, 'data', data_filename)
                with open(path, encoding='utf-8') as f:
                    text = f.read().strip()
                payload = json.loads(text[text.index('=') + 1:].strip().removesuffix(';'))

                for code, monthly in payload.items():
                    with self.subTest(file=data_filename, code=code):
                        for field in ('price', 'nav', 'premium'):
                            expected = [
                                item for item in complete[code][field]
                                if item['date'].startswith(month)
                            ]
                            self.assertEqual(monthly[field], expected)


def daily_premium_limit():
    # Import lazily so this test also checks the generated files as plain JSON.
    import daily_update
    return daily_update.MAX_ABS_PREMIUM


if __name__ == '__main__':
    unittest.main()
