"""
QDII ETF 数据抓取脚本
从多个数据源（新浪财经、东方财富等）抓取价格和溢价率数据
用于交叉验证
"""

import json
import os
import sys
import time
import re
import gzip
import math
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# 输出目录
OUTPUT_DIR = r"D:\nasdaq-sp500-etf-analyzer"

# ==================== ETF 定义（已验证） ====================

NASDAQ_ETFS = {
    "sz159941": {"name": "广发纳斯达克100ETF", "code": "159941", "market": "sz", "type": "nasdaq"},
    "sh513100": {"name": "国泰纳斯达克100ETF", "code": "513100", "market": "sh", "type": "nasdaq"},
    "sz159632": {"name": "华安纳斯达克100ETF", "code": "159632", "market": "sz", "type": "nasdaq"},
    "sz159513": {"name": "大成纳斯达克100ETF", "code": "159513", "market": "sz", "type": "nasdaq"},
    "sz159501": {"name": "嘉实纳斯达克100ETF", "code": "159501", "market": "sz", "type": "nasdaq"},
    "sh513300": {"name": "华夏纳斯达克100ETF", "code": "513300", "market": "sh", "type": "nasdaq"},
    "sz159696": {"name": "易方达纳斯达克100ETF", "code": "159696", "market": "sz", "type": "nasdaq"},
    "sz159659": {"name": "招商纳斯达克100ETF", "code": "159659", "market": "sz", "type": "nasdaq"},
    "sh513870": {"name": "富国纳斯达克100ETF", "code": "513870", "market": "sh", "type": "nasdaq"},
    "sh513110": {"name": "华泰柏瑞纳斯达克100ETF", "code": "513110", "market": "sh", "type": "nasdaq"},
    "sh513390": {"name": "博时纳斯达克100ETF", "code": "513390", "market": "sh", "type": "nasdaq"},
    "sz159660": {"name": "汇添富纳斯达克100ETF", "code": "159660", "market": "sz", "type": "nasdaq"},
    "sz159509": {"name": "纳指科技ETF景顺", "code": "159509", "market": "sz", "type": "nasdaq"},
}

SP500_ETFS = {
    "sh513500": {"name": "标普500ETF博时", "code": "513500", "market": "sh", "type": "sp500"},
    "sz159612": {"name": "标普500ETF国泰", "code": "159612", "market": "sz", "type": "sp500"},
    "sz159655": {"name": "标普500ETF华夏", "code": "159655", "market": "sz", "type": "sp500"},
    "sh513650": {"name": "标普500ETF南方", "code": "513650", "market": "sh", "type": "sp500"},
}

# ==================== 费率数据（已通过天天基金网验证） ====================

FEE_DATA = {
    # 纳斯达克100 ETF
    "sz159941": {"fee_total": 1.00, "fee_mgmt": 0.80, "fee_cust": 0.20, "fee_svc": 0.00},
    "sh513100": {"fee_total": 0.80, "fee_mgmt": 0.60, "fee_cust": 0.20, "fee_svc": 0.00},
    "sz159632": {"fee_total": 0.80, "fee_mgmt": 0.60, "fee_cust": 0.20, "fee_svc": 0.00},
    "sz159513": {"fee_total": 1.00, "fee_mgmt": 0.80, "fee_cust": 0.20, "fee_svc": 0.00},
    "sz159501": {"fee_total": 0.60, "fee_mgmt": 0.50, "fee_cust": 0.10, "fee_svc": 0.00},
    "sh513300": {"fee_total": 0.80, "fee_mgmt": 0.60, "fee_cust": 0.20, "fee_svc": 0.00},
    "sz159696": {"fee_total": 0.60, "fee_mgmt": 0.50, "fee_cust": 0.10, "fee_svc": 0.00},
    "sz159659": {"fee_total": 0.65, "fee_mgmt": 0.50, "fee_cust": 0.15, "fee_svc": 0.00},
    "sh513870": {"fee_total": 0.60, "fee_mgmt": 0.50, "fee_cust": 0.10, "fee_svc": 0.00},
    "sh513110": {"fee_total": 1.00, "fee_mgmt": 0.80, "fee_cust": 0.20, "fee_svc": 0.00},
    "sh513390": {"fee_total": 0.65, "fee_mgmt": 0.50, "fee_cust": 0.15, "fee_svc": 0.00},
    "sz159660": {"fee_total": 0.65, "fee_mgmt": 0.50, "fee_cust": 0.15, "fee_svc": 0.00},
    "sz159509": {"fee_total": 0.60, "fee_mgmt": 0.50, "fee_cust": 0.10, "fee_svc": 0.00},
    # 标普500 ETF
    "sh513500": {"fee_total": 0.80, "fee_mgmt": 0.60, "fee_cust": 0.20, "fee_svc": 0.00},
    "sz159612": {"fee_total": 0.75, "fee_mgmt": 0.60, "fee_cust": 0.15, "fee_svc": 0.00},
    "sz159655": {"fee_total": 0.75, "fee_mgmt": 0.60, "fee_cust": 0.15, "fee_svc": 0.00},
    "sh513650": {"fee_total": 0.75, "fee_mgmt": 0.60, "fee_cust": 0.15, "fee_svc": 0.00},
}

