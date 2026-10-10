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
TOP_COUNT = 5
BEIJING = timezone(timedelta(hours=8))
REQUIRED_SECRETS = ('ALERT_TO_EMAIL', 'ALERT_SMTP_HOST', 'ALERT_SMTP_USER', 'ALERT_SMTP_PASSWORD')


def alert_date(now):
    return now.astimezone(BEIJING).date().isoformat()


def months_ago(day, months):
    """Calendar-month lookback, clamping dates such as March 31 to February 28."""
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    return day.replace(year=year, month=month,
                       day=min(day.day, calendar.monthrange(year, month)[1]))


PREMIUM_SERIES = ('iopv_premium', 'premium')


def verified_series(info, through_date, key):
    """Recheck one stored premium series against prices and its own denominator.

    ``premium`` pairs a price date with the unit NAV published for that same
    date; ``iopv_premium`` is checked against the session's recorded IOPV.
    Both are rejected when the stored value disagrees with its own inputs.
    """
    prices = {item['date']: item.get('value') for item in info.get('price') or []
              if daily_update.is_valid_date(item.get('date'))}
    if key == 'premium':
        refs = {item['date']: item.get('value') for item in info.get('nav') or []
                if daily_update.is_valid_date(item.get('date'))}
    else:
        refs = {item['date']: item.get('iopv') for item in info.get(key) or []
                if daily_update.is_valid_date(item.get('date'))}
    verified = {}
    for record in info.get(key) or []:
        day = record.get('date')
        if not daily_update.is_valid_date(day) or day > through_date:
            continue
        if key == 'premium':
            nav_day = record.get('nav_date')
            # Final published unit-NAV premium requires matching observation dates.
            if not daily_update.is_valid_date(nav_day) or nav_day != day:
                continue
        try:
            premium = float(record['value'])
            price = float(prices.get(day))
            ref = float(refs.get(day))
        except (ValueError, TypeError, KeyError):
            continue
        if not all(map(math.isfinite, (premium, price, ref))) or price <= 0 or ref <= 0:
            continue
        if abs(premium) > daily_update.MAX_ABS_PREMIUM:
            continue
        if abs((price / ref - 1) * 100 - premium) > 0.01:
            continue
        verified[day] = premium
    return verified


def verified_premiums(info, through_date):
    """Recheck historical values against that day's price and referenced NAV."""
    return verified_series(info, through_date, 'premium')


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
    """All four on-exchange groups, fresh and verified, highest premium first.

    Prefers the IOPV series (same gauge as broker apps, available on the day)
    and falls back to the published unit-NAV premium for funds whose IOPV
    history has not been collected yet.
    """
    selected = []
    for label, filename in GROUPS:
        data = json.loads((root / filename).read_text(encoding='utf-8'))
        if not data:
            raise ValueError(f'{filename} has no funds')
        for code, info in data.items():
            premiums = info.get('premium') or []
            if not premiums:
                continue
            chosen = None
            for key in PREMIUM_SERIES:
                history = verified_series(info, price_date, key)
                if price_date in history:
                    chosen = (key, history)
                    break
            if chosen is None:
                continue
            key, history = chosen
            record = latest_record(info.get(key) or [], price_date)
            if record is None:
                continue
            nav_history = (verified_premiums(info, price_date) if key == 'iopv_premium'
                           else history)
            selected.append({
                'group': label, 'code': code, 'name': info.get('name') or code,
                'premium': history[price_date], 'price_date': price_date,
                'series': key,
                'nav_date': record.get('nav_date') or price_date,
                'nav_premium': nav_history.get(price_date),
                'iopv': record.get('iopv'),
                'comparison': premium_comparison(history, price_date),
            })
    return sorted(selected, key=lambda item: (-item['premium'], item['code']))


def latest_record(records, day):
    """Stored row for ``day`` used to read series-specific metadata."""
    for record in records:
        if record.get('date') == day:
            return record
    return None


def select_board(root, price_date, limit=TOP_COUNT):
    """Daily digest: the lowest and highest verified premiums for one session.

    Every trading day gets a digest, whether or not anything is cheap: the low
    table drives watch-list decisions, the high table shows where the QDII quota
    squeeze is most expensive. Items below THRESHOLD are flagged for emphasis.
    """
    ranked = select_ranked_funds(root, price_date)
    for item in ranked:
        item['alert'] = item['premium'] < THRESHOLD
    lowest = sorted(ranked, key=lambda item: (item['premium'], item['code']))[:limit]
    return {
        'price_date': price_date,
        'total': len(ranked),
        'alerts': sum(1 for item in ranked if item['alert']),
        'lowest': lowest,
        'highest': ranked[:limit],
    }


