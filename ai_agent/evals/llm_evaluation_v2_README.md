# Independent offline LLM evaluator v2

仅读取 `llm_results_latest.json` 内嵌的 37 份真实运行 traces；不运行 agent、不调用 API、不查询数据库。原 Golden、冻结 evaluator 和 legacy results/report 保留。

```bash
python -m ai_agent.evals.run_llm_evaluation_v2
python -m unittest ai_agent.evals.test_llm_evaluation_v2 -v
```

可指定其他已保存的完整 37-case results：`--input /path/to/results.json`。输出为 `llm_results_v2_latest.json` 与 `llm_evaluation_v2_report.md`，退出 0 表示离线评估成功完成，并不表示全部模型 case 通过；真实失败保留在结果中。

`llm_v2_contracts.json` 是独立评估视图，声明各问题的核心任务、必需/可选工具及意图、状态、正文事实和专属边界。原 Golden 不变。四个 multi-intent 在新视图中允许 answered；绝对量分析可用 growth 辅助 funnel，Cart 诊断仍必须具备 growth，funnel 不能替代。

Exact set 指标严格比较完整集合；overall 的 required-set 规则允许已声明的合理辅助工具/意图。Micro intent P/R/F1 汇总标签，包含 unsupported/ambiguous。选用 exact set 与 overall 时应同时说明两者规则差异。

Numeric source 重用现有 ledger/source/unit/display/direction checker 离线重算，不直接信任 trace 的 passed 标记，不混入因果/语义判定。Golden 原 17 个 numeric display requirement 保留原 tolerance，并检查精确来源字段，而非仅查同工具相近数值。缺数字、精度不足、源值缺失分别记录；正确的两位小数回答可通过 source metric，但不能通过要求四位精度的 display metric。Display metric 单独保留全部历史要求，不强制每个任务回答整个主题的额外数字。

Semantic predicates 只读取 answer/facts/analysis/candidate_actions/caveats，不读 evidence 字符串或 tool metadata/definitions。用有限同义 regex 组、局部否定及部分矛盾断言检测；未知 predicate fail closed。数值事实必须有对应实际 call/field 的 identity evidence、通过 grounding 且显示在正文中。Tool-result availability 单列任务数值事实可用性；它不是正文 coverage。

Boundary 对 12 个 boundary/causal case 单独检查专属规则，不依赖其他事实、工具或状态。Forbidden metric 使用原禁止声明规则，仅对正文计算，命名为违规 case 数/rate；零是好结果。Overall 另要求 case-specific facts、required sets、status、参数、grounding、预算、无错误和禁断言；与 legacy strict case pass 并列。

自检的协议数据是 synthetic fixtures，不依赖模型某次具体用语；另有保存 run 的离线 integration checks，无数据时这些 integration checks 跳过。测试拦截 network、agent.run、DuckDB 连接，避免误执行。运行前后校验 manifest 中所有冻结文件及原 LLM results/report 字节。

这是首次 run 之后设计的探索性评估，不是预注册/held-out benchmark。有限 regex 不保证完整语义理解、复杂否定或全部因果检测；来源合法不证明文字结论含义正确。未来模型比较应先冻结此独立 contract，并增加未见措辞和反例。所有旧成绩仍是原严格历史成绩。

本轮验证：27 个 v2 self-tests 通过，连同既有离线协议/grounding/reasoning 回归共 116 个 tests 通过。仅对保存 traces 离线复评，manifest 与原 LLM results/comparison report 哈希保持不变。
