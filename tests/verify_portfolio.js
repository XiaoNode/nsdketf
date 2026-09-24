/* 用极简 DOM stub 加载 portfolio.js, 校验持仓数据与表单行为 */
global.window = {};
const mkEl = () => ({
  innerHTML: '', textContent: '', value: '', dataset: {}, style: {}, disabled: false,
  listeners: {}, addEventListener(type, cb) { (this.listeners[type] ||= []).push(cb); },
  click() { (this.listeners.click || []).forEach(cb => cb({ target: this })); },
  querySelectorAll() { return []; }, querySelector() { return null; },
  focus() {}, closest() { return null; }
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
  sz159941: { name: '广发纳斯达克100ETF', price: [{ date: '2026-09-03', value: 1.5 }],
    premium: [{ date: '2026-08-01', value: 3.25 }, { date: '2026-08-20', value: -0.5 }, { date: '2026-09-03', value: 5.1 }] },
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

console.log('\n=== 5. 汇总行列对齐(13列) ===');
pf.setEditing(null);
pf.renderHoldingsTable();
const subtotalRow = els.pfTbody.innerHTML.split('</tr>')[0];
const cellCount = (subtotalRow.match(/<td/g) || []).length + (parseInt((subtotalRow.match(/colspan="(\d+)"/) || [0, 1])[1], 10) - 1);
check('汇总行占列数', cellCount, 13);

console.log('\n=== 6. 可选溢价: 手填优先，空值按买入日查，旧记录不写回 ===');
const etf = pf.ALL_ETF.sz159941;
check('手填溢价优先', pf.buyPremiumInfo({ date: '2026-08-01', buyPremium: 1.75 }, etf).value, 1.75);
check('手填零值有效', pf.buyPremiumInfo({ date: '2026-08-01', buyPremium: 0 }, etf).value, 0);
check('手填负溢价有效', pf.buyPremiumInfo({ date: '2026-08-01', buyPremium: -1.2 }, etf).value, -1.2);
check('未填匹配买入当日', pf.buyPremiumInfo({ date: '2026-08-01' }, etf).value, 3.25);
check('未填不回退最新日', pf.buyPremiumInfo({ date: '2026-08-19' }, etf).value, null);
check('无历史溢价的 ETF 不虚构数值', pf.buyPremiumInfo({ date: '2026-08-01' }, pf.ALL_ETF.sh513100).value, null);
check('未填日期无值', pf.buyPremiumInfo({ date: '' }, etf).value, null);
check('空输入非零', pf.optionalPremium('').value, null);
check('负数输入', pf.optionalPremium('-0.8').value, -0.8);
check('无穷值被拒', pf.optionalPremium('Infinity').valid, false);
const old = pf.loadHoldings([{ code: 'sz159941', lots: [{ date: '2026-08-01', shares: 10, price: 1.1 }] }]);
check('旧记录不写回溢价', Object.hasOwn(old[0].lots[0], 'buyPremium'), false);
check('旧记录展示自动溢价', pf.buyPremiumInfo(old[0].lots[0], etf).value, 3.25);
const loaded = pf.loadHoldings([{ code: 'sz159941', lots: [
  { date: '2026-08-01', shares: 1, price: 1.1, buyPremium: 0 },
  { date: '2026-08-20', shares: 1, price: 1.2, buyPremium: -2 },
  { date: '2026-09-03', shares: 1, price: 1.2, buyPremium: null }
] }]);
check('Gist/导入保留零值', loaded[0].lots[0].buyPremium, 0);
check('Gist/导入保留负值', loaded[0].lots[1].buyPremium, -2);
check('Gist/导入空值不存零', Object.hasOwn(loaded[0].lots[2], 'buyPremium'), false);
check('旧版单笔记录兼容', Object.hasOwn(pf.loadHoldings([{ code: 'sz159941', shares: 2, costPerShare: 1.2 }])[0].lots[0], 'buyPremium'), false);
contains('表单存在可选溢价输入', pf.holdingsPanelHTML(), 'id="pfBuyPremium"', true);
contains('表格存在溢价列', pf.holdingsPanelHTML(), '<th>买入溢价</th>', true);
pf.setHoldings(loaded); pf.expand('sz159941'); pf.renderHoldingsTable();
contains('编辑保留零值', (pf.setEditing({ code: 'sz159941', lot: loaded[0].lots[0] }), pf.renderHoldingsTable(), els.pfTbody.innerHTML), 'id="pfEditBuyPremium" class="pf-input-mini" value="0"', true);
pf.setEditing(null); pf.renderHoldingsTable();
contains('自动溢价明确标识', els.pfTbody.innerHTML, '日终历史溢价（自动）', true);

console.log('\n=== 7. 新增、编辑、缓存持久化 ===');
pf.setHoldings([]);
pf.bindHoldingsPanel();
els.pfCode.value = 'sz159941'; els.pfDate.value = '2026-08-01';
els.pfShares.value = '100'; els.pfCost.value = '1.2'; els.pfFee.value = '0.12';
els.pfBuyPremium.value = '-0.75'; els.btnAdd.click();
check('新增记录保存溢价', pf.getHoldings()[0].lots[0].buyPremium, -0.75);
check('缓存保存手填溢价', JSON.parse(localStorage.getItem('nsdketf_holdings_cache'))[0].lots[0].buyPremium, -0.75);
check('添加成功清空溢价输入', els.pfBuyPremium.value, '');
els.pfShares.value = '50'; els.pfCost.value = '1.3'; els.pfFee.value = '0.1';
els.btnAdd.click();
check('留空新增不写溢价', Object.hasOwn(pf.getHoldings()[0].lots[1], 'buyPremium'), false);
check('留空新增按日期显示', pf.buyPremiumInfo(pf.getHoldings()[0].lots[1], etf).value, 3.25);
const originalCost = pf.computeRow(pf.getHoldings()[0]).netCost;
pf.setEditing({ code: 'sz159941', lot: pf.getHoldings()[0].lots[0] });
document.getElementById('pfEditDate').value = '2026-08-01';
document.getElementById('pfEditShares').value = '100';
document.getElementById('pfEditPrice').value = '1.2';
document.getElementById('pfEditFee').value = '0.12';
document.getElementById('pfEditBuyPremium').value = '0';
const editSave = mkEl(); editSave.dataset.editSave = 'sz159941|0';
els.pfTbody.querySelectorAll = selector => selector === '[data-edit-save]' ? [editSave] : [];
pf.renderHoldingsTable(); editSave.click();
check('编辑保存零值溢价', pf.getHoldings()[0].lots[0].buyPremium, 0);
check('仅改溢价不改变实际成本', pf.computeRow(pf.getHoldings()[0]).netCost, originalCost);
pf.setEditing({ code: 'sz159941', lot: pf.getHoldings()[0].lots[0] });
document.getElementById('pfEditBuyPremium').value = ''; pf.renderHoldingsTable(); editSave.click();
check('编辑留空恢复自动且不写回', Object.hasOwn(pf.getHoldings()[0].lots[0], 'buyPremium'), false);
check('缓存编辑后不保存空溢价', Object.hasOwn(JSON.parse(localStorage.getItem('nsdketf_holdings_cache'))[0].lots[0], 'buyPremium'), false);
pf.setHoldings([]); pf.renderHoldingsTable();
contains('空状态横跨新增列', els.pfTbody.innerHTML, 'colspan="13"', true);

console.log('\n' + (fail === 0 ? 'ALL PASS' : 'FAILED') + '  ' + pass + ' passed, ' + fail + ' failed\n');
process.exit(fail === 0 ? 0 : 1);
