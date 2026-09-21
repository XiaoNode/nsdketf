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
from datetime import datetime, timedelta, timezone


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
# 天天基金移动端阶段涨幅接口（近1/2/3/5年收益率，含复权，免登录）
FUND_PERIOD_API = 'https://fundmobapi.eastmoney.com/FundMNewApi/FundMNPeriodIncrease'
# 天天基金 F10 特色数据页（指数基金年化跟踪误差 + 跟踪指数）
FUND_TSDATA_URL = 'https://fundf10.eastmoney.com/tsdata_{code}.html'
# 天天基金详情 JS（含完整复权净值序列，用于交叉校验区间涨幅）
FUND_PINGZHONG_URL = 'https://fund.eastmoney.com/pingzhongdata/{code}.js'
# 腾讯美股指数日线（用于交叉校验跟踪误差，免登录且稳定）
US_INDEX_KLINE_API = 'https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get'
# 纳指被动基金跟踪的指数在腾讯行情里的代码
TRACK_INDEX_KLINE_SYMBOL = 'usNDX'
# 区间涨幅字段 -> (移动端 title, 自然年跨度)
PERIOD_RETURN_FIELDS = (
    ('ret_1y', '1N', 1),
    ('ret_2y', '2N', 2),
    ('ret_3y', '3N', 3),
    ('ret_5y', '5N', 5),
)
# 涨幅交叉校验允许的最大偏差（百分点）。官方接口取复权收益，自算亦按复权序列，
# 正常应完全吻合或仅因交易日对齐差零点几个百分点。
PERIOD_RETURN_TOLERANCE = 1.5
# 跟踪误差交叉校验允许的最大偏差（百分点）
TRACKING_ERROR_TOLERANCE = 0.6
TRACKING_DAYS_PER_YEAR = 252


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
    pure_code = code[2:] if re.match(r'^(?:sh|sz|of)\d{6}$', code) else code
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


def merge_otc_data(info, navs, replace_all_nav=False):
    """Merge an over-the-counter fund's published NAV without inventing price/premium."""
    updated = dict(info)
    if replace_all_nav:
        nav_dict = dict(navs)
    else:
        cutoff = min(navs)
        nav_dict = {
            item['date']: item['value']
            for item in info.get('nav', [])
            if item['date'] < cutoff
        }
        nav_dict.update(navs)
    updated['nav'] = [
        {'date': date, 'value': value}
        for date, value in sorted(nav_dict.items())
    ]
    updated['price'] = []
    updated['premium'] = []
    return updated, changed_data_months(info, updated)


def prepare_otc_update(codes, json_file, full_nav=False):
    """Update OTC QDII NAVs; failures are isolated from exchange-traded ETF groups."""
    json_path = os.path.join(DIR, json_file)
    with open(json_path, 'r', encoding='utf-8') as f:
        all_data = json.load(f)
    original_all_data = copy.deepcopy(all_data)
    failures = []
    print(f'\n>>> Preparing {json_file} ({len(codes)} OTC funds)...')
    for code in codes:
        info = all_data.get(code)
        if not info:
            failures.append(f'{code}: missing metadata')
            continue
        print(f"  Processing {code} ({info['name']})...")
        try:
            source_code = info.get('source_code', code)
            if full_nav:
                start_date = min((item['date'] for item in info.get('nav', [])), default=None)
                navs = fetch_nav(source_code, start_date=start_date)
            else:
                navs = fetch_nav(source_code)
            updated, _ = merge_otc_data(info, navs, replace_all_nav=full_nav)
            all_data[code] = updated
        except Exception as exc:
            failures.append(f'{code}: {exc}')
    changed_months = changed_data_months_all(original_all_data, all_data)
    return {
        'codes': codes,
        'json_file': json_file,
        'all_data': all_data,
        'changed_months': changed_months,
        'failures': failures,
    }


