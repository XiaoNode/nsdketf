// 验证 index.html 的 mergeMonthly：IOPV 两口径（iopv_premium / iopv_premium_est）
// 是「月度文件只存当月切片」的序列，必须跨月拼接；曾因被最新月份整体覆盖，
// 导致合并后只剩当月一片、IOPV 口径图只剩最近几天。
// 运行：node tests/verify_merge_monthly.js
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

let passed = 0, failed = 0;
function check(label, condition, detail) {
  if (condition) { passed++; console.log(`  [OK]   ${label}`); }
  else { failed++; console.log(`  [FAIL] ${label}${detail ? ' => ' + detail : ''}`); }
}

// 从 index.html 提取函数源码（花括号配对，跳过字符串内的括号由内容本身保证无花括号）
function extractFunction(name) {
  const start = html.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${name} not found in index.html`);
  const open = html.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}') {
      depth--;
      if (depth === 0) return html.slice(start, i + 1);
    }
  }
  throw new Error(`${name}: unbalanced braces`);
}
const mergeMonthly = eval(`(${extractFunction('mergeMonthly')})`);

// 与 index.html 的 collectMonthly 同序加载月度文件（时间正序）
function collectMonthly(prefix, startYear, startMonth) {
  const months = [];
  const now = new Date();
  let y = startYear, m = startMonth;
  while (y < now.getFullYear() || (y === now.getFullYear() && m <= now.getMonth() + 1)) {
    const ym = '' + y + (m < 10 ? '0' + m : m);
    const file = path.join(ROOT, 'data', `${prefix}_data_${y}-${m < 10 ? '0' + m : m}.js`);
    if (fs.existsSync(file)) {
      const text = fs.readFileSync(file, 'utf8');
      const body = text.slice(text.indexOf('=') + 1).replace(/;\s*$/, '');
      months.push(eval(`(${body})`));
    }
    m++; if (m > 12) { m = 1; y++; }
  }
  return months;
}

console.log('\n=== 1. 合成用例：跨月拼接 / 后月覆盖 / 旧月真实记录保留 ===');
{
  const months = [
    { A: { name: 'A', price: [{ date: '2026-09-30', value: 2.0 }],
           iopv_premium: [{ date: '2026-09-30', value: 12.0, iopv: 1.8 }],
           iopv_premium_est: [{ date: '2026-09-29', value: 11.0, estimated: true },
                              { date: '2026-09-30', value: 12.0, estimated: true }] } },
    { A: { name: 'A', price: [{ date: '2026-10-09', value: 2.2 }],
           iopv_premium: [{ date: '2026-10-08', value: 13.0, iopv: 1.9 }],
           iopv_premium_est: [{ date: '2026-10-09', value: 14.0, estimated: true }] } },
  ];
  const merged = mergeMonthly(months);
  const a = merged.A;
  check('真实 IOPV 跨月保留（9-30 + 10-08）',
    a.iopv_premium.length === 2 && a.iopv_premium[0].date === '2026-09-30'
      && a.iopv_premium[1].date === '2026-10-08',
    JSON.stringify(a.iopv_premium));
  check('估算 IOPV 跨月拼接（9-29/9-30/10-09）',
    a.iopv_premium_est.length === 3
      && a.iopv_premium_est[2].date === '2026-10-09',
    JSON.stringify(a.iopv_premium_est));
  check('序列按日期升序',
    a.iopv_premium_est.every((v, i, arr) => i === 0 || arr[i - 1].date < v.date));
}

console.log('\n=== 2. 合成用例：后月份重算覆盖同日期，不产生重复 ===');
{
  const months = [
    { A: { iopv_premium_est: [{ date: '2026-10-01', value: 10.0, estimated: true }] } },
    { A: { iopv_premium_est: [{ date: '2026-10-01', value: 10.5, estimated: true },
                              { date: '2026-10-02', value: 11.0, estimated: true }] } },
  ];
  const est = mergeMonthly(months).A.iopv_premium_est;
  check('同日期条目唯一且取后月份值',
    est.length === 2 && est[0].value === 10.5 && est[1].value === 11.0,
    JSON.stringify(est));
}

console.log('\n=== 3. 合成用例：旧月份独有字段不被新月清空 ===');
{
  const months = [
    { A: { iopv_premium_est: [{ date: '2026-09-30', value: 9.0, estimated: true }] } },
    { A: { price: [{ date: '2026-10-09', value: 2.2 }] } },
  ];
  const merged = mergeMonthly(months);
  check('后月无 iopv_premium_est 时保留旧月值',
    (merged.A.iopv_premium_est || []).length === 1,
    JSON.stringify(merged.A.iopv_premium_est));
  check('price 仍正常拼接', (merged.A.price || []).length === 1);
}

console.log('\n=== 4. 真实数据：纳指组合并结果覆盖一年 ===');
{
  const merged = mergeMonthly(collectMonthly('etf', 2024, 5));
  const one = merged.sz159501;
  check('sz159501 存在', !!one);
  const est = one.iopv_premium_est || [];
  const real = one.iopv_premium || [];
  check('估算 IOPV 序列非空（修复前合并后为空）', est.length > 0,
    `length=${est.length}`);
  check('估算序列向前覆盖至少一年',
    est.length > 200 && est[0].date <= '2025-10-01',
    `first=${est[0] && est[0].date}, length=${est.length}`);
  check('估算序列末尾衔接真实采集起点（2026-09-30）',
    est[est.length - 1].date === '2026-09-30',
    est[est.length - 1].date);
  check('真实 IOPV 记录保留（10-08 起）',
    real.length >= 4 && real[real.length - 1].date >= '2026-10-12',
    `length=${real.length}, last=${real[real.length - 1] && real[real.length - 1].date}`);
  const realDates = new Set(real.map(r => r.date));
  check('估算与真实记录日期不重叠（图表契约）',
    !est.some(e => realDates.has(e.date)));
  check('price/premium/nav 序列仍完整',
    one.price.length > 200 && one.nav.length > 200 && one.premium.length > 200,
    `price=${one.price.length}, nav=${one.nav.length}, premium=${one.premium.length}`);
  check('price 最新日不旧于 10-12',
    one.price[one.price.length - 1].date >= '2026-10-12',
    one.price[one.price.length - 1].date);
  check('13 只 ETF 全部合并出估算序列',
    Object.keys(merged).every(c => (merged[c].iopv_premium_est || []).length > 0));
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