def latest_verified_date(root):
    """Most recent date carrying verified premiums in any group and any series.

    Prefers IOPV-series dates (same-day, no NAV dependency) over unit-NAV dates
    so the digest anchor can advance immediately after a session close, even when
    QDII NAV publication lags multiple days. This prevents long-holiday scenarios
    where the anchor gets stuck pre-holiday while post-holiday prices accumulate
    unreported.
    
    Returns the latest date across all funds and both premium series (iopv_premium
    and premium), ensuring every collected session eventually gets reported.
    """
    latest = None
    for _, filename in GROUPS:
        path = root / filename
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            continue
        for info in data.values():
            # Check IOPV series first (same-day availability)
            for key in PREMIUM_SERIES:
                for day in verified_series(info, '9999-12-31', key):
                    if latest is None or day > latest:
                        latest = day
    return latest


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
    state = read_state(state_path)
    if state.get('date') == today and state.get('status') == 'attempted':
        print('[Alert] Today already sent; skip')
        write_output(output_path, False)
        return False
    price_date = latest_verified_date(root)
    if not price_date:
        print('[Alert] No verified premium data available; skip')
        write_output(output_path, False)
        return False
    if state.get('price_date') == price_date and state.get('status') in ('reserved', 'attempted'):
        print(f'[Alert] Session {price_date} already reported; skip')
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
    board = select_board(root, price_date)
    if not board['lowest']:
        print(f'[Alert] No verified premium for price date {price_date}; skip')
        write_output(output_path, False)
        return False
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        'date': today, 'price_date': price_date, 'status': 'reserved',
        'fingerprint': fingerprint(board),
    }, indent=2) + '\n', encoding='utf-8')
    print(f'[Alert] Reserved {today} for session {price_date}: '
          f'{len(board["lowest"])} lowest / {len(board["highest"])} highest / '
          f'{board["alerts"]} below {THRESHOLD}%; send only after push')
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
    board = select_board(root, state['price_date'])
    if not board['lowest'] or fingerprint(board) != state.get('fingerprint'):
        print('[Alert] Market data changed since reservation; skip')
        write_output(output_path, False)
        return False
    state['status'] = 'attempted'
    state_path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    write_output(output_path, True)
    print(f'[Alert] Claimed {today} for session {state["price_date"]}; commit and push before SMTP')
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


def nav_value_text(item):
    value = item.get('nav_premium')
    return f'{value:+.4f}%' if value is not None else '净值未披露'


def item_lines(item):
    previous, change, averages = comparison_cells(item)
    flag = '  ⚠ 低于5%，重点关注' if item.get('alert') else ''
    gauge = 'IOPV口径，同券商App' if item.get('series') == 'iopv_premium' else '单位净值口径'
    return [
        f'{item["group"]} | {item["name"]} ({item["code"]}){flag}',
        f'  当前溢价：{item["premium"]:+.4f}%（{gauge}，收盘日 {item["price_date"]}）',
        f'  单位净值口径：{nav_value_text(item)}（净值日 {item["nav_date"]}）',
        f'  上一有效交易日：{previous}；变化：{change}',
        f'  近1个月平均：{averages[1]}',
        f'  近3个月平均：{averages[3]}',
        f'  近1年平均：{averages[12]}',
        '',
    ]


def item_cells(item, position):
    previous, change, averages = comparison_cells(item)
    return [str(position), item['group'], f'{item["name"]}（{item["code"]}）',
            f'{item["premium"]:+.4f}%', nav_value_text(item), previous, change,
            averages[1], averages[3], averages[12], item['nav_date']]


HEADINGS = ['排名', '类别', '基金（代码）', '当前溢价', '单位净值口径', '上一有效日', '变化',
            '近1个月均值', '近3个月均值', '近1年均值', '数据日']


def series_label(board):
    keys = {item.get('series') for item in board['lowest'] + board['highest']}
    if keys == {'iopv_premium'}:
        return 'IOPV 口径（收盘价 ÷ 当日基金份额参考净值，同券商 App / 雪球，当日可得）'
    if keys == {'premium'}:
        return '单位净值口径（收盘价 ÷ 同日已披露单位净值）'
    return '优先 IOPV 口径，缺 IOPV 的基金回退单位净值口径'


def render_table(items, ordered):
    rows = [' | '.join(HEADINGS)]
    table_rows = []
    for position, item in enumerate(ordered, start=1):
        cells = item_cells(item, position)
        rows.append(' | '.join(cells))
        style = ' style="background:#fff2cc"' if item.get('alert') else ''
        table_rows.append(f'<tr{style}>'
                          + ''.join(f'<td>{html.escape(cell)}</td>' for cell in cells)
                          + '</tr>')
    html_table = ('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">'
                  + '<thead><tr>' + ''.join(f'<th>{html.escape(label)}</th>' for label in HEADINGS)
                  + '</tr></thead><tbody>' + ''.join(table_rows) + '</tbody></table>')
    return rows, html_table


