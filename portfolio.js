/* ============================================================
 * 我的持仓 (Portfolio) —— 跨市场聚合 + GitHub Gist 跨设备同步
 * 依赖全局: ETF_DATA / SP500_DATA / US50_DATA / DJIA_DATA (index.html)
 * 存储: 已连接 GitHub -> 私有 Gist (跨设备); 未连接 -> localStorage (本浏览器)
 * 清算: 每次渲染读取各 ETF 最新价 (数据文件每日 21:30 自动更新 -> 即"每日清算")
 * ============================================================ */
(function () {
  'use strict';

  /* ---------- 跨市场合并查找表 ---------- */
  const ALL_ETF = Object.assign({}, ETF_DATA, SP500_DATA, US50_DATA, DJIA_DATA);
  const MARKET_LABEL = { ndx: '纳斯达克100', sp500: '标普500', us50: '美国50', djia: '道琼斯' };
  const MARKET_ORDER = [['ndx', '纳斯达克100'], ['sp500', '标普500'], ['us50', '美国50'], ['djia', '道琼斯']];
  const MARKET_OF = {};
  MARKET_ORDER.forEach(([m]) => {
    const src = m === 'ndx' ? ETF_DATA : m === 'sp500' ? SP500_DATA : m === 'us50' ? US50_DATA : DJIA_DATA;
    Object.keys(src).forEach(c => { MARKET_OF[c] = m; });
  });

  /* ---------- GitHub Gist 存储适配器 ---------- */
  const GistStore = {
    TOKEN_KEY: 'nsdketf_gh_token',
    GIST_KEY: 'nsdketf_gist_id',
    USER_KEY: 'nsdketf_gh_user',
    CACHE_KEY: 'nsdketf_holdings_cache',
    FILENAME: 'nsdketf-holdings.json',
    rawToken() { return localStorage.getItem(this.TOKEN_KEY) || ''; },
    token() { return this.rawToken().trim(); },
    gistId() { return localStorage.getItem(this.GIST_KEY); },
    user() { return localStorage.getItem(this.USER_KEY); },
    headers() {
      return {
        'Authorization': 'Bearer ' + this.token(),
        'Content-Type': 'application/json',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
      };
    },
    async verifyToken() {
      const r = await fetch('https://api.github.com/user', { headers: this.headers() });
      if (!r.ok) {
        let msg = 'Token 校验失败 (HTTP ' + r.status + ')';
        try {
          const body = await r.json();
          if (body && body.message) msg += ': ' + body.message;
        } catch (_) { /* ignore */ }
        throw new Error(msg);
      }
      return r.json();
    },
    async findExistingGist() {
      // 跨设备：先在本账号下查找已存在的持仓 Gist 并复用，避免每台浏览器各建一个
      let url = 'https://api.github.com/gists?per_page=100';
      while (url) {
        const r = await fetch(url, { headers: this.headers() });
        if (!r.ok) return null;
        const list = await r.json();
        if (!Array.isArray(list) || list.length === 0) break;
        for (const g of list) {
          if (g.files && g.files[this.FILENAME]) return g.id;
        }
        const link = (typeof r.headers && r.headers.get) ? r.headers.get('link') : null;
        url = null;
        if (link) {
          const m = link.match(/<([^>]+)>;\s*rel="next"/);
          if (m) url = m[1];
        }
      }
      return null;
    },
    async ensureGist() {
      let id = this.gistId();
      if (id) return id;
      // 跨设备：先查本账号下是否已有持仓 Gist，复用之
      id = await this.findExistingGist();
      if (id) { localStorage.setItem(this.GIST_KEY, id); return id; }
      const r = await fetch('https://api.github.com/gists', {
        method: 'POST', headers: this.headers(),
        body: JSON.stringify({ description: 'nsdketf 持仓数据 (自动生成)', public: false, files: { [this.FILENAME]: { content: JSON.stringify({ version: 1, holdings: [] }, null, 2) } } })
      });
      if (!r.ok) throw new Error('创建 Gist 失败 (HTTP ' + r.status + ')');
      const data = await r.json();
      localStorage.setItem(this.GIST_KEY, data.id);
      return data.id;
    },
    async load() {
      const id = this.gistId();
      if (!id) return [];
      const r = await fetch('https://api.github.com/gists/' + id, { headers: this.headers() });
      if (!r.ok) throw new Error('读取 Gist 失败 (HTTP ' + r.status + ')');
      const data = await r.json();
      const content = data.files && data.files[this.FILENAME] && data.files[this.FILENAME].content;
      if (!content) return [];
      const parsed = JSON.parse(content);
      return Array.isArray(parsed.holdings) ? parsed.holdings : [];
    },
    async save(holdings) {
      const id = await this.ensureGist();
      const r = await fetch('https://api.github.com/gists/' + id, {
        method: 'PATCH', headers: this.headers(),
        body: JSON.stringify({ files: { [this.FILENAME]: { content: JSON.stringify({ version: 1, updatedAt: new Date().toISOString(), holdings }, null, 2) } } })
      });
      if (!r.ok) throw new Error('保存 Gist 失败 (HTTP ' + r.status + ')');
    }
  };

  /* ---------- 持仓状态 ---------- */
  let HOLDINGS = [];      // [{code, shares, costPerShare}]
  let GH_CONNECTED = false;

  async function initHoldings() {
    // 1) 本地缓存优先 (离线可用)
    try {
      const c = localStorage.getItem(GistStore.CACHE_KEY);
      if (c) HOLDINGS = loadHoldings(JSON.parse(c));
    } catch (e) { /* ignore */ }
    // 2) 若已连接, 以 Gist 为准覆盖
    if (GistStore.token() && GistStore.gistId()) {
      try {
        const h = await GistStore.load();
        HOLDINGS = loadHoldings(h);
        localStorage.setItem(GistStore.CACHE_KEY, JSON.stringify(HOLDINGS));
        GH_CONNECTED = true;
      } catch (e) {
        GH_CONNECTED = false; // 保留本地缓存
      }
    }
  }

  async function persistHoldings() {
    localStorage.setItem(GistStore.CACHE_KEY, JSON.stringify(HOLDINGS));
    if (GH_CONNECTED && GistStore.token() && GistStore.gistId()) {
      try {
        await GistStore.save(HOLDINGS);
        toast('已同步到 GitHub Gist');
      } catch (e) {
        toast('Gist 同步失败: ' + e.message + '（已保存到本浏览器）', true);
      }
    }
  }

  /* ---------- 计算 ---------- */
  function computeRow(h) {
    const etf = ALL_ETF[h.code];
    if (!etf) return null;
    const price = (etf.price && etf.price.length) ? etf.price[etf.price.length - 1].value : null;
    const lots = (h.lots || []).filter(l => Number(l.shares) > 0);
    let shares = 0, cost = 0;
    lots.forEach(l => { const s = Number(l.shares), p = Number(l.price); shares += s; cost += s * p; });
    const avgCost = shares > 0 ? cost / shares : null;
    const marketValue = price != null ? shares * price : null;
    const pnl = marketValue != null ? marketValue - cost : null;
    const pnlPct = (cost > 0 && pnl != null) ? (pnl / cost * 100) : null;
    return { etf, price, shares, avgCost, cost, marketValue, pnl, pnlPct, lots };
  }

  function lotValues(lot, price) {
    const s = Number(lot.shares), p = Number(lot.price);
    const amount = s * p;
    const mv = price != null ? s * price : null;
    const pnl = mv != null ? mv - amount : null;
    const pct = (amount > 0 && pnl != null) ? (pnl / amount * 100) : null;
    return { s, p, amount, mv, pnl, pct, date: lot.date };
  }

  function normalizeHolding(h) {
    if (!h || !h.code || !ALL_ETF[h.code]) return null;
    if (Array.isArray(h.lots)) {
      h.lots = h.lots
        .filter(l => l && Number(l.shares) > 0)
        .map(l => ({ date: (l.date || '').toString(), shares: Number(l.shares), price: Number(l.price) }));
    } else if (h.shares != null && h.costPerShare != null) {
      // 兼容旧版单笔均价格式
      h.lots = [{ date: '', shares: Number(h.shares), price: Number(h.costPerShare) }];
    } else {
      h.lots = [];
    }
    return h;
  }

  function loadHoldings(arr) {
    if (!Array.isArray(arr)) return [];
    return arr.map(normalizeHolding).filter(Boolean);
  }

  function computeSummary() {
    let totalMV = 0, totalCost = 0, hasPrice = false;
    HOLDINGS.forEach(h => {
      const r = computeRow(h);
      if (r && r.marketValue != null) { totalMV += r.marketValue; totalCost += r.cost; hasPrice = true; }
    });
    const totalPnl = totalMV - totalCost;
    const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost * 100) : null;
    return { totalMV, totalCost, totalPnl, totalPnlPct, hasPrice };
  }

  /* ---------- 格式化 ---------- */
  const fmtMoney = v => v == null ? '—' : '¥' + v.toFixed(2);
  const fmtPrice = v => v == null ? '—' : '¥' + v.toFixed(3);
  const fmtPct = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  function pnlClass(v) { return v == null ? 'zero-val' : v >= 0 ? 'pos-val' : 'neg-val'; }

  function toast(msg, isErr) {
    const el = document.getElementById('pfToast');
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
    el.style.borderColor = isErr ? 'rgba(239,68,68,.4)' : 'rgba(34,197,94,.4)';
    el.style.color = isErr ? '#f87171' : '#4ade80';
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.style.display = 'none'; }, 3200);
  }

  /* ---------- 顶层渲染分发 ---------- */
  function renderHoldings() {
    const area = document.getElementById('chartArea');
    if (!area) return;
    if (!GH_CONNECTED && !GistStore.token()) {
      area.innerHTML = connectCardHTML();
      bindConnect();
    } else {
      area.innerHTML = holdingsPanelHTML();
      bindHoldingsPanel();
      renderHoldingsTable();
    }
  }

  /* ---------- 连接卡片 (未连接) ---------- */
  function connectCardHTML() {
    return `
<div class="chart-card">
  <h3>&#128274; 连接 GitHub 以跨设备同步持仓</h3>
  <div class="chart-desc">持仓数据保存在你自己的<strong>私有 Gist</strong>，可在任意设备/地区访问。仅需具备 <code>gist</code> 权限的 Personal Access Token。未连接时数据仅保存在当前浏览器。</div>
  <div class="pf-toolbar" style="margin-top:16px">
    <div class="pf-field" style="flex:1;min-width:280px">
      <label>GitHub Token（gist 权限）</label>
      <input type="password" id="ghToken" class="pf-input" placeholder="ghp_xxx 或 github_pat_xxx" autocomplete="off">
    </div>
    <button class="pf-btn" id="btnConnect">连接</button>
  </div>
  <div class="pf-toolbar" style="margin-top:4px">
    <a href="https://github.com/settings/tokens?type=beta" target="_blank" style="color:var(--accent);font-size:12px">如何创建 Token（勾选 gist 即可）</a>
    <span style="color:var(--text2);font-size:12px">· Token 仅存于本浏览器 localStorage，仅用于读写你的私有 Gist</span>
  </div>
  <div id="connectMsg"></div>
</div>`;
  }

  function bindConnect() {
    const btn = document.getElementById('btnConnect');
    const input = document.getElementById('ghToken');
    if (!btn || !input) return;
    btn.addEventListener('click', async () => {
      const tok = input.value.trim();
      if (!tok) { document.getElementById('connectMsg').innerHTML = '<div class="alert-warn" style="margin-top:12px"><span>&#9888;</span>请输入 Token</div>'; return; }
      btn.disabled = true; btn.textContent = '连接中…';
      localStorage.setItem(GistStore.TOKEN_KEY, tok); // 先写入，verifyToken 才能读到
      try {
        const me = await GistStore.verifyToken();
        localStorage.setItem(GistStore.USER_KEY, me.login);
        await GistStore.ensureGist();
        GH_CONNECTED = true;
        const h = await GistStore.load();
        HOLDINGS = loadHoldings(h);
        localStorage.setItem(GistStore.CACHE_KEY, JSON.stringify(HOLDINGS));
        toast('已连接 @' + me.login);
        renderHoldings();
      } catch (e) {
        localStorage.removeItem(GistStore.TOKEN_KEY);
        localStorage.removeItem(GistStore.GIST_KEY);
        GH_CONNECTED = false;
        let hint = '请检查 Token 是否有效且具备 gist 权限。';
        if (e.message && e.message.includes('401')) {
          hint += '<br><strong>常见原因</strong>：<br>1. Token 被撤销或过期；<br>2. 复制时带上了空格（已自动去除）；<br>3. 浏览器走了本地代理（截图中 Remote Address 为 127.0.0.1:10808），代理可能篡改/丢弃了 Authorization 头，请尝试关闭 Clash/V2Ray 等代理后重试。';
        }
        document.getElementById('connectMsg').innerHTML = '<div class="alert-warn" style="margin-top:12px"><span>&#9888;</span>' + esc(e.message) + '。' + hint + '</div>';
        btn.disabled = false; btn.textContent = '连接';
      }
    });
  }

  /* ---------- 持仓面板 (已连接 / 本浏览器模式) ---------- */
  function holdingsPanelHTML() {
    const user = GistStore.user();
    const statusHtml = GH_CONNECTED
      ? `<span style="color:#4ade80">&#10003; 已连接 @${esc(user || '')} · 私有 Gist 同步</span>`
      : `<span style="color:var(--text2)">&#128274; 未连接 GitHub · 数据仅存于本浏览器（连接后可跨设备）</span>`;
    return `
<div class="pf-account">
  <div style="font-size:14px;font-weight:600">&#128722; 我的持仓（跨市场汇总）</div>
  <div style="font-size:12px">${statusHtml}</div>
  <div style="margin-left:auto;display:flex;gap:8px">
    <button class="pf-btn-ghost" id="btnRefresh">&#8635; 刷新</button>
    <button class="pf-btn-ghost" id="btnExport">&#11015; 导出</button>
    <button class="pf-btn-ghost" id="btnImport">&#11014; 导入</button>
    <input type="file" id="pfImportFile" accept=".json" style="display:none">
    ${GH_CONNECTED ? '<button class="pf-btn-ghost" id="btnLogout">退出</button>' : ''}
  </div>
</div>
<div id="pfToast" style="display:none;padding:8px 14px;border-radius:8px;margin-bottom:12px;font-size:12px;background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.3)"></div>

<div class="chart-card">
  <h3>&#10133; 添加建仓记录</h3>
  <div class="chart-desc">按买入日期记录每笔建仓（分批买入可加多条）。系统按最新价自动清算每笔与汇总。</div>
  <div class="pf-toolbar" style="margin-top:12px;flex-wrap:wrap">
    <div class="pf-field"><label>ETF</label><select id="pfCode" class="pf-select">${codeOptionsHTML()}</select></div>
    <div class="pf-field"><label>买入日期</label><input type="date" id="pfDate" class="pf-input"></div>
    <div class="pf-field"><label>份额</label><input type="number" id="pfShares" class="pf-input" placeholder="如 100" min="0" step="any"></div>
    <div class="pf-field"><label>买入单价(¥/份)</label><input type="number" id="pfCost" class="pf-input" placeholder="如 1.234" min="0" step="any"></div>
    <div class="pf-field" style="min-width:120px"><label>投入金额</label><span id="pfAmountPreview" style="padding:6px 10px;font-weight:600">¥0.00</span></div>
    <button class="pf-btn" id="btnAdd">添加记录</button>
  </div>
</div>

<div id="pfSummary"></div>

<div class="chart-card">
  <h3>&#128202; 持仓明细</h3>
  <div class="chart-desc">每笔建仓独立清算，同一 ETF 的多笔合并为汇总行；顶部卡片为总持仓。红=盈利，绿=亏损（A股惯例）。</div>
  <table class="premium-table" style="margin-top:8px">
    <thead><tr><th>名称 / 代码</th><th>市场</th><th>日期</th><th>份额</th><th>买入单价</th><th>投入金额</th><th>最新价</th><th>当前市值</th><th>盈亏额</th><th>盈亏%</th><th></th></tr></thead>
    <tbody id="pfTbody"></tbody>
  </table>
</div>`;
  }

  function codeOptionsHTML() {
    let html = '';
    MARKET_ORDER.forEach(([m, label]) => {
      const src = m === 'ndx' ? ETF_DATA : m === 'sp500' ? SP500_DATA : m === 'us50' ? US50_DATA : DJIA_DATA;
      const codes = Object.keys(src).sort((a, b) => src[a].name.localeCompare(src[b].name, 'zh'));
      if (!codes.length) return;
      html += `<optgroup label="${esc(label)}">`;
      codes.forEach(c => { html += `<option value="${c}">${esc(src[c].name)} (${c.toUpperCase()})</option>`; });
      html += '</optgroup>';
    });
    return html;
  }

  function bindHoldingsPanel() {
    const add = document.getElementById('btnAdd');
    const codeEl = document.getElementById('pfCode');
    const dateEl = document.getElementById('pfDate');
    const sharesEl = document.getElementById('pfShares');
    const costEl = document.getElementById('pfCost');
    const amtEl = document.getElementById('pfAmountPreview');
    if (dateEl && !dateEl.value) dateEl.value = new Date().toISOString().slice(0, 10);
    const previewAmount = () => {
      const s = parseFloat(sharesEl.value), p = parseFloat(costEl.value);
      amtEl.textContent = '¥' + ((isFinite(s) && isFinite(p)) ? (s * p).toFixed(2) : '0.00');
    };
    if (sharesEl) sharesEl.addEventListener('input', previewAmount);
    if (costEl) costEl.addEventListener('input', previewAmount);

    if (add) add.addEventListener('click', () => {
      const code = codeEl.value;
      const date = dateEl ? dateEl.value : '';
      const shares = parseFloat(sharesEl.value);
      const price = parseFloat(costEl.value);
      if (!code || !(shares > 0) || !(price > 0)) { toast('请填写有效的 ETF / 份额 / 买入单价', true); return; }
      let ex = HOLDINGS.find(h => h.code === code);
      if (!ex) { ex = { code, lots: [] }; HOLDINGS.push(ex); }
      ex.lots.push({ date: date || '', shares, price });
      persistHoldings(); renderHoldingsTable();
      sharesEl.value = ''; costEl.value = ''; if (amtEl) amtEl.textContent = '¥0.00';
    });

    const refresh = document.getElementById('btnRefresh');
    if (refresh) refresh.addEventListener('click', async () => {
      if (!GH_CONNECTED) { toast('未连接，使用本浏览器数据'); renderHoldingsTable(); return; }
      try { HOLDINGS = loadHoldings(await GistStore.load()); localStorage.setItem(GistStore.CACHE_KEY, JSON.stringify(HOLDINGS)); renderHoldingsTable(); toast('已从 Gist 刷新'); }
      catch (e) { toast('刷新失败: ' + e.message, true); }
    });

    const exp = document.getElementById('btnExport');
    if (exp) exp.addEventListener('click', () => {
      const blob = new Blob([JSON.stringify({ version: 1, updatedAt: new Date().toISOString(), holdings: HOLDINGS }, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'nsdketf-holdings.json';
      a.click();
      URL.revokeObjectURL(a.href);
    });

    const imp = document.getElementById('btnImport');
    const file = document.getElementById('pfImportFile');
    if (imp && file) imp.addEventListener('click', () => file.click());
    if (file) file.addEventListener('change', e => {
      const f = e.target.files[0]; if (!f) return;
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const data = JSON.parse(reader.result);
          if (!Array.isArray(data.holdings)) throw new Error('格式错误');
          HOLDINGS = loadHoldings(data.holdings);
          persistHoldings(); renderHoldingsTable(); toast('已导入 ' + HOLDINGS.length + ' 只 ETF');
        } catch (err) { toast('导入失败: ' + err.message, true); }
      };
      reader.readAsText(f);
    });

    const logout = document.getElementById('btnLogout');
    if (logout) logout.addEventListener('click', () => {
      localStorage.removeItem(GistStore.TOKEN_KEY);
      localStorage.removeItem(GistStore.GIST_KEY);
      localStorage.removeItem(GistStore.USER_KEY);
      GH_CONNECTED = false;
      renderHoldings();
    });
  }

  function renderHoldingsTable() {
    const tbody = document.getElementById('pfTbody');
    const sumBox = document.getElementById('pfSummary');
    if (!tbody) return;
    if (!HOLDINGS.length) {
      tbody.innerHTML = '<tr><td colspan="11" style="color:var(--text2);text-align:center;padding:24px">暂无持仓，先在上方添加建仓记录。</td></tr>';
      if (sumBox) sumBox.innerHTML = '';
      return;
    }
    let rows = '';
    HOLDINGS.forEach(h => {
      const r = computeRow(h);
      if (!r || !r.lots.length) return;
      const mkt = MARKET_LABEL[MARKET_OF[h.code]] || '—';
      // 该 ETF 汇总行
      rows += `<tr class="pf-subtotal">
        <td colspan="2"><div style="font-weight:700">${esc(r.etf.name)}</div><div class="etf-code">${h.code.toUpperCase()}</div></td>
        <td>${mkt}</td>
        <td style="font-weight:600">${r.shares}</td>
        <td>${fmtPrice(r.avgCost)}</td>
        <td style="font-weight:600">${fmtMoney(r.cost)}</td>
        <td>${fmtPrice(r.price)}</td>
        <td style="font-weight:600">${fmtMoney(r.marketValue)}</td>
        <td class="${pnlClass(r.pnl)}" style="font-weight:600">${r.pnl == null ? '—' : (r.pnl >= 0 ? '+' : '') + '¥' + r.pnl.toFixed(2)}</td>
        <td class="${pnlClass(r.pnlPct)}" style="font-weight:600">${fmtPct(r.pnlPct)}</td>
        <td><button class="pf-btn-ghost" data-del-code="${h.code}">清仓</button></td>
      </tr>`;
      // 每笔建仓明细
      r.lots.forEach((lot, i) => {
        const lv = lotValues(lot, r.price);
        rows += `<tr class="pf-lot">
          <td></td><td></td>
          <td>${esc(lot.date || '—')}</td>
          <td>${lv.s}</td>
          <td>${fmtPrice(lv.p)}</td>
          <td>${fmtMoney(lv.amount)}</td>
          <td>${fmtPrice(r.price)}</td>
          <td>${fmtMoney(lv.mv)}</td>
          <td class="${pnlClass(lv.pnl)}">${lv.pnl == null ? '—' : (lv.pnl >= 0 ? '+' : '') + '¥' + lv.pnl.toFixed(2)}</td>
          <td class="${pnlClass(lv.pct)}">${fmtPct(lv.pct)}</td>
          <td><button class="pf-btn-ghost" data-del-lot="${h.code}|${i}">删除</button></td>
        </tr>`;
      });
    });
    tbody.innerHTML = rows;
    tbody.querySelectorAll('[data-del-lot]').forEach(b => b.addEventListener('click', () => {
      const [code, idx] = b.dataset.delLot.split('|');
      const ex = HOLDINGS.find(h => h.code === code);
      if (ex) { ex.lots.splice(Number(idx), 1); if (!ex.lots.length) HOLDINGS = HOLDINGS.filter(h => h.code !== code); }
      persistHoldings(); renderHoldingsTable();
    }));
    tbody.querySelectorAll('[data-del-code]').forEach(b => b.addEventListener('click', () => {
      HOLDINGS = HOLDINGS.filter(h => h.code !== b.dataset.delCode);
      persistHoldings(); renderHoldingsTable();
    }));

    if (sumBox) {
      const s = computeSummary();
      sumBox.innerHTML = `
<div class="pf-summary">
  <div class="info-card"><div class="card-header"><div class="card-name">总市值</div></div><div class="metric"><span class="metric-label">按最新价合计</span><span class="metric-value" style="font-size:16px">${fmtMoney(s.totalMV)}</span></div></div>
  <div class="info-card"><div class="card-header"><div class="card-name">总投入</div></div><div class="metric"><span class="metric-label">Σ 每笔建仓金额</span><span class="metric-value" style="font-size:16px">${fmtMoney(s.totalCost)}</span></div></div>
  <div class="info-card"><div class="card-header"><div class="card-name">总盈亏</div></div><div class="metric"><span class="metric-label">市值 − 投入</span><span class="metric-value ${pnlClass(s.totalPnl)}" style="font-size:16px">${s.totalPnl == null ? '—' : (s.totalPnl >= 0 ? '+' : '') + '¥' + s.totalPnl.toFixed(2)}</span></div></div>
  <div class="info-card"><div class="card-header"><div class="card-name">总收益率</div></div><div class="metric"><span class="metric-label">盈亏 / 投入</span><span class="metric-value ${pnlClass(s.totalPnlPct)}" style="font-size:16px">${fmtPct(s.totalPnlPct)}</span></div></div>
</div>`;
    }
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /* ---------- 启动 ---------- */
  if (typeof document !== 'undefined') {
    initHoldings();
    // 暴露给 index.html 的 switchIndex 调用
    window.renderHoldings = renderHoldings;
  }

  // 仅 Node 环境导出 (供单元测试, 浏览器中 module 未定义, 无副作用)
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { computeRow, computeSummary, lotValues, fmtPct, fmtMoney, fmtPrice, ALL_ETF, MARKET_OF, loadHoldings, setHoldings: function (h) { HOLDINGS = h; } };
  }
})();
