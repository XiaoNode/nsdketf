// 页面运行时冒烟测试：用 vm + 最小 DOM stub 在 Node 里完整执行
// index.html 的内联脚本（含末尾的 renderAll()）与 portfolio.js。
// 两个场景：
//   A. Chart 正常（stub）——各 tab/指数切换无错，mergeMonthly 拼接在页面环境回归
//   B. Chart 缺失（模拟 CDN 全挂）——页面不崩、图表位显示降级提示；
//      Chart 迟到加载后 renderAll 可恢复（对应 2026-09 staticfile / 2026-10 jsdelivr 白图事故）
// 运行：node tests/verify_page_runtime.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

let passed = 0, failed = 0;
function check(label, condition, detail) {
  if (condition) { passed++; console.log(`  [OK]   ${label}`); }
  else { failed++; console.log(`  [FAIL] ${label}${detail ? ' => ' + detail : ''}`); }
}

// ---------- 最小 DOM stub ----------
function makeEl(id) {
  const el = {
    id: id || '', innerHTML: '', textContent: '', value: '', checked: false,
    dataset: {}, style: {}, disabled: false, hidden: false, files: [], parentNode: null,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    children: [],
    addEventListener() {}, removeEventListener() {},
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    appendChild(c) { el.children.push(c); return c; },
    remove() {}, closest() { return null; },
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    getContext() {
      return { canvas: {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
        stroke() {}, arc() {}, fill() {}, save() {}, restore() {}, scale() {},
        translate() {}, setLineDash() {}, fillText() {}, measureText() { return { width: 0 }; } };
    },
  };
  return el;
}

function loadPage({ withChart }) {
  const elCache = new Map();
  const documentStub = {
    head: makeEl('head'), body: makeEl('body'),
    getElementById(id) {
      if (!elCache.has(id)) {
        const el = makeEl(id);
        el.parentNode = makeEl(id + ':wrap'); // 图表降级文案写进父容器
        elCache.set(id, el);
      }
      return elCache.get(id);
    },
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    createElement() { return makeEl(); },
    addEventListener() {},
  };
  const storage = new Map();
  const sandbox = {
    document: documentStub,
    window: null,
    Chart: withChart ? function Chart() { return { destroy() {}, update() {}, resize() {} }; } : undefined,
    navigator: { userAgent: 'node-smoke' },
    location: { href: 'https://xiaonode.github.io/nsdketf/', reload() {} },
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    localStorage: {
      getItem: k => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: k => storage.delete(k),
    },
    fetch: async (url) => ({
      ok: true, status: 200,
      json: async () => (String(url).includes('_last_update')
        ? { last_run_bj: '2026-10-14 00:30:00', trigger: 'schedule' }
        : {}),
      text: async () => '',
    }),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);

  const rejections = [];
  const onRejection = err => rejections.push(err);
  process.on('unhandledRejection', onRejection);

  // 按文档顺序：数据脚本 -> 内联脚本 -> portfolio.js
  const srcRe = /<script src="([^"]+)"><\/script>/g;
  const scripts = [];
  let m;
  while ((m = srcRe.exec(html)) !== null) scripts.push(m[1]);
  const local = scripts.filter(s => !/^https?:/.test(s));
  const dataScripts = local.filter(s => s.startsWith('data/'));
  const lateScripts = local.filter(s => !s.startsWith('data/'));

  function loadScript(src) {
    const file = path.join(ROOT, src);
    if (!fs.existsSync(file)) throw new Error(`missing script: ${src}`);
    let code = fs.readFileSync(file, 'utf8');
    code = code.replace(/^const\s+([A-Za-z_$][\w$]*)\s*=/, 'globalThis.$1 =');
    vm.runInContext(code, sandbox, { filename: src });
  }

  for (const src of dataScripts) loadScript(src);
  // 页面主脚本 = 含 mergeMonthly 的内联块（CDN fallback 块更短且不含它）
  const inlineBlocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const inline = inlineBlocks.find(b => b.includes('function mergeMonthly'));
  if (!inline) throw new Error('inline page script not found');
  vm.runInContext(inline, sandbox, { filename: 'index.html:inline' });
  for (const src of lateScripts) loadScript(src);

  return {
    sandbox, elCache, rejections,
    cleanup: () => process.removeListener('unhandledRejection', onRejection),
    probe: expr => vm.runInContext(expr, sandbox),
  };
}

// ================= 场景 A：Chart 正常 =================
console.log('\n=== A. Chart 正常：完整渲染 ===');
let page;
try {
  page = loadPage({ withChart: true });
  check('数据脚本 + 内联脚本 + portfolio.js 加载无异常', true);
} catch (err) {
  check('数据脚本 + 内联脚本 + portfolio.js 加载无异常', false, err.stack);
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(1);
}
const { sandbox, probe } = page;

