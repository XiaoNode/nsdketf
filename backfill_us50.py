#!/usr/bin/env python3
"""backfill_us50.py — 一次性向前追溯最近约 5 个月（160 交易日）的
美国50 (MSCI) QDII ETF 价格 / 净值 / 折溢价历史，并做双数据源交叉校验。

数据来源对比：
  价格源 A : 新浪财经 K 线   (money.finance.sina.com.cn)        —— 与每日更新管道一致
  价格源 B : 腾讯财经 K 线   (web.ifzq.gtimg.cn)                —— 独立第二数据源
  净值源   : 东方财富 基金历史净值 (api.fund.eastmoney.com/f10/lsjz，基金公司公布值)
  折溢价   : 分别用 源A价格 / 源B价格 与同一净值计算，比较两套溢价率差异做交叉校验

产出：
  - us50_all.json            （含 sh513850 / sz159577 的 5 个月 price / nav / premium）
  - data/us50_data_YYYY-MM.js（逐月拆分，供前端按现有约定加载）
  - index.html               （在标普数据块之后注入 <script> 引用）
  - us50_backfill_report.json（双源对比报告，含最大溢价差、超阈值点数）
"""
import os
import re
import json
import urllib.parse
import urllib.request
from datetime import datetime as dt, timedelta

import daily_update as du

US50_META = {
    'sh513850': {
        'code': 'sh513850',
        'name': '易方达MSCI美国50ETF(QDII)',
        'fee_total': 0.60, 'fee_mgmt': 0.50, 'fee_cust': 0.10, 'fee_svc': 0.00,
        'size': '',  # TODO: 核实最新规模（亿元）
    },
    'sz159577': {
        'code': 'sz159577',
        'name': '汇添富MSCI美国50ETF(QDII)',
        'fee_total': 0.60, 'fee_mgmt': 0.50, 'fee_cust': 0.10, 'fee_svc': 0.00,
        'size': '',  # TODO: 核实最新规模（亿元）
    },
}

BACKFILL_DAYS = 160  # 约 5 个月交易日
MAX_PREMIUM = du.MAX_ABS_PREMIUM