def update_otc_quota_status(json_file):
    """Refresh public Eastmoney quota/status text when available.

    This endpoint is HTML, not a stable API; failure is intentionally non-fatal.
    The page may reflect the selected sales channel, so the UI labels it as
    "公开页面状态" rather than a universal all-channel quota.
    """
    json_path = os.path.join(DIR, json_file)
    with open(json_path, 'r', encoding='utf-8') as f:
        all_data = json.load(f)
    checked_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for code, info in all_data.items():
        try:
            url = f'https://fund.eastmoney.com/{code}.html'
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode('utf-8', 'ignore')
            match = re.search(r'交易状态：</span><span[^>]*>(.*?)</span>', html)
            if not match:
                raise ValueError('quota status not found')
            status = re.sub(r'<[^>]+>', '', match.group(1))
            status = re.sub(r'\s+', ' ', status).replace('&nbsp;', ' ').strip()
            status = status.rstrip(' (')
            if '单日累计购买上限' in status and not status.endswith(')'):
                status += ')'
            limit_match = re.search(r'单日累计购买上限\s*([0-9,.]+)元', status)
            info['purchase_status'] = ' '.join(status.split())
            info['purchase_limit'] = float(limit_match.group(1).replace(',', '')) if limit_match else None
            info['purchase_checked_at'] = checked_at
        except Exception as exc:
            print(f'    [Warning] quota/status skipped for {code}: {exc}')
    write_text_atomic(json_path, json.dumps(all_data, ensure_ascii=False))
    return all_data


