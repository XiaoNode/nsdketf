import argparse
import bisect
import json
import math
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime


DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(DIR, 'index.html')
DATA_DIR = os.path.join(DIR, 'data')
HEADERS = {'User-Agent': 'Mozilla/5.0'}
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')
DAILY_PRICE_DAYS = 30
DAILY_NAV_DAYS = 40
MAX_ABS_PREMIUM = 50.0
REQUEST_RETRIES = 3


def load_codes(json_file):
    """Read all ETF codes from a complete data file."""
    path = os.path.join(DIR, json_file)
    with open(path, 'r', encoding='utf-8') as f:
        return list(json.load(f).keys())


def is_valid_date(value):
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        return False
    try:
        datetime.strptime(value, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def parse_positive_number(value, field, code):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f'{code}: invalid {field} value {value!r}')
    return number


def request_with_retry(label, operation):
    last_error = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt < REQUEST_RETRIES:
                delay = 2 ** (attempt - 1)
                print(f'    [Retry {attempt}/{REQUEST_RETRIES}] {label}: {exc}')
                time.sleep(delay)
    raise RuntimeError(f'{label} failed after {REQUEST_RETRIES} attempts: {last_error}') from last_error


def fetch_nav(code, days=DAILY_NAV_DAYS, start_date=None):
    """Fetch actual published NAV records.

    With start_date, all pages back to that date are fetched. Otherwise only
    the most recent ``days`` source records are requested.
    """
    pure_code = code[2:]
    # Eastmoney silently caps this endpoint at 20 records per page even when
    # a larger pageSize is requested.
    page_size = 20
    max_pages = None if start_date else math.ceil(days / page_size)
    results = {}
    page = 1

    while max_pages is None or page <= max_pages:
        params = urllib.parse.urlencode({
            'callback': 'x',
            'fundCode': pure_code,
            'pageIndex': page,
            'pageSize': page_size,
            'startDate': start_date or '',
            'endDate': '',
        })
        url = f'https://api.fund.eastmoney.com/f10/lsjz?{params}'
        headers = {
            **HEADERS,
            'Referer': f'https://fundf10.eastmoney.com/F10/jjjz_{pure_code}.html',
        }

        def load_page():
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                text = resp.read().decode('utf-8').strip()
            if text.startswith('x(') and text.endswith(')'):
                text = text[2:-1]
            payload = json.loads(text)
            if payload.get('ErrCode') not in (None, 0):
                raise ValueError(f"Eastmoney error: {payload.get('ErrMsg')}")
            data = payload.get('Data') or {}
            return data.get('LSJZList') or []

        records = request_with_retry(f'NAV {code} page {page}', load_page)
        if not records:
            break

        for item in records:
            date = item.get('FSRQ')
            nav = item.get('DWJZ')
            if not date or not nav or nav == '--':
                continue
            if not is_valid_date(date):
                raise ValueError(f'{code}: invalid NAV date {date!r}')
            results[date] = parse_positive_number(nav, 'NAV', code)

        if len(records) < page_size:
            break
        page += 1
        if start_date:
            time.sleep(0.05)

    if not results:
        raise RuntimeError(f'NAV {code}: source returned no usable records')
    return results


