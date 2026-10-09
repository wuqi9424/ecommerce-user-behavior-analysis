# AI Analytics Agent / 电商运营分析 Copilot MVP

在已验证的 SQL 01–09 与 DuckDB 数据层之上，提供可重复调用、只读、可追溯的运营问答接口，减少问答时重复计算指标与混用口径。没有重做清洗、分层或增长分析。

当前是 **deterministic rule-based prototype**，不是完整 autonomous agent，也没有接入 LLM。解释由接收结构化工具结果的通用 Python reasoning rules 生成，system prompt 保存下一阶段的行为契约，目前不会执行或调用模型。

## 架构

```text
User Question → Intent Routing → Read-only Analytics Tool → DuckDB
              → Structured Result → Deterministic Reasoning → Explanation
```

`agent.py` 只把关键词映射到五个白名单函数，不把问题拼入 SQL。工具返回 dict/list，输出包含 `detected_intent`、`tool_used`、`key_data`、`interpretation`、`caveats`，并显式返回 `status`（answered / unsupported / ambiguous）。解释分为 answer、facts、analysis、candidate_actions、caveats；numeric_evidence 记录展示数值的来源路径、允许的派生公式与格式。关键数值来自本次工具结果或明确派生计算。工具元数据包含数据库相对路径、源表、观察窗口、时区、单位、SQL 来源及 SHA-256，方便核对查询版本。

## 为什么暂不开放自由 SQL

自由生成 SQL 容易改变分母、日期窗口、去重单位，甚至把事件比误当用户转化率。当前复用固定已验证 SQL：SQL 04 只运行集合漏斗部分；SQL 07/08/09 只准备其连接内临时聚合，跳过展示用 SELECT 和清理段。SQL 04 等长周末查询原样保存在工具常量；统计方法沿用 BI 导出脚本。没有用户 SQL 参数或数据库写入接口。

所有工具均使用 `duckdb.connect(..., read_only=True)`，`finally` 关闭连接。固定 SQL 只允许 SELECT、时区设置、CREATE OR REPLACE TEMP TABLE/VIEW；TEMP 对象仅属于当前连接，关闭即释放，数据库持久表不变。临时聚合可能使用 DuckDB spill 空间，读取一亿行仍需时间；此 MVP 不做结果缓存。数据库缺失、查询失败、非法 top_n 均明确报错，CLI 写 stderr 并返回非零状态，不用快照代替结果。

## 五个工具

| 意图 / 工具 | 结构化输出 | 来源 |
|---|---|---|
| platform / get_platform_overview | 总事件、活跃/总用户、商品、品类、四类事件、购买用户 | SQL 03 |
| growth / get_weekend_growth | 两个周末去重用户、事件、人均强度、增长率；首次/此前观测用户及事件贡献；描述性 bridge | SQL 09 |
| funnel / get_funnel_comparison | 日均浏览/意向/购买用户、四项用户集合比例、有效比例天数 | SQL 04 |
| opportunity / get_opportunity_analysis(top_n=5) | 总/合格/机会品类；P1/P2/P3 pair、去重用户与行为均值；Top N、动态门槛 | SQL 08 |
| experiment / get_ab_test_summary | 两组分配人数、购买率、lift、p-value、CI、MDE、simulated_assignment 与 caveat | SQL 07、BI 统计方法 |

`top_n` 必须是 1–100 的整数，使用绑定参数。工具可直接从 `ai_agent.tools` 导入。

## 支持的问题与路由

例如平台规模、周末流量增长、转化漏斗变化、运营优先级/机会品类、模拟实验显著性。关键词规则公开在 `agent.py`；先识别独立任务，多意图返回 ambiguous。单一召回效果问题映射 experiment，用户召回候选映射 opportunity；周末作为时间背景，不单独造成漏斗问题歧义。未知问题与已识别的数据边界问题返回 unsupported，不查询数据库。

路由是轻量关键词匹配，不具备完整语义理解；复杂措辞、否定与多语言变体可能误判。回答覆盖对应意图的通用分析模式，暂不支持任意筛选、用户明细、个性化时间窗口或真实品类名称。

## Deterministic reasoning