def fetch_fund_period_returns(code):
    """Fetch trailing 1/2/3/5-year returns for an OTC fund.

    Uses Eastmoney's mobile ``FundMNPeriodIncrease`` endpoint, which publishes
    adjusted (复权) cumulative returns keyed by ``1N``/``2N``/``3N``/``5N``.
    Periods shorter than the fund's history come back as an empty string and
    are normalised to ``None`` so the UI can render them as N/A.
    """
    params = urllib.parse.urlencode({
        'FCODE': code,
        'deviceid': 'workbuddy',
        'plat': 'Android',
        'product': 'EFund',
        'version': '6.2.8',
    })
    url = f'{FUND_PERIOD_API}?{params}'

    def load():
        req = urllib.request.Request(url, headers={**HEADERS, 'Referer': 'https://fund.eastmoney.com/'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode('utf-8', 'ignore'))
        if not payload.get('Success'):
            raise ValueError(f"period API error: {payload.get('ErrMsg') or payload.get('ErrCode')}")
        return payload

    payload = request_with_retry(f'period returns {code}', load)
    by_title = {item.get('title'): item.get('syl') for item in payload.get('Datas') or []}
    result = {}
    for field, title, _years in PERIOD_RETURN_FIELDS:
        raw = by_title.get(title)
        try:
            result[field] = round(float(raw), 2) if raw not in (None, '', '--') else None
        except (TypeError, ValueError):
            result[field] = None
    expansion = payload.get('Expansion') or {}
    result['perf_asof'] = expansion.get('TIME') or None
    result['established'] = expansion.get('ESTABDATE') or None
    return result


def fetch_fund_tracking_error(code):
    """Fetch the annualised tracking error and tracked index from F10 page.

    Eastmoney's ``tsdata`` page publishes ``年化跟踪误差`` under the
    "指数基金指标" section.  The page is HTML rather than a stable API, so a
    parse failure is treated as "no data" by the caller.
    """
    url = FUND_TSDATA_URL.format(code=code)

    def load():
        req = urllib.request.Request(url, headers={**HEADERS, 'Referer': 'https://fund.eastmoney.com/'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode('utf-8', 'ignore')

    html = request_with_retry(f'tracking error {code}', load)
    section = html[html.find('指数基金指标'):]
    if not section:
        raise ValueError('指数基金指标 section not found')
    match = re.search(
        r'<td\s*>([^<]+)</td>\s*<td\s*>([\d.]+)%</td>\s*<td\s*>([\d.]+)%</td>',
        section[:3000],
    )
    if not match:
        raise ValueError('tracking error rows not found')
    asof_match = re.search(r'截止至：([\d-]+)', section[:3000])
    return {
        'track_index': match.group(1).strip(),
        'track_err': round(float(match.group(2)), 2),
        'peer_track_err': round(float(match.group(3)), 2),
        'track_err_asof': asof_match.group(1) if asof_match else None,
    }


def fetch_fund_adjusted_nav_series(code):
    """Return ``(raw, adjusted)`` date->value series for a fund.

    ``Data_netWorthTrend`` gives the published unit NAV (``y``) plus the day's
    adjusted return (``equityReturn``).  Cumulative-multiplying the returns
    reconstructs an adjusted series that is comparable with the official
    period returns for funds that paid dividends (dividends show up as a
    ``unitMoney`` adjustment and a ``0`` equityReturn that day).

    Two bases are returned because either may be the correct comparison
    depending on the fund: a non-distributing fund's raw NAV matches the
    published returns exactly, while a distributing fund needs the adjusted
    series.  Cross-checks accept a match on either basis.
    """
    url = f'{FUND_PINGZHONG_URL.format(code=code)}'

    def load():
        req = urllib.request.Request(url, headers={**HEADERS, 'Referer': 'https://fund.eastmoney.com/'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode('utf-8', 'ignore')

    text = request_with_retry(f'nav series {code}', load)
    match = re.search(r'var\s+Data_netWorthTrend\s*=\s*(\[.*?\]);', text)
    if not match:
        raise ValueError('Data_netWorthTrend not found')
    records = json.loads(match.group(1))
    raw = {}
    adjusted = {}
    cumulative = 1.0
    tz = timezone(timedelta(hours=8))
    for item in records:
        stamp = item.get('x')
        if stamp is None:
            continue
        date = datetime.fromtimestamp(int(stamp) / 1000, tz).strftime('%Y-%m-%d')
        try:
            raw[date] = float(item['y'])
        except (KeyError, TypeError, ValueError):
            continue
        equity_return = item.get('equityReturn')
        if equity_return is not None:
            try:
                cumulative *= 1 + float(equity_return) / 100
            except (TypeError, ValueError):
                pass
        adjusted[date] = cumulative
    if len(raw) < 260:
        raise ValueError(f'nav history too short ({len(raw)} rows)')
    return raw, adjusted


def compute_period_return(series, years):
    """Compute a trailing return (%) from a date->value series."""
    end_date = max(series)
    start_date = (
        datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=365 * years)
    ).strftime('%Y-%m-%d')
    prior = [date for date in series if date <= start_date]
    if not prior:
        return None
    base = series[max(prior)]
    if not base:
        return None
    return round((series[end_date] / base - 1) * 100, 2)


def fetch_us_index_closes(symbol=TRACK_INDEX_KLINE_SYMBOL, count=1200):
    """Fetch daily closes for a US index from Tencent (used to verify TE)."""
    params = urllib.parse.urlencode({'param': f'{symbol},day,,,{count},qfq'})
    url = f'{US_INDEX_KLINE_API}?{params}'

    def load():
        req = urllib.request.Request(url, headers={**HEADERS, 'Referer': 'https://gu.qq.com/'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode('utf-8', 'ignore'))
        rows = (payload.get('data') or {}).get(symbol) or {}
        klines = rows.get('qfqday') or rows.get('day') or []
        if not klines:
            raise ValueError('index kline empty')
        return {row[0]: float(row[2]) for row in klines}

    return request_with_retry(f'index kline {symbol}', load)


def compute_tracking_error(fund_series, index_closes, years=1):
    """Annualised tracking error from daily fund-vs-index return differences."""
    end_date = max(fund_series)
    start_date = (
        datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=365 * years)
    ).strftime('%Y-%m-%d')
    common = sorted(
        date for date in fund_series if date >= start_date and date in index_closes
    )
    if len(common) < 60:
        return None
    diffs = []
    for previous, current in zip(common, common[1:]):
        fund_return = fund_series[current] / fund_series[previous] - 1
        index_return = index_closes[current] / index_closes[previous] - 1
        diffs.append(fund_return - index_return)
    mean = sum(diffs) / len(diffs)
    variance = sum((value - mean) ** 2 for value in diffs) / (len(diffs) - 1)
    return round(math.sqrt(variance) * math.sqrt(TRACKING_DAYS_PER_YEAR) * 100, 2)


def update_ndx_passive_performance(json_file='ndx_passive_all.json'):
    """Attach trailing returns and tracking error to each OTC passive fund.

    Every field is sourced from a public Eastmoney endpoint and then
    independently recomputed from raw NAV / index series where possible.  A
    mismatch is reported as a warning but never overwrites the published
    figure, so the stored value always reflects the fund company's own data.

    Returns ``(all_data, changed)`` where ``changed`` indicates whether any
    persisted field differs from what was on disk.  Callers should refresh the
    monthly JS when ``changed`` is true, otherwise a day with no NAV movement
    would leave the published performance stale.
    """
    json_path = os.path.join(DIR, json_file)
    with open(json_path, 'r', encoding='utf-8') as f:
        all_data = json.load(f)
    original = copy.deepcopy(all_data)

    index_closes = None
    try:
        index_closes = fetch_us_index_closes()
        print(f'  [Perf] NDX index history: {len(index_closes)} rows')
    except Exception as exc:
        print(f'  [Warning] index history unavailable, TE cross-check skipped: {exc}')

    perf_fields = [field for field, _t, _y in PERIOD_RETURN_FIELDS] + [
        'track_index', 'track_err', 'peer_track_err', 'track_err_asof',
        'perf_asof', 'established',
    ]
    for code, info in all_data.items():
        print(f"  [Perf] {code} ({info.get('name')})...")
        try:
            returns = fetch_fund_period_returns(code)
            info.update({field: returns[field] for field, _t, _y in PERIOD_RETURN_FIELDS})
            info['perf_asof'] = returns['perf_asof']
            info['established'] = returns['established'] or info.get('established')
        except Exception as exc:
            print(f'    [Warning] period returns skipped for {code}: {exc}')

        try:
            tracking = fetch_fund_tracking_error(code)
            info.update(tracking)
        except Exception as exc:
            print(f'    [Warning] tracking error skipped for {code}: {exc}')

        # --- 交叉校验：用净值序列自算区间涨幅（原始/复权两种基准取其一命中即可）---
        raw_series = adjusted_series = None
        try:
            raw_series, adjusted_series = fetch_fund_adjusted_nav_series(code)
            for field, _title, years in PERIOD_RETURN_FIELDS:
                official = info.get(field)
                if official is None:
                    continue
                computed = [
                    value for value in (
                        compute_period_return(raw_series, years),
                        compute_period_return(adjusted_series, years),
                    ) if value is not None
                ]
                if not computed:
                    continue
                if all(abs(official - value) > PERIOD_RETURN_TOLERANCE for value in computed):
                    print(f'    [Warning] {code} {field}: official {official} '
                          f'vs computed (raw {computed[0]} / adj {computed[-1]})')
        except Exception as exc:
            print(f'    [Warning] period cross-check skipped for {code}: {exc}')

        # --- 交叉校验：用指数日线自算跟踪误差（原始/复权两种基准取其一命中即可）---
        if raw_series is not None and index_closes:
            try:
                official_te = info.get('track_err')
                track_index = info.get('track_index') or ''
                # 跟踪非纳指100的基金（如科技市值加权）无法用 NDX 校验
                if official_te is not None and '纳斯达克100' in track_index:
                    computed = [
                        value for value in (
                            compute_tracking_error(raw_series, index_closes, years=1),
                            compute_tracking_error(adjusted_series, index_closes, years=1),
                        ) if value is not None
                    ]
                    if computed and all(
                        abs(official_te - value) > TRACKING_ERROR_TOLERANCE
                        for value in computed
                    ):
                        print(f'    [Warning] {code} track_err: official {official_te} '
                              f'vs computed (raw {computed[0]} / adj {computed[-1]})')
            except Exception as exc:
                print(f'    [Warning] TE cross-check skipped for {code}: {exc}')

    changed = any(
        all_data[code].get(field) != original[code].get(field)
        for code in all_data
        for field in perf_fields
    )
    write_text_atomic(json_path, json.dumps(all_data, ensure_ascii=False))
    print(f'  [Perf] Wrote {json_file} (changed={changed})')
    return all_data, changed


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

        if not any(subset[code].get('price') or subset[code].get('nav') for code in subset):
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

    Returns a dict with ``pe`` (TTM/trailing PE), ``forward_pe``
    (12-month blended forward PE), and a long-history forward percentile,
    or None on failure.  The API is public, requires no login and updates
    daily.

    Important: HOM's ``trailing`` series only starts in 2026-05, so it
    cannot be used for a meaningful historical percentile.  The ``forward``
    series goes back to 2001 and is used for the percentile instead.
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
    # Prefer the daily blended-forward series computed from published ETF
    # holdings; fall back to the weekly terminal forward series.
    forward_pe = current.get('forwardOwn') or current.get('forward')
    if pe is None:
        print('  [NDX valuation] HOM response has no trailing PE.')
        return None

    # Determine the *actual* observation date for each metric from the
    # corresponding historical series.  ``payload['updated']`` is only the
    # API/cache refresh date and may be ahead of the latest data point.
    trailing_history = payload.get('trailing', [])
    forward_history = payload.get('forward', [])

    def _last_date(rows):
        for row in reversed(rows):
            d = row.get('date')
            if d:
                return d
        return payload.get('updated')

    pe_history_date = _last_date(trailing_history)
    forward_history_date = _last_date(forward_history)

    # Long-history forward PE series (weekly since 2001) is the only
    # series long enough for a meaningful percentile rank.
    forward_series = [row.get('value') for row in forward_history if row.get('value') is not None]
    forward_pe_pct = _percentile_rank(float(forward_pe), forward_series) if forward_pe is not None else None

    rec = {
        'pe': float(pe),
        'forward_pe': float(forward_pe) if forward_pe is not None else None,
        'forward_pe_pct': forward_pe_pct,
        'pe_history_date': pe_history_date,
        'forward_history_date': forward_history_date,
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

    Sources:
    - History of Market (public, daily TTM/Forward PE, long forward history)
    - Danjuan/Xueqiu (login-required TTM PE/PB/ROE/dividend/PEG/percentiles)

    Returns a merged dict or None when no source is available.
    """
    rec = fetch_hom_ndx_valuation()
    if rec:
        print(f'  [NDX valuation] HOM: PE={rec.get("pe")} forward={rec.get("forward_pe")}')

    danjuan = fetch_danjuan_ndx_valuation()
    if danjuan:
        print(f'  [NDX valuation] Danjuan: PE={danjuan.get("pe")} label={danjuan.get("label")}')
        # Danjuan's TTM PE/PB/ROE/dividend/PEG/quantiles are authoritative
        # when available because they match the domestic reference most users
        # expect.  We keep them in separate keys so the UI can tell the user
        # which source each number came from.
        for key in ('pb', 'pb_pct', 'roe', 'dividend_yield', 'peg',
                    'pe_30', 'pe_mid', 'pe_70', 'position_pct', 'label'):
            if key in danjuan:
                rec[key] = danjuan[key]
        # Danjuan's TTM PE percentile overwrites HOM's forward percentile.
        if 'pe_pct' in danjuan:
            rec['pe_pct'] = danjuan['pe_pct']
            rec['pe_history_date'] = datetime.now().strftime('%Y-%m-%d')
        if 'pe' in danjuan:
            rec['pe'] = danjuan['pe']

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
    store['source'] = (
        'History of Market (https://historyofmarket.com) 每日 NDX TTM/Forward PE，免登录；'
        '蛋卷基金 (https://danjuanfunds.com) TTM PE/PB/ROE/股息率/PEG，需登录 cookie。'
        '不同数据商标的 TTM PE 口径可能略有差异（成分股 EPS 时点、亏损股处理、加权方式）。'
    )
    series = store.setdefault('series', [])

    rec = fetch_ndx_valuation()
    if rec is None:
        store['updated'] = series[-1].get('checked') or series[-1].get('date') if series else ''
        print('  [NDX valuation] no update (kept existing data).')
    else:
        # Use the actual data-source date as the record date, not the script-run
        # date, so the UI never shows "data date = today" while the metric still
        # carries last week’s value.
        rec['date'] = rec.get('pe_history_date') or today
        rec['checked'] = today

        # Avoid stacking identical records when the upstream source has not yet
        # published a new observation (e.g. forward PE is weekly).
        if series:
            last = series[-1]
            same = (
                last.get('pe_history_date') == rec.get('pe_history_date') and
                last.get('forward_history_date') == rec.get('forward_history_date') and
                last.get('pe') == rec.get('pe') and
                last.get('forward_pe') == rec.get('forward_pe')
            )
            if same:
                # Keep the existing data point but record that we checked today.
                last['checked'] = today
                store['updated'] = today
                print(f'  [NDX valuation] source unchanged ({rec.get("pe_history_date")}); '
                      f'kept existing record, checked {today}.')
            elif last.get('date') == rec.get('date'):
                series[-1] = rec
                store['updated'] = today
                print(f'  [NDX valuation] refreshed {rec.get("date")}: PE={rec.get("pe")} '
                      f'forward={rec.get("forward_pe")} forward_pct={rec.get("forward_pe_pct")}')
            else:
                series.append(rec)
                store['updated'] = today
                print(f'  [NDX valuation] appended {rec.get("date")}: PE={rec.get("pe")} '
                      f'forward={rec.get("forward_pe")} forward_pct={rec.get("forward_pe_pct")}')
        else:
            series.append(rec)
            store['updated'] = today
            print(f'  [NDX valuation] initialized {rec.get("date")}: PE={rec.get("pe")} '
                  f'forward={rec.get("forward_pe")} forward_pct={rec.get("forward_pe_pct")}')

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
    try:
        update_otc_quota_status('ndx_passive_all.json')
    except Exception as exc:
        print(f'\n[Warning] OTC quota update skipped: {exc}')

    results = [
        (prepare_update(load_codes('etf_all.json'), 'etf_all.json', args.full_nav), 'etf'),
        (prepare_update(load_codes('sp500_all.json'), 'sp500_all.json', args.full_nav), 'sp500'),
        (prepare_update(load_codes('us50_all.json'), 'us50_all.json', args.full_nav), 'us50'),
        (prepare_update(load_codes('djia_all.json'), 'djia_all.json', args.full_nav), 'djia'),
    ]
    failures = [failure for result, _ in results for failure in result['failures']]
    if failures:
        print('\nUpdate aborted; no data files were written:')
        for failure in failures:
            print(f'  [Error] {failure}')
        return 1

    for result, prefix in results:
        write_update(result, prefix)

    # 场外纳指被动基金独立更新：即使某个场外源失败，也不阻断四类 ETF 主流程
    otc_result = prepare_otc_update(
        load_codes('ndx_passive_all.json'),
        'ndx_passive_all.json',
        args.full_nav,
    )
    if otc_result['failures']:
        for failure in otc_result['failures']:
            print(f'  [Warning] OTC update failed: {failure}')
    else:
        # 阶段涨幅 / 跟踪误差需在写月度文件前回填，否则界面要到下一轮才生效
        try:
            enriched, perf_changed = update_ndx_passive_performance('ndx_passive_all.json')
            otc_result['all_data'] = enriched
            if perf_changed:
                # 即使净值无变化（changed_months 为空），绩效字段也需刷新月度文件，
                # 否则界面仍显示上一轮的涨幅/跟踪误差。
                months = {
                    item['date'][:7]
                    for info in enriched.values()
                    for item in info.get('nav', [])
                    if is_valid_date(item.get('date'))
                }
                if months:
                    otc_result['changed_months'] = set(otc_result['changed_months']) | {max(months)}
        except Exception as exc:
            print(f'\n[Warning] OTC performance update skipped: {exc}')
        write_update(otc_result, 'ndx_passive')

    # NDX 估值参考（蛋卷）：独立更新，失败不影响 ETF 主流程
    try:
        update_ndx_valuation()
    except Exception as exc:
        print(f'\n[Warning] NDX valuation update skipped: {exc}')

    print('\nGlobal update complete!')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
