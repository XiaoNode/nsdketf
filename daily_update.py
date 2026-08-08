import argparse
import bisect
import copy
import json
import math
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta


DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(DIR, 'index.html')
DATA_DIR = os.path.join(DIR, 'data')
HEADERS = {'User-Agent': 'Mozilla/5.0'}
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')
DAILY_PRICE_DAYS = 30
DAILY_NAV_DAYS = 40
MAX_ABS_PREMIUM = 50.0
REQUEST_RETRIES = 3
NDX_VALUATION_FILE = 'ndx_valuation.json'
# 蛋卷估值接口（PB / ROE / 股息率 / PEG / 综合百分位，需登录 cookie）
NDX_VALUATION_API = 'https://danjuanfunds.com/djapi/index/valuation/NDX'
# History of Market 公开 JSON（TTM PE / Forward PE，每日更新，免登录）
NDX_HOM_API = 'https://historyofmarket.com/api/ndx/forward-pe.json'


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


def changed_data_months_all(original, updated):
    """Collect months that changed between two full all_data dictionaries."""
    changed = set()
    for code in original:
        if code not in updated:
            continue
        changed.update(changed_data_months(original[code], updated[code]))
    return changed


def previous_trading_day(date_str):
    """Return the previous weekday (Mon-Fri) before date_str."""
    d = datetime.strptime(date_str, '%Y-%m-%d').date()
    while True:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            return d.strftime('%Y-%m-%d')


def normalize_group_premiums(all_data, codes):
    """Recompute premiums using the latest common NAV date for the group.

    QDII ETF NAVs are published at different times across fund companies.  For a
    cross-ETF comparison chart, using the newest per-fund NAV for each price date
    can make some funds appear to jump while others still use an older NAV.  This
    function finds the latest NAV date that is available for **all** funds in the
    group and uses that date for the tail of the premium series so the displayed
    series share a uniform NAV cutoff.  Earlier dates continue to use the latest
    available NAV per fund.

    If the newest common NAV is older than the previous trading day for the
    latest price date, the latest price date's premium is dropped rather than
    shown with a stale NAV.  This prevents QDII ETFs from displaying a T-day
    premium computed from a T-2 (or earlier) NAV simply because some funds have
    not yet published T-1 NAV.
    """
    if not codes:
        return

    nav_date_sets = []
    for code in codes:
        dates = {item['date'] for item in all_data.get(code, {}).get('nav', [])
                 if is_valid_date(item.get('date'))}
        nav_date_sets.append(dates)

    common_dates = set.intersection(*nav_date_sets)
    if not common_dates:
        return

    common_date = max(common_dates)

    latest_price_date = None
    for code in codes:
        info = all_data.get(code)
        if info and info.get('price'):
            candidate = max(p['date'] for p in info['price'])
            if latest_price_date is None or candidate > latest_price_date:
                latest_price_date = candidate
    if latest_price_date:
        expected_nav_date = previous_trading_day(latest_price_date)
        stale_tail = common_date < expected_nav_date
    else:
        stale_tail = False

    for code in codes:
        info = all_data.get(code)
        if not info:
            continue

        nav_dict = {item['date']: item['value'] for item in info.get('nav', [])}
        price_arr = info.get('price', [])
        premium_arr = []
        rejected = []

        for price in price_arr:
            pdate = price['date']
            pval = price['value']

            if pdate > common_date:
                ndate = common_date
            else:
                valid = [d for d in nav_dict if d <= pdate]
                if not valid:
                    continue
                ndate = max(valid)

            if stale_tail and pdate == latest_price_date and ndate < expected_nav_date:
                print(f"    [Warning] {code}: latest price date {pdate} expects NAV "
                      f"{expected_nav_date}, but newest common NAV is {common_date}; "
                      "dropping stale premium")
                continue

            nval = nav_dict[ndate]
            premium = round((pval / nval - 1) * 100, 4)
            if abs(premium) > MAX_ABS_PREMIUM:
                rejected.append((pdate, premium))
                continue
            premium_arr.append({'date': pdate, 'value': premium, 'nav_date': ndate})

        info['premium'] = premium_arr


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

    original_all_data = copy.deepcopy(all_data)
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
            updated, _, rejected = merge_etf_data(
                info, prices, navs, replace_all_nav=full_nav
            )
            all_data[code] = updated
            for date, premium in rejected:
                print(f'    [Warning] Skipped implausible premium {premium:.4f}% on {date}')
        except Exception as exc:
            failures.append(f'{code}: {exc}')

    normalize_group_premiums(all_data, codes)
    changed_months = changed_data_months_all(original_all_data, all_data)

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


def _percentile_rank(value, series):
    """Return the percentile rank of ``value`` within ``series`` (0-100)."""
    if not series:
        return None
    clean = [float(x) for x in series if x is not None]
    if not clean:
        return None
    below = sum(1 for x in clean if x < value)
    equal = sum(1 for x in clean if x == value)
    return round((below + equal / 2) / len(clean) * 100, 2)


