"""Finite, conservative semantic rules for answer prose only (no model/API)."""
import re

PROSE_FIELDS = ('answer', 'facts', 'analysis', 'candidate_actions', 'caveats')

def prose(answer):
    return '\n'.join(v if isinstance(v, str) else '\n'.join(v)
                     for k in PROSE_FIELDS if (v := answer.get(k)))

# Each alternative requires ALL patterns in one sentence; no metadata fallback.
# Unknown concepts fail closed. These are bounded lexical predicates, not an NLP judge.
RULES = {
 'buy_not_order': [[r'buy|购买行为|购买事件', r'不等于|不是|≠|而非|不等同', r'订单|order']],
 'simulated': [[r'simulated_assignment|模拟(?:分组|分配|分桶)']],
 'no_exposure': [[r'没有真实|无真实|不代表真实', r'treatment|处理|触达', r'exposure|曝光|触达']],
 'no_effect': [[r'不能|无法|不代表|不是', r'召回|处理|干预|触达', r'因果|效果|有效|无效']],
 'not_same': [[r'不显著|未达.*显著|未观察到.*差异', r'不等于|不代表', r'相同|为零']],
 'priority_not_probability': [[r'P1|分层|优先级', r'不是|不代表|不足以证明|不能', r'购买概率|概率排序|概率预测']],
 'size_not_priority': [[r'人数|规模|覆盖', r'不代表|不应|不能|不等于', r'概率|优先|回报|意向']],
 'first_not_registered': [[r'首次.*观测|newly observed', r'不等于|不是|不能.*认定|不等同', r'注册']],
 'inactive_not_churn': [[r'购买沉寂', r'不等于|不能.*(?:判定|推断)|无法判断', r'流失|churn']],
 'anonymous': [[r'匿名', r'category_id|品类|类别|标识|ID']],
 'no_gmv': [[r'无法|不能', r'GMV'], [r'没有|不包含|缺少', r'价格|金额', r'GMV']],
 'no_income': [[r'无法|不能', r'收入|营收'], [r'没有|缺少', r'价格|GMV']],
 'no_channel': [[r'无法|不能', r'渠道', r'归因|带来|贡献']],
 'no_long_repeat': [[r'无法|不能|不足|不应', r'长期', r'短期|窗口|范围|数据']],
 'no_roi': [[r'无法|不能', r'ROI', r'数据|价格|成本|GMV']],
 'no_ad_cause': [[r'无法|不能', r'广告', r'因果|导致|贡献']],
 'scale_intensity': [[r'规模|用户', r'扩大|增长|更多|变多', r'人均|每位', r'上升|增长|更高|增至']],
 'absolute_relative': [[r'购买|buy', r'事件|用户', r'上升|增至|增长'], [r'意向', r'购买', r'比例|转化', r'下降|负|变化.*-']],
 'intent_buy_largest': [[r'意向.*购买', r'更明显|最大|降幅']],
 'cart_not_proof': [[r'Cart|加购', r'不能证明|不等于|不是一回事', r'购买|意愿|转化']],
 'priority_signals': [[r'P1', r'加购|购物车|cart', r'P3', r'浏览']],
 'daily_funnel': [[r'每日|同日', r'用户集合|用户.*比例', r'均值|平均']],
 'top_categories': [[r'品类|类目|category_id', r'排序|依次|优先关注|候选']],
 'mde_not_effect': [[r'MDE', r'不是|不应|不等于', r'预期|效果']],
 'not_significant': [[r'未达到统计显著|不显著|未达.*显著|未显示.*统计差异']],
}
# Prevent passing a denial when the same answer also asserts its opposite.
CONTRADICTIONS = {
 'buy_not_order': r'(?:buy event|购买行为事件)\s*(?:就是|等于|is|=)\s*(?:订单|order)',
 'priority_not_probability': r'P1\s*用户(?:一定|必然)(?:更容易)?购买',
 'first_not_registered': r'(?:newly observed|首次观测)\s*(?:用户)?(?:就是|等于)\s*(?:新增)?注册用户',
 'inactive_not_churn': r'购买沉寂用户(?:已经|均已|就是)流失',
 'anonymous': r'匿名.{0,20}(?:可以|能够|能).{0,20}(?:识别|对应|揭示).{0,10}(?:真实|名称)',
 'not_same': r'不显著(?:就|即|意味着|等于)(?:无效|两组相同|效果为零)',
}

def concept_check(answer, concept):
    if concept not in RULES: raise ValueError('Unknown semantic predicate: '+concept)
    body=prose(answer)
    # Sentence boundaries retain commas so conjunctions can be expressed naturally.
    sentences=re.split(r'[。；;\n]', body)
    def match(pattern, sentence): return re.search(pattern, sentence, re.I) is not None
    positives=[s for s in sentences if any(all(match(p,s) for p in group) for group in RULES[concept])]
    # Obvious double negations/questions are not accepted evidence for a denial.
    positives=[s for s in positives if not re.search(r'并非不能|不是不能|不能不|不是没有|并非没有|并非不是|不是不|难道|是否|吗[？?]?',s)]
    if concept=='absolute_relative':
        # Both absolute volume increase AND relative conversion decline required.
        positives=[body] if all(any(all(match(p,s) for p in group) for s in sentences) for group in RULES[concept]) else []
    contradiction=[]
    for s in sentences:
        for m in re.finditer(CONTRADICTIONS.get(concept, r'(?!)'),s,re.I):
            prefix=re.split(r'[，,]|但是|然而|但|却',s[:m.start()])[-1]
            if not re.search(r'不能|不应|无法|不是|不得',prefix): contradiction.append(s)
    return {'predicate':concept,'passed':bool(positives) and not contradiction,
            'matched_prose':positives,'contradictions':contradiction}