def fetch_price(code, days=DAILY_PRICE_DAYS):
    """Fetch recent unadjusted market closing prices from Sina."""
    params = urllib.parse.urlencode({
        'symbol': code,
        'scale': 240,
        'ma': 'no',
        'datalen': days,
    })
    url = ('https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'CN_MarketData.getKLineData?{params}')

    def load_prices():
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        if not isinstance(payload, list) or not payload:
            raise ValueError('source returned no price records')
        parsed = {}
        for item in payload:
            date = str(item.get('day', '')).split(' ')[0]
            if not is_valid_date(date):
                raise ValueError(f'invalid price date {date!r}')
            parsed[date] = parse_positive_number(item.get('close'), 'price', code)
        return parsed

    return request_with_retry(f'Price {code}', load_prices)


def changed_data_months(before, after):
    changed = set()
    for field in ('price', 'nav', 'premium'):
        old_items = before.get(field, [])
        new_items = after.get(field, [])
        months = {
            item['date'][:7]
            for item in old_items + new_items
            if is_valid_date(item.get('date'))
        }
        for month in months:
            old_month = [item for item in old_items if item['date'].startswith(month)]
            new_month = [item for item in new_items if item['date'].startswith(month)]
            if old_month != new_month:
                changed.add(month)
    return changed


def merge_etf_data(info, prices, navs, replace_all_nav=False):
    """Merge source data while preserving NAV publication dates."""
    updated = dict(info)
    price_dict = {item['date']: item['value'] for item in info.get('price', [])}
    price_dict.update(prices)

    if replace_all_nav:
        nav_dict = dict(navs)
    else:
        cutoff = min(navs)
        # Recent stored rows may be synthetic values created by the old updater.
        # Replace the entire fetched window so they cannot influence new premiums.
        nav_dict = {
            item['date']: item['value']
            for item in info.get('nav', [])
            if item['date'] < cutoff
        }
        nav_dict.update(navs)

    price_arr = [
        {'date': date, 'value': value}
        for date, value in sorted(price_dict.items())
    ]
    nav_arr = [
        {'date': date, 'value': value}
        for date, value in sorted(nav_dict.items())
    ]

    nav_dates = sorted(nav_dict)
    premium_arr = []
    rejected = []
    for price in price_arr:
        price_date = price['date']
        nav_index = bisect.bisect_right(nav_dates, price_date) - 1
        if nav_index < 0:
            continue
        nav_date = nav_dates[nav_index]
        premium = round((price['value'] / nav_dict[nav_date] - 1) * 100, 4)
        if abs(premium) > MAX_ABS_PREMIUM:
            rejected.append((price_date, premium))
            continue
        premium_arr.append({
            'date': price_date,
            'value': premium,
            'nav_date': nav_date,
        })

    updated['price'] = price_arr
    updated['nav'] = nav_arr
    updated['premium'] = premium_arr
    return updated, changed_data_months(info, updated), rejected


def update_html_scripts(month, prefix):
    """Add a monthly data script reference when a new month appears."""
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError(f'invalid month {month!r}')
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    script_tag = f'<script src="data/{prefix}_data_{month}.js"></script>'
    if script_tag in html:
        return

    pattern = rf'<script src="data/{prefix}_data_(\d{{4}}-\d{{2}})\.js"></script>'
    matches = list(re.finditer(pattern, html))
    if not matches:
        raise RuntimeError(f'cannot find existing {prefix} script references in index.html')
    latest = max(matches, key=lambda match: match.group(1))
    html = html[:latest.end()] + f'\n{script_tag}' + html[latest.end():]
    write_text_atomic(HTML_PATH, html)


def write_text_atomic(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.tmp-', dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def prepare_update(codes, json_file, full_nav=False):
    json_path = os.path.join(DIR, json_file)
    with open(json_path, 'r', encoding='utf-8') as f:
        all_data = json.load(f)

    changed_months = set()
    failures = []
    print(f'\n>>> Preparing {json_file} ({len(codes)} ETFs)...')
    for code in codes:
        info = all_data.get(code)
        if not info:
            failures.append(f'{code}: missing metadata')
            continue
        print(f"  Processing {code} ({info['name']})...")
        try:
            prices = fetch_price(code)
            if full_nav:
                start_date = min(item['date'] for item in info.get('price', []))
                navs = fetch_nav(code, start_date=start_date)
            else:
                navs = fetch_nav(code)
            updated, months, rejected = merge_etf_data(
                info, prices, navs, replace_all_nav=full_nav
            )
            all_data[code] = updated
            changed_months.update(months)
            for date, premium in rejected:
                print(f'    [Warning] Skipped implausible premium {premium:.4f}% on {date}')
        except Exception as exc:
            failures.append(f'{code}: {exc}')

    return {
        'codes': codes,
        'json_file': json_file,
        'all_data': all_data,
        'changed_months': changed_months,
        'failures': failures,
    }


def write_update(result, prefix):
    all_data = result['all_data']
    json_path = os.path.join(DIR, result['json_file'])
    write_text_atomic(json_path, json.dumps(all_data, ensure_ascii=False))

    for month in sorted(result['changed_months']):
        subset = {}
        for code in result['codes']:
            info = all_data[code]
            base = {key: value for key, value in info.items()
                    if key not in ('price', 'premium', 'nav')}
            subset[code] = {
                **base,
                'price': [item for item in info.get('price', [])
                          if item['date'].startswith(month)],
                'premium': [item for item in info.get('premium', [])
                            if item['date'].startswith(month)],
                'nav': [item for item in info.get('nav', [])
                        if item['date'].startswith(month)],
            }

        if not any(subset[code]['price'] for code in subset):
            continue
        js_path = os.path.join(DATA_DIR, f'{prefix}_data_{month}.js')
        if not os.path.exists(js_path):
            update_html_scripts(month, prefix)
        var_name = f'{prefix.upper()}_DATA_{month.replace("-", "")}'
        content = f'const {var_name} = {json.dumps(subset, ensure_ascii=False)};\n'
        write_text_atomic(js_path, content)
        print(f'  [JS] Updated {js_path}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Update ETF price and NAV data')
    parser.add_argument(
        '--full-nav',
        action='store_true',
        help='refetch all NAV history and remove legacy forward-filled NAV rows',
    )
    args = parser.parse_args(argv)

    print(f"ETF Daily Update Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    results = [
        (prepare_update(load_codes('etf_all.json'), 'etf_all.json', args.full_nav), 'etf'),
        (prepare_update(load_codes('sp500_all.json'), 'sp500_all.json', args.full_nav), 'sp500'),
    ]
    failures = [failure for result, _ in results for failure in result['failures']]
    if failures:
        print('\nUpdate aborted; no data files were written:')
        for failure in failures:
            print(f'  [Error] {failure}')
        return 1

    for result, prefix in results:
        write_update(result, prefix)
    print('\nGlobal update complete!')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
