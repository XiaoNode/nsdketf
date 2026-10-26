/* 用极简 DOM stub 加载 portfolio.js, 校验持仓数据与表单行为 */
const { isDeepStrictEqual } = require('node:util');
// 以下汇率均为隔离测试夹具，不代表真实历史行情。
const fxRecords = {
  '2026-08-01': { date: '2026-08-01', value: 7.12, status: 'verified' },
  '2026-08-20': { date: '2026-08-20', value: 7.18, status: 'verified' }
};
let resolveFxReady;
global.window = { FX: {
  ready: () => new Promise(resolve => { resolveFxReady = resolve; }),
  lookup: date => fxRecords[date] || null,
  status: () => '当日无双源已核验汇率'
} };
const run = async () => {
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
  const ok = isDeepStrictEqual(actual, expected);
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

console.log('\n=== 5. 汇总行列对齐(14列) ===');
pf.setEditing(null);
pf.renderHoldingsTable();
const subtotalRow = els.pfTbody.innerHTML.split('</tr>')[0];
const cellCount = (subtotalRow.match(/<td/g) || []).length + (parseInt((subtotalRow.match(/colspan="(\d+)"/) || [0, 1])[1], 10) - 1);
check('汇总行占列数', cellCount, 14);

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
contains('空状态横跨新增列', els.pfTbody.innerHTML, 'colspan="14"', true);

console.log('\n=== 8. 汇率精确日期匹配与不可信导入 ===');
check('旧记录未迁移汇率字段', Object.hasOwn(old[0].lots[0], 'buyFx'), false);
check('旧记录动态匹配当日汇率', pf.buyFxInfo(old[0].lots[0]).value, 7.12);
check('缺失日期不使用前日或最新值', pf.buyFxInfo({ date: '2026-08-02' }).value, null);
check('空日期不自动匹配', pf.buyFxInfo({ date: '' }).value, null);
check('自动汇率标识核验口径', pf.buyFxInfo(old[0].lots[0]).label, '自动参考 · ECB-FRED同日跨时点参考核验');
for (const input of ['0', '-1', '100', 'Infinity', 'NaN', 'abc']) {
  check('拒绝手填汇率 ' + input, pf.optionalFx(input, '2026-08-01').valid, false);
}
check('手填必须有日期', pf.optionalFx('7.2', '').valid, false);
check('清空恢复自动匹配', pf.optionalFx('', '2026-08-01').value.value, 7.12);
check('无数据留空可保存且不伪造汇率', pf.optionalFx('', '2026-08-02'), { valid: true, value: null });
const forged = { value: 9.9, date: '2026-08-01', source: 'ecb_fred', status: 'verified' };
check('导入伪造核验值被真实数据替换', pf.normalizeBuyFx(forged, forged.date).value, 7.12);
const stale = { ...forged, date: '2026-08-02' };
check('无来源数据时导入verified降级', pf.normalizeBuyFx(stale, stale.date).status, 'unverified');
check('已存参考无数据可保留', pf.buyFxInfo({ date: stale.date, buyFx: stale }).value, 9.9);
check('已存参考不能显示已核验', pf.buyFxInfo({ date: stale.date, buyFx: stale }).status, 'unverified');
const manual = { value: 7.25, date: '2026-08-01', source: 'manual', status: 'verified' };
check('手填导入不能宣称核验', pf.normalizeBuyFx(manual, manual.date).status, 'manual');
check('手填优先于当日自动参考', pf.buyFxInfo({ date: manual.date, buyFx: manual }).value, 7.25);
for (const invalid of [null, [], '7.1', { ...manual, value: Infinity }, { ...manual, value: NaN }, { ...manual, value: '7.2' }, { ...manual, value: 0 }, { ...manual, value: 100 }, { ...manual, date: '2026-08-20' }, { ...manual, source: 'unknown' }]) {
  check('不合法导入汇率被丢弃 ' + JSON.stringify(invalid), pf.normalizeBuyFx(invalid, '2026-08-01'), null);
}
const legacy = pf.loadHoldings([{ code: 'sz159941', shares: 2, costPerShare: 1.2 }])[0].lots[0];
check('旧版无日期记录仍可用且不伪造汇率', pf.buyFxInfo(legacy).value, null);

console.log('\n=== 9. 新增/编辑汇率与人民币盈亏不变 ===');
els.pfTbody.querySelectorAll = () => [];
pf.setEditing(null); pf.setHoldings([]);
els.pfCode.value = 'sz159941'; els.pfDate.value = '2026-08-01';
const addLot = value => {
  els.pfShares.value = '100'; els.pfCost.value = '1.2'; els.pfFee.value = '0.12';
  els.pfBuyFx.value = value; els.btnAdd.click();
};
addLot('0'); check('非法手填阻止新增', pf.getHoldings().length, 0);
els.pfBuyFx.validity = { badInput: true };
addLot(''); check('浏览器数字输入错误不能作为留空保存', pf.getHoldings().length, 0);
els.pfBuyFx.validity = { badInput: false };
addLot('7.25');
check('新增手填记录元数据', pf.getHoldings()[0].lots[0].buyFx, { ...manual, status: 'manual' });
check('新增成功清空汇率输入', els.pfBuyFx.value, '');
addLot('');
check('新增留空保存自动来源', pf.getHoldings()[0].lots[1].buyFx, { value: 7.12, date: '2026-08-01', source: 'ecb_fred', status: 'verified' });
const beforeFxEdit = pf.computeRow(pf.getHoldings()[0]);
const saveFxEdit = (value, date = '2026-08-01') => {
  const lot = pf.getHoldings()[0].lots[0];
  pf.setEditing({ code: 'sz159941', lot });
  const button = mkEl(); button.dataset.editSave = 'sz159941|0';
  els.pfTbody.querySelectorAll = selector => selector === '[data-edit-save]' ? [button] : [];
  pf.renderHoldingsTable();
  els.pfEditDate.value = date; els.pfEditShares.value = '100'; els.pfEditPrice.value = '1.2';
  els.pfEditFee.value = '0.12'; els.pfEditBuyPremium.value = ''; els.pfEditBuyFx.value = value;
  button.click();
};
saveFxEdit('-7'); check('非法编辑保留原手填值', pf.getHoldings()[0].lots[0].buyFx.value, 7.25);
saveFxEdit('7.3'); check('编辑保存新手填值', pf.getHoldings()[0].lots[0].buyFx.value, 7.3);
saveFxEdit(''); check('编辑清空恢复自动', pf.getHoldings()[0].lots[0].buyFx.source, 'ecb_fred');
saveFxEdit('', '2026-08-20'); check('改日期后重新精确匹配', pf.getHoldings()[0].lots[0].buyFx.value, 7.18);
saveFxEdit('', '2026-08-02'); check('缺失日清空移除历史自动记录', Object.hasOwn(pf.getHoldings()[0].lots[0], 'buyFx'), false);
const afterFxEdit = pf.computeRow(pf.getHoldings()[0]);
for (const key of ['cost', 'totalFee', 'netCost', 'marketValue', 'pnl', 'pnlPct']) {
  check('汇率不改变人民币 ' + key, afterFxEdit[key], beforeFxEdit[key]);
}
check('单笔盈亏也不乘汇率', pf.lotValues({ date: manual.date, shares: 100, price: 1.2, fee: 0.12, buyFx: manual }, 1.5).pnl, pf.lotValues({ shares: 100, price: 1.2, fee: 0.12 }, 1.5).pnl);

console.log('\n=== 10. 真实导出/导入事件及异步完成保留表单 ===');
saveFxEdit('7.3', '2026-08-01');
let exportedBlob;
const originalCreate = URL.createObjectURL;
const originalRevoke = URL.revokeObjectURL;
URL.createObjectURL = blob => { exportedBlob = blob; return 'blob:portfolio-test'; };
URL.revokeObjectURL = () => {};
document.createElement = () => mkEl();
els.btnExport.click();
const exportedText = await exportedBlob.text();
const exported = JSON.parse(exportedText);
check('实际导出包含手填汇率', exported.holdings[0].lots[0].buyFx.value, 7.3);
check('实际导出包含自动来源', exported.holdings[0].lots[1].buyFx.source, 'ecb_fred');
pf.setHoldings([]);
global.FileReader = class { readAsText(file) { this.result = file.content; this.onload(); } };
els.pfImportFile.listeners.change[0]({ target: { files: [{ content: exportedText }] } });
check('实际导入恢复手填记录', pf.getHoldings()[0].lots[0].buyFx, exported.holdings[0].lots[0].buyFx);
check('实际导入重新核验自动记录', pf.getHoldings()[0].lots[1].buyFx, exported.holdings[0].lots[1].buyFx);
check('缓存/Gist共用序列化完整保留buyFx', JSON.parse(localStorage.getItem('nsdketf_holdings_cache'))[0].lots[0].buyFx, exported.holdings[0].lots[0].buyFx);
URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke;

els.pfDate.value = '2026-08-20'; els.pfBuyFx.value = '';
els.pfDate.listeners.change[0]();
check('日期改变立即更新预览值', els.pfFxPreview.innerHTML.startsWith('7.1800'), true);
els.pfDate.value = '2026-08-02'; els.pfDate.listeners.input[0]();
check('缺失日期预览不前向填充', els.pfFxPreview.innerHTML.startsWith('—'), true);
els.pfBuyFx.value = '7.35'; els.pfBuyFx.listeners.input[0]();
check('手填预览明确未双源验证', els.pfFxPreview.innerHTML.includes('手填，未双源验证'), true);
els.pfBuyFx.value = ''; els.pfShares.value = '777'; els.pfCost.value = '1.111';
els.pfEditDate.value = '2026-08-02'; els.pfEditBuyFx.value = '7.4'; els.pfEditShares.value = '888';
const cell = mkEl(); cell.dataset.fxLot = 'sz159941|1';
document.querySelectorAll = selector => selector === '[data-fx-lot]' ? [cell] : [];
const tableBeforeReady = els.pfTbody.innerHTML;
const areaBeforeReady = document.getElementById('chartArea').innerHTML;
fxRecords['2026-08-02'] = { date: '2026-08-02', value: 7.22, status: 'verified' };
resolveFxReady(); await Promise.resolve(); await Promise.resolve();
check('加载完成更新新增自动预览', els.pfFxPreview.innerHTML.startsWith('7.2200'), true);
check('加载完成更新已展示汇率单元格', cell.innerHTML.startsWith('7.1200'), true);
check('加载完成保持新增份额草稿', els.pfShares.value, '777');
check('加载完成保持新增价格草稿', els.pfCost.value, '1.111');
check('加载完成保持编辑汇率草稿', els.pfEditBuyFx.value, '7.4');
check('加载完成保持编辑份额草稿', els.pfEditShares.value, '888');
check('加载完成不重绘表格', els.pfTbody.innerHTML === tableBeforeReady, true);
check('加载完成不重绘整个面板', els.chartArea.innerHTML, areaBeforeReady);

console.log('\n=== 11. 离线编辑保留参考与 Gist 往返 ===');
pf.setHoldings([{ code: 'sz159941', lots: [{ date: '2026-08-03', shares: 100, price: 1.2, fee: 0.12, buyFx: { value: 7.19, date: '2026-08-03', source: 'ecb_fred', status: 'verified' } }] }]);
saveFxEdit('', '2026-08-03');
check('离线同日编辑保留已存参考', pf.getHoldings()[0].lots[0].buyFx.value, 7.19);
check('离线同日编辑降级为未复核', pf.getHoldings()[0].lots[0].buyFx.status, 'unverified');
let gistSaved;
global.fetch = async (url, options) => {
  check('Gist 请求使用测试 Token', options.headers.Authorization, 'Bearer test-token');
  if (url === 'https://api.github.com/user') return { ok: true, json: async () => ({ login: 'test-user' }) };
  if (url !== 'https://api.github.com/gists/test-gist') throw new Error('禁止测试访问未预期地址');
  if (options.method === 'PATCH') {
    gistSaved = JSON.parse(JSON.parse(options.body).files['nsdketf-holdings.json'].content);
    return { ok: true };
  }
  return { ok: true, json: async () => ({ files: { 'nsdketf-holdings.json': { content: exportedText } } }) };
};
els.pfTbody.querySelectorAll = () => [];
window.renderHoldings();
localStorage.setItem('nsdketf_gist_id', 'test-gist');
els.ghToken.value = 'test-token';
await els.btnConnect.listeners.click[0]();
check('Gist 读取保留手填汇率', pf.getHoldings()[0].lots[0].buyFx.value, 7.3);
check('Gist 读取重核自动汇率', pf.getHoldings()[0].lots[1].buyFx.status, 'verified');
els.pfDate.value = '2026-08-20'; els.pfBuyFx.value = '7.4';
els.pfShares.value = '100'; els.pfCost.value = '1.2'; els.pfFee.value = '0.12';
await els.btnAdd.listeners.click[els.btnAdd.listeners.click.length - 1]();
await Promise.resolve(); await Promise.resolve();
check('Gist 保存完整手填元数据', gistSaved.holdings[0].lots[2].buyFx, { value: 7.4, date: '2026-08-20', source: 'manual', status: 'manual' });

console.log('\n' + (fail === 0 ? 'ALL PASS' : 'FAILED') + '  ' + pass + ' passed, ' + fail + ' failed\n');
process.exitCode = fail === 0 ? 0 : 1;
};
run().catch(error => { console.error(error); process.exitCode = 1; });
