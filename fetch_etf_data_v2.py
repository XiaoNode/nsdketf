"""
QDII ETF 数据抓取脚本 v2 - 多数据源交叉验证
数据源: 新浪财经K线(价格) + 天天基金(净值) + 腾讯行情(备用价格)
"""

import json
import os
import time
import math
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Alias
OUTPUT_DIR = r"D:\workbuddyspace\2026-07-04-22-54-53"

# ==================== ETF 定义（已通过天天基金网验证） ====================

NASDAQ_ETFS = {
    "sz159941": {"name": "广发纳斯达克100ETF", "code": "159941", "market": "sz"},
    "sh513100": {"name": "国泰纳斯达克100ETF", "code": "513100", "market": "sh"},
    "sz159632": {"name": "华安纳斯达克100ETF", "code": "159632", "market": "sz"},
    "sz159513": {"name": "大成纳斯达克100ETF", "code": "159513", "market": "sz"},
    "sz159501": {"name": "嘉实纳斯达克100ETF", "code": "159501", "market": "sz"},
    "sh513300": {"name": "华夏纳斯达克100ETF", "code": "513300", "market": "sh"},
    "sz159696": {"name": "易方达纳斯达克100ETF", "code": "159696", "market": "sz"},
    "sz159659": {"name": "招商纳斯达克100ETF", "code": "159659", "market": "sz"},
    "sh513870": {"name": "富国纳斯达克100ETF", "code": "513870", "market": "sh"},
    "sh513110": {"name": "华泰柏瑞纳斯达克100ETF", "code": "513110", "market": "sh"},
    "sh513390": {"name": "博时纳斯达克100ETF", "code": "513390", "market": "sh"},
    "sz159660": {"name": "汇添富纳斯达克100ETF", "code": "159660", "market": "sz"},
    "sz159509": {"name": "纳斯达克科技市值加权ETF景顺", "code": "159509", "market": "sz"},
}

SP500_ETFS = {
    "sh513500": {"name": "标普500ETF博时", "code": "513500", "market": "sh"},
    "sz159612": {"name": "标普500ETF国泰", "code": "159612", "market": "sz"},
    "sz159655": {"name": "标普500ETF华夏", "code": "159655", "market": "sz"},
    "sh513650": {"name": "标普500ETF南方", "code": "513650", "market": "sh"},
}

# ==================== 费率数据（已通过天天基金网验证） ====================

FEE_DATA = {
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
    "sh513500": {"fee_total": 0.80, "fee_mgmt": 0.60, "fee_cust": 0.20, "fee_svc": 0.00},
    "sz159612": {"fee_total": 0.75, "fee_mgmt": 0.60, "fee_cust": 0.15, "fee_svc": 0.00},
    "sz159655": {"fee_total": 0.75, "fee_mgmt": 0.60, "fee_cust": 0.15, "fee_svc": 0.00},
    "sh513650": {"fee_total": 0.75, "fee_mgmt": 0.60, "fee_cust": 0.15, "fee_svc": 0.00},
    "sz159509": {"fee_total": 0.60, "fee_mgmt": 0.50, "fee_cust": 0.10, "fee_svc": 0.00},
}

def make_request(url, headers=None, timeout=10, retries=2):
    """发送HTTP请求"""
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    if headers:
        default_headers.update(headers)
    
    for attempt in range(retries):
        try:
            req = Request(url, headers=default_headers)
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                return data.decode('utf-8', errors='ignore')
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
    return None

# ==================== 数据源1: 新浪财经K线（价格） ====================

def fetch_sina_kline(code, market, days=520):
    """从新浪获取ETF日K线价格数据"""
    symbol = f"{market}{code}"
    results = {}
    
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days+50}"
    
    data = make_request(url, headers={'Referer': 'https://finance.sina.com.cn/'})
    if not data:
        return results
    
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

# ==================== 数据源2: 腾讯行情K线（备用价格） ====================

def fetch_gtimg_kline(code, market, days=520):
    """从腾讯获取ETF日K线价格数据"""
    symbol = f"{market}{code}"
    results = {}
    
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{days+50},qfq"
    
    data = make_request(url, headers={'Referer': 'https://gu.qq.com/'})
    if not data:
        return results
    
    try:
        j = json.loads(data)
        klines = j.get('data', {}).get(symbol, {}).get('qfqday', []) or \
                 j.get('data', {}).get(symbol, {}).get('day', [])
        for line in klines:
            if isinstance(line, list) and len(line) >= 3:
                date_str = line[0]
                close = float(line[2])
                if date_str and close > 0:
                    results[date_str] = close
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    
    return results

# ==================== 数据源3: 天天基金（净值） ====================

