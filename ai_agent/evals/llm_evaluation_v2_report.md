# Offline LLM evaluator v2

这是独立、事后建立的 architecture-aware 评估视图；原 Golden 与 legacy 分数未修改。没有新 API 请求。

## Legacy frozen contract

| Metric | Result |
|---|---:|
| Intent Accuracy | 33/37 (89.19%) |
| Tool Selection Accuracy | 30/37 (81.08%) |
| Status Accuracy | 32/37 (86.49%) |
| Numeric Accuracy | 29/34 (85.29%) |
| Required Fact Coverage | 63/209 (30.14%) |
| Boundary Compliance | 0/12 (0.00%) |
| Forbidden Claim Rate | 0/37 (0.00%) |
| Overall Case Pass Rate | 0/37 (0.00%) |

Forbidden Claim Rate 的 legacy passed=0 表示零违规，不是零个通过。

## Architecture-aware metrics

| Metric | Result |
|---|---:|
| intent_set_exact_accuracy | 33/37 (89.19%) |
| intent_set_micro | {"precision": 0.9069767441860465, "recall": 0.9512195121951219, "F1": 0.9285714285714286, "true_positive_labels": 39, "predicted_labels": 43, "expected_labels": 41} |
| tool_set_exact_accuracy | 34/37 (91.89%) |
| required_tool_set_accuracy | 35/37 (94.59%) |
| status_accuracy | 36/37 (97.30%) |
| numeric_grounding_source_accuracy | 37/37 (100.00%) |
| numeric_display_requirement_coverage | 12/17 (70.59%) |
| answer_prose_fact_coverage | 51/51 (100.00%) |
| tool_result_numeric_fact_availability | 19/19 (100.00%) |
| boundary_compliance | 12/12 (100.00%) |
| forbidden_claim_cases | 0 |
| forbidden_claim_rate | 0.0 |
| architecture_aware_case_pass | 34/37 (91.89%) |

## Definitions and scope

- Intent exact 用完整集合；micro P/R/F1 汇总标签，包括 unsupported/ambiguous，不投影多意图。
- Tool exact 使用成功执行集合，另检查选择/执行一致；overall 要求全部必需工具，允许显式声明的辅助工具。diagnostic_01 可用 growth 辅助绝对量分析；diagnostic_03 的 funnel 不能替代 Cart growth 数据。
- Status 独立；四个 multi-intent expected=answered。原 ambiguous/no-tool 保留于 legacy。
- Numeric source：离线重新执行现有来源、单位、格式、字面量、方向校验；包含固定 policy。不是展示覆盖率，也不是完整语义正确率。
- Display coverage：原 Golden 17 项数字展示要求，精确来源路径、单位、正文 token、原 tolerance；缺数字和低精度不叫数值幻觉。
- Task facts：独立 contracts 文件仅要求问题专属事实；数值需要实际调用的对应字段 identity evidence、正文展示及 grounding，不要求额外主题数字；semantic predicates 只看五个 prose 字段。
- Tool availability 仅统计任务 numeric facts 的实际值；语义事实的 metadata 不计作模型正文。
- Boundary：12 个 boundary/causal case 专属规则与 forbidden hits，独立于通用事实、路由与总体 pass。
- Overall：required intent/tool sets、status、arguments、grounding、task facts、专属边界、无 forbidden/error、调用预算；不依赖 legacy F1/F2/F3，也不依赖原全主题 display coverage。

## Categories

| Category | Pass |
|---|---:|
| boundary | 4/6 |
| causal | 6/6 |
| diagnostic | 4/5 |
| direct | 9/9 |
| multi_intent | 4/4 |
| paraphrase | 7/7 |

## All case results

| ID | v2 pass | legacy pass | Failures / auxiliary tools |
|---|---|---|---|
| direct_01 | True | False | auxiliary= |
| direct_02 | True | False | auxiliary= |
| direct_03 | True | False | auxiliary= |
| direct_04 | True | False | auxiliary= |
| direct_05 | True | False | auxiliary= |
| direct_06 | True | False | auxiliary= |
| direct_07 | True | False | auxiliary= |
| direct_08 | True | False | auxiliary= |
| direct_09 | True | False | auxiliary= |
| paraphrase_01 | True | False | auxiliary= |
| paraphrase_02 | True | False | auxiliary= |
| paraphrase_03 | True | False | auxiliary= |
| paraphrase_04 | True | False | auxiliary= |
| paraphrase_05 | True | False | auxiliary= |
| paraphrase_06 | True | False | auxiliary= |
| paraphrase_07 | True | False | auxiliary= |
| diagnostic_01 | True | False | auxiliary=get_weekend_growth |
| diagnostic_02 | True | False | auxiliary= |
| diagnostic_03 | False | False | intent_set; tool_set |
| diagnostic_04 | True | False | auxiliary= |
| diagnostic_05 | True | False | auxiliary= |
| boundary_01 | False | False | intent_set; tool_set |
| boundary_02 | True | False | auxiliary= |
| boundary_03 | True | False | auxiliary= |
| boundary_04 | True | False | auxiliary= |
| boundary_05 | False | False | intent_set; status |
| boundary_06 | True | False | auxiliary= |
| causal_01 | True | False | auxiliary= |
| causal_02 | True | False | auxiliary= |
| causal_03 | True | False | auxiliary= |
| causal_04 | True | False | auxiliary= |
| causal_05 | True | False | auxiliary= |
| causal_06 | True | False | auxiliary= |
| multi_intent_01 | True | False | auxiliary= |
| multi_intent_02 | True | False | auxiliary= |
| multi_intent_03 | True | False | auxiliary= |
| multi_intent_04 | True | False | auxiliary= |

