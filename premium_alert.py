"""Daily low-premium email alert for on-exchange ETFs.

Run after market-data update and tests: reserve -> push -> claim -> push -> send.
The public reservation file contains only dates, status, and a digest of public
market data. Recipient and SMTP credentials are read from Actions Secrets.
"""

import argparse
import calendar
import hashlib
import html
import json
import math
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import daily_update

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / '.github' / 'premium-alert-state.json'
GROUPS = (
    ('纳斯达克100', 'etf_all.json'),
    ('标普500', 'sp500_all.json'),
    ('美国50', 'us50_all.json'),
    ('道琼斯', 'djia_all.json'),
)
THRESHOLD = 5.0
BEIJING = timezone(timedelta(hours=8))
REQUIRED_SECRETS = ('ALERT_TO_EMAIL', 'ALERT_SMTP_HOST', 'ALERT_SMTP_USER', 'ALERT_SMTP_PASSWORD')


def alert_date(now):
    return now.astimezone(BEIJING).date().isoformat()


def expected_price_date(now):
    local = now.astimezone(BEIJING)
    day = local.date()
    # Before the close (including the 08:20 catch-up run), use the prior session.
    if local.hour < 16:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def months_ago(day, months):
    """Calendar-month lookback, clamping dates such as March 31 to February 28."""
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    return day.replace(year=year, month=month,
                       day=min(day.day, calendar.monthrange(year, month)[1]))


def verified_premiums(info, through_date):
    """Recheck historical values against that day's price and referenced NAV."""
    prices = {item['date']: item.get('value') for item in info.get('price') or []
              if daily_update.is_valid_date(item.get('date'))}
    navs = {item['date']: item.get('value') for item in info.get('nav') or []
            if daily_update.is_valid_date(item.get('date'))}
    verified = {}
    for record in info.get('premium') or []:
        day = record.get('date')
        nav_day = record.get('nav_date')
        if not daily_update.is_valid_date(day) or day > through_date:
            continue
        if not daily_update.is_valid_date(nav_day):
            continue
        if nav_day < daily_update.previous_trading_day(day) or nav_day > day:
            continue
        try:
            premium = float(record['value'])
            price = float(prices.get(day))
            nav = float(navs.get(nav_day))
        except (ValueError, TypeError, KeyError):
            continue
        if not all(map(math.isfinite, (premium, price, nav))) or price <= 0 or nav <= 0:
            continue
        if abs(premium) > daily_update.MAX_ABS_PREMIUM:
            continue
        if abs((price / nav - 1) * 100 - premium) > 0.01:
            continue
        verified[day] = premium
    return verified


def premium_comparison(history, price_date):
    """Compare previous available session; averages exclude today's alert session."""
    prior = sorted((day, value) for day, value in history.items() if day < price_date)
    previous_day, previous_value = prior[-1] if prior else (None, None)
    today = datetime.strptime(price_date, '%Y-%m-%d').date()
    averages = {}
    for months in (1, 3, 12):
        start = months_ago(today, months).isoformat()
        values = [value for day, value in prior if start <= day]
        # An incomplete inception window must not be passed off as a full-period average.
        averages[months] = (sum(values) / len(values), len(values)) if values and prior[0][0] <= start else None
    return {
        'previous_date': previous_day, 'previous_premium': previous_value,
        'averages': averages,
    }


def select_ranked_funds(root, price_date):
    """All four on-exchange groups, fresh and verified, highest premium first."""
    selected = []
    for label, filename in GROUPS:
        data = json.loads((root / filename).read_text(encoding='utf-8'))
        if not data:
            raise ValueError(f'{filename} has no funds')
        for code, info in data.items():
            premiums = info.get('premium') or []
            if not premiums:
                continue
            record = premiums[-1]
            if record.get('date') != price_date:
                continue
            history = verified_premiums(info, price_date)
            premium = history.get(price_date)
            if premium is not None:
                selected.append({
                    'group': label, 'code': code, 'name': info.get('name') or code,
                    'premium': premium, 'price_date': price_date,
                    'nav_date': record['nav_date'],
                    'comparison': premium_comparison(history, price_date),
                })
    return sorted(selected, key=lambda item: (-item['premium'], item['code']))


def select_candidates(root, price_date):
    """Return only verified funds below the strict alert threshold."""
    return sorted((item for item in select_ranked_funds(root, price_date)
                   if item['premium'] < THRESHOLD),
                  key=lambda item: (item['premium'], item['code']))


