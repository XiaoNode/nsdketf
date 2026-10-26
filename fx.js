/* USD/CNY reference history. No portfolio data is sent to the data providers. */
(function (root) {
  'use strict';
  const LIMIT = 0.5; // Same-date, different-fixing-time plausibility check, percent.
  let data = null, loading = null, error = '', chart = null, renderId = 0;
  let years = 10;
  const dayOK = s => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) &&
    Number.isFinite(Date.parse(s)) && new Date(s).toISOString().slice(0, 10) === s;
  const rateOK = n => typeof n === 'number' && Number.isFinite(n) && n > 0 && n < 100;
  const today = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  function validate(payload) {
    if (!payload || payload.pair !== 'USD/CNY' || !Array.isArray(payload.records)) throw new Error('汇率文件格式错误');
    const seen = new Set();
    const records = payload.records.map(r => {
      if (!r || !dayOK(r.date) || r.date > today() || seen.has(r.date)) throw new Error('汇率日期无效或重复');
      seen.add(r.date);
      if (r.value != null && !rateOK(r.value)) throw new Error('汇率数值无效');
      if (r.secondary_value != null && !rateOK(r.secondary_value)) throw new Error('复核汇率无效');
      if (!['verified', 'pending', 'conflict'].includes(r.status)) throw new Error('汇率校验状态无效');
      const deviation = rateOK(r.value) && rateOK(r.secondary_value)
        ? Math.abs(r.value / r.secondary_value - 1) * 100 : null;
      if (r.status === 'verified' && (deviation === null || deviation > LIMIT + 0.00001)) throw new Error('双源汇率未通过复算');
      return Object.assign({}, r, { deviation_pct: deviation });
    }).sort((a, b) => a.date.localeCompare(b.date));
    return Object.assign({}, payload, { records });
  }
  function ready() {
    if (loading) return loading;
    loading = fetch('data/fx_usdcny.json', { cache: 'no-cache' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(p => { data = validate(p); error = ''; return data; })
      .catch(() => { error = '汇率数据加载失败或校验不通过，请稍后刷新重试'; return null; });
    return loading;
  }
  function record(date) { return data && data.records.find(r => r.date === date) || null; }
  function lookup(date) {
    const r = record(date);
    return r && r.status === 'verified' ? Object.assign({}, r) : null;
  }
  function status(date) {
    if (error) return error;
    if (!data) return '汇率数据加载中';
    const r = record(date);
    if (!r) return '无买入当日汇率，不使用相邻日期替代';
    if (r.status === 'verified') return r.cached ? '历史已核验缓存，本轮未完成复核' : '同日双源参考核验通过（非成交汇率）';
    return r.status === 'conflict' ? '双源偏差超限，已隔离' : '单源待核验，暂不自动填入';
  }
  function rangeRecords(records, count, end) {
    const d = new Date((end || today()) + 'T00:00:00Z');
    const month = d.getUTCMonth();
    d.setUTCFullYear(d.getUTCFullYear() - count);
    if (d.getUTCMonth() !== month) d.setUTCDate(0);
    const start = d.toISOString().slice(0, 10);
    return records.filter(r => r.date >= start && r.date <= (end || today()));
  }
  const esc = x => String(x == null ? '' : x).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = x => rateOK(x) ? x.toFixed(4) : '—';
  function svgChart(rows) {
    const vals = rows.flatMap(r => [r.value, r.secondary_value]).filter(rateOK);
    if (!vals.length) return '<p style="color:#334155">本窗口无汇率数据</p>';
    const lo = Math.min(...vals) - 0.03, hi = Math.max(...vals) + 0.03;
    const x = i => 55 + i / Math.max(1, rows.length - 1) * 890;
    const y = v => 270 - (v - lo) / (hi - lo) * 240;
    let svg = '<svg viewBox="0 0 980 320" role="img" aria-label="美元兑人民币历史参考汇率" style="width:100%;height:100%;background:white">';
    for (let i = 0; i <= 4; i++) {
      const v = lo + (hi - lo) * i / 4, yy = y(v);
      svg += `<line x1="55" x2="945" y1="${yy}" y2="${yy}" stroke="#e2e8f0"/><text x="5" y="${yy+4}" fill="#475569" font-size="12">${v.toFixed(2)}</text>`;
    }
    const series = [{color:'#2563eb',get:r=>r.status==='verified'?r.value:null}, {color:'#64748b',get:r=>r.secondary_value}];
    series.forEach(s => {
      let segment = [];
      const flush = () => { if (segment.length) svg += `<polyline points="${segment.join(' ')}" fill="none" stroke="${s.color}" stroke-width="1.5"/>`; segment=[]; };
      rows.forEach((r,i) => { const v=s.get(r); if(rateOK(v)) segment.push(x(i).toFixed(2)+','+y(v).toFixed(2)); else flush(); });
      flush();
    });
    rows.forEach((r,i) => {
      if (!rateOK(r.value)) return;
      const color=r.status==='verified'?'#2563eb':r.status==='conflict'?'#dc2626':'#b45309';
      svg += `<circle cx="${x(i)}" cy="${y(r.value)}" r="${r.status==='verified'?1:2.5}" fill="${color}"><title>${esc(r.date)} ECB ${number(r.value)} / FRED ${number(r.secondary_value)} · ${esc(status(r.date))}</title></circle>`;
    });
    [0, Math.floor((rows.length-1)/2), rows.length-1].forEach((i,j) => { if(rows[i]) svg+=`<text x="${x(i)}" y="295" text-anchor="${j===0?'start':j===2?'end':'middle'}" fill="#475569" font-size="12">${esc(rows[i].date)}</text>`; });
    return svg + '<text x="55" y="315" fill="#475569" font-size="12">蓝：ECB 已核验 / 灰：FRED / 棕：待核验 / 红：异常（悬停查看）</text></svg>';
  }
  function dispose() { renderId++; if (chart) { chart.destroy(); chart = null; } }
  async function render() {
    dispose();
    const id = renderId;
    const area = document.getElementById('chartArea');
    if (!area) return;
    area.innerHTML = '<div class="chart-card" id="fxPanel"><h3>美元兑人民币 · 近十年参考汇率</h3><p>正在读取双源校验数据…</p></div>';
    await ready();
    if (id !== renderId || !document.getElementById('fxPanel')) return;
    const panel = document.getElementById('fxPanel');
    if (!data || !data.records.length) {
      panel.innerHTML = '<h3>美元兑人民币参考汇率</h3><p>' + esc(error || '暂无真实汇率数据，等待每日任务成功抓取') + '</p><button class="pf-btn-ghost" id="fxRetry">重新加载</button>';
      document.getElementById('fxRetry').onclick = () => { loading = null; render(); };
      return;
    }
    const rows = rangeRecords(data.records, years);
    const verified = data.records.filter(r => r.status === 'verified');
    const latest = verified[verified.length - 1];
    const primary = data.records.filter(r => rateOK(r.value));
    const last = primary[primary.length - 1];
    const counts = rows.reduce((out, r) => { out[r.status]++; return out; }, { verified: 0, pending: 0, conflict: 0 });
    const age = latest ? Math.floor((Date.parse(today()) - Date.parse(latest.date)) / 86400000) : null;
    panel.innerHTML = `<h3>美元兑人民币 · USD/CNY</h3>
      <div class="chart-desc">1 美元兑换多少人民币；数值上升 = 美元升值 / 人民币贬值。本图是日参考价，不是盘中现价、央行中间价或基金实际估值汇率。</div>
      <div style="display:flex;gap:24px;flex-wrap:wrap;margin:16px 0">
        <div>最近双源通过 <strong>${number(latest && latest.value)}</strong><br><small>${esc(latest ? latest.date : '暂无')} · ECB 参考交叉汇率</small></div>
        <div>主源最新 <strong>${number(last && last.value)}</strong><br><small>${esc(last ? last.date : '暂无')} · ${esc(last ? status(last.date) : '暂无')}</small></div>
        <div>数据采集时间<br><small>${esc(data.updated_at || '未记录')}</small></div>
      </div>
      <div class="alert-warn" style="display:block">${age != null ? '最近已核验数据距今 ' + age + ' 个自然日。' : ''}FRED 发布有滞后；每天检查更新不代表当天必有双源数据。${data.fetch_status && data.fetch_status !== 'ok' ? '<strong>本轮抓取未完整成功，部分数据来自历史缓存。</strong>' : ''}${age > 10 ? '<strong>数据较旧，请勿当作当前行情。</strong>' : ''}${data.coverage && !data.coverage.history_complete ? '<strong>十年历史覆盖尚不完整。</strong>' : ''}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin:14px 0">${[1,3,5,10].map(n => `<button class="pf-btn-ghost" data-fx-years="${n}" ${years === n ? 'disabled' : ''}>近${n}年</button>`).join('')}<button class="pf-btn-ghost" id="fxReset">重置缩放</button></div>
      <div style="height:360px;background:#fff;border-radius:8px;padding:12px"><canvas id="fxChart"></canvas></div>
      <p class="chart-desc">本窗口 ${esc(rows.length ? rows[0].date + ' 至 ' + rows[rows.length-1].date : '无数据')}；通过 ${counts.verified} / 待核验 ${counts.pending} / 偏差异常 ${counts.conflict}。缺失日期不填补；异常点不纳入已核验曲线。</p>
      <details style="margin-top:12px"><summary>数据来源、校验方法与边界</summary>
        <p>主源：<a href="https://www.ecb.europa.eu/stats/eurofxref/index.en.html" target="_blank" rel="noopener noreferrer">欧洲央行 ECB</a>，EUR/CNY ÷ EUR/USD，折算为每美元人民币。副源：<a href="https://fred.stlouisfed.org/series/DEXCHUS" target="_blank" rel="noopener noreferrer">美联储 H.10 / FRED DEXCHUS</a>，纽约正午参考汇率。</p>
        <p>按来源标注的同一日期核验，绝对偏差 |ECB / FRED − 1| ≤ 0.5% 才通过。两者采样时点不同，这是独立来源的合理性复核，不是同一时点精确对账；超限也可能是真实日内波动。保留两源原值，不平移日期凑匹配，不把 CNH、中间价混入。</p>
        <p>QDII 人民币收益已包含汇率影响；买入记录的参考汇率不重复计入盈亏。不同基金的估值汇率和对冲安排不同，不能直接据此还原基金净值。</p>
      </details>
      <details style="margin-top:12px"><summary>查看最近 10 条双源明细</summary><div style="overflow-x:auto"><table class="premium-table"><thead><tr><th>日期</th><th>ECB 折算</th><th>FRED</th><th>偏差</th><th>状态</th></tr></thead><tbody>${data.records.slice(-10).reverse().map(r => `<tr><td>${esc(r.date)}</td><td>${number(r.value)}</td><td>${number(r.secondary_value)}</td><td>${r.deviation_pct == null ? '—' : r.deviation_pct.toFixed(3) + '%'}</td><td>${esc(status(r.date))}</td></tr>`).join('')}</tbody></table></div></details>`;
    panel.querySelectorAll('[data-fx-years]').forEach(b => { b.onclick = () => { years = Number(b.dataset.fxYears); render(); }; });
    const canvas = document.getElementById('fxChart');
    if (typeof root.Chart !== 'function') {
      canvas.parentNode.innerHTML = svgChart(rows);
      document.getElementById('fxReset').disabled = true;
      document.getElementById('fxReset').textContent = '基础图表模式（无需外部库）';
      return;
    }
    chart = new root.Chart(canvas.getContext('2d'), {
      type: 'line',
      data: { labels: rows.map(r => r.date), datasets: [
        { label: 'ECB · 双源通过', data: rows.map(r => r.status === 'verified' ? r.value : null), borderColor: '#2563eb', pointRadius: 0, borderWidth: 1.5, spanGaps: false },
        { label: 'FRED · 独立参考', data: rows.map(r => r.secondary_value), borderColor: '#64748b', pointRadius: 0, borderWidth: 1, borderDash: [4,4], spanGaps: false },
        { label: 'ECB · 待核验', data: rows.map(r => r.status === 'pending' ? r.value : null), borderColor: '#b45309', backgroundColor: '#b45309', pointRadius: 2, showLine: false },
        { label: 'ECB · 偏差异常', data: rows.map(r => r.status === 'conflict' ? r.value : null), borderColor: '#dc2626', backgroundColor: '#dc2626', pointRadius: 3, showLine: false }
      ] },
      options: { responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: '#334155', boxWidth: 12 } },
          tooltip: { callbacks: { label: c => c.dataset.label + ': ' + Number(c.parsed.y).toFixed(4) } },
          zoom: { zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' }, pan: { enabled: true, mode: 'x' } } },
        scales: { x: { ticks: { color: '#475569', maxTicksLimit: 10, maxRotation: 0 }, grid: { color: '#f1f5f9' } },
          y: { title: { display: true, text: '人民币 / 1 美元', color: '#334155' }, ticks: { color: '#475569' }, grid: { color: '#e2e8f0' } } }
      }
    });
    document.getElementById('fxReset').onclick = () => { if (chart && chart.resetZoom) chart.resetZoom(); };
  }
  root.FX = { ready, lookup, status, render, dispose, validate, rangeRecords };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.FX;
})(typeof window !== 'undefined' ? window : globalThis);
