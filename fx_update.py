"""下载 USD/CNY 日历史；ECB 与 FRED 仅作同日期、跨时点合理性复核。"""
import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXCHUS"
THRESHOLD_PCT = 0.5
STALE_AFTER_DAYS = 10  # FRED 日观察值按周公布，不能把抓取成功当成最新日期。
SOURCES = [
    {"id": "ecb", "name": "ECB euro foreign exchange reference rates", "url": ECB_URL,
     "method": "同日 EUR/CNY ÷ EUR/USD；欧洲参考时点，不是可成交价或收盘价"},
    {"id": "fred", "name": "FRED DEXCHUS / Federal Reserve H.10", "url": FRED_URL,
     "method": "纽约正午电汇买入参考价，CNY per USD；日观察值按周公布"},
]


def history_start(today):
    try:
        return today.replace(year=today.year - 10)
    except ValueError:
        return today.replace(year=today.year - 10, day=28)


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "nsdketf-fx/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.read()
    except urllib.error.URLError as exc:
        if os.name != "nt" or not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        # Windows 系统 curl 使用系统信任库；不使用 -k 或关闭任何证书验证。
        result = subprocess.run(
            ["curl.exe", "--fail", "--silent", "--show-error", "--location",
             "--proto", "=https", "--proto-redir", "=https", "--max-time", "60", url],
            capture_output=True, check=True, timeout=65,
        )
        return result.stdout


def positive_number(raw):
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    # 本链路只处理 USD、EUR、CNY；拒绝每百单位报价等明显量纲错误。
    return value if math.isfinite(value) and 0 < value < 100 else None


