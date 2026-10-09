# Golden Evaluation Report

运行时间（带时区）：2026-10-09T19:14:57.473601+11:00。Python 3.13.0，DuckDB 1.5.6。

Golden cases：**37**；分类：

| Category | Cases |
|---|---:|
| direct | 9 |
| paraphrase | 7 |
| diagnostic | 5 |
| boundary | 6 |
| causal | 6 |
| multi_intent | 4 |

## Metrics

| Metric | Count / Total | Result |
|---|---:|---:|
| Intent Accuracy | 37 / 37 | 100.00% |
| Tool Selection Accuracy | 37 / 37 | 100.00% |
| Status Accuracy | 37 / 37 | 100.00% |
| Numeric Accuracy | 34 / 34 | 100.00% |
| Required Fact Coverage | 209 / 209 | 100.00% |
| Boundary Compliance | 12 / 12 | 100.00% |
| Forbidden Claim Rate | 0 / 37 | 0.00% |
| Overall Case Pass Rate | 37 / 37 | 100.00% |

Numeric Accuracy 分母为 17 个全量 ground truth 检查加各 case 的数值要求；Required Fact Coverage 按原子事实检查计数。Boundary Compliance 为 boundary/causal cases 的状态、工具调用、边界说明与禁断言全部通过比例；Forbidden Claim Rate 为出现至少一个禁断言的 case 比例。整体 case 要求意图、工具、状态、全部 required facts、禁断言及新增数值 grounding 均通过。

## Failed golden cases

无。此结果仅适用于冻结测试集，不能推断任意问题均正确。

## Failure paths

- missing_database_api：PASS。"Database not found: /var/folders/tz/gjky35816558kj5_7z80yp240000gn/T/agent-eval-zegy0u9c/missing.duckdb"
- missing_database_cli：PASS。{"returncode": 1, "stderr": "{\"error\": \"FileNotFoundError\", \"message\": \"Database not found: /var/folders/tz/gjky35816558kj5_7z80yp240000gn/T/agent-eval-zegy0u9c/missing.duckdb\"}\n", "file_created": false}
- top_n_api_0：PASS。"top_n must be an integer between 1 and 100"
- top_n_api_101：PASS。"top_n must be an integer between 1 and 100"
- top_n_api_1.5：PASS。"top_n must be an integer between 1 and 100"
- top_n_api_'5'：PASS。"top_n must be an integer between 1 and 100"
- top_n_api_True：PASS。"top_n must be an integer between 1 and 100"
- top_n_cli_0：PASS。{"returncode": 1, "stderr": "{\"error\": \"ValueError\", \"message\": \"top_n must be an integer between 1 and 100\"}\n"}
- top_n_cli_101：PASS。{"returncode": 1, "stderr": "{\"error\": \"ValueError\", \"message\": \"top_n must be an integer between 1 and 100\"}\n"}
- top_n_cli_1.5：PASS。{"returncode": 2, "stderr": "usage: agent.py [-h] [--top-n TOP_N] question\nagent.py: error: argument --top-n: invalid int value: '1.5'\n"}
- write_request：PASS。{"returncode": 2, "meaning": "explicit unsupported response; nonzero because no answer/tool execution"}
- unknown_intent：PASS。{"returncode": 2, "meaning": "explicit unsupported response; nonzero because no answer/tool execution"}

unsupported/ambiguous 输出明确结构化状态，CLI 返回 2；执行失败返回 1，argparse 非整数也返回 2。没有重命名、删除或写入数据库；缺失数据库使用内部路径替换测试。

## Before / after

本轮 explanation 修复前：30/37 = 81.08%；修复后：37/37 = 100.00%。

| Metric | Before | After |
|---|---:|---:|
| Intent Accuracy | 100.00% | 100.00% |
| Tool Selection Accuracy | 100.00% | 100.00% |
| Status Accuracy | 100.00% | 100.00% |
| Numeric Accuracy | 100.00% | 100.00% |
| Required Fact Coverage | 96.65% | 100.00% |
| Boundary Compliance | 83.33% | 100.00% |
| Forbidden Claim Rate | 0.00% | 0.00% |
| Overall Case Pass Rate | 81.08% | 100.00% |