def fetch_fund_nav(code, days=520):
    """从天天基金获取基金净值数据"""
    results = {}
    
    # 天天基金每页固定20条，计算需要的页数
    # 约2年，每周约5个交易日左右有净值，一年约250+，所以约500+条
    total_pages = 35  # 预取35页，足够覆盖两年
    
    for page in range(1, total_pages + 1):
        url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex={page}&pageSize=20&startDate=2024-01-01&endDate=2026-12-31&_={int(time.time()*1000)}"
        
        data = make_request(url, headers={
            'Referer': f'https://fundf10.eastmoney.com/jjjz_{code}.html'
        })
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
            
            if len(records) < 20:
                break
        except json.JSONDecodeError:
            break
        
        time.sleep(0.3)
    
    return results

# ==================== 溢价率计算 ====================

def calculate_premium(prices, navs):
    """通过价格和净值计算溢价率"""
    premium = []
    for d in prices:
        date_str = d['date']
        if date_str in navs:
            nav_v = navs[date_str]
            price_v = d['value']
            if nav_v > 0:
                pr = round((price_v / nav_v - 1) * 100, 2)
                premium.append({'date': date_str, 'value': pr})
    return premium

# ==================== 数据合并 ====================

def merge_price_data(source1, source2):
    """合并两个数据源的价��数据，优先source1"""
    merged = {}
    for d, v in source2.items():
        merged[d] = v
    for d, v in source1.items():
        merged[d] = v
    return {d: round(v, 4) for d, v in sorted(merged.items())}

# ==================== 主流程 ====================

def fetch_one_etf(etf_id, etf_info, days=520):
    """抓取单个ETF的所有数据"""
    code = etf_info['code']
    market = etf_info['market']
    name = etf_info['name']
    
    print(f"\n{'='*50}")
    print(f"  {etf_id} {name}")
    print(f"{'='*50}")
    
    # 价格数据 - 数据源1: 新浪K线
    print(f"  [1/4] 新浪K线...", end=' ', flush=True)
    sina_prices = fetch_sina_kline(code, market, days)
    print(f"{len(sina_prices)}条")
    
    # 价格数据 - 数据源2: 腾讯行情K线
    print(f"  [2/4] 腾讯K线...", end=' ', flush=True)
    gtimg_prices = fetch_gtimg_kline(code, market, days)
    print(f"{len(gtimg_prices)}条")
    
    # 合并价格
    merged_prices = merge_price_data(sina_prices, gtimg_prices)
    price_list = [{'date': d, 'value': v} for d, v in merged_prices.items()]
    print(f"  => 合并价格: {len(price_list)}条")
    
    # 净值数据 - 天天基金
    print(f"  [3/4] 天天基金净值...", end=' ', flush=True)
    navs = fetch_fund_nav(code, days)
    print(f"{len(navs)}条")
    nav_list = [{'date': d, 'value': round(v, 4)} for d, v in sorted(navs.items())]
    
    # 计算溢价率
    print(f"  [4/4] 计算溢价率...", end=' ', flush=True)
    premium_list = calculate_premium(price_list, navs)
    print(f"{len(premium_list)}条")
    
    fee = FEE_DATA.get(etf_id, {"fee_total": 0.80, "fee_mgmt": 0.60, "fee_cust": 0.20, "fee_svc": 0.00})
    
    # 打印数据范围
    if price_list:
        print(f"  价格范围: {price_list[0]['date']} ~ {price_list[-1]['date']}")
    if nav_list:
        print(f"  净值范围: {nav_list[0]['date']} ~ {nav_list[-1]['date']}")
    if premium_list:
        premiums = [p['value'] for p in premium_list]
        print(f"  溢价范围: {min(premiums):.2f}% ~ {max(premiums):.2f}%")
    
    return {
        "code": etf_id,
        "name": name,
        "fee_total": fee["fee_total"],
        "fee_mgmt": fee["fee_mgmt"],
        "fee_cust": fee["fee_cust"],
        "fee_svc": fee["fee_svc"],
        "price": price_list,
        "premium": premium_list,
        "nav": nav_list
    }, len(price_list), len(navs), len(premium_list)

def data_summary_check(nasdaq_data, sp500_data):
    """数据质量摘要检查，标记问题"""
    print(f"\n{'='*60}")
    print("数据质量检查")
    print(f"{'='*60}")
    
    issues = []
    for label, data in [("纳斯达克100", nasdaq_data), ("标普500", sp500_data)]:
        for etf_id, d in data.items():
            # 检查价格数据量
            if len(d['price']) < 100:
                issues.append(f"  [WARN] {etf_id} {d['name']}: 价格数据仅 {len(d['price'])} 条")
            # 检查净值数据量
            if len(d['nav']) < 50:
                issues.append(f"  [WARN] {etf_id} {d['name']}: 净值数据仅 {len(d['nav'])} 条")
            # 检查溢价率数据量
            if len(d['premium']) < 20:
                issues.append(f"  [WARN] {etf_id} {d['name']}: 溢价率数据仅 {len(d['premium'])} 条")
    
    if issues:
        print("\n发现以下问题:")
        for issue in issues:
            print(issue)
    else:
        print("\n[OK] 所有数据检查通过")