def parse_ecb(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        text = archive.read("eurofxref-hist.csv").decode("utf-8-sig")
    rows = csv.DictReader(io.StringIO(text), skipinitialspace=True)
    if not {"Date", "USD", "CNY"}.issubset(rows.fieldnames or []):
        raise ValueError("ECB CSV 缺少 Date/USD/CNY 列")
    values = {}
    for row in rows:
        usd, cny = positive_number(row["USD"]), positive_number(row["CNY"])
        if usd is not None and cny is not None:
            day = date.fromisoformat(row["Date"]).isoformat()
            value = positive_number(cny / usd)
            if value is None:
                raise ValueError("ECB 交叉汇率量纲异常")
            if day in values and values[day] != value:
                raise ValueError("ECB 同日存在不同观察值")
            values[day] = value
    return values


def parse_fred(payload):
    rows = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    fields = rows.fieldnames or []
    day_field = "observation_date" if "observation_date" in fields else "DATE"
    if day_field not in fields or "DEXCHUS" not in fields:
        raise ValueError("FRED CSV 缺少日期/DEXCHUS 列")
    values = {}
    for row in rows:
        value = positive_number(row["DEXCHUS"])
        if value is not None:
            day = date.fromisoformat(row[day_field]).isoformat()
            if day in values and values[day] != value:
                raise ValueError("FRED 同日存在不同观察值")
            values[day] = value
    return values


def check_coverage(values, start, today):
    selected = {day: value for day, value in values.items()
                if start.isoformat() <= day <= today.isoformat()}
    if len(selected) < 2000:
        raise ValueError("十年历史不足 2000 个有效观察值")
    if min(selected) > (start + timedelta(days=14)).isoformat():
        raise ValueError("十年历史起点缺失")
    return selected


def fetch_source(source_id, today):
    parser, url = (parse_ecb, ECB_URL) if source_id == "ecb" else (parse_fred, FRED_URL)
    try:
        values = check_coverage(parser(download(url)), history_start(today), today)
        return values, None
    except Exception as exc:
        return {}, f"{type(exc).__name__}: 数据源抓取或历史完整性校验失败；保留旧验证数据"


def build_document(ecb, fred, previous=None, errors=None, now=None):
    for values in (ecb, fred):
        if any(not isinstance(value, (int, float)) or positive_number(value) is None
               for value in values.values()):
            raise ValueError("汇率输入必须是正数、有限值且小于100")
    for row in (previous or {}).get("records", []):
        for field in ("value", "secondary_value", "unverified_primary_value", "unverified_secondary_value"):
            value = row.get(field)
            if value is not None and (not isinstance(value, (int, float)) or positive_number(value) is None):
                raise ValueError("缓存汇率存在非法观察值，拒绝覆盖")
    now = now or datetime.now(timezone.utc)
    today = now.date()
    start = history_start(today).isoformat()
    errors = errors or {}
    old_rows = {row["date"]: row for row in (previous or {}).get("records", [])
                if start <= row["date"] <= today.isoformat()}
    records = []
    for day in sorted(set(ecb) | set(fred) | set(old_rows)):
        if not start <= day <= today.isoformat():
            continue
        primary, secondary = ecb.get(day), fred.get(day)
        if primary is not None and secondary is not None:
            deviation = abs(primary / secondary - 1) * 100
            records.append({"date": day, "value": round(primary, 8),
                            "secondary_value": secondary, "deviation_pct": round(deviation, 6),
                            "status": "verified" if deviation <= THRESHOLD_PCT else "conflict"})
        elif day in old_rows and old_rows[day].get("status") == "verified":
            # 保留上次双源确认的整条观察值，不拿单源修订值冒充已复核数据。
            record = dict(old_rows[day])
            record["cached"] = True
            record["refresh_status"] = "pending"
            if primary is not None:
                record["unverified_primary_value"] = round(primary, 8)
            if secondary is not None:
                record["unverified_secondary_value"] = secondary
            records.append(record)
        elif primary is not None or secondary is not None:
            records.append({"date": day, "value": round(primary, 8) if primary is not None else None,
                            "secondary_value": secondary, "deviation_pct": None, "status": "pending"})
        else:
            record = dict(old_rows[day])
            record["cached"] = True
            records.append(record)
    verified = [row for row in records if row["status"] == "verified"]
    latest = verified[-1]["date"] if verified else None
    counts = {status: sum(row["status"] == status for row in records)
              for status in ("verified", "pending", "conflict")}
    complete = bool(records and len(records) >= 2000
                    and records[0]["date"] <= (history_start(today) + timedelta(days=14)).isoformat())
    stale = bool(errors) or latest is None or (today - date.fromisoformat(latest)).days > STALE_AFTER_DAYS
    sources = []
    for source in SOURCES:
        values = ecb if source["id"] == "ecb" else fred
        item = dict(source)
        item.update({"fetch_status": "failed" if source["id"] in errors else "ok",
                     "last_observation_date": max(values) if values else None,
                     "observation_count": len(values)})
        sources.append(item)
    return {
        "pair": "USD/CNY", "unit": "CNY per USD", "updated_at": now.isoformat(),
        "last_verified_date": latest, "sources": sources, "threshold_pct": THRESHOLD_PCT,
        "comparison": "同一观察日期、不同机构与参考时点的合理性复核，并非同收盘精确一致；不跨日匹配",
        "deviation_formula": "abs(ECB / FRED - 1) * 100",
        "fetch_status": "failed" if len(errors) == 2 else "degraded" if errors else "ok",
        "is_stale": stale, "stale_after_days": STALE_AFTER_DAYS, "errors": errors,
        "coverage": {"requested_start": start, "requested_end": today.isoformat(),
                     "first_date": records[0]["date"] if records else None,
                     "last_date": records[-1]["date"] if records else None,
                     "record_count": len(records), "history_complete": complete, **counts},
        "records": records,
    }


def atomic_write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".fx-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(document, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        handle.write("\n")
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description="更新近十年 USD/CNY 双源日历史")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "data/fx_usdcny.json")
    args = parser.parse_args()
    previous = None
    if args.output.exists():
        # 旧文件异常时不覆盖，以免丢失已验证历史。
        previous = json.loads(args.output.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda name: fetch_source(name, now.date()), ("ecb", "fred")))
    errors = {name: result[1] for name, result in zip(("ecb", "fred"), results) if result[1]}
    document = build_document(results[0][0], results[1][0], previous, errors, now)
    atomic_write(args.output, document)
    print(json.dumps({key: document[key] for key in
                      ("fetch_status", "is_stale", "last_verified_date", "coverage", "errors")},
                     ensure_ascii=False))
    # 数据源故障记录在 JSON 中，但不阻断独立的 ETF 更新和发布。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