def fetch_hom_ndx_valuation():
    """Fetch NDX TTM / Forward PE from History of Market public JSON API.

    Returns a dict with at least ``pe`` (TTM PE) and ``forward_pe``
    (daily forward PE), or None on failure.  The API is public, requires
    no login and updates daily.
    """
    req = urllib.request.Request(
        NDX_HOM_API,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        print(f'  [NDX valuation] HOM request failed: {exc}')
        return None

    current = payload.get('current') or {}
    pe = current.get('trailing')
    forward_pe = current.get('forwardOwn') or current.get('forward')
    if pe is None:
        print('  [NDX valuation] HOM response has no trailing PE.')
        return None

    # Compute a percentile rank from the available daily trailing history.
    trailing_history = payload.get('trailing', [])
    pe_series = [row.get('value') for row in trailing_history if row.get('value') is not None]
    pe_pct = _percentile_rank(float(pe), pe_series)

    rec = {
        'pe': float(pe),
        'forward_pe': float(forward_pe) if forward_pe is not None else None,
        'pe_pct': pe_pct,
        'pe_history_date': payload.get('updated'),
        'coverage_trailing': current.get('trailingCoverage'),
        'coverage_forward': current.get('forwardCoverage'),
    }
    return {k: v for k, v in rec.items() if v is not None}


def fetch_danjuan_ndx_valuation():
    """Fetch NDX index valuation from Danjuan (Xueqiu) valuation API.

    The endpoint requires a logged-in session, supplied via the
    DANJUAN_COOKIE environment variable. Returns a dict of valuation
    metrics or None when unavailable. Field names are mapped defensively
    because Danjuan/Xueqiu may rename them.
    """
    cookie = os.environ.get('DANJUAN_COOKIE')
    if not cookie:
        return None
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://danjuanfunds.com/dj-valuation-table-detail/NDX',
        'Cookie': cookie,
        'Accept': 'application/json',
    }
    req = urllib.request.Request(NDX_VALUATION_API, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        print(f'  [NDX valuation] Danjuan request failed: {exc}')
        return None
    if data.get('result_code') not in (0, None) or 'data' not in data:
        print(f"  [NDX valuation] Danjuan API rejected: {data.get('message')} "
              f"(result_code={data.get('result_code')})")
        return None
    d = data['data']

    def pick(*keys):
        for k in keys:
            if k in d and d[k] not in (None, ''):
                return d[k]
        return None

    rec = {
        'pe': pick('pe', 'pe_ttm', 'pe_lyr'),
        'pe_pct': pick('pe_percentile', 'pe_pct', 'pe_percent'),
        'pb': pick('pb', 'pb_ttm'),
        'pb_pct': pick('pb_percentile', 'pb_pct', 'pb_percent'),
        'roe': pick('roe'),
        'dividend_yield': pick('dividend_yield', 'dy', 'dividend', 'yield'),
        'peg': pick('peg', 'forecast_peg', 'peg_ttm'),
        'pe_30': pick('pe_30', 'pe_30_point', 'p30'),
        'pe_mid': pick('pe_mid', 'pe_median', 'median'),
        'pe_70': pick('pe_70', 'pe_70_point', 'p70'),
        'position_pct': pick('current_year_percentile', 'position_pct', 'percentile'),
        'label': pick('color', 'label', 'valuation', 'assessment'),
    }
    print('  [NDX valuation] Danjuan raw keys:', list(d.keys()))
    return {k: v for k, v in rec.items() if v is not None}


def fetch_ndx_valuation():
    """Fetch NDX valuation from multiple sources.

    Priority:
    1. History of Market (public, daily TTM/Forward PE)
    2. Danjuan/Xueqiu (login-required PB/ROE/dividend/PEG/percentiles)

    Returns a merged dict or None when no source is available.
    """
    rec = fetch_hom_ndx_valuation()
    if rec:
        print(f'  [NDX valuation] HOM: PE={rec.get("pe")} forward={rec.get("forward_pe")}')

    danjuan = fetch_danjuan_ndx_valuation()
    if danjuan:
        print(f'  [NDX valuation] Danjuan: PE={danjuan.get("pe")} label={danjuan.get("label")}')
        # Danjuan's PB/ROE/dividend/PEG/quantiles are authoritative when available.
        for key in ('pb', 'pb_pct', 'roe', 'dividend_yield', 'peg',
                    'pe_30', 'pe_mid', 'pe_70', 'position_pct', 'label'):
            if key in danjuan:
                rec[key] = danjuan[key]
        # Only overwrite HOM percentile if Danjuan provided one.
        if 'pe_pct' in danjuan:
            rec['pe_pct'] = danjuan['pe_pct']

    if not rec:
        print('  [NDX valuation] all sources unavailable; keeping last value.')
        return None
    return rec


def update_ndx_valuation():
    """Load ndx_valuation.json, refresh with the latest valuation, and emit
    data/ndx_valuation.js for the frontend. Falls back to the previous value
    (no error, no abort) when the fetch is unavailable.
    """
    json_path = os.path.join(DIR, NDX_VALUATION_FILE)
    today = datetime.now().strftime('%Y-%m-%d')

    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            store = json.load(f)
    else:
        store = {}
    store['source'] = 'History of Market NDX 估值 (每日 TTM/Forward PE，免登录) + 蛋卷基金 (PB/ROE/股息率/PEG，需登录 cookie)'
    series = store.setdefault('series', [])

    rec = fetch_ndx_valuation()
    if rec is None:
        store['updated'] = series[-1]['date'] if series else ''
        print('  [NDX valuation] no update (kept existing data).')
    else:
        rec['date'] = today
        if series and series[-1]['date'] == today:
            series[-1] = rec
        else:
            series.append(rec)
        store['updated'] = today
        print(f'  [NDX valuation] updated {today}: PE={rec.get("pe")} '
              f'PE%={rec.get("pe_pct")}')

    write_text_atomic(json_path, json.dumps(store, ensure_ascii=False, indent=2))
    js_path = os.path.join(DATA_DIR, 'ndx_valuation.js')
    content = f'const NDX_VALUATION = {json.dumps(store, ensure_ascii=False)};\n'
    write_text_atomic(js_path, content)


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

    # NDX 估值参考（蛋卷）：独立更新，失败不影响 ETF 主流程
    try:
        update_ndx_valuation()
    except Exception as exc:
        print(f'\n[Warning] NDX valuation update skipped: {exc}')

    print('\nGlobal update complete!')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