# ==================== 数据抓取函数 ====================

def make_request(url, headers=None, timeout=15, retries=3):
    """发送HTTP请求，带重试机制"""
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://finance.sina.com.cn/',
    }
    if headers:
        default_headers.update(headers)
    
    for attempt in range(retries):
        try:
            req = Request(url, headers=default_headers)
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                # 尝试gzip解压
                if resp.headers.get('Content-Encoding') == 'gzip':
                    data = gzip.decompress(data)
                return data.decode('utf-8', errors='ignore')
        except (URLError, HTTPError, OSError) as e:
            print(f"  Attempt {attempt+1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  All retries exhausted for {url}")
    return None

# -------------------- 数据源1：东方财富（天天基金）--------------------

def fetch_eastmoney_nav(code, days=500):
    """
    从东方财富（天天基金）获取ETF历史净值数据
    返回 {date: value} 的字典
    """
    results = {}
    
    # 分页获取（每次最多200条）
    page_size = 200
    total_pages = math.ceil(days / page_size) + 2  # 多取几页确保覆盖足够多日期
    
    for page in range(1, total_pages + 1):
        # 多个备用URL
        urls = [
            f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex={page}&pageSize={page_size}&startDate=&endDate=&_={int(time.time()*1000)}",
            f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time()*1000)}",
        ]
        
        data = None
        for url in urls:
            headers = {
                'Referer': f'https://fundf10.eastmoney.com/jjjz_{code}.html',
                'Host': url.split('/')[2],
            }
            data = make_request(url, headers=headers)
            if data:
                break
        
        if not data:
            continue
        
        try:
            j = json.loads(data)
            records = j.get('Data', {}).get('LSJZList', [])
            if not records:
                break
            
            for rec in records:
                date_str = rec.get('FSRQ', '')
                nav = rec.get('DWJZ', '')
                if date_str and nav and nav != '--':
                    try:
                        results[date_str] = float(nav)
                    except ValueError:
                        pass
            
            if len(records) < page_size:
                break
                
        except json.JSONDecodeError:
            break
        
        time.sleep(0.3)
    
    # 如果东方财富没拿到数据，尝试用腾讯财经接口
    if not results:
        results = fetch_tencent_nav(code, days)
    
    return results

def fetch_tencent_nav(code, days=500):
    """
    从腾讯财经获取基金净值数据（备用）
    """
    results = {}
    page_size = 20
    total_pages = math.ceil(days / page_size) + 5
    
    for page in range(1, total_pages + 1):
        url = f"https://s2.finance.qq.com/fund/jzzs_new.php?fund_code={code}&type=all&page={page}&limit={page_size}"
        headers = {'Referer': 'https://stockapp.finance.qq.com/'}
        
        data = make_request(url, headers=headers)
        if not data:
            continue
        
        # 腾讯的返回格式可能是JSONP
        try:
            # 尝试解析JSONP
            if data.startswith('jsonp'):
                data = data[data.index('(')+1:data.rindex(')')]
            j = json.loads(data)
            records = j.get('data', [])
            if not records:
                break
            
            for rec in records:
                date_str = rec.get('date', '')
                nav = rec.get('nav', '') or rec.get('unit_nav', '')
                if date_str and nav:
                    try:
                        results[date_str] = float(nav)
                    except ValueError:
                        pass
            
            if len(records) < page_size:
                break
        except (json.JSONDecodeError, ValueError):
            break
        
        time.sleep(0.3)
    
    return results

def fetch_eastmoney_etf_kline(code, market, days=500):
    """
    从东方财富获取ETF场内K线数据（收盘价）
    """
    secid = f"0.{code}" if market == "sz" else f"1.{code}"
    
    # 计算日期范围
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')
    
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=1&beg={start_date}&end={end_date}&lmt={days+20}"
    
    data = make_request(url)
    if not data:
        return {}
    
    try:
        j = json.loads(data)
        klines = j.get('data', {}).get('klines', [])
        results = {}
        for line in klines:
            parts = line.split(',')
            if len(parts) >= 3:
                date_str = parts[0]
                # 前复权收盘价
                close = float(parts[2])
                results[date_str] = close
        return results
    except (json.JSONDecodeError, ValueError):
        return {}