def fetch_tencent_price(code, days=BACKFILL_DAYS):
    """Fetch raw daily closing prices from Tencent finance K-line API (source B)."""
    end = dt.now().strftime('%Y-%m-%d')
    start = (dt.now() - timedelta(days=BACKFILL_DAYS + 10)).strftime('%Y-%m-%d')
    url = (f'https://web.ifzq.gtimg.cn/appstock/app/kline/kline'
           f'?param={code},day,{start},{end},{days + 30}')

    def load():
        req = urllib.request.Request(url, headers=du.HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode('utf-8')
        payload = json.loads(text)
        node = (payload.get('data') or {}).get(code)
        if not node:
            raise ValueError('no data node')
        rows = node.get('day') or node.get('qfqday') or []
        if not rows:
            raise ValueError('no kline rows')
        parsed = {}
        for row in rows:
            # row: [date, open, close, high, low, volume, ...]
            date = row[0]
            if not du.is_valid_date(date):
                raise ValueError(f'invalid price date {date!r}')
            parsed[date] = du.parse_positive_number(row[2], 'price', code)
        return parsed

    return du.request_with_retry(f'Tencent price {code}', load)


def compute_premium(price_dict, nav_dict):
    """Return sorted [{date,value,nav_date}] from a price dict and nav dict."""
    nav_dates = sorted(nav_dict)
    out = []
    for date in sorted(price_dict):
        idx = du.bisect.bisect_right(nav_dates, date) - 1
        if idx < 0:
            continue
        nd = nav_dates[idx]
        prem = round((price_dict[date] / nav_dict[nd] - 1) * 100, 4)
        if abs(prem) > MAX_PREMIUM:
            continue
        out.append({'date': date, 'value': prem, 'nav_date': nd})
    return out


def backfill():
    today = dt.now()
    start_date = (today - timedelta(days=BACKFILL_DAYS)).strftime('%Y-%m-%d')
    print(f'US50 backfill: {start_date} ~ {today.strftime("%Y-%m-%d")} '
          f'(~{BACKFILL_DAYS} trading days)')

    all_data = {}
    report = {
        'generated': today.strftime('%Y-%m-%d %H:%M:%S'),
        'start_date': start_date,
        'sources': {
            'price_a': 'Sina kline (money.finance.sina.com.cn)',
            'price_b': 'Tencent finance kline (web.ifzq.gtimg.cn)',
            'nav': 'Eastmoney fund NAV (api.fund.eastmoney.com/f10/lsjz)',
        },
        'codes': {},
    }

    for code, meta in US50_META.items():  # noqa
        print(f'\n>>> {code} ({meta["name"]})')
        prices_a = du.fetch_price(code, days=BACKFILL_DAYS)        # Sina (source A)
        prices_b = fetch_tencent_price(code, days=BACKFILL_DAYS)   # Tencent (source B)
        navs = du.fetch_nav(code, start_date=start_date)           # Eastmoney NAV

        prem_a = compute_premium(prices_a, navs)
        prem_b = compute_premium(prices_b, navs)

        # 双源交叉校验：在共同价格日上比较两套溢价率
        common = sorted(set(prices_a) & set(prices_b))
        nav_dates = sorted(navs)
        diffs = []
        max_abs = 0.0
        for d in common:
            ni = du.bisect.bisect_right(nav_dates, d) - 1
            nd = nav_dates[ni]
            pa = (prices_a[d] / navs[nd] - 1) * 100
            pb = (prices_b[d] / navs[nd] - 1) * 100
            diff = abs(pa - pb)
            if diff > max_abs:
                max_abs = diff
            if diff > 0.1:
                diffs.append({
                    'date': d,
                    'sina_premium': round(pa, 4),
                    'tencent_premium': round(pb, 4),
                    'diff': round(diff, 4),
                })

        report['codes'][code] = {
            'sina_price_points': len(prices_a),
            'tencent_price_points': len(prices_b),
            'nav_points': len(navs),
            'common_price_dates': len(common),
            'max_abs_premium_diff_pct': round(max_abs, 4),
            'diff_over_0.1pct_count': len(diffs),
            'sample_diffs': diffs[:10],
            'latest_sina_premium': prem_a[-1] if prem_a else None,
            'latest_tencent_premium': prem_b[-1] if prem_b else None,
        }
        print(f'    Sina价格点={len(prices_a)} 腾讯价格点={len(prices_b)} '
              f'净值点={len(navs)} 共同日={len(common)} '
              f'最大溢价差={max_abs:.4f}% 超0.1%阈值点={len(diffs)}')

        # 以新浪价格为基准序列（与每日更新管道一致），净值同源
        all_data[code] = {
            **meta,
            'price': [{'date': d, 'value': v} for d, v in sorted(prices_a.items())],
            'nav': [{'date': d, 'value': v} for d, v in sorted(navs.items())],
            'premium': prem_a,
        }

    # 组内（两只美国50）按最新共同净值日归一化，与每日更新逻辑一致
    du.normalize_group_premiums(all_data, list(US50_META.keys()))

    json_path = os.path.join(du.DIR, 'us50_all.json')
    du.write_text_atomic(json_path, json.dumps(all_data, ensure_ascii=False))
    print(f'\n[OK] wrote {json_path}')

    _write_monthly(all_data)

    rep_path = os.path.join(du.DIR, 'us50_backfill_report.json')
    du.write_text_atomic(rep_path, json.dumps(report, ensure_ascii=False, indent=2))
    print(f'[OK] wrote {rep_path}')
    return all_data


def _write_monthly(all_data):
    months = set()
    for info in all_data.values():
        for arr in (info.get('price', []), info.get('nav', []), info.get('premium', [])):
            for item in arr:
                months.add(item['date'][:7])

    for month in sorted(months):
        subset = {}
        for code, info in all_data.items():
            base = {k: v for k, v in info.items() if k not in ('price', 'nav', 'premium')}
            subset[code] = {
                **base,
                'price': [x for x in info.get('price', []) if x['date'].startswith(month)],
                'premium': [x for x in info.get('premium', []) if x['date'].startswith(month)],
                'nav': [x for x in info.get('nav', []) if x['date'].startswith(month)],
            }
        if not any(subset[c]['price'] for c in subset):
            continue
        js_path = os.path.join(du.DATA_DIR, f'us50_data_{month}.js')
        var_name = f'US50_DATA_{month.replace("-", "")}'
        content = f'const {var_name} = {json.dumps(subset, ensure_ascii=False)};\n'
        du.write_text_atomic(js_path, content)
        print(f'  [JS] {js_path}')

    _inject_html_scripts(sorted(months))


def _inject_html_scripts(months):
    html_path = du.HTML_PATH
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    pattern = re.compile(r'<script src="data/sp500_data_(\d{4}-\d{2})\.js"></script>')
    matches = list(pattern.finditer(html))
    if not matches:
        raise RuntimeError('cannot find sp500 script anchor in index.html')
    anchor_end = matches[-1].end()
    new_tags = ''
    for m in months:
        tag = f'<script src="data/us50_data_{m}.js"></script>'
        if tag not in html:
            new_tags += '\n' + tag
    if new_tags:
        html = html[:anchor_end] + new_tags + html[anchor_end:]
        du.write_text_atomic(html_path, html)
        print(f'  [HTML] injected {len(months)} us50 script tag(s) after sp500 block')


if __name__ == '__main__':
    backfill()
