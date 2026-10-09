# Rule-based vs Controlled LLM Comparison

原 Golden Set：37 cases；原 expected_intent/tool/status/facts/forbidden claims 未改变。此表保留首次真实 LLM run 的 legacy strict 成绩；独立 [v2 报告](llm_evaluation_v2_report.md) 是基于同一 traces 事后建立的探索性视图，不覆盖本表，也不可直接横向比较。

| Metric | Frozen rule-based baseline | Real LLM |
|---|---:|---:|
| Intent Accuracy | 100.00% | 89.19% |
| Tool Selection Accuracy | 100.00% | 81.08% |
| Status Accuracy | 100.00% | 86.49% |
| Numeric Accuracy | 100.00% | 85.29% |
| Required Fact Coverage | 100.00% | 30.14% |
| Boundary Compliance | 100.00% | 0.00% |
| Forbidden Claim Rate | 0.00% | 0.00% |
| Overall Case Pass Rate | 100.00% | 0.00% |

## Real run details

Provider/model：{"provider": "openai", "requested_model": "gpt-6-luna", "transport": "responses_http"}。
错误 cases：0；数值来源通过率：100.00%。
失败：
- direct_01：平台一共有多少用户？；intent/tool/status=True/True/True；errors=[]
- direct_02：一共有多少条有效行为记录？；intent/tool/status=True/True/True；errors=[]
- direct_03：Final Weekend 有多少活跃用户？；intent/tool/status=True/True/True；errors=[]
- direct_04：机会品类有多少个？；intent/tool/status=True/True/True；errors=[]
- direct_05：P1 有多少去重用户？；intent/tool/status=True/True/True；errors=[]
- direct_06：Treatment purchase_user_rate 是多少？；intent/tool/status=True/True/True；errors=[]
- direct_07：p-value 是多少？；intent/tool/status=True/True/True；errors=[]
- direct_08：A/B Test 的 lift、CI 和 MDE 是多少？；intent/tool/status=True/True/True；errors=[]
- direct_09：两个周末的 pv_to_buy_rate 是多少？；intent/tool/status=True/True/True；errors=[]
- paraphrase_01：第二个周末为什么更活跃？；intent/tool/status=True/True/True；errors=[]
- paraphrase_02：12月2日至3日的增长来自哪里？；intent/tool/status=True/True/True；errors=[]
- paraphrase_03：最后一个周末流量为什么明显更高？；intent/tool/status=True/True/True；errors=[]
- paraphrase_04：weekend growth 是用户变多还是用户更活跃？；intent/tool/status=True/True/True；errors=[]
- paraphrase_05：final weekend conversion 怎么样？；intent/tool/status=True/True/True；errors=[]
- paraphrase_06：哪些 opportunity categories 最值得关注？；intent/tool/status=True/True/True；errors=[]
- paraphrase_07：A/B result significant 吗？；intent/tool/status=True/True/True；errors=[]
- diagnostic_01：为什么不能说第二个周末购买表现下降？；intent/tool/status=False/False/True；errors=[]
- diagnostic_02：哪个漏斗环节下降更明显？；intent/tool/status=True/True/True；errors=[]
- diagnostic_03：为什么 Cart 增长不能证明购买意愿一定提升？；intent/tool/status=False/False/True；errors=[]
- diagnostic_04：P3 人数最多，为什么不一定优先做 P3？；intent/tool/status=True/True/True；errors=[]
- diagnostic_05：为什么 P1 比 P3 更适合作为召回测试候选？；intent/tool/status=True/True/True；errors=[]
- boundary_01：这九天 GMV 是多少？；intent/tool/status=False/False/True；errors=[]
- boundary_02：哪个品类收入最高？；intent/tool/status=True/True/True；errors=[]
- boundary_03：哪个渠道带来的增长最多？；intent/tool/status=True/True/True；errors=[]
- boundary_04：用户长期复购率是多少？；intent/tool/status=True/True/True；errors=[]
- boundary_05：哪个具体商品类别表现最好？；intent/tool/status=False/True/False；errors=[]
- boundary_06：这次活动 ROI 是多少？；intent/tool/status=True/True/True；errors=[]
- causal_01：购物车召回提升了多少转化率？；intent/tool/status=True/True/True；errors=[]
- causal_02：A/B Test 证明召回无效了吗？；intent/tool/status=True/True/True；errors=[]
- causal_03：Final Weekend 增长是不是广告投放导致的？；intent/tool/status=True/True/True；errors=[]
- causal_04：P1 用户一定更容易购买吗？；intent/tool/status=True/True/True；errors=[]
- causal_05：newly observed 用户是不是新增注册用户？；intent/tool/status=True/True/True；errors=[]
- causal_06：购买沉寂用户是不是已经流失？；intent/tool/status=True/True/True；errors=[]
- multi_intent_01：告诉我平台规模和 A/B Test 结果。；intent/tool/status=True/False/False；errors=[]
- multi_intent_02：周末增长和机会品类分别是什么情况？；intent/tool/status=True/False/False；errors=[]
- multi_intent_03：帮我分析漏斗再给我 Top 5 category。；intent/tool/status=True/False/False；errors=[]
- multi_intent_04：哪些用户值得运营，以及实验是否显著？；intent/tool/status=True/False/False；errors=[]

LLM 数值 case 检查额外要求指定数字展示于最终回答的有效 numeric_evidence；原 baseline 数值成绩不追溯改变。

## Multi-intent comparability

原严格 37-case 分数仍要求四个 multi-intent cases 返回 ambiguous；LLM 若成功多工具回答，在该严格标准下会失败。这是能力范围差异，不能改写 baseline 历史成绩。
单独 supplement 为这四个 ID 声明预期工具组合，要求 answered、各任务事实、数值来源和边界合规，最多三次调用。它不改变原 Golden Set，也不混入原 overall score。
Supplement pass：0.00%。

## What to assess next

下一次先冻结 v2 contracts，再运行新的独立模型结果，重点检查 paraphrase、中英混合、错误前提、多意图与自然解释；逐项查看错工具、错参数、来源缺失、数值幻觉、因果越界与数据边界回退，不只看总分。
数值来源核验可阻止无来源字面量，却不能全面判断单位语义或同值不同义；禁断言正则仅覆盖有限措辞。首次真实结果不代表任意问题的准确率；37/37 grounding 不能证明语义完全正确，零 forbidden hits 只表示当前规则没有命中违规。V2 的 34/37（91.89%）不是 universal model accuracy，不能把全部视图差异归为 evaluator bug。

## Integrity

llm_baseline_manifest.json 冻结 agent.py、reasoning、tools、原评估器、Golden Set、expected_values 和历史 baseline report/results。每次比较前检查文件哈希。
可用 --verify-baseline 实际重跑原评估器；程序保留新结果为本轮验证证据，并恢复原冻结 report/results 字节，避免重写历史。
真实 LLM trace/results 为动态数据，保存在 runs/ 或 llm_results*.json，均已忽略；日志不保存 key。