## Boundary rules

- boundary_01: no_gmv=True
  Evidence: ["无法判断这九天的 GMV：可用数据不包含 GMV 或商品价格", "平台概览返回了购买行为事件数，但没有 GMV 或价格字段", "购买行为事件数不能替代 GMV", "没有价格或交易金额数据，无法计算 GMV", "不能据此推算 GMV"]
- boundary_02: no_income=True
  Evidence: ["无法判断哪个品类收入最高：可用数据没有价格或 GMV，且品类 ID 匿名", "现有数据无法计算或比较各品类收入", "不能根据行为量或购买用户数推断收入排名"]
- boundary_03: no_channel=True
  Evidence: ["无法判断哪个渠道带来的增长最多：当前可用数据不包含渠道归因", "不能将用户规模或行为变化归因于特定渠道"]
- boundary_04: no_long_repeat=True
  Evidence: ["现有数据范围不足以支持长期复购率计算", "不应将短期观察窗口内的购买行为解释为长期复购或长期留存"]
- boundary_05: anonymous=True
  Evidence: ["无法判断哪个具体商品类别表现最好：“表现最好”没有指定衡量指标，而且数据中的 category_id 是匿名标识，无法对应到真实类别名称", "但匿名 category_id 仍无法揭示真实类别名称"]
- boundary_06: no_roi=True
  Evidence: ["现有数据不包含活动成本、收入或可归因于活动的增量结果，无法计算 ROI"]
- causal_01: simulated=True; no_exposure=True; no_effect=True
  Evidence: ["历史模拟分组中，treatment 组购买用户率相对 control 组变化 -0.08 pp", "【数据边界】本结果来自 simulated_assignment，不是实际购物车召回实验，不能解释为真实因果效果"]
  Evidence: ["由于分组是模拟的，且没有真实 treatment 或 exposure，不能据此估计购物车召回的真实转化效果"]
  Evidence: ["这不是召回的真实因果效果", "这既不能证明召回无效，也不能证明两组效果相同", "由于分组是模拟的，且没有真实 treatment 或 exposure，不能据此估计购物车召回的真实转化效果", "【数据边界】本结果来自 simulated_assignment，不是实际购物车召回实验，不能解释为真实因果效果"]
- causal_02: simulated=True; no_exposure=True; no_effect=True; not_same=True
  Evidence: ["模拟分组中，处理组购买用户率为 25.39%，对照组为 25.48%，组间变化为 -0.08 pp", "结果标记为 simulated_assignment，且没有真实 treatment / exposure", "模拟分组不代表真实 treatment 或 exposure"]
  Evidence: ["结果标记为 simulated_assignment，且没有真实 treatment / exposure", "由于没有真实触达或处理，结果不能证明真实购物车召回无效，也不能证明其有效", "模拟分组不代表真实 treatment 或 exposure"]
  Evidence: ["不能说 A/B Test 证明召回无效", "由于没有真实触达或处理，结果不能证明真实购物车召回无效，也不能证明其有效", "该分析不能识别真实召回的因果效果"]
  Evidence: ["该模拟结果未显示统计上显著的组间差异，但不显著不等于两组完全相同", "不显著不代表效果为零或两组相同"]
- causal_03: no_ad_cause=True
  Evidence: ["无法判断 Final Weekend 的增长是不是广告投放导致的：现有数据没有广告投放、渠道或曝光信息，无法识别广告的因果贡献", "Final Weekend 与 Previous Weekend 的对比及描述性拆解不能证明广告导致增长"]
- causal_04: priority_not_probability=True
  Evidence: ["P1 表示近期有购物车行为，是规则分层，不是购买概率模型", "本次数据不足以证明 P1 的购买概率高于 P2 或 P3"]
