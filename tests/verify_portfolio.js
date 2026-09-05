/* 临时验证脚本: 用极简 DOM stub 加载 portfolio.js, 校验本次四项改动 */
global.window = {};
const mkEl = () => ({
  innerHTML: '', textContent: '', value: '', dataset: {}, style: {}, disabled: false,
  addEventListener() {}, querySelectorAll() { return []; },
  querySelector() { return null; }, focus() {}, closest() { return null; }
});
const els = {};
global.document = {
  getElementById(id) { if (!els[id]) els[id] = mkEl(); return els[id]; },
  querySelectorAll() { return []; }, querySelector() { return null; }
};
global.localStorage = {
  _d: {}, getItem(k) { return Object.prototype.hasOwnProperty.call(this._d, k) ? this._d[k] : null; },
  setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; }
};
global.ETF_DATA = {
  sz159941: { name: '广发纳斯达克100ETF', price: [{ date: '2026-09-03', value: 1.5 }] },
  sh513100: { name: '国泰纳斯达克100ETF', price: [{ date: '2026-09-03', value: 1.2 }] }
};
global.SP500_DATA = {}; global.US50_DATA = {}; global.DJIA_DATA = {};

const pf = require('../portfolio.js');

let pass = 0, fail = 0;
function check(name, actual, expected) {
  const ok = String(actual) === String(expected);
  console.log((ok ? '  [OK]   ' : '  [FAIL] ') + name + '  =>  ' + actual + (ok ? '' : '  (expected ' + expected + ')'));
  ok ? pass++ : fail++;
}
function contains(name, haystack, needle, want) {
  const has = haystack.indexOf(needle) !== -1;
  const ok = has === want;
  console.log((ok ? '  [OK]   ' : '  [FAIL] ') + name + '  =>  含"' + needle + '": ' + has);
  ok ? pass++ : fail++;
}

console.log('\n=== 1. 默认费率: 万分之一 + 单笔最低 0.1 元 ===');
check('FEE_DEFAULTS.rate (万1)', pf.FEE_DEFAULTS.rate, 0.0001);
check('FEE_DEFAULTS.min', pf.FEE_DEFAULTS.min, 0.1);
check('成交额 ¥10000 -> 手续费', pf.calcFee(10000).toFixed(4), '1.0000');
check('成交额 ¥500   -> 触发最低', pf.calcFee(500).toFixed(4), '0.1000');
check('成交额 ¥100   -> 触发最低', pf.calcFee(100).toFixed(4), '0.1000');
check('百分比输入框显示', pf.formatRatePct(0.0001), '0.01');
check('万分之几换算', pf.formatRateWan(0.0001), '万1');

console.log('\n=== 2. 每 ETF 默认折叠 ===');
pf.setHoldings([{
  code: 'sz159941',
  lots: [
    { date: '2026-08-01', shares: 1000, price: 1.400, fee: 0.14 },
    { date: '2026-08-20', shares: 2000, price: 1.450, fee: 0.29 }
  ]
}]);
pf.renderHoldingsTable();
let html = els.pfTbody.innerHTML;
check('默认折叠 isExpanded(sz159941)', pf.isExpanded('sz159941'), false);
contains('折叠时不渲染明细行', html, 'class="pf-lot', false);
contains('汇总行仍渲染', html, 'pf-subtotal', true);
contains('汇总行显示笔数', html, '2 笔', true);
contains('汇总行有折叠箭头', html, '&#9656;', true);
contains('汇总行保留清仓入口', html, 'data-clear-code="sz159941"', true);

pf.expand('sz159941');
pf.renderHoldingsTable();
html = els.pfTbody.innerHTML;
console.log('\n=== 3. 展开后: 编辑按钮 + 淡化的删除按钮 ===');
contains('展开后渲染明细行', html, 'class="pf-lot"', true);
contains('每笔有编辑按钮', html, 'data-edit-lot="sz159941|0"', true);
contains('每笔有删除按钮', html, 'data-del-lot="sz159941|1"', true);
contains('删除按钮使用淡化样式', html, 'pf-btn-mini-danger', true);
contains('编辑按钮使用常规样式', html, 'class="pf-btn-mini" data-edit-lot', true);

console.log('\n=== 4. 编辑态渲染输入框 ===');
const holdings = pf.getHoldings();
pf.setEditing({ code: 'sz159941', lot: holdings[0].lots[0] });
pf.renderHoldingsTable();
html = els.pfTbody.innerHTML;
contains('编辑行渲染日期输入', html, 'id="pfEditDate"', true);
contains('编辑行渲染份额输入', html, 'id="pfEditShares"', true);
contains('编辑行渲染单价输入', html, 'id="pfEditPrice"', true);
contains('编辑行渲染手续费输入', html, 'id="pfEditFee"', true);
contains('编辑行有保存按钮', html, 'data-edit-save="sz159941|0"', true);
contains('编辑行有取消按钮', html, 'data-edit-cancel="1"', true);
contains('编辑行保留原值', html, 'value="1000"', true);

console.log('\n=== 5. 汇总行列对齐(12列) ===');
pf.setEditing(null);
pf.renderHoldingsTable();
const subtotalRow = els.pfTbody.innerHTML.split('</tr>')[0];
const cellCount = (subtotalRow.match(/<td/g) || []).length + (parseInt((subtotalRow.match(/colspan="(\d+)"/) || [0, 1])[1], 10) - 1);
check('汇总行占列数', cellCount, 12);

console.log('\n' + (fail === 0 ? 'ALL PASS' : 'FAILED') + '  ' + pass + ' passed, ' + fail + ' failed\n');
process.exit(fail === 0 ? 0 : 1);
