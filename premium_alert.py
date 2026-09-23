"""Daily low-premium email alert for on-exchange ETFs.

Run after market-data update and tests: reserve -> push -> claim -> push -> send.
The public reservation file contains only dates, status, and a digest of public
market data. Recipient and SMTP credentials are read from Actions Secrets.
"""

import argparse
import hashlib
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


def select_candidates(root, price_date):
    """Only include verified, fresh premiums; never treat missing data as zero."""
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
            nav_date = record.get('nav_date')
            if not daily_update.is_valid_date(nav_date):
                continue
            if nav_date < daily_update.previous_trading_day(price_date) or nav_date > price_date:
                continue
            price = next((item.get('value') for item in reversed(info.get('price') or [])
                          if item.get('date') == price_date), None)
            nav = next((item.get('value') for item in reversed(info.get('nav') or [])
                        if item.get('date') == nav_date), None)
            try:
                premium = float(record['value'])
                p = float(price)
                n = float(nav)
            except (ValueError, TypeError, KeyError):
                continue
            if not all(map(math.isfinite, (premium, p, n))) or p <= 0 or n <= 0:
                continue
            if abs((p / n - 1) * 100 - premium) > 0.01:
                continue
            if premium < THRESHOLD:
                selected.append({
                    'group': label, 'code': code, 'name': info.get('name') or code,
                    'premium': premium, 'price_date': price_date, 'nav_date': nav_date,
                })
    return sorted(selected, key=lambda item: (item['premium'], item['code']))


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


def build_message(candidates, today, recipient, sender):
    mail = EmailMessage()
    mail['From'] = sender
    mail['To'] = recipient
    mail['Subject'] = f'场内ETF低溢价关注提醒｜{today}｜{len(candidates)}只'
    rows = [f'{item["group"]} | {item["name"]} ({item["code"]}) | '
            f'{item["premium"]:+.4f}% | 收盘日 {item["price_date"]} | '
            f'净值日 {item["nav_date"]}' for item in candidates]
    mail.set_content('\n'.join([
        f'北京时间 {today}，以下 {len(candidates)} 只场内ETF的有效溢价率低于 5%：',
        '', *rows, '',
        '口径：场内收盘价 ÷ 已披露单位净值 − 1；并非实时IOPV溢价。',
        '来源：本项目每日更新的收盘价与基金净值；QDII净值存在披露滞后。',
        '仅作关注提醒，不构成投资建议。',
    ]))
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
    mail = build_message(candidates, today, env['ALERT_TO_EMAIL'], sender)
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


def send_test_email(env=None, smtp_factory=None):
    """One-off SMTP connectivity check; does not affect the daily alert state."""
    env = os.environ if env is None else env
    if not all(env.get(key) for key in REQUIRED_SECRETS):
        raise ValueError('mail Secrets missing')
    port = int(env.get('ALERT_SMTP_PORT') or '465')
    if port not in (465, 587):
        raise ValueError('ALERT_SMTP_PORT must be 465 or 587')
    mail = EmailMessage()
    mail['From'] = env['ALERT_SMTP_USER']
    mail['To'] = env['ALERT_TO_EMAIL']
    mail['Subject'] = '场内ETF低溢价提醒｜邮件通道测试'
    mail.set_content('这是一封一次性测试邮件，用于确认 GitHub Actions 的 SMTP 配置可以正常发信。\n'
                     '此邮件不代表任何 ETF 触发了低溢价提醒，也不影响每日提醒次数。')
    context = ssl.create_default_context()
    if smtp_factory is None:
        smtp_factory = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    connection = (smtp_factory(env['ALERT_SMTP_HOST'], port, timeout=20, context=context)
                  if port == 465 else smtp_factory(env['ALERT_SMTP_HOST'], port, timeout=20))
    with connection:
        if port == 587:
            connection.starttls(context=context)
        connection.login(env['ALERT_SMTP_USER'], env['ALERT_SMTP_PASSWORD'])
        connection.send_message(mail)
    print('[Alert] Test email submitted to SMTP server')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Reserve and send one low-premium ETF email per Beijing day')
    parser.add_argument('action', choices=('prepare', 'claim', 'send', 'test-email'))
    args = parser.parse_args(argv)
    now = datetime.now(BEIJING)
    try:
        if args.action == 'prepare':
            prepare(ROOT, STATE_PATH, now, output_path=os.environ.get('GITHUB_OUTPUT'))
        elif args.action == 'claim':
            claim(ROOT, STATE_PATH, now, output_path=os.environ.get('GITHUB_OUTPUT'))
        elif args.action == 'test-email':
            send_test_email()
        else:
            send(ROOT, STATE_PATH, now)
    except Exception:
        # SMTP exceptions can echo recipient or credentials; never print them.
        print('[Alert] Alert step failed; inspect configuration without printing secrets', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
