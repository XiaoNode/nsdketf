"""
一次性脚本：将 纳斯达克科技市值加权ETF景顺(159509, sz159509) 加入纳斯达克分析（跟踪纳斯达克科技市值加权指数 NDXTMC，非纯纳斯达克100）。
- 抓取新浪价格 + 天天基金净值（历史上全量）
- 计算折溢价率
- 合并进 etf_all.json（全量）
- 生成 2024-05 ~ 2026-07 月度 JS（与现有范围对齐）
- 在 index.html 中注册该 ETF 的数据引用（新增月份脚本标签）
"""
import json, os, re, urllib.request, time
from datetime import datetime

DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(DIR, 'index.html')

CODE = 'sz159509'          # 新浪/场内代码
PURE = '159509'            # 天天基金纯数字代码
NAME = '纳斯达克科技市值加权ETF景顺'
FEE = {'fee_total': 0.60, 'fee_mgmt': 0.50, 'fee_cust': 0.10, 'fee_svc': 0.00}
SIZE = 30                  # 近似规模(亿元)，展示用

HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}

def fetch_sina_prices(sym, days=650):
    url = (f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={days}')
    req = urllib.request.Request(url, headers=HEADERS)
    txt = urllib.request.urlopen(req, timeout=15).read().decode('utf-8')
    rows = json.loads(txt)
    return {r['day'].split(' ')[0]: float(r['close']) for r in rows}

def fetch_eastmoney_nav(pure, max_pages=40):
    all_rows = []
    for page in range(1, max_pages + 1):
        url = f'https://fundf10.eastmoney.com/F10DataApi.aspx?type=lsjz&code={pure}&page={page}&per=20'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        txt = urllib.request.urlopen(req, timeout=15).read().decode('utf-8')
        rows = re.findall(r'<tr><td>(\d{4}-\d{2}-\d{2})</td><td[^>]*>([\d\.]+)</td>', txt)
        if not rows:
            break
        all_rows = rows + all_rows  # 前面页更早
        if len(rows) < 20:
            break
        time.sleep(0.2)
    return {d: float(v) for d, v in all_rows}

def main():
    print(f'>>> 抓取 {CODE} {NAME} ...')
    prices = fetch_sina_prices(CODE)
    navs = fetch_eastmoney_nav(PURE)
    print(f'    价格 {len(prices)} 条 ({min(prices)}~{max(prices)}); 净值 {len(navs)} 条 ({min(navs)}~{max(navs)})')

    # 合并价格
    price_list = [{'date': d, 'value': round(v, 4)} for d, v in sorted(prices.items())]
    # 净值（按日期回溯填充，与 daily_update.py 逻辑一致）
    nav_list = []
    prev_nav = None
    for p in price_list:
        d = p['date']
        if d in navs:
            prev_nav = navs[d]
        elif prev_nav is not None:
            pass  # 用上一个交易日净值
        if prev_nav is not None:
            nav_list.append({'date': d, 'value': round(prev_nav, 4)})
    # 折溢价率
    nav_map = {n['date']: n['value'] for n in nav_list}
    premium_list = []
    for p in price_list:
        if p['date'] in nav_map:
            prem = round((p['value'] / nav_map[p['date']] - 1) * 100, 4)
            premium_list.append({'date': p['date'], 'value': prem})

    etf_record = {
        'code': CODE, 'name': NAME,
        'fee_total': FEE['fee_total'], 'fee_mgmt': FEE['fee_mgmt'],
        'fee_cust': FEE['fee_cust'], 'fee_svc': FEE['fee_svc'],
        'size': SIZE,
        'price': price_list, 'nav': nav_list, 'premium': premium_list
    }
    print(f'    生成 price {len(price_list)}, nav {len(nav_list)}, premium {len(premium_list)}')

    # 合并进 etf_all.json
    json_path = os.path.join(DIR, 'etf_all.json')
    with open(json_path, 'r', encoding='utf-8') as f:
        all_data = json.load(f)
    all_data[CODE] = etf_record
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False)
    print(f'    [OK] 已合并进 etf_all.json (当前 {len(all_data)} 只)')

    # 生成月度 JS (2024-05 ~ 2026-07)
    months = []
    y, m = 2024, 5
    while (y, m) <= (2026, 7):
        months.append(f'{y}-{m:02d}')
        m += 1
        if m > 12:
            m = 1; y += 1

    for month in months:
        subset = {CODE: {
            'code': CODE, 'name': NAME,
            'fee_total': FEE['fee_total'], 'fee_mgmt': FEE['fee_mgmt'],
            'fee_cust': FEE['fee_cust'], 'fee_svc': FEE['fee_svc'], 'size': SIZE,
            'price': [i for i in price_list if i['date'].startswith(month)],
            'nav': [i for i in nav_list if i['date'].startswith(month)],
            'premium': [i for i in premium_list if i['date'].startswith(month)],
        }}
        var_name = f'ETF_DATA_{month.replace("-", "")}'
        js_path = os.path.join(DIR, f'etf_data_{month}.js')
        # 该月 JS 已存在则合并进原文件（其它 ETF 数据保留）
        base = {}
        if os.path.exists(js_path):
            with open(js_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            # 找第一个 '=' 之后的内容，截到末尾的 '};'
            eq = content.find('=')
            jstr = content[eq + 1:].strip()
            if jstr.endswith(';'):
                jstr = jstr[:-1]
            base = json.loads(jstr)
        base[CODE] = subset[CODE]
        with open(js_path, 'w', encoding='utf-8') as f:
            f.write(f'const {var_name} = ' + json.dumps(base, ensure_ascii=False) + ';\n')
    print(f'    [OK] 已更新 {len(months)} 个月度 JS 文件 (2024-05~2026-07)')

    # 注册到 HTML
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()
    updated = False
    # 1. 添加 <script src="etf_data_2024-05.js"> 已在，需确保每月都有
    for month in months:
        tag = f'<script src="etf_data_{month}.js"></script>'
        if tag not in html:
            # 找同类型最后一个插入
            pat = re.compile(r'<script src="etf_data_[\d-]+\.js"></script>')
            ms = list(pat.finditer(html))
            if ms:
                last = ms[-1]
                html = html[:last.end()] + '\n    ' + tag + html[last.end():]
                updated = True
    # 数据合并已改为前端动态收集（collectMonthly），只需保证 <script> 标签存在
    if updated:
        with open(HTML_PATH, 'w', encoding='utf-8') as f:
            f.write(html)
        print('    [OK] 已在 index.html 注册脚本引用')
    else:
        print('    [INFO] HTML 引用已存在，无需修改')

    print('完成！')

if __name__ == '__main__':
    main()