def fingerprint(candidates):
    payload = json.dumps(candidates, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def read_state(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def write_output(path, should_send):
    if path:
        with open(path, 'a', encoding='utf-8') as output:
            output.write(f'send={str(should_send).lower()}\n')


def prepare(root, state_path, now, output_path=None, env=None):
    env = os.environ if env is None else env
    local = now.astimezone(BEIJING)
    # Never resend yesterday's close in the next morning's catch-up run.
    if local.weekday() >= 5 or local.hour < 16:
        print('[Alert] Not a post-close weekday run; skip')
        write_output(output_path, False)
        return False
    today = alert_date(now)
    if read_state(state_path).get('date') == today:
        print('[Alert] Today already reserved; skip')
        write_output(output_path, False)
        return False
    if not all(env.get(key) for key in REQUIRED_SECRETS):
        print('[Alert] Mail Secrets not configured; skip without reserving the day')
        write_output(output_path, False)
        return False
    if env.get('ALERT_SMTP_PORT') and env['ALERT_SMTP_PORT'] not in ('465', '587'):
        print('[Alert] SMTP port must be 465 or 587; skip')
        write_output(output_path, False)
        return False
    price_date = expected_price_date(now)
    candidates = select_candidates(root, price_date)
    if not candidates:
        print(f'[Alert] No verified ETF below {THRESHOLD}% for price date {price_date}')
        write_output(output_path, False)
        return False
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        'date': today, 'price_date': price_date, 'status': 'reserved',
        'fingerprint': fingerprint(candidates),
    }, indent=2) + '\n', encoding='utf-8')
    print(f'[Alert] Reserved {today}: {len(candidates)} eligible ETFs; send only after push')
    write_output(output_path, True)
    return True


def claim(root, state_path, now, output_path=None, env=None):
    """Durably claim the single send attempt before the SMTP operation."""
    env = os.environ if env is None else env
    state = read_state(state_path)
    today = alert_date(now)
    if state.get('date') != today or state.get('status') != 'reserved':
        print('[Alert] No unclaimed reservation for today; skip')
        write_output(output_path, False)
        return False
    if not all(env.get(key) for key in REQUIRED_SECRETS):
        print('[Alert] Mail Secrets missing; skip')
        write_output(output_path, False)
        return False
    if env.get('ALERT_SMTP_PORT') and env['ALERT_SMTP_PORT'] not in ('465', '587'):
        print('[Alert] SMTP port must be 465 or 587; skip')
        write_output(output_path, False)
        return False
    candidates = select_candidates(root, state['price_date'])
    if not candidates or fingerprint(candidates) != state.get('fingerprint'):
        print('[Alert] Market data changed since reservation; skip')
        write_output(output_path, False)
        return False
    state['status'] = 'attempted'
    state_path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    write_output(output_path, True)
    print(f'[Alert] Claimed {today}; commit and push before SMTP')
    return True


def comparison_cells(item):
    comparison = item['comparison']
    previous = (f'{comparison["previous_premium"]:+.4f}% ({comparison["previous_date"]})'
                if comparison['previous_date'] else '数据不足')
    change = (f'{item["premium"] - comparison["previous_premium"]:+.4f} 个百分点'
              if comparison['previous_date'] else '数据不足')
    averages = {}
    for months in (1, 3, 12):
        result = comparison['averages'][months]
        averages[months] = (f'{result[0]:+.4f}%（{result[1]}个有效交易日）'
                            if result else '数据不足')
    return previous, change, averages