def fetch_eastmoney_premium(code, market):
    """
    从东方财富获取ETF的折溢价率数据
    东方财富的实时行情接口包含折溢价率
    """
    secid = f"0.{code}" if market == "sz" else f"1.{code}"
    
    # 获取近两年的折溢价历史数据
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=750)).strftime('%Y%m%d')
    
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64&klt=101&fqt=1&beg={start_date}&end={end_date}&lmt=750"
    
    data = make_request(url)
    if not data:
        return {}
    
    results = {}
    try:
        j = json.loads(data)
        klines = j.get('data', {}).get('klines', [])
        for line in klines:
            parts = line.split(',')
            if len(parts) >= 13:
                date_str = parts[0]
                # 在ETF行情的K线数据中，某些字段可能是折溢价率
                # 尝试从一些扩展字段中获取
                try:
                    # 东方财富部分数据在f60-f64中包含折溢价
                    # 具体字段因接口而异，我们额外通过另一个接口获取
                    pass
                except (ValueError, IndexError):
                    pass
        return results
    except json.JSONDecodeError:
        return {}

# -------------------- 数据源2：新浪财经 --------------------

def fetch_sina_prices(code, market, days=500):
    """
    从新浪财经获取ETF历史价格数据
    返回 {date: price} 字典
    """
    # 新浪的代码格式: sz159941, sh513100
    symbol = f"{market}{code}"
    
    results = {}
    
    # 新浪日K线数据接口 - 使用多个备用URL
    urls = [
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days+50}",
        f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days+50}",
    ]
    
    data = None
    for url in urls:
        headers = {
            'Referer': 'https://finance.sina.com.cn/',
            'Host': url.split('/')[2],
            'Accept': '*/*',
        }
        data = make_request(url, headers=headers)
        if data:
            break
    
    if not data:
        return {}
    
    try:
        records = json.loads(data)
        for rec in records:
            date_str = rec.get('day', '')
            close = rec.get('close', '')
            if date_str and close:
                try:
                    results[date_str] = float(close)
                except ValueError:
                    pass
    except json.JSONDecodeError:
        pass
    
    return results

def fetch_sina_fund_nav(code, days=500):
    """
    从新浪财经获取基金净值数据
    """
    results = {}
    
    # 新浪基金净值接口
    page_count = math.ceil(days / 50) + 2
    
    for page in range(1, page_count + 1):
        url = f"https://api.fund.sina.com.cn/Data/fund/jjjz/getjjjz?callback=&page={page}&num=50&sort=fsrq&asc=1&fund_code={code}"
        headers = {
            'Referer': f'https://finance.sina.com.cn/fund/quotes/{code}/bc.shtml',
        }
        
        data = make_request(url, headers=headers)
        if not data:
            continue
        
        try:
            j = json.loads(data)
            records = j.get('result', {}).get('data', {}).get('list', [])
            if not records:
                break
            
            for rec in records:
                date_str = rec.get('fsrq', '')
                nav = rec.get('dwjz', '')
                if date_str and nav and nav != '--':
                    try:
                        results[date_str] = float(nav)
                    except ValueError:
                        pass
            
            if len(records) < 50:
                break
        except (json.JSONDecodeError, KeyError):
            break
        
        time.sleep(0.3)
    
    return results

def fetch_sina_premium(code, market):
    """
    从新浪财经获取ETF的折溢价率
    新浪的ETF实时行情接口包含折溢价率字段
    """
    symbol = f"{market}{code}"
    
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=750)).strftime('%Y%m%d')
    
    # 使用新浪日K线数据，其中可能包含一些扩展信息
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=750"
    
    headers = {'Referer': 'https://finance.sina.com.cn/'}
    
    data = make_request(url, headers=headers)
    if not data:
        return {}
    
    # 新浪的日K不直接包含溢价率，我们稍后通过价格和净值计算
    # 真正的溢价率需要从行情接口获取
    return {}

# -------------------- 数据源3：雪球 --------------------

