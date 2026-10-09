# Explanation layer：修改前失败定位

依据：完整读取 `evaluation_cases.json` 的 37 个定义，并核对 `evaluation_report.md` 与此前实际响应。保存修改前 30/37（81.08%）记录为 `evaluation_results_explanation_before.json`。不调整 expected facts 或 forbidden claims。

| ID / question | 当前回答缺什么 | 失败类型 | 现有工具数据足够吗 | 解释层未使用信息的原因 |
|---|---|---|---|---|
| diagnostic_01：为什么不能说第二个周末购买表现下降？ | 购买人数增加与用户集合比例下降可以同时发生 | required_fact | 是；avg_buy_users、pv_to_buy_rate 均已返回 | 固定模板只遍历率，没有将人数与比例关系关联 |
| diagnostic_02：哪个漏斗环节下降更明显？ | 两个相邻环节的百分点降幅比较 | required_fact | 是；pv_to_intent_rate、intent_to_buy_rate | 已输出各项差值，却不进行大小比较；完整漏斗/直接购买率不应当作额外相邻环节 |
| diagnostic_03：为什么 Cart 增长不能证明购买意愿一定提升？ | 区分行为次数、参与强度与潜在购买意愿；禁止因果推断 | required_fact | 足够描述事件与人均强度变化；不足以确认心理意愿或因果 | 通用增长模板只讨论规模/强度及渠道归因，未解释行为代理指标边界 |
| diagnostic_04：P3 人数最多，为什么不一定优先做 P3？ | 规模与优先级是不同维度，强度规则与预计收益不可混用 | required_fact | 是；三层 distinct_users、pairs、平均行为与 definitions | 只输出 P1，未比较层规模或解释纯浏览门槛 |
| diagnostic_05：为什么 P1 比 P3 更适合作为召回测试候选？ | P1 cart 与 P3 无 cart/fav、纯浏览的信号差异 | required_fact | 是；definitions.P1/P3、avg_cart/avg_fav、动态 PV 门槛 | 没有把三个层的结构化规则映射到候选动作 |
| causal_04：P1 用户一定更容易购买吗？ | 规则型运营优先级不是购买概率模型；不保证真实购买率高 | required_fact + Boundary Compliance | 足够说明规则；没有未来 outcome 或校准购买概率 | “不是收益预测”没有明确回应购买概率与保证性错误前提 |
| causal_06：购买沉寂用户是不是已经流失？ | 无法判断长期流失，短窗口未买不等于平台不活跃 | required_fact + Boundary Compliance | 否；unsupported 的 key_data 为空，五个工具不返回沉寂群体活跃数据 | 通用拒答 analysis 为空，没有给短窗口与长期结论的通用限制 |

沉寂问题继续保持 unsupported、零工具调用。不引入 SQL 06 查询或从项目文字复制 97.67% 等数值。只给一般性的条件说明：**如果**最后一天仍有平台行为，则仍有活跃证据；当前未查询该群体，不能断言实际发生。

## 可复用模式

1. Absolute vs Relative Change：根据人数与率分别判断上升/下降/持平/缺失，避免只看比例。
2. Rate Change Comparison：比较相邻漏斗步骤的百分点差，处理并列、无下降、缺失，不声称显著性或因果。
3. Priority ≠ Probability：规则意向信号用于候选优先级，不是预测或保证购买。
4. Inactivity ≠ Churn：观察窗口内未买、平台不活跃、长期流失分别处理；没有群体数据时只作条件解释。
5. Association ≠ Causality：事件共现或增长不足以识别心理意愿、活动贡献或因果。
6. Simulated Experiment ≠ Treatment Effect：依据 experiment_type，分开报告统计差异与真实策略效果。
7. Scale ≠ Priority：根据返回的各层规模决定最大层，但不以人数决定运营优先级；人数跨层不可相加。

这些规则接收结构化数据与固定数据边界，不读取原始 question，也不读取 Golden Set 或 expected_values。