def build_message(candidates, today, recipient, sender, ranked=None):
    ranked = sorted(candidates if ranked is None else ranked,
                    key=lambda item: (-item['premium'], item['code']))
    mail = EmailMessage()
    mail['From'] = sender
    mail['To'] = recipient
    mail['Subject'] = f'场内ETF低溢价关注提醒｜{today}｜{len(candidates)}只'
    rows = []
    for item in candidates:
        previous, change, averages = comparison_cells(item)
        rows.extend([
            f'{item["group"]} | {item["name"]} ({item["code"]})',
            f'  当前溢价：{item["premium"]:+.4f}%（收盘日 {item["price_date"]}，净值日 {item["nav_date"]}）',
            f'  上一有效交易日：{previous}；变化：{change}',
            f'  近1个月平均：{averages[1]}',
            f'  近3个月平均：{averages[3]}',
            f'  近1年平均：{averages[12]}',
            '',
        ])
    header = '排名 | 类别 | 基金（代码） | 当前溢价 | 上一有效日 | 变化 | 近1个月均值 | 近3个月均值 | 近1年均值 | 净值日'
    ranking = [header]
    table_rows = []
    for position, item in enumerate(ranked, start=1):
        previous, change, averages = comparison_cells(item)
        cells = [str(position), item['group'], f'{item["name"]}（{item["code"]}）',
                 f'{item["premium"]:+.4f}%', previous, change,
                 averages[1], averages[3], averages[12], item['nav_date']]
        ranking.append(' | '.join(cells))
        table_rows.append('<tr>' + ''.join(f'<td>{html.escape(cell)}</td>' for cell in cells) + '</tr>')
    alert_lines = [f'北京时间 {today}，以下 {len(candidates)} 只场内ETF的有效溢价率低于 5%：',
                   '', *rows]
    plain = '\n'.join([
        *alert_lines,
        f'当日场内ETF溢价从高到低（{len(ranked)}只；仅含当日有效数据）：',
        *ranking, '',
        '历史均值按对应日历区间内的有效交易日等权平均，排除本次收盘日；历史不足完整区间显示数据不足。',
        '口径：场内收盘价 ÷ 已披露单位净值 − 1；并非实时IOPV溢价。',
        '来源：本项目每日更新的收盘价与基金净值；QDII净值存在披露滞后。',
        '仅作关注提醒，不构成投资建议。',
    ])
    mail.set_content(plain)
    if ranked:
        headings = ['排名', '类别', '基金（代码）', '当前溢价', '上一有效日', '变化',
                    '近1个月均值', '近3个月均值', '近1年均值', '净值日']
        html_body = ('<html><body><div style="white-space:pre-wrap">'
                     + html.escape('\n'.join(alert_lines).strip())
                     + '</div><h3>当日场内ETF溢价从高到低</h3>'
                     + '<p>仅含当日有效数据，按当前溢价降序排列。</p>'
                     + '<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">'
                     + '<thead><tr>' + ''.join(f'<th>{html.escape(label)}</th>' for label in headings)
                     + '</tr></thead><tbody>' + ''.join(table_rows) + '</tbody></table>'
                     + '<p>历史均值按对应日历区间内的有效交易日等权平均，排除本次收盘日；'
                     + '历史不足完整区间显示数据不足。场内收盘价 ÷ 已披露单位净值 − 1；'
                     + '并非实时IOPV溢价。仅作关注提醒，不构成投资建议。</p></body></html>')
        mail.add_alternative(html_body, subtype='html')
    return mail


def send(root, state_path, now, env=None, smtp_factory=None):
    """Send a *previously committed* reservation; never reserve in this step."""
    env = os.environ if env is None else env
    state = read_state(state_path)
    today = alert_date(now)
    if state.get('date') != today or state.get('status') != 'attempted' or not state.get('fingerprint'):
        raise ValueError('no claimed attempt for today')
    if not all(env.get(key) for key in REQUIRED_SECRETS):
        raise ValueError('mail Secrets missing')
    candidates = select_candidates(root, state['price_date'])
    if not candidates or fingerprint(candidates) != state['fingerprint']:
        raise ValueError('market data changed since reservation; refusing to send')
    if now.astimezone(BEIJING).weekday() >= 5 or now.astimezone(BEIJING).hour < 16:
        raise ValueError('send window has closed; not retrying this attempt')
    # Avoid secret values in logs. Only SMTP over TLS is supported.
    port = int(env.get('ALERT_SMTP_PORT') or '465')
    if port not in (465, 587):
        raise ValueError('ALERT_SMTP_PORT must be 465 or 587')
    host = env['ALERT_SMTP_HOST']
    sender = env['ALERT_SMTP_USER']
    ranked = select_ranked_funds(root, state['price_date'])
    mail = build_message(candidates, today, env['ALERT_TO_EMAIL'], sender, ranked=ranked)
    context = ssl.create_default_context()
    if smtp_factory is None:
        smtp_factory = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    connection = (smtp_factory(host, port, timeout=20, context=context) if port == 465
                  else smtp_factory(host, port, timeout=20))
    with connection:
        if port == 587:
            connection.starttls(context=context)
        connection.login(sender, env['ALERT_SMTP_PASSWORD'])
        connection.send_message(mail)
    print(f'[Alert] Submitted one email for {len(candidates)} ETFs on {today}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Reserve and send one low-premium ETF email per Beijing day')
    parser.add_argument('action', choices=('prepare', 'claim', 'send'))
    args = parser.parse_args(argv)
    now = datetime.now(BEIJING)
    try:
        if args.action == 'prepare':
            prepare(ROOT, STATE_PATH, now, output_path=os.environ.get('GITHUB_OUTPUT'))
        elif args.action == 'claim':
            claim(ROOT, STATE_PATH, now, output_path=os.environ.get('GITHUB_OUTPUT'))
        else:
            send(ROOT, STATE_PATH, now)
    except Exception:
        # SMTP exceptions can echo recipient or credentials; never print them.
        print('[Alert] Alert step failed; inspect configuration without printing secrets', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