路由、状态和 CLI 退出码保持不变。改进仅来自通用 explanation reasoning；变化的 case：
- diagnostic_01：intent funnel → funnel；pass False → True。
- diagnostic_02：intent funnel → funnel；pass False → True。
- diagnostic_03：intent growth → growth；pass False → True。
- diagnostic_04：intent opportunity → opportunity；pass False → True。
- diagnostic_05：intent opportunity → opportunity；pass False → True。
- causal_04：intent opportunity → opportunity；pass False → True。
- causal_06：intent unsupported → unsupported；pass False → True。

## Provenance and integrity

- 数据库前后 SHA-256 一致：True。
- 工具源文件与修复前一致：True。
- 评估器自检：16 项通过。
- 运行中每个真实工具查询一次；每个问题经过真实 analyze 路由与解释层，工具调用由 spy 记录。同一运行的重复工具请求复用深拷贝结果，仅在评估器内；不读取 last_run.json 作答案缓存。输出 JSON 保存每个 case 的实际响应、检查细节、源码及评估集哈希。

## Deterministic baseline limitations

关键词路由不能充分理解否定、复杂语义或任意中英混合；固定解释模板可能没有回应诊断问题。精确事实检查只覆盖预设路径与短语，禁止断言正则仅覆盖已列举措辞，0% 命中不能证明没有幻觉。原 Numeric Accuracy 仍按结构化字段计数，保证与之前分母一致。新增轻量检查核验 explanation 的数值来源、派生公式、格式与字面覆盖；它不全面验证单位语义或数字所关联的自然语言主张。不把这些限制包装成已经解决，也不放宽事实要求以提高 pass rate。

## Next: LLM Tool Calling evaluation

可使用同一 Golden Set 比较 semantic routing、真实工具选择/参数、numeric grounding、boundary compliance 与 hallucination。接入时保持外部工具白名单，记录调用轨迹，新增自然语言数字与来源核验、prompt injection、工具失败传播、重复运行稳定性。当前已建立可冻结的有限测试集 baseline；100% 仅指此集。后续接入采用同一测试集比较，保持白名单与工具失败显式传播。本轮不接模型或自由 SQL。


## Generic reasoning improvements

完整修改前定位见 [explanation_failure_analysis.md](explanation_failure_analysis.md)。

- diagnostic_01：Absolute vs Relative Change 使用日均购买人数与比例关系，区分规模与承接。
- diagnostic_02：只比较相邻漏斗步骤的百分点差，避免把完整路径作为第三个相邻环节；处理并列、缺失与无下降。
- diagnostic_03：Association ≠ Causality 区分事件强度、潜在意愿与因果证据。
- diagnostic_04 / diagnostic_05 / causal_04：Scale ≠ Priority 与 Priority ≠ Probability 同时解释三层行为规则、规模和候选动作，不保证购买。
- causal_06：Inactivity ≠ Churn 只给通用条件边界；保持 unsupported / 零调用，不编造沉寂群体最后一天活跃人数。
- experiment：依据 simulated_assignment 分开处理统计显著性与真实 treatment effect；p-value/CI 改变时结论随之改变。

## Explanation numeric grounding

新增数值来源检查：37/37 cases；检查 286 个数值字面量、286 条来源记录。
展示数值来自工具字段路径，或白名单的 difference / pct_change；仅允许 alpha 与 CI 水平两个明确统计设置。评估器独立重算，拒绝无来源数值、伪造来源值与未使用的记录。
不修改原 Numeric Accuracy 的分母。标识符 P1/P2/P3/Q75 不当成数值主张；相同数值可对应不同语义，当前检查不能证明单位和每句话的因果关系正确。
额外反事实 reasoning / grounding 回归：15 tests，pass=True。原 16 个评估器自检与 12 个失败路径仍独立执行。

## Baseline freeze decision

当前完整 case、数值来源与回归通过，建议冻结 deterministic baseline。冻结的是当前有限窗口和白名单工具上的回归基准，不是任意问题均正确的保证。
protected_files_unchanged 核对本轮修改前 tools、evaluation_cases、expected_values 文件哈希；未修改数据库或任何工具查询，也未调整 expected facts / forbidden claims。
