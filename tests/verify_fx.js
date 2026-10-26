// Offline deterministic FX contract and async rendering tests.
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../fx.js'), 'utf8');
let checks = 0;
function test(name, fn) { fn(); checks++; console.log('[OK] ' + name); }
const sample = { pair: 'USD/CNY', records: [
  {date:'2020-01-02',value:7,secondary_value:7.001,status:'verified'},
  {date:'2020-01-03',value:7.1,secondary_value:7,status:'conflict'},
  {date:'2020-01-06',value:7.02,secondary_value:null,status:'pending'}
] };
function context(payload) {
  const elements = new Map();
  const element = () => ({innerHTML:'',parentNode:{innerHTML:''},querySelectorAll:()=>[],getContext:()=>({})});
  elements.set('chartArea', element()); elements.set('fxPanel',element());
  const charts=[];
  const c = { console, Date, fetch:async()=>({ok:true,json:async()=>payload}),
    document:{ getElementById:id => { if(!elements.has(id)) elements.set(id,element()); return elements.get(id); } },
    Chart:function(ctx, config){this.config=config;this.destroy=()=>{this.destroyed=true;};charts.push(this);} };
  c.window=c; vm.createContext(c); vm.runInContext(source,c); return {c, elements,charts};
}
(async()=>{
  const {c,elements,charts}=context(sample), FX=c.FX;
  test('reject reversed units/pair',()=>assert.throws(()=>FX.validate({pair:'CNY/USD',records:[]})));
  test('reject duplicate dates',()=>assert.throws(()=>FX.validate({pair:'USD/CNY',records:[sample.records[0],sample.records[0]]})));
  test('reject future / invalid calendar date',()=>{
    for(const date of ['2999-01-01','2020-02-30']) assert.throws(()=>FX.validate({pair:'USD/CNY',records:[{...sample.records[0],date}]}));
  });
  test('reject false verified, nulls, negative and nonfinite',()=>{
    for(const value of [null,0,-7,Infinity,NaN,8]) assert.throws(()=>FX.validate({pair:'USD/CNY',records:[{...sample.records[0],value}]}));
  });
  test('recompute deviation rather than trust payload flag',()=>{
    assert.throws(()=>FX.validate({pair:'USD/CNY',records:[{...sample.records[1],status:'verified',deviation_pct:0}]}));
  });
  test('calendar year range respects leap-day boundary',()=>{
    const r=FX.rangeRecords([{date:'2019-02-27'},{date:'2019-02-28'},{date:'2020-03-01'}],1,'2020-02-29');
    assert.strictEqual(r.length,1); assert.strictEqual(r[0].date,'2019-02-28');
  });
  await FX.ready();
  test('exact verified date returns copy',()=>{const r=FX.lookup('2020-01-02');assert.strictEqual(r.value,7);r.value=8;assert.strictEqual(FX.lookup('2020-01-02').value,7);});
  test('weekend / absent / pending / conflict never auto-fill',()=>{for(const date of ['2020-01-03','2020-01-04','2020-01-06','2019-01-01']) assert.strictEqual(FX.lookup(date),null);});
  await FX.render();
  test('chart creates all status series',()=>assert.strictEqual(charts[0].config.data.datasets.length,4));
  FX.dispose();
  test('dispose destroys chart instance',()=>assert.strictEqual(charts[0].destroyed,true));
  const bad=context({}), badFX=bad.c.FX;
  await badFX.ready();
  test('invalid downloaded data fail closed without rejection',()=>{assert.strictEqual(badFX.lookup('2020-01-02'),null);assert.ok(badFX.status('2020-01-02').includes('失败'));});
  await badFX.render();
  test('missing data renders explicit retry state',()=>assert.ok(bad.elements.get('fxPanel').innerHTML.includes('重新加载')));
  const noChart=context(sample); delete noChart.c.Chart; await noChart.c.FX.render();
  test('CDN failure still renders real SVG history',()=>assert.ok(noChart.elements.get('fxChart').parentNode.innerHTML.includes('<svg')));
  console.log(checks+' FX assertions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
