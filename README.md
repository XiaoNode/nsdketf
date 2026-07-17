# 纳斯达克100 / 标普500 QDII ETF 分析器

追踪 14 只纳斯达克100 QDII ETF + 4 只标普500 QDII ETF 的场内价格、净值、折溢价率与费率，提供前端可视化分析。

## 功能

- 📊 折溢价率趋势可视化（Chart.js）
- 💰 场内价格 vs 基金净值对比
- 📋 费率对比（管理费 / 托管费 / 销售服务费）
- 🔄 GitHub Actions 每日自动更新数据

## 数据来源

- 场内价格：新浪财经 K 线接口
- 基金净值：天天基金（东方财富）净值接口

## 项目结构

```
nasdaq-sp500-etf-analyzer/
├── .github/workflows/daily-update.yml  # 每日自动更新 + 部署 Pages
├── index.html                          # 前端单页应用
├── etf_all.json / sp500_all.json       # 完整历史数据
├── data/                               # 按月拆分的前端数据
│   ├── etf_data_YYYY-MM.js             # 纳斯达克100月度数据
│   └── sp500_data_YYYY-MM.js           # 标普500月度数据
├── daily_update.py                     # 每日更新脚本（标准库，无需依赖）
├── fetch_etf_data.py / fetch_etf_data_v2.py  # 历史数据一次性抓取
└── requirements.txt                    # 仅标准库
```

## 本地运行

```bash
python daily_update.py
```

## 自动更新

GitHub Actions 每日北京时间 15:00 自动运行 `daily_update.py`，提交更新后的数据文件并部署到 GitHub Pages。

Pages 地址：`https://<username>.github.io/<repo-name>/`

## 已收录 ETF

**纳斯达克100（14只）**：国泰(513100)、嘉实(159501)、广发(159941)、华夏(513300)、招商(159659)、富国(513870)、华安(159632)、大成(159513)、易方达(159696)、博时(513390)、汇添富(159660)、华泰柏瑞(513110)、纳斯达克科技市值加权ETF景顺(159509，跟踪纳斯达克科技市值加权指数NDXTMC)、日兴(513520)

**标普500（4只）**：博时(513500)、国泰(159612)、华夏(159655)、南方(513650)

## 免责声明

本项目仅用于数据展示与学习研究，不构成任何投资建议。QDII ETF 存在额度限制导致的折溢价风险，请以官方披露信息为准。
