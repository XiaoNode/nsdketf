import json
import os
import re
import urllib.request
import time
from datetime import datetime

# ========== 配置 ==========
# 自动适配本地与 CI 环境：基于脚本所在目录解析项目根目录
DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(DIR, 'etf-analyzer.html')
HEADERS = {'User-Agent': 'Mozilla/5.0'}

def load_codes(json_file):
    """从全量JSON读取已收录的ETF代码，保证每日更新覆盖所有已录入标的。
    新增ETF只需把数据写进对应JSON（参考 add_etf_159509.py），无需再改这里。"""
    path = os.path.join(DIR, json_file)
    with open(path, 'r', encoding='utf-8') as f:
        return list(json.load(f).keys())

def fetch_nav(code, days=20):
    """从天天基金获取最近N天净值"""
    pure_code = code[2:]
    url = f'https://fundf10.eastmoney.com/F10DataApi.aspx?type=lsjz&code={pure_code}&page=1&per={days}'
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode('utf-8')
        rows = re.findall(r'<tr><td>(\d{4}-\d{2}-\d{2})</td><td[^>]*>([\d\.]+)</td>', text)
        return {r[0]: float(r[1]) for r in rows}
    except Exception as e:
        print(f"    [Error] Fetching NAV for {code}: {e}")
        return {}

def fetch_price(code, days=20):
    """从新浪获取最近N天价格"""
    url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={days}'
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return {d['day'].split(' ')[0]: float(d['close']) for d in data}
    except Exception as e:
        print(f"    [Error] Fetching Price for {code}: {e}")
        return {}

def update_html_scripts(month, prefix):
    """向HTML中注入新月份的脚本引用。
    数据合并已改为前端动态收集（collectMonthly），这里只需保证对应的 <script> 标签存在。"""
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    script_tag = f'<script src="{prefix}_data_{month}.js"></script>'
    if script_tag in html:
        return

    print(f"  [HTML] Adding {month} script reference to HTML...")

    # 找到该类型的最后一个脚本位置，插入新月份标签
    pattern = rf'<script src="{prefix}_data_[\d-]+\.js"></script>'
    matches = list(re.finditer(pattern, html))
    if matches:
        last_match = matches[-1]
        html = html[:last_match.end()] + f'\n    {script_tag}' + html[last_match.end():]
        with open(HTML_PATH, 'w', encoding='utf-8') as f:
            f.write(html)

def process_update(codes, json_file, prefix):
    json_path = os.path.join(DIR, json_file)
    with open(json_path, 'r', encoding='utf-8') as f:
        all_data = json.load(f)
        
    updated_months = set()
    
    print(f"\n>>> Updating {json_file} ({len(codes)} ETFs)...")
    for code in codes:
        info = all_data.get(code)
        if not info: continue
        
        print(f"  Processing {code} ({info['name']})...")
        prices = fetch_price(code)
        navs = fetch_nav(code)
        
        if not prices or not navs: continue
        
        # 合并逻辑
        price_dict = {p['date']: p['value'] for p in info.get('price', [])}
        nav_dict = {n['date']: n['value'] for n in info.get('nav', [])}
        
        price_dict.update(prices)
        nav_dict.update(navs)
        
        dates = sorted(price_dict.keys())
        price_arr, nav_arr, prem_arr = [], [], []
        
        for d in dates:
            p_val = price_dict[d]
            n_val = nav_dict.get(d)
            if n_val is None:
                # 尝试回溯上一个交易日净值
                prev = [nd for nd in nav_dict.keys() if nd < d]
                if prev: n_val = nav_dict[max(prev)]
            
            if n_val:
                price_arr.append({'date': d, 'value': p_val})
                nav_arr.append({'date': d, 'value': n_val})
                prem = round((p_val / n_val - 1) * 100, 4)
                prem_arr.append({'date': d, 'value': prem})
                updated_months.add(d[:7])
        
        info['price'], info['nav'], info['premium'] = price_arr, nav_arr, prem_arr
        time.sleep(0.2)
        
    # 保存 JSON
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False)
        
    # 更新月度 JS
    for month in updated_months:
        subset = {}
        for code in codes:
            info = all_data[code]
            base = {k: v for k, v in info.items() if k not in ('price', 'premium', 'nav')}
            subset[code] = {
                **base,
                'price': [item for item in info.get('price', []) if item['date'].startswith(month)],
                'premium': [item for item in info.get('premium', []) if item['date'].startswith(month)],
                'nav': [item for item in info.get('nav', []) if item['date'].startswith(month)],
            }
        
        if any(subset[c]['price'] for c in subset):
            js_path = os.path.join(DIR, f'{prefix}_data_{month}.js')
            var_name = f'{prefix.upper()}_DATA_{month.replace("-", "")}'
            # 跨月检测与HTML更新
            if not os.path.exists(js_path):
                update_html_scripts(month, prefix)
                
            with open(js_path, 'w', encoding='utf-8') as f:
                f.write(f'const {var_name} = ' + json.dumps(subset, ensure_ascii=False) + ';\n')
            print(f"  [JS] Updated {js_path}")

if __name__ == '__main__':
    print(f"ETF Daily Update Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    process_update(load_codes('etf_all.json'), 'etf_all.json', 'etf')
    process_update(load_codes('sp500_all.json'), 'sp500_all.json', 'sp500')
    print("\nGlobal update complete!")