`reasoning/diagnostic_rules.py` 将结构化事实转换成业务解释，不接收原始 question，不读取 Golden Set 或 expected_values，不查询数据库。Agent 路由保持原规则，工具继续只返回结构化事实。

可复用规则包括：人数与比例分开判断、相邻环节百分点差比较、优先级不等于购买概率、购买沉寂不等于长期流失、相关不等于因果、模拟分组不等于真实策略效果、规模不等于运营优先级。结论随工具中的指标关系变化，处理方向反转、并列、缺失与无下降。P1/P2/P3 是已声明的业务规则标签，不是对具体问题的专门答案。

unsupported 仍然不调用工具；对购买沉寂仅作一般性的条件说明，不能断言该群体实际仍活跃，也不复制 SQL 06 的群体数值。

## 数据边界

- 2017-11-25–2017-12-03，Asia/Shanghai，九天公开样本，不代表全平台或长期行为。
- buy event ≠ order；newly observed ≠ new registered user；category_id 匿名。
- 无 price / GMV、真实 promotion / channel / exposure，不能确认增长原因、收益或营销因果效果。
- 增长使用两天去重用户，漏斗使用每日人数及每日用户集合比例等权均值；比例以 0–100 表示，百分点以 pp 表示。
- 漏斗非严格同商品路径；品类意向→购买为“意向且购买 / 全部意向”，平台为“浏览且意向且购买 / 浏览且意向”，不能混用。
- 机会池仅机会品类子池，均值按 user-category pair 等权；跨层 distinct_users 不可相加。池使用九天排除目标品类 buy，只能用于 12/03 结束后的下一轮运营。
- A/B Test 为 simulated assignment。没有真实 treatment / exposure，不能证明购物车召回有效或无效，不显著不等于相同。MDE 不是预期效果。

## CLI

从项目根目录，使用已安装项目 DuckDB 依赖的 Python：

```bash
source .venv/bin/activate
python -m ai_agent.agent "为什么第二个周末流量增长了？"
python -m ai_agent.agent "哪类用户最值得优先运营？"
python -m ai_agent.agent "A/B Test 是否证明购物车召回有效？"
python -m ai_agent.agent "哪些品类存在机会？" --top-n 3
python -m ai_agent.evals.run_checks
```

仅需 DuckDB；未添加框架、API、凭据、RAG、MCP 或 UI。已有数据库必须存在，不自动执行 pipeline 或重建数据库。CLI 回答成功返回 0，工具执行失败返回 1，unsupported / ambiguous 或参数解析失败返回 2；拒答与歧义仍输出结构化 JSON。

## Evaluation

当前 rule-based prototype 已建立 37 个问题的 Golden Evaluation Set，作为项目 AI 层的回归基准。运行：

```bash
python -m ai_agent.evals.run_evaluation
```

评估意图、实际工具调用、状态、冻结数值、必要事实、数据边界与禁断言，报告真实失败；有失败时进程返回非零。详见 [Evaluation 说明](evals/README.md) 和 [运行报告](evals/evaluation_report.md)。未来接入 LLM Tool Calling 后，使用同一测试集比较 semantic routing、tool selection、numeric grounding、boundary compliance 与 hallucination；新增轻量 numeric grounding 独立核验来源路径、difference / pct_change、统计设置与展示格式；仍不全面理解单位语义或每句话的因果含义。

本轮 explanation 改进使 Overall Pass 从 30/37（81.08%）升到 37/37（100%），原 12 个失败路径、16 个评估器自检和 13 个 CLI 回归均通过。新增 15 个反事实 reasoning / grounding tests 验证结论随数据变化、拒绝无来源或伪造数字。expected facts、forbidden claims 和五个工具查询未改变。100% 仅适用于冻结测试集，不代表任意措辞或业务问题都正确。

## 下一阶段

当前 37 个 Golden cases 已全部通过，建议冻结这版有限范围的 deterministic baseline，下一阶段进入受控 LLM Tool Calling，将这五个只读函数作为白名单并校验参数。对 LLM 解释核对数字来源和禁止推断，继续保持工具失败显式报错。只有在评估证明有必要时，再研究 constrained text-to-SQL（限定 SELECT、表/字段/指标、资源限制和独立验证），不直接开放自由 SQL。

验证入口与运行记录见 [evals/README.md](evals/README.md)。