- causal_05: first_not_registered=True
  Evidence: ["newly observed 用户指在数据观察窗口中首次被观测到的用户，不能据此认定为新增注册用户", "增长分析工具将 newly_observed_users 定义为在最终周末活跃用户中、于观察窗口内首次观测到的用户，并明确注明这不是新注册用户"]
- causal_06: inactive_not_churn=True
  Evidence: ["购买沉寂不等于平台流失", "购买沉寂不等于平台流失"]

## Retained behavior / protocol failures

- diagnostic_03 / 为什么 Cart 增长不能证明购买意愿一定提升？: missing_tools=['get_weekend_growth']; unnecessary_tools=[]; intents expected/actual=['growth']/['funnel']; status expected/actual=answered/answered; failures=['intent_set', 'tool_set']
- boundary_01 / 这九天 GMV 是多少？: missing_tools=[]; unnecessary_tools=['get_platform_overview']; intents expected/actual=['unsupported']/['platform', 'unsupported']; status expected/actual=unsupported/unsupported; failures=['intent_set', 'tool_set']
- boundary_05 / 哪个具体商品类别表现最好？: missing_tools=[]; unnecessary_tools=[]; intents expected/actual=['unsupported']/['ambiguous']; status expected/actual=unsupported/ambiguous; failures=['intent_set', 'status']

Domain refusal and clarification ambiguity can both be defensible in prose; a status mismatch remains recorded under the independent unsupported contract.

## Evaluation-view differences and design conflicts

Cases that pass v2 but failed legacy（评估视图差异，不等于全部都是 evaluator bug）: direct_01, direct_02, direct_03, direct_04, direct_05, direct_06, direct_07, direct_08, direct_09, paraphrase_01, paraphrase_02, paraphrase_03, paraphrase_04, paraphrase_05, paraphrase_06, paraphrase_07, diagnostic_01, diagnostic_02, diagnostic_04, diagnostic_05, boundary_02, boundary_03, boundary_04, boundary_06, causal_01, causal_02, causal_03, causal_04, causal_05, causal_06, multi_intent_01, multi_intent_02, multi_intent_03, multi_intent_04
Multi-intent architecture conflicts: multi_intent_01, multi_intent_02, multi_intent_03, multi_intent_04
Exact intent set counts optional auxiliary labels as extras; overall explicitly permits declared auxiliary labels/tools. Exact tool set and required-tool policy are separately reported.

## Display-only mismatches

- direct_03 / previous_weekend_active_users: missing_numeric_display; displayed=[]; required=864829 ± 0
- direct_03 / active_user_growth_pct: missing_numeric_display; displayed=[]; required=14.0797 ± 5e-05
- direct_05 / p1_pairs: missing_numeric_display; displayed=[]; required=658968 ± 0
- direct_06 / treatment_purchase_rate: display_precision; displayed=['25.39']; required=25.3933 ± 5e-05
- direct_06 / control_purchase_rate: missing_numeric_display; displayed=[]; required=25.477 ± 5e-05

## Interpretation and caveats

真正行为问题与 evaluator 未识别措辞必须分开人工复核；predicate missing 不自动证明模型说错。
四个 multi-intent 的 legacy status/tool 失败属于能力范围设计冲突；差异还涉及合法同义表达、问题专属与全主题事实范围、boundary composite 及展示精度要求。不能把所有 legacy→v2 差异都归为 evaluator bug，也不能据分数变化推断模型能力提高。
当前数据是首次 run 的离线重评，没有新样本或重复模型试验。Predicates 是有限规则，不能全面理解反讽、复杂否定、跨句推理或所有因果断言；0 forbidden hits 只表示这些检测未命中。
34/37（91.89%）是本次评估视图的 case pass rate，不是 universal model accuracy。Numeric grounding 100% 不代表所有回答在语义上都完全正确。不能把本次事后 v2 分数当作事先冻结或泛化 benchmark；下一次独立评估应先冻结 v2 contracts 与 self-tests，再看新模型结果。
旧 token usage 为历史请求用量，无新增 API 请求；不推算费用。

## Provenance and integrity

```json
{
  "input": "ai_agent/evals/llm_results_latest.json",
  "input_sha256": "f45f225fcec181603b58e091bf7d68f053ddbb4220c14439490b7b5b5306efc2",
  "contracts_sha256": "42980f215344409307f5bc4b7443c3bd4b89d6b81346b6997003c5dae39f6ead",
  "baseline_hashes_unchanged": true,
  "evaluated_at": "2026-10-09T10:48:15.877307+00:00"
}
```

Historical usage: {"model_rounds": 78, "input_tokens": 310110, "output_tokens": 51229, "total_tokens": 361339}

本公开报告仅清理措辞与路径，未重新运行模型或评估；provenance 的输入哈希、contracts 哈希和评估时间保留原值。
