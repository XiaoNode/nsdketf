# nsdketf - 场内 QDII ETF 溢价分析工具

> 📊 追踪纳指100/标普500/美国50/道琼斯 QDII ETF 的场内价格、净值、折溢价率，提供双口径（IOPV + 单位净值）溢价分析与每日邮件播报

[![GitHub Pages](https://img.shields.io/badge/Pages-在线访问-blue)](https://666ghj.github.io/nsdketf/)
[![Auto Update](https://img.shields.io/badge/Auto%20Update-每日5轮-green)](https://github.com/666ghj/nsdketf/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 🎯 核心功能

### 📈 双口径溢价分析
- **IOPV 口径**：收盘价 ÷ 当日 IOPV（基金份额参考净值），同券商 App，当日即可获得
- **单位净值口径**：收盘价 ÷ 同日已披露单位净值（T+1~T+2，QDII 长假后延迟）
- 历史溢价回测（包含 IOPV 估算历史，基于标的指数涨跌推算）

### 📊 可视化看板
- 折溢价趋势图（Chart.js，支持缩放/平移）
- 场内价格 vs 基金净值对比
- 费率横向对比（管理费/托管费/销售服务费）
- 纳指100估值指标（PE/Forward PE，来源 History of Market）
- 场外联接基金追踪对比（18 只纳指联接 A 类收益率对比）

### 📧 每日低溢价播报
- **每交易日自动发送**：溢价最低 5 只 + 最高 5 只
- **双口径数据**：IOPV 优先（当日可得），缺 IOPV 回退单位净值口径
- 低于 5% 标的黄色高亮 ⚠️，建议优先关注
- 历史均值（近 1/3/12 个月）辅助判断

### 🔄 全自动更新
- **5 轮/天**（北京 15:15/16:40/20:20/22:20/次日 08:20），幂等互补
- 数据源双备份（新浪 + 腾讯 IOPV fallback），抗单点故障
- 多重校验（IOPV/NAV 比值、自算溢价与行情偏差、异常值拦截）

---

## 📦 已收录 ETF

| 分类 | 数量 | 代码列表 |
|---|---|---|
| **纳指100** | 13 只 | 嘉实 159501、富国 513870、易方达 159696、博时 513390、招商 159659、汇添富 159660、国泰 513100、华夏 513300、华安 159632、华泰柏瑞 513110、景顺 159509（跟踪 NDXTMC）、大成 159513、广发 159941 |
| **标普500** | 4 只 | 南方 513650、国泰 159612、华夏 159655、博时 513500 |
| **美国50** | 2 只 | 易方达 513850、汇添富 159577 |
| **道琼斯** | 1 只 | 鹏华 513400 |
| **场外联接** | 18 只 | 纳指100联接 A 类基金（用于费率/追踪误差对比） |

**总计**：20 只场内 ETF + 18 只场外联接基金

---

## 🚀 快速开始

### 在线访问
👉 **[https://666ghj.github.io/nsdketf/](https://666ghj.github.io/nsdketf/)**

### 本地运行
```bash
# 克隆仓库
git clone https://github.com/666ghj/nsdketf.git
cd nsdketf

# 手动更新数据（Python 标准库，无需额外依赖）
python daily_update.py

# 本地预览（任意 HTTP 服务器）
python -m http.server 8000
# 访问 http://localhost:8000
```

---

## 📂 项目结构

```
nsdketf/
├── .github/
│   ├── workflows/daily-update.yml       # Actions 调度（5 轮/天）
│   └── premium-alert-state.json         # 邮件播报状态（去重锚点）
├── data/
│   ├── etf_data_YYYY-MM.js              # 纳指100月度前端数据
│   ├── sp500_data_YYYY-MM.js            # 标普500月度数据
│   ├── us50_data_YYYY-MM.js             # 美国50月度数据
│   ├── djia_data_YYYY-MM.js             # 道琼斯月度数据
│   └── ndx_passive_data_YYYY-MM.js      # 场外联接月度数据
├── tests/                               # 单元测试（55 个用例）
├── index.html                           # 前端单页应用（无框架，纯 JS）
├── daily_update.py                      # 每日更新核心脚本
├── premium_alert.py                     # 低溢价邮件播报
├── etf_all.json                         # 纳指100完整历史（2.3MB）
├── sp500_all.json / us50_all.json / djia_all.json  # 其他分组
├── ndx_passive_all.json                 # 场外联接完整数据
├── ndx_valuation.json                   # 纳指估值指标
└── README.md
```

---

## 🔧 数据来源

| 数据类型 | 主源 | 备源 | 更新频率 |
|---|---|---|---|
| **场内价格** | 新浪财经 K 线 | 腾讯行情（从 IOPV 快照提取） | 每日 5 轮 |
| **基金净值** | 天天基金（东方财富） | - | 每日 5 轮（QDII 延迟 T+1~T+2） |
| **IOPV** | 腾讯行情实时快照 | - | 收盘后采集（15:00~23:00） |
| **标的指数** | 腾讯美股日线 | - | 用于 IOPV 历史估算 |
| **纳指估值** | History of Market | - | PE/Forward PE 每日更新 |

**防错机制**：
- IOPV/NAV 比值校验（0.5~2.0）
- 自算溢价与行情偏差校验（≤0.5pp）
- 异常值拦截（绝对溢价 >50% 跳过）
- 幂等设计（历史数据不覆盖，仅更新同日期记录）

---

## ⚙️ 自动更新机制

### GitHub Actions 调度
```yaml
# 北京时间 5 轮/天（UTC 周一~周五）
- 15:15  # 收盘快照：抓取当日 IOPV
- 16:40  # 提前首跑：收盘价尽早入库
- 20:20  # 主力前补：早披露净值一并收入
- 22:20  # 主力：多数 QDII T-1 净值已披露
- 08:20  # 次日兜底：前晚延迟/失败的补跑
```

### 邮件播报
- **触发**：每交易日 16:00 后（工作日，跳过周末）
- **去重**：通过 `premium-alert-state.json` 锚点（数据日 `price_date`）保证同一会话不重发
- **内容**：最低 5 只 + 最高 5 只，双口径溢价，历史均值，<5% 标黄
- **配置**：通过 GitHub Secrets（`ALERT_TO_EMAIL` / `ALERT_SMTP_*`）

---

## 🧪 测试与质量

```bash
# 运行全部测试（55 个用例）
python -m unittest discover -s tests -v

# 代码检查
python -m py_compile daily_update.py premium_alert.py
git diff --check  # 检查空白符错误
```

**测试覆盖**：
- 价格/净值抓取幂等性
- IOPV 时间窗口边界
- 溢价计算精度
- 邮件播报去重逻辑
- IOPV 历史估算校准

---

## 🛡️ 隐私与安全

✅ **无硬编码敏感信息**：所有邮件凭证通过 GitHub Secrets 注入  
✅ **无个人数据暴露**：`premium-alert-state.json` 仅含数据日期、状态、指纹（SHA256）  
✅ **日志脱敏**：SMTP 异常不打印收件人/密码  
✅ **开源友好**：所有数据源均为公开免登录接口

---

## 📜 免责声明

本项目仅用于数据展示与学习研究，**不构成任何投资建议**。

- QDII ETF 存在额度限制导致的折溢价风险
- 净值披露存在延迟（T+1~T+2，长假后可能更久）
- 历史溢价不代表未来表现
- 请以基金公司官方披露信息为准

---

## 📄 许可证

[MIT License](LICENSE) - 自由使用，保留署名

---

## 🤝 贡献

欢迎提交 Issue 或 Pull Request！

**改进方向**：
- [ ] 增加恐慌指数（VIX/VXN）展示
- [ ] 支持更多 QDII 品种（原油/黄金/港股）
- [ ] 溢价预测模型（基于历史波动）

---

**最后更新**：2026-10-12  
**数据质量审查报告**：[AUDIT_REPORT_2026-10-10.md](AUDIT_REPORT_2026-10-10.md)
