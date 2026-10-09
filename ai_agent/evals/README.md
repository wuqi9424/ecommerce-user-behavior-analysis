# Golden Evaluation / AI 层回归基准

本目录保留冻结 deterministic baseline 与 legacy 严格评估，并新增独立 LLM evaluator v2。`run_evaluation` 不调用 LLM；`run_llm_evaluation` 的 llm/compare 模式会调用真实模型；`run_llm_evaluation_v2` 只读取已有 traces 离线复评。三者均不使用 LLM-as-a-judge；运行依赖 Python 标准库和项目已有 DuckDB。

## 运行

从项目根目录执行：

```bash
source .venv/bin/activate
python -m ai_agent.evals.run_evaluation
```

生成 `evaluation_results_after.json` 与 `evaluation_report.md`。Deterministic explanation 改进的历史对比基准为 `evaluation_results_explanation_before.json`（30/37），保留原 `evaluation_results_before.json` 的早期路由改进历史。默认命令不覆盖这些历史基准。也可用 `--comparison evaluation_results_before.json` 查看早期对比；正常回归无需覆盖历史记录。

**退出码：**全部 golden cases、数值与 failure-path 检查通过返回 0；任意失败返回 1。不要把「成功生成报告」当成「所有 case 通过」。Agent CLI 的 unsupported/ambiguous 输出明确拒答/歧义状态，返回 2 表示未回答；数据库缺失和非法范围参数返回 1；argparse 非整数返回 2。

旧 MVP 检查仍可运行：`python -m ai_agent.evals.run_checks`。`last_run.json` 属于第一轮基础检查记录，不是 Golden Evaluation 的答案来源。

## 数据结构

- `evaluation_cases.json`：37 个问题，每个包含 id、category、question、expected_intent、expected_tool、expected_status、required_facts、forbidden_claims、notes。
- `expected_values.json`：17 个独立人工冻结数值、来源意图、结构化输出路径、绝对容差、窗口和单位。不是从当前工具输出自动生成预期值。整数要求精确相等；百分比/pp 的四位快照允许 0.00005 绝对误差。
- `run_evaluation.py`：校验 schema，执行真实查询、路由与解释、记录工具调用，逐条检查并生成报告。
- `evaluation_results_before.json`：早期路由修改前历史。
- `evaluation_results_explanation_before.json` / `evaluation_results_after.json`：历史解释层修改前后的源码、数据库、评估集哈希，实际响应与逐项证据。
- `explanation_baseline_manifest.json`：历史解释层修改前 tools、evaluation_cases、expected_values 哈希；评估时检查这些受保护文件未改。
- `explanation_failure_analysis.md`：七条失败问题的逐条定位与通用模式。
- `numeric_grounding.py`：解释数值的独立来源/派生/格式检查。
- `test_reasoning.py`：15 个额外反事实与数值检查回归。
- `evaluation_report.md`：分类、指标、失败原因、修复前后比较与限制。

required_facts 使用三类检查：`number` 引用冻结数值；`text` 指定 response / key_data / interpretation 范围及 any_of 短语；`sections` 要求 facts、analysis、candidate_actions 非空。对诊断解释的要求放在 interpretation，不能用 key_data 中的定义冒充已回答问题。

forbidden_claims 使用带 ID 的正则断言模式。仅检查解释和 caveats，不检查用户问题或源数据定义。按标点拆句，对同一短句中禁断言前出现「不能/无法/不是」等否定词跳过，避免把边界纠正误报为违规。模式覆盖 buy=order、首次观测=注册、长期复购/流失、真实召回效果、因果增长、收入/GMV/ROI、必然购买。正则不能识别所有语义或复杂否定；无命中不能证明无幻觉。

## 分类与指标

37 个问题：direct 9、paraphrase 7、diagnostic 5、boundary 6、causal 6、multi_intent 4。