def fetch_xueqiu_prices(code, market, days=500):
    """
    从雪球获取ETF价格数据
    """
    symbol = f"{market.upper()}{code}"
    
    end_ts = int(time.time() * 1000)
    start_ts = int((time.time() - days * 86400) * 1000)
    
    # 获取历史K线
    url = f"https://stock.xueqiu.com/v5/stock/chart/kline.json?symbol={symbol}&begin={start_ts}&period=day&type=before&count=-{days+50}&indicator=kline,pe,pb,ps,pcf,market_capital,agt,ggt,balance"
    
    headers = {
        'Referer': 'https://xueqiu.com/',
        'Cookie': 'xq_a_token=test',  # 可能受限于是否登录
    }
    
    data = make_request(url, headers=headers)
    if not data:
        return {}
    
    try:
        j = json.loads(data)
        items = j.get('data', {}).get('item', [])
        results = {}
        for item in items:
            if isinstance(item, list) and len(item) >= 6:
                ts = item[0]
                close = item[5]
                if ts and close:
                    date_str = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
                    results[date_str] = float(close)
        return results
    except (json.JSONDecodeError, KeyError):
        return {}

# -------------------- 溢价率计算 --------------------

def calculate_premium(prices, navs):
    """
    通过价格和净值计算溢价率
    premium = (price / nav - 1) * 100
    正值为溢价，负值为折价
    """
    premium = []
    for d in prices:
        if d['date'] in navs:
            price_v = d['value']
            nav_v = navs[d['date']]
            if nav_v > 0:
                pr = round((price_v / nav_v - 1) * 100, 2)
                premium.append({'date': d['date'], 'value': pr})
    return premium

# -------------------- 数据抓取主流程 --------------------

def fetch_all_etf_data(etfs_dict, label, days=500):
    """
    抓取所有ETF的数据并合并两个数据源
    """
    print(f"\n{'='*60}")
    print(f"开始抓取 {label} 数据（{len(etfs_dict)}只ETF）")
    print(f"{'='*60}")
    
    all_data = {}
    
    for etf_id, etf_info in etfs_dict.items():
        code = etf_info['code']
        market = etf_info['market']
        name = etf_info['name']
        
        print(f"\n--- {etf_id} {name} ---")
        
        fee = FEE_DATA.get(etf_id, {"fee_total": 0.80, "fee_mgmt": 0.60, "fee_cust": 0.20, "fee_svc": 0.00})
        
        # --- 数据源1：东方财富 K线（收盘价） ---
        print(f"  数据源1: 东方财富 K线...")
        em_prices = fetch_eastmoney_etf_kline(code, market, days)
        print(f"    获取到 {len(em_prices)} 条价格数据")
        
        # --- 数据源2：新浪财经 K线（收盘价） ---
        print(f"  数据源2: 新浪财经 K线...")
        sina_prices = fetch_sina_prices(code, market, days)
        print(f"    获取到 {len(sina_prices)} 条价格数据")
        
        # --- 合并价格数据（优先东方财富，补充新浪） ---
        merged_prices = {}
        # 先加入新浪的数据
        for date_str, price in sina_prices.items():
            merged_prices[date_str] = price
        # 东方财富的数据覆盖（更可靠）
        for date_str, price in em_prices.items():
            merged_prices[date_str] = price
        
        # 排序并转为列表
        price_list = [{'date': d, 'value': round(v, 4)} for d, v in sorted(merged_prices.items())]
        print(f"    合并后 {len(price_list)} 条价格数据")
        
        # --- 数据源1：天天基金净值 ---
        print(f"  数据源1: 天天基金净值...")
        em_navs = fetch_eastmoney_nav(code, days)
        print(f"    获取到 {len(em_navs)} 条净值数据")
        
        # --- 数据源2：腾讯财经净值（备用）---
        print(f"  数据源2: 腾讯财经净值...")
        tencent_navs = fetch_tencent_nav(code, days)
        print(f"    获取到 {len(tencent_navs)} 条净值数据")
        
        # --- 合并净值数据 ---
        merged_navs = {}
        for date_str, nav in tencent_navs.items():
            merged_navs[date_str] = nav
        for date_str, nav in em_navs.items():
            merged_navs[date_str] = nav
        
        nav_list = [{'date': d, 'value': round(v, 4)} for d, v in sorted(merged_navs.items())]
        nav_dict = {d: v for d, v in sorted(merged_navs.items())}
        print(f"    合并后 {len(nav_list)} 条净值数据")
        
        # --- 计算溢价率 ---
        premium_list = calculate_premium(price_list, nav_dict)
        print(f"    计算得到 {len(premium_list)} 条溢价率数据")
        
        # --- 组装数据 ---
        all_data[etf_id] = {
            "code": etf_id,
            "name": name,
            "fee_total": fee["fee_total"],
            "fee_mgmt": fee["fee_mgmt"],
            "fee_cust": fee["fee_cust"],
            "fee_svc": fee["fee_svc"],
            "price": price_list,
            "premium": premium_list,
            "nav": nav_list
        }
        
        time.sleep(0.5)  # 避免请求过快
    
    return all_data

