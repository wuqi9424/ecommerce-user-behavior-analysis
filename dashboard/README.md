# Power BI / Tableau 聚合数据

本目录提供可直接导入 Power BI 或 Tableau 的小体积 CSV。原始行为表约一亿行，不适合在作品集 Dashboard 中直接加载：文件大、刷新慢，也容易在 BI 层重复实现 SQL 口径。本项目采用：

**Raw Data → DuckDB / SQL → Aggregated BI Data → Power BI / Tableau**

运行方式：

```bash
python dashboard/export_bi_data.py
```

脚本从项目根目录或其他目录启动均可，使用只读连接读取 `data/processed/ecommerce.duckdb`，自动覆盖 `dashboard/data/` 中六个同名 CSV，并打印行数、列数和文件大小。需要先按照项目 [README](../README.md#12-如何运行)构建数据库。

## 数据文件

| 文件 | 粒度 | 来源 | 推荐可视化 |
|---|---|---|---|
| `daily_metrics.csv` | 每个自然日一行，共 9 行 | SQL 03 的每日平台指标 | DAU、事件量、购买用户率趋势 |
| `weekend_comparison.csv` | 每个等长周末一行，共 2 行 | SQL 09 的两天去重规模与强度口径 | 周末 KPI 卡、规模与强度对比 |
| `funnel_comparison.csv` | 每个等长周末一行，共 2 行 | SQL 04 的每日用户集合漏斗均值 | 漏斗、比例变化对比 |
| `user_priority_segments.csv` | P1/P2/P3 各一行 | SQL 08 的机会品类子池 | 运营候选池规模、行为对比 |
| `opportunity_categories.csv` | 每个机会品类一行，共 770 行 | SQL 08 已验证的机会品类规则 | Top 品类、增长散点图、明细表 |
| `ab_test_summary.csv` | Treatment/Control 各一行 | SQL 07 与 Notebook 07 | 模拟实验卡片、组间率与 CI |

`daily_metrics.csv` 包含活跃用户、四类事件、购买用户、购买用户率和人均事件。`weekend_comparison.csv` 的用户在每个两天窗口内去重，Previous 行的增长率为空，Final 行相对 Previous 计算。`funnel_comparison.csv` 的人数是每日人数的两天算术均值，比例也是每日用户级比例的等权均值；`avg_intent_users` 包含当日所有收藏或加购用户，而 `intent_to_buy_rate` 沿用 SQL 04 的嵌套集合分母，即“浏览且有意向且购买 / 浏览且有意向”。

`user_priority_segments.csv` 和 `opportunity_categories.csv` 只导出聚合结果，不含 user_id 或 item_id。P1/P2/P3 是 user-category 组合的规则型优先级，不是购买概率模型；同一用户可能跨品类出现，分层去重用户数不能相加。`category_id` 是匿名编号，不推断真实品类名称。

品类文件的 `intent_to_buy_rate` 沿用 SQL 08 的品类口径：“同品类意向且购买 / 同品类全部意向用户”，不额外要求浏览，因此不能与 SQL 04 的嵌套漏斗直接混用。`high_intent_nonbuyer_users` 为该品类近期 P1/P2 组合数；每个品类内一个用户只有一个组合，因而也等于该品类的去重高意向未购买用户数。

`ab_test_summary.csv` 重复保存整体 lift、p-value、95% CI 与 MDE，便于在两个组别筛选状态下显示实验卡片。这是历史数据的确定性模拟分组，没有真实 treatment 或 exposure；结果只展示 A/B Test 分析流程，不能证明购物车召回有效或无效。

## 字段与单位

- 所有 `*_rate`、`*_pct`、`*_growth_pct` 和 `*_pp` 均使用 **0–100 数值尺度**。例如 14.08% 保存为 `14.08`；百分点变化也直接保存为百分点数值。BI 中请除以 100 后再套用百分比格式，或直接显示并添加 `%`/`pp` 后缀。
- 日期使用 `YYYY-MM-DD`；CSV 编码为 UTF-8；数值列不混入中文文本。
- `buy_events` 是购买行为事件数，不是订单数；数据没有 order_id。
- 用户转化率来自去重用户集合，不使用 buy event / pv event 冒充用户转化率。无序集合漏斗不证明同商品时间路径。
- `first_observed_date` 仅表示九天窗口内首次出现，不是注册日期；Dashboard 当前不导出该用户级字段。
- 九天内行为不能解释为长期留存、长期复购或长期流失。
- SQL 08 使用 12/02–12/03 行为形成下一轮运营候选池，不能反向用于 SQL 07 在 12/02 启动的历史模拟实验选人。

CSV 是发布用聚合数据，可以提交 GitHub。重新导出前请确认本地数据库与项目已验证版本一致；脚本会核对核心人数、机会品类数、P1 规模和模拟实验购买用户率，不一致时明确报错。