| 指标 | 分母与判定 |
|---|---|
| Intent Accuracy | 37 cases，实际意图与预期相同 |
| Tool Selection Accuracy | 37 cases，tool_used 和 spy 记录的真实调用均相符；拒答/歧义必须零调用 |
| Status Accuracy | 37 cases，answered / unsupported / ambiguous 相符；before 旧输出缺少 status 时由意图推导并标记 inferred |
| Numeric Accuracy | 17 个全量 ground truth 检查 + 17 个 case 数值要求，共 34 次检查；缺失、不有限、bool、错值均失败 |
| Required Fact Coverage | 全部 209 个原子事实检查，按通过项占比计算 |
| Boundary Compliance | 12 个 boundary/causal cases 的整体通过率：包含纠错说明，不只检查拒答或未出现禁断言 |
| Forbidden Claim Rate | 37 cases 中至少出现一个禁断言的 case 比例，越低越好 |
| Overall Case Pass Rate | 意图、工具、状态、全部 required facts、无 forbidden claims、新增 explanation numeric grounding 同时通过 |

原 Numeric Accuracy 继续只计算结构化 key_data，分母保持 34；新增 Explanation Numeric Grounding 独立报告，不混入原指标分母。历史 explanation 前后报告使用相同评估集与工具源码，当时只改解释层，路由规则与工具查询不变。任何失败保留细节，不降低事实要求来提高分数。

## 实际执行与失败路径

每轮先实际查询五个工具各一次，再对每个问题执行真实 `agent.analyze`。spy 包装保留原工具名称，记录调用并返回本轮真实结果的深拷贝；仅评估器内复用结果，避免 37 问重复扫描一亿行。Agent 本身没有增加缓存，不使用历史 JSON 当查询结果。

12 个 failure-path 检查：缺失数据库 API/CLI；top_n=0、101、浮点、字符串、bool 的 API；0、101、非整数的 CLI；写入请求；未知 intent。缺失路径用 unittest.mock 和子进程内部路径替换，不移动或删除真实数据库；非法参数验证不得调用 connection。写入请求与未知问题不得调用工具。测试前后计算数据库 SHA-256，必须一致。

评估器另外自检：注入禁断言、正确否定、错值、bool、NaN、缺失数值和缺失事实，确保检查器能报告问题。它们是检查器自测，不计入 Golden Case 总数或七项指标。

## 下一阶段

Evaluation set 是项目 AI 层的回归基准。首次真实 LLM run 已完成，原严格成绩和独立 v2 离线复评并列保留。下一次模型评估应先冻结 v2 contracts 与 self-tests，再运行新的独立模型结果；继续扩展未见措辞、中英文否定、prompt injection、多次运行稳定性和工具错误传播。

Deterministic baseline 的历史 Golden Set 结果为 37/37，有限范围 baseline 已冻结。原七条失败通过通用 reasoning patterns 修复；详见 [evaluation_report.md](evaluation_report.md)。当前分数不能代表任意语言或业务问题的准确率。

## Explanation numeric grounding

生产解释层用 numeric_evidence 保存数字的来源路径、operation、display 与 format_spec。支持 identity（直接字段）、difference（后值减前值）、pct_change（后值/前值−一，再乘百分比尺度），以及两个具名统计设置 significance_alpha / confidence_level_pct。所有数据量均来自工具字段，不从冻结快照生成。

评估器独立实现重算，不调用生产 calculate。逐条核验来源、计算和格式，再扫描 answer / facts / analysis / candidate_actions / caveats 中的数值字面量；没有合法记录或记录没有实际显示均失败。P1/P2/P3/Q75 等标识符不当成量化主张；未标注的数值日期不会被自动豁免。支持紧邻中文的数字，避免遗漏“增加某数字人”这样的措辞。

它只验证来源与数值字面覆盖，不推断每个数字的单位语义；同值不同义不能完全区分，也不能替代事实覆盖或因果边界检查。独立 LLM grounding 层已增加来源和单位校验，仍不能保证完整语义正确性。

额外回归运行：

```bash
python -m unittest ai_agent.evals.test_reasoning -v
```