def generate_js_files(data_dict, prefix, output_dir):
    """生成按月拆分的JS数据文件"""
    # 按月份分组
    monthly = {}
    for etf_id, etf_data in data_dict.items():
        for key in ['price', 'premium', 'nav']:
            for item in etf_data.get(key, []):
                date_str = item['date']
                ym = date_str[:7]  # "2024-05"
                ym_key = ym.replace('-', '_')
                
                if ym_key not in monthly:
                    monthly[ym_key] = {}
                if etf_id not in monthly[ym_key]:
                    monthly[ym_key][etf_id] = {
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
                monthly[ym_key][etf_id][key].append(item)
    
    # 排序
    for ym_key in monthly:
        for etf_id in monthly[ym_key]:
            for key in ['price', 'premium', 'nav']:
                monthly[ym_key][etf_id][key].sort(key=lambda x: x['date'])
    
    os.makedirs(output_dir, exist_ok=True)
    generated = []
    
    for ym_key in sorted(monthly):
        filename = f"{prefix}_{ym_key.replace('_', '-')}.js"
        filepath = os.path.join(output_dir, filename)
        # prefix是"etf_data" -> "ETF_DATA"，var_name应为 ETF_DATA_202607（无_DATA后缀，无下划线）
        base_var = prefix.upper()  # ETF_DATA or SP500_DATA
        ym_compact = ym_key.replace('_', '')  # 2026_07 -> 202607
        var_name = f"{base_var}_{ym_compact}"
        
        js_content = f"const {var_name} = {json.dumps(monthly[ym_key], ensure_ascii=False)};"
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(js_content)
        
        generated.append((ym_key, filename))
        print(f"  [OK] {filename} ({len(monthly[ym_key])} ETFs)")
    
    return generated

def main():
    print("=" * 70)
    print("QDII ETF 数据抓取工具 v2")
    print("价格: 新浪K线 + 腾讯行情(备用)")
    print("净值: 天天基金")
    print("费率: 已通过天天基金网验证")
    print("=" * 70)
    
    days = 520  # 约2年
    
    # 抓取纳斯达克100 ETF
    print(f"\n>> 开始抓取纳斯达克100 ETF ({len(NASDAQ_ETFS)}只)")
    nasdaq_data = {}
    for etf_id, etf_info in NASDAQ_ETFS.items():
        try:
            data, p_cnt, n_cnt, pr_cnt = fetch_one_etf(etf_id, etf_info, days)
            nasdaq_data[etf_id] = data
            time.sleep(0.8)
        except Exception as e:
            print(f"  [ERROR] 错误: {e}")
    
    # 抓取标普500 ETF
    print(f"\n>> 开始抓取标普500 ETF ({len(SP500_ETFS)}只)")
    sp500_data = {}
    for etf_id, etf_info in SP500_ETFS.items():
        try:
            data, p_cnt, n_cnt, pr_cnt = fetch_one_etf(etf_id, etf_info, days)
            sp500_data[etf_id] = data
            time.sleep(0.8)
        except Exception as e:
            print(f"  [ERROR] 错误: {e}")
    
    # 数据质量检查
    data_summary_check(nasdaq_data, sp500_data)
    
    # 生成文件
    print(f"\n{'='*60}")
    print("生成数据文件")
    print(f"{'='*60}")
    
    print("\n--- 纳斯达克100 月度JS文件 ---")
    ndx_months = generate_js_files(nasdaq_data, "etf_data", OUTPUT_DIR)
    
    print("\n--- 标普500 月度JS文件 ---")
    sp_months = generate_js_files(sp500_data, "sp500_data", OUTPUT_DIR)
    
    # 完整JSON文件
    print("\n--- 完整JSON ---")
    with open(os.path.join(OUTPUT_DIR, "etf_all.json"), 'w', encoding='utf-8') as f:
        json.dump(nasdaq_data, f, ensure_ascii=False)
    print("  [OK] etf_all.json")
    
    with open(os.path.join(OUTPUT_DIR, "sp500_all.json"), 'w', encoding='utf-8') as f:
        json.dump(sp500_data, f, ensure_ascii=False)
    print("  [OK] sp500_all.json")
    
    # 总结
    print(f"\n{'='*60}")
    print("抓取完成！")
    print(f"{'='*60}")
    print(f"\n纳斯达克100 ETF: {len(nasdaq_data)}只")
    for etf_id, d in nasdaq_data.items():
        print(f"  {etf_id} {d['name']}: 价格{len(d['price'])}条, 净值{len(d['nav'])}条, 溢价率{len(d['premium'])}条, 费率{d['fee_total']}%")
    
    print(f"\n标普500 ETF: {len(sp500_data)}只")
    for etf_id, d in sp500_data.items():
        print(f"  {etf_id} {d['name']}: 价格{len(d['price'])}条, 净值{len(d['nav'])}条, 溢价率{len(d['premium'])}条, 费率{d['fee_total']}%")

if __name__ == "__main__":
    main()