def build_message(board, today, recipient, sender):
    """One digest per verified session: lowest board + highest board."""
    price_date = board['price_date']
    mail = EmailMessage()
    mail['From'] = sender
    mail['To'] = recipient
    alert_note = f'，其中 {board["alerts"]} 只低于 {THRESHOLD:g}%' if board['alerts'] else f'，无低于 {THRESHOLD:g}% 的标的'
    mail['Subject'] = (f'场内ETF每日溢价播报｜发信 {today}｜'
                       f'数据日 {price_date}{alert_note}')
    summary = [f'北京时间 {today} 收盘后播报：数据日 {price_date}（价格日与净值日一致），'
               f'当日 {board["total"]} 只场内ETF有效'
               f'{alert_note}。',
               f'排序口径：{series_label(board)}。']
    low_plain, low_html = render_table(board['lowest'], board['lowest'])
    high_plain, high_html = render_table(
        board['highest'], sorted(board['highest'], key=lambda item: (-item['premium'], item['code'])))
    plain_blocks = ['']
    if board['alerts']:
        plain_blocks.append(f'低于 {THRESHOLD:g}% 的标的（{board["alerts"]}只）：')
        plain_blocks.extend(line for item in board['lowest'] if item.get('alert')
                            for line in item_lines(item))
    low_detail = [line for item in board['lowest'] for line in item_lines(item)]
    high_detail = [line for item in board['highest'] for line in item_lines(item)]
    plain = '\n'.join([
        *summary,
        '',
        f'【溢价最低 {len(board["lowest"])} 只】（升序，最值得先看）',
        ' | '.join(HEADINGS),
        *low_plain[1:],
        '',
        '明细：',
        *low_detail,
        f'【溢价最高 {len(board["highest"])} 只】（降序，注意溢价风险）',
        ' | '.join(HEADINGS),
        *high_plain[1:],
        '',
        '明细：',
        *high_detail,
        *plain_blocks,
        '历史均值按对应日历区间内的有效交易日等权平均，排除本次收盘日；历史不足完整区间显示数据不足。',
        '口径：场内收盘价 ÷ 同日已披露单位净值 − 1；未披露当日净值则不列入，并非IOPV溢价。',
        '来源：本项目每日更新的收盘价与基金净值；QDII净值存在披露滞后，长假后可能补发旧数据日。',
        '仅作关注提醒，不构成投资建议。',
    ])
    mail.set_content(plain)
    body = ['<html><body><div style="white-space:pre-wrap">'
            + html.escape('\n'.join(summary).strip()) + '</div>']
    if board['alerts']:
        body.append('<p>⚠ 黄色底纹行为溢价低于 '
                    f'{THRESHOLD:g}% 的标的，建议优先关注。</p>')
    body.append(f'<h3>溢价最低 {len(board["lowest"])} 只（升序）</h3>{low_html}')
    body.append(f'<h3>溢价最高 {len(board["highest"])} 只（降序）</h3>'
                '<p>溢价越高，追高风险越大。</p>' + high_html)
    body.append('<p>历史均值按对应日历区间内的有效交易日等权平均，排除本次收盘日；'
                '历史不足完整区间显示数据不足。场内收盘价 ÷ 同日已披露单位净值 − 1；'
                '未披露当日净值则不列入，并非IOPV溢价。仅作关注提醒，不构成投资建议。</p>')
    mail.add_alternative(''.join(body) + '</body></html>', subtype='html')
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
    board = select_board(root, state['price_date'])
    if not board['lowest'] or fingerprint(board) != state['fingerprint']:
        raise ValueError('market data changed since reservation; refusing to send')
    if now.astimezone(BEIJING).weekday() >= 5 or now.astimezone(BEIJING).hour < 16:
        raise ValueError('send window has closed; not retrying this attempt')
    # Avoid secret values in logs. Only SMTP over TLS is supported.
    port = int(env.get('ALERT_SMTP_PORT') or '465')
    if port not in (465, 587):
        raise ValueError('ALERT_SMTP_PORT must be 465 or 587')
    host = env['ALERT_SMTP_HOST']
    sender = env['ALERT_SMTP_USER']
    mail = build_message(board, today, env['ALERT_TO_EMAIL'], sender)
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
    print(f'[Alert] Submitted one digest for session {state["price_date"]} on {today} '
          f'({len(board["lowest"])} lowest / {board["alerts"]} below {THRESHOLD}%)')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Reserve and send one daily ETF premium digest per verified session')
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