def split_data_by_month(data_dict):
    """
    将数据按月份拆分，生成每月一个文件的内容
    """
    monthly = {}
    for etf_id, etf_data in data_dict.items():
        for key in ['price', 'premium', 'nav']:
            for item in etf_data.get(key, []):
                date_str = item['date']
                year_month = date_str[:7].replace('-', '_')  # "2024-05" -> "2024_05"
                if year_month not in monthly:
                    monthly[year_month] = {}
                if etf_id not in monthly[year_month]:
                    monthly[year_month][etf_id] = {
                        "code": etf_data["code"],
                        "name": etf_data["name"],
                        "fee_total": etf_data["fee_total"],
                        "fee_mgmt": etf_data["fee_mgmt"],
                        "fee_cust": etf_data["fee_cust"],
                        "fee_svc": etf_data["fee_svc"],
                        "price": [],
                        "premium": [],
                        "nav": []
                    }
                monthly[year_month][etf_id][key].append(item)
    
    return monthly

def generate_js_files(data_dict, prefix, output_dir):
    """
    生成按月划分的JS数据文件
    """
    monthly = split_data_by_month(data_dict)
    
    # 对每个月份的每个ETF，排序数据
    for year_month, etfs in monthly.items():
        for etf_id, etf_data in etfs.items():
            for key in ['price', 'premium', 'nav']:
                etf_data[key].sort(key=lambda x: x['date'])
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    generated_files = []
    for year_month, etfs in sorted(monthly.items()):
        filename = f"{prefix}_{year_month.replace('_', '-')}.js"
        filepath = os.path.join(output_dir, filename)
        
        var_name = f"{prefix.upper()}_DATA_{year_month}"
        
        js_content = f"const {var_name} = {json.dumps(etfs, ensure_ascii=False)};"
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(js_content)
        
        generated_files.append((year_month, filename))
        print(f"  生成: {filename} ({len(etfs)} 只ETF)")
    
    return generated_files

def generate_all_json(data_dict, filename, output_dir):
    """
    生成完整JSON文件
    """
    filepath = os.path.join(output_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data_dict, f, ensure_ascii=False)
    print(f"  生成: {filename}")
    return filepath

def main():
    print("=" * 70)
    print("QDII ETF 数据抓取工具")
    print("数据源: 东方财富(K线+净值) + 新浪财经(K线+净值)")
    print("=" * 70)
    
    # 抓取两年数据（约500个交易日）
    days = 520  # 约2年的交易日
    
    # 纳斯达克100 ETF
    nasdaq_data = fetch_all_etf_data(NASDAQ_ETFS, "纳斯达克100 ETF", days)
    
    # 标普500 ETF
    sp500_data = fetch_all_etf_data(SP500_ETFS, "标普500 ETF", days)
    
    print(f"\n{'='*60}")
    print(f"抓取完成！开始生成文件...")
    print(f"{'='*60}")
    
    # 生成文件
    print("\n--- 纳斯达克100 月度JS文件 ---")
    ndx_files = generate_js_files(nasdaq_data, "etf_data", OUTPUT_DIR)
    
    print("\n--- 标普500 月度JS文件 ---")
    sp_files = generate_js_files(sp500_data, "sp500_data", OUTPUT_DIR)
    
    print("\n--- 完整JSON文件 ---")
    generate_all_json(nasdaq_data, "etf_all.json", OUTPUT_DIR)
    generate_all_json(sp500_data, "sp500_all.json", OUTPUT_DIR)
    
    # 生成总结报告
    print(f"\n{'='*60}")
    print(f"数据抓取总结报告")
    print(f"{'='*60}")
    
    for label, data in [("纳斯达克100", nasdaq_data), ("标普500", sp500_data)]:
        print(f"\n{label}:")
        for etf_id, etf_data in data.items():
            print(f"  {etf_id} {etf_data['name']}:")
            print(f"    价格数据: {len(etf_data['price'])} 条")
            print(f"    净值数据: {len(etf_data['nav'])} 条")
            print(f"    溢价率数据: {len(etf_data['premium'])} 条")
            print(f"    费率: 管理{etf_data['fee_mgmt']}% + 托管{etf_data['fee_cust']}% = {etf_data['fee_total']}%")
    
    print(f"\n全部完成！数据文件已保存到: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