完整 Evaluation 也会执行这些 15 个 tests；原 16 个 evaluator self-tests 和 12 个 failure-path tests 维持原定义。13 个实际 CLI 回归由 `run_checks` 单独运行。反事实 tests 用合成数据改变规模/比例方向、最大层、相邻环节降幅及 p-value/CI，证明解释不是冻结答案；合成值仅用于 tests，不进入 Agent 或真实结果。

## 独立 Controlled LLM comparison

`run_llm_evaluation` 支持 `--mode baseline|llm|compare`；`--verify-baseline` 实际重跑原完整评估，保存本轮证据并恢复冻结历史报告。`llm_baseline_manifest.json` 检查 baseline 代码、工具、原评估集与报告哈希。缺少模型配置时 LLM 各项为 N/A，非零退出；不使用 scripted provider 伪造模型成绩。

```bash
python -m ai_agent.evals.run_llm_evaluation --mode baseline --verify-baseline
python -m ai_agent.evals.run_llm_evaluation --mode llm --save-traces
python -m unittest ai_agent.evals.test_llm_control -v
```

同一 37 个问题保留严格历史标准。LLM Numeric Accuracy 除实际工具值外，还要求 case 指定数字出现在最终解释的有效 numeric_evidence 中；这项展示要求比原 baseline 更严格，不追溯修改 baseline 分数。原四个 multi-intent 另用 `llm_multi_intent_expectations.json` 检查工具组合、指定参数、各任务事实、边界及 grounding，单独报告，不混入严格 overall。

每个真实模型 case 使用独立 provider 会话，记录实际调用及 call_id；首次 37-case 真实运行结果已保存。协议测试使用 ScriptedProvider 和 mocked HTTP，仅检验宿主控制与评估投影。动态 trace/results 被忽略，比较报告保留可审阅结论。详见 [比较报告](llm_comparison_report.md)。


## Legacy 与 architecture-aware v2

Legacy evaluator 用途是保留原 Golden expectations 下的严格历史比较：deterministic baseline 为 37/37，首次真实 LLM run 的 strict overall 为 0/37。它保留单意图投影、原事实短语与全主题要求，以及 composite boundary 指标；这个 strict 分数不能直接解释为模型所有回答都错误。历史冻结报告中的“本轮不接模型”描述的是当时的 baseline 阶段，不代表当前 LLM 模块状态。

[V2 evaluator](run_llm_evaluation_v2.py) 用途是只读取已保存 traces，按独立 [contracts](llm_v2_contracts.json) 检查完整意图/工具集合、状态、问题专属正文事实与边界。它把来源 grounding 与 Golden 数字展示要求分开，工具 metadata/definitions 不代替回答正文。首次 traces 的 v2 overall 为 34/37（91.89%），来源 grounding 为 37/37，数字展示要求为 12/17；这不是 universal model accuracy。

V2 是首次 run 之后建立的 **post-hoc exploratory evaluation**，不是预注册或 held-out benchmark。两种视图的事实分母、任务范围与判定不同，**不可直接横向比较**，不能把 legacy→v2 的全部差异称为 evaluator bug 或模型提升。差异还包括多意图设计冲突、事实范围和展示精度要求；错误工具与状态不符仍如实保留。

```bash
# 仅离线复评，不调用 API；读取本地保存的 results
python -m ai_agent.evals.run_llm_evaluation_v2
python -m unittest ai_agent.evals.test_llm_evaluation_v2 -v
```

详见 [v2 README](llm_evaluation_v2_README.md) 与 [v2 报告](llm_evaluation_v2_report.md)。当前离线 unittest 共 116 项通过（含 27 项 v2 self-tests）；另有 16 项 legacy evaluator 自检及 12 项失败路径检查通过。离线测试不等于模型质量证据；12/12 边界规则通过与零 forbidden hits 仅反映当前规则覆盖范围。

下一次比较应先冻结 v2 contracts，再采集新的独立模型结果；保留两种视图、逐项失败和来源证据。`ai_agent/runs/`、`llm_results_latest.json`、`llm_results_v2_latest.json` 继续被 Git 忽略，原始 traces 不纳入公开提交。
