"""Deterministic rule-based prototype, not an autonomous or LLM agent."""
import argparse
import json
import re
import sys
from .reasoning import explain, explain_unavailable
from .tools import (get_platform_overview, get_weekend_growth, get_funnel_comparison,
                    get_opportunity_analysis, get_ab_test_summary)

TOOLS = {'platform': get_platform_overview, 'growth': get_weekend_growth,
         'funnel': get_funnel_comparison, 'opportunity': get_opportunity_analysis,
         'experiment': get_ab_test_summary}
RULES = {
    'experiment': ('a/b', 'ab test', 'a b test', 'ab result', 'experiment', '实验',
                   '显著', 'significant', 'p-value', 'purchase_user_rate',
                   'treatment', 'control'),
    'funnel': ('funnel', 'conversion', '漏斗', '转化', '环节', '购买表现',
               'pv_to_buy', 'pv_to_intent', 'intent_to_buy'),
    'opportunity': ('opportunity', 'priority', '召回', '运营', '机会', '品类',
                    'category', 'categories', '哪类用户', '哪些用户'),
    'growth': ('growth', '增长', '上涨', '流量', '更活跃', '用户变多',
               'newly observed', '首次观测'),
    'platform': ('platform', 'overview', '平台', '总览', '规模', '多少用户',
                 '事件数', '用户数', '有效行为', '行为记录', 'total_events', 'total_users'),
}
CAVEATS = ['buy event ≠ order；仅九天观察，不能推断长期行为。',
           '无 price / GMV、真实 promotion / channel / exposure；不能把相关性解释成因果。',
           'newly observed ≠ new registered user；category_id 匿名。']
UNSUPPORTED = ('gmv', 'roi', '收入', '营收', '长期', '真实品类', '渠道', '促销',
               '具体商品类别', '真实商品类别', '购买沉寂', '流失',
               '删除', '修改数据库', '写入数据库', '更新数据库', '执行sql', '执行 sql')


def _clause_intents(text):
    """Separate explicit analytic topics from context and subordinate metrics."""
    matches = {intent for intent, words in RULES.items() if any(w in text for w in words)}
    if re.search(r'\b(?:mde|ci|lift)\b', text):
        matches.add('experiment')
    if re.search(r'\bp[123]\b', text):
        matches.add('opportunity')
    if ('召回' in text and re.search(r'提升|有效|无效|效果|因果|带来', text)):
        matches.add('experiment')
    # Recall effect questions have experiment-specific metrics, not a second task.
    if 'experiment' in matches:
        matches.discard('funnel')
        if '召回' in text and not any(w in text for w in ('哪些用户', '哪类用户', '机会品类', '运营')):
            matches.discard('opportunity')
    weekend_context = 'weekend' in text or '周末' in text
    if weekend_context and not matches:
        matches.add('growth')
    # Generic platform wording can describe the scope of a specific analysis.
    if len(matches) > 1 and 'platform' in matches and not any(
            w in text for w in ('平台规模', '平台总览', '总事件', 'total_events', 'total_users')):
        matches.discard('platform')
    return matches


def detect_intent(question):
    text = question.casefold().strip()
    if any(word in text for word in UNSUPPORTED) or re.search(
            r'\b(?:delete|drop|update|insert|alter|truncate)\b', text):
        return 'unsupported'
    # An observed-vs-registered comparison is answerable as a boundary explanation;
    # requests for registration counts/dates remain unsupported.
    if '注册' in text and not (
            any(w in text for w in ('newly observed', '首次观测'))
            and any(w in text for w in ('是不是', '是否等于', '等同', '不等于', '区别'))):
        return 'unsupported'
    # Recognize independent tasks before applying within-clause disambiguation.
    clauses = [c for c in re.split(r'[，,；;。]|以及|分别|再给|同时|和|与', text) if c.strip()]
    clause_topics = set().union(*(_clause_intents(c) for c in clauses)) if clauses else set()
    if len(clause_topics) > 1:
        return 'ambiguous'
    matches = _clause_intents(text)
    if len(matches) > 1:
        return 'ambiguous'
    return next(iter(matches)) if matches else 'unsupported'

def analyze(question, top_n=5):
    intent = detect_intent(question)
    if intent not in TOOLS:
        return {'status': intent, 'detected_intent': intent, 'tool_used': None, 'key_data': {},
                'interpretation': explain_unavailable(intent),
                'caveats': CAVEATS, 'routing_type': 'deterministic_rule_based_prototype'}
    tool = TOOLS[intent]
    data = tool(top_n=top_n) if intent=='opportunity' else tool()
    return {'status': 'answered', 'detected_intent': intent, 'tool_used': tool.__name__, 'key_data': data,
            'interpretation': explain(intent,data),
            'caveats': CAVEATS + ([data['caveat']] if intent=='experiment' else []),
            'routing_type': 'deterministic_rule_based_prototype'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('question')
    parser.add_argument('--top-n',type=int,default=5)
    args = parser.parse_args()
    try:
        result = analyze(args.question, args.top_n)
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        return 0 if result['status'] == 'answered' else 2
    except Exception as error:
        print(json.dumps({'error':type(error).__name__,'message':str(error)},ensure_ascii=False),file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