const ETF_DATA = probe('ETF_DATA');
check('ETF_DATA 已生成且含 13 只', ETF_DATA && Object.keys(ETF_DATA).length === 13,
  ETF_DATA && Object.keys(ETF_DATA).length);
const one = ETF_DATA && ETF_DATA.sz159501;
check('sz159501 估算 IOPV 序列跨月拼接成功（>200 天）',
  one && (one.iopv_premium_est || []).length > 200,
  one && (one.iopv_premium_est || []).length);
check('sz159501 真实 IOPV 记录保留（>=4 天）',
  one && (one.iopv_premium || []).length >= 4,
  one && (one.iopv_premium || []).length);

console.log('\n=== A2. IOPV 图表数据集（默认 1 年 + 全部跨度）===');
try {
  const d1 = sandbox.buildIopvDatasets();
  check('默认 1 年区间回推约一年（2025-10 附近起）',
    d1.allDates[0] >= '2025-10-01' && d1.allDates[0] <= '2025-10-31', d1.allDates[0]);
  check('默认选中 2 只基金', d1.datasets.length === 2, d1.datasets.length);
  check('含估算标注点（历史段为估算）', d1.estDates.size > 100, d1.estDates.size);
  probe("currentRange='all'");
  probe("selectedCodes.add('sz159696');selectedCodes.add('sh513390');selectedCodes.add('sz159659')");
  const dAll = sandbox.buildIopvDatasets();
  check('「全部」区间回溯到 2024 年中', dAll.allDates[0] <= '2024-06-30', dAll.allDates[0]);
  check('「全部」含 5 只基金数据集', dAll.datasets.length === 5, dAll.datasets.length);
} catch (err) { check('buildIopvDatasets 无异常', false, err.stack); }

console.log('\n=== A3. 各 tab 渲染函数 ===');
for (const fn of ['renderPriceTab', 'renderPremiumTab', 'renderNavTab', 'renderInfoTab',
  'renderValuationTab', 'renderToolsTab', 'renderArbitrageAnalysis', 'renderComparisonTable']) {
  try { sandbox[fn](); check(`${fn}()`, true); }
  catch (err) { check(`${fn}()`, false, err.message); }
}

console.log('\n=== A4. 指数切换 + renderAll ===');
for (const idx of ['ndx', 'sp500', 'us50', 'djia', 'ndx_passive', 'holdings']) {
  try { sandbox.switchIndex(idx); sandbox.renderAll(); check(`switchIndex('${idx}') + renderAll`, true); }
  catch (err) { check(`switchIndex('${idx}') + renderAll`, false, err.message); }
}
check('window.renderHoldings 已挂载', typeof sandbox.window.renderHoldings === 'function');
page.cleanup();

// ================= 场景 B：Chart 缺失（CDN 全挂） =================
console.log('\n=== B. Chart 缺失：页面不崩 + 降级提示 + 迟到恢复 ===');
let pageB;
try {
  pageB = loadPage({ withChart: false });
  check('Chart 缺失时页面脚本仍无异常执行（不中断 renderAll）', true);
} catch (err) {
  check('Chart 缺失时页面脚本仍无异常执行', false, err.stack);
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(1);
}
try {
  const wrap = pageB.elCache.get('chartPrice');
  check('图表位显示降级提示（非白屏）',
    !!(wrap && wrap.parentNode && /图表库加载失败/.test(wrap.parentNode.innerHTML)),
    wrap && wrap.parentNode && wrap.parentNode.innerHTML.slice(0, 60));
  // 侧边栏等非图表内容照常渲染（stub 的 appendChild 不反射到 innerHTML，查 children）
  const list = pageB.elCache.get('etfList');
  check('侧边栏已渲染（ETF 列表非空）',
    !!(list && list.children.length > 0 &&
      list.children.some(ch => /嘉实/.test(ch.innerHTML || ch.textContent || ''))),
    list && `children=${list.children.length}`);
  // Chart 缺失时不应创建图表实例
  check('降级路径未创建图表实例', pageB.probe('Object.keys(charts).length') === 0,
    pageB.probe('Object.keys(charts).join(",")'));
  // 模拟 Chart 迟到加载：恢复后 renderAll 正常出图
  pageB.sandbox.Chart = function Chart() { return { destroy() {}, update() {}, resize() {} }; };
  pageB.sandbox.renderAll();
  check('Chart 迟到后 renderAll 恢复出图无异常', true);
  check('恢复后图表实例已创建（chartPrice）',
    pageB.probe('Object.keys(charts)').includes('chartPrice'),
    pageB.probe('Object.keys(charts).join(",")'));
} catch (err) { check('场景 B 断言过程无异常', false, err.stack); }
pageB.cleanup();

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
