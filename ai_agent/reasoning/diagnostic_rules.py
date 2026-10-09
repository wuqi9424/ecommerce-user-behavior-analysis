"""Business reasoning over tool results. No question text, SQL, or test snapshots."""
from math import isfinite

# Policy constants are analysis settings, not observed business measurements.
POLICIES = {'significance_alpha': 0.05, 'confidence_level_pct': 95}


def resolve(data, path):
    for segment in path.split('/'):
        if isinstance(data, list):
            key, value = segment.split('=', 1)
            found = [row for row in data if str(row.get(key)) == value]
            if len(found) != 1:
                raise ValueError(f'Expected one row for {segment}, got {len(found)}')
            data = found[0]
        else:
            data = data[segment]
    return data


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def calculate(data, operation, paths, policy=None):
    """Small allowlist of derived numbers; evaluation independently recomputes it."""
    if operation == 'policy':
        if paths or policy not in POLICIES:
            raise ValueError('Invalid policy evidence')
        return POLICIES[policy]
    values = [resolve(data, path) for path in paths]
    if not all(finite(value) for value in values):
        raise ValueError('Missing or nonfinite numeric evidence')
    if operation == 'identity' and len(values) == 1:
        return values[0]
    if operation == 'difference' and len(values) == 2:
        return values[1] - values[0]
    if operation == 'pct_change' and len(values) == 2 and values[0] != 0:
        return (values[1] / values[0] - 1) * 100
    raise ValueError(f'Unsupported derivation: {operation}')


class Evidence:
    """Render all quantitative literals from data paths or named policy constants."""
    def __init__(self, data):
        self.data = data
        self.entries = []

    def number(self, path=None, *, operation='identity', paths=None,
               policy=None, format_spec='.4f', unit=''):
        paths = paths if paths is not None else ([path] if path else [])
        value = calculate(self.data, operation, paths, policy)
        if format_spec not in (',.0f', '.4f', '.2f', '.0f'):
            raise ValueError('Unsupported numeric display format')
        display = format(value, format_spec)
        self.entries.append({'operation': operation, 'paths': paths, 'policy': policy,
                             'value': value, 'display': display,
                             'format_spec': format_spec, 'unit': unit})
        return display


def change_direction(before, after):
    if not finite(before) or not finite(after):
        return '无法比较'
    if after > before:
        return '增加'
    if after < before:
        return '下降'
    return '持平'


def absolute_vs_relative(before_volume, after_volume, before_rate, after_rate):
    volume = change_direction(before_volume, after_volume)
    rate = change_direction(before_rate, after_rate)
    if volume == '无法比较' or rate == '无法比较':
        return '人数或比例缺失，无法完整比较购买规模与购买承接。'
    return (f'购买人数{volume}，浏览→购买比例{rate}；绝对购买规模与相对比例需要分别判断。'
            '不能仅凭比例变化概括整个购买表现，也不能仅凭人数变化概括转化表现。')


def compare_adjacent_rates(previous, final):
    labels = {'pv_to_intent_rate': '浏览→意向', 'intent_to_buy_rate': '意向→购买'}
    deltas = {key: final[key] - previous[key] for key in labels
              if finite(previous.get(key)) and finite(final.get(key))}
    if len(deltas) != len(labels):
        return '相邻环节比例缺失，无法完整比较降幅。'
    negative = {key: value for key, value in deltas.items() if value < 0}
    if not negative:
        return '两个相邻环节均未观察到比例下降。'
    worst = min(negative.values())
    leaders = [key for key, value in negative.items() if abs(value - worst) <= 1e-9]
    if len(leaders) > 1:
        return '两个相邻环节的百分点降幅相同。'
    return (f'{labels[leaders[0]]}的百分点降幅更大（相邻环节比较）；'
            '这是描述性差异，不是统计显著性或因果贡献比较。')


def association_not_causality():
    return ('Cart 等行为次数及人均强度变化是可观察信号，不能证明购买意愿一定提升；'
            '同一用户可能重复产生事件，事件次数不是意向用户转化率。'
            '行为共现、规模变化与时间变化不能把相关性解释成因果；'
            '缺少真实渠道、活动和曝光，无法判断增长原因或策略贡献。')


def priority_not_probability():
    return ('P1/P2/P3 是 rule-based operational priority，不是购买概率模型；'
            '加购是比纯浏览更接近购买动作的行为信号，适合优先形成测试假设，'
            '并不证明真实购买概率更高，不能确定购买概率或保证未来购买。')


def scale_not_priority(priorities):
    known = [row for row in priorities if finite(row.get('distinct_users'))]
    if len(known) != len(priorities) or not known:
        return '分层人数缺失，无法比较规模；规模不等于优先级。'
    maximum = max(row['distinct_users'] for row in known)
    largest = '、'.join(sorted(row['priority_segment'] for row in known
                              if row['distinct_users'] == maximum))
    return (f'{largest} 的去重用户规模最大；规模不等于优先级。'
            '资源分配还需考虑意向信号、可触达性、成本与真实增量，当前没有收益证据。')


def inactivity_not_churn():
    # No segmentation tool result is available. Conditional explanation only.
    return ('短窗口内没有 buy 不等于长期流失，购买沉寂不等于平台流失；'
            '九天观察不能据此判定流失。若最后一天仍有平台行为，则仍存在活跃行为。'
            '当前工具未返回沉寂群体的最近活跃数据，无法判断其实际活跃状态。')


def simulated_not_treatment_effect(data):
    if data.get('experiment_type') == 'simulated_assignment':
        return ('simulated_assignment 没有真实 treatment / exposure，不能估计真实 treatment effect；'
                '不能据此证明购物车召回有效或无效。模拟标签下的 lift 是描述性组间差异。')
    return '当前只支持 simulated_assignment；实验类型无法识别，不能据此解释真实策略效果。'


def result(answer, facts, analysis, actions, caveats, evidence):
    return {'answer': answer, 'facts': facts, 'analysis': analysis,
            'candidate_actions': actions, 'caveats': caveats,
            'numeric_evidence': evidence.entries}


def explain_platform(data):
    e = Evidence(data)
    facts = [f"事件 {e.number('total_events',format_spec=',.0f')}，观测用户 {e.number('total_users',format_spec=',.0f')}，购买用户 {e.number('purchasing_users',format_spec=',.0f')}。"]
    return result('当前九天样本的平台规模如下。', facts,
                  '事件规模与去重用户规模分别计数，buy 不能作为订单数。', [],
                  ['九天公开样本不代表平台全量或长期行为。'], e)


def weekend_rows(data):
    return (resolve(data, 'periods/period_label=previous_weekend'),
            resolve(data, 'periods/period_label=final_weekend'))


def explain_growth(data):
    previous, final = weekend_rows(data)
    e = Evidence(data)
    p = 'periods/period_label=previous_weekend/'
    f = 'periods/period_label=final_weekend/'
    scale = change_direction(previous.get('active_users'), final.get('active_users'))
    intensity = change_direction(previous.get('events_per_active_user'), final.get('events_per_active_user'))
    if scale == '增加' and intensity == '增加':
        relationship = '规模与人均强度共同增长'
    else:
        relationship = f'用户规模{scale}，人均强度{intensity}'
    facts = [
        f"活跃用户 {e.number(p+'active_users',format_spec=',.0f')} → {e.number(f+'active_users',format_spec=',.0f')}，增长 {e.number(f+'active_user_growth_pct')}%。",
        f"总事件增长 {e.number(f+'total_event_growth_pct')}%；人均事件 {e.number(p+'events_per_active_user')} → {e.number(f+'events_per_active_user')}。",
        f"Final 首次观测用户 {e.number('newly_observed_users',format_spec=',.0f')}；此前观测用户 {e.number('previously_observed_users',format_spec=',.0f')}。",
    ]
    for metric in ('cart_events', 'buy_events'):
        facts.append(f"{metric}: {e.number(p+metric,format_spec=',.0f')} → {e.number(f+metric,format_spec=',.0f')}，事件增长 {e.number(operation='pct_change',paths=[p+metric,f+metric])}%。")
    return result(relationship+'；实际业务原因无法判断。', facts,
                  relationship+'是描述性分解。'+association_not_causality(),
                  ['补充渠道、曝光与用户行为路径；对购买承接候选策略开展真实实验。'],
                  ['首次观测不是新注册用户；固定顺序 bridge 不是因果贡献率。'], e)


def explain_funnel(data):
    previous, final = weekend_rows(data)
    e = Evidence(data)
    p = 'periods/period_label=previous_weekend/'
    f = 'periods/period_label=final_weekend/'
    facts = []
    for name in ('avg_pv_users','avg_intent_users','avg_buy_users'):
        if finite(previous.get(name)) and finite(final.get(name)):
            facts.append(f"{name}: {e.number(p+name,format_spec='.2f')} → {e.number(f+name,format_spec='.2f')}（日均去重用户）。")
    for name in ('pv_to_intent_rate','intent_to_buy_rate','pv_to_buy_rate','full_funnel_rate'):
        if finite(previous.get(name)) and finite(final.get(name)):
            facts.append(f"{name}: {e.number(p+name)}% → {e.number(f+name)}%；变化 {e.number(operation='difference',paths=[p+name,f+name])} 个百分点。")
    summary = absolute_vs_relative(previous.get('avg_buy_users'),final.get('avg_buy_users'),
                                   previous.get('pv_to_buy_rate'),final.get('pv_to_buy_rate'))
    comparison = compare_adjacent_rates(previous, final)
    return result(summary+' '+comparison, facts,
                  '这是每日无序用户集合比例的等权均值；'+summary+' '+comparison,
                  ['优先调查描述性降幅较大的相邻环节；补充路径与曝光，再验证候选策略。'],
                  ['无法确认同商品顺序路径或下降原因；漏斗比例下降不证明流程故障。'], e)


def explain_opportunity(data):
    e = Evidence(data)
    definitions = data['definitions']
    priorities = data['priorities']
    facts = [f"机会品类 {e.number('opportunity_categories',format_spec=',.0f')}。"]
    for row in priorities:
        segment = row['priority_segment']
        path = 'priorities/priority_segment='+segment+'/'
        facts.append(f"{segment}: {e.number(path+'user_category_pairs',format_spec=',.0f')} 个组合、{e.number(path+'distinct_users',format_spec=',.0f')} 名去重用户；规则：{definitions[segment]}。")
    # Policy recommendation rests on the declared signal rule, never group size.
    cart_candidates = [row['priority_segment'] for row in priorities
                       if definitions.get(row['priority_segment']) == '有近期 cart'
                       and row.get('user_category_pairs',0)>0]
    if cart_candidates:
        recommendation = '、'.join(cart_candidates)+' 加购未买组合可作为优先召回测试候选。'
    else:
        recommendation = '当前未找到有明确加购规则的非空候选层，不能给出购物车召回优先名单。'
    return result(recommendation, facts,
                  priority_not_probability()+' P1 有 cart；P2 无 cart、有 fav；P3 为无 cart/fav 的纯浏览门槛组合。'
                  +scale_not_priority(priorities),
                  ['按规则意向信号选择候选，再核对可触达性、按用户去重并控制触达频次。',
                   '在观察窗口结束之后测试召回、收藏提醒或推荐；价格与权益提醒需补实时数据。'],
                  ['匿名品类没有真实商品名；跨层去重用户不能相加。',
                   '按 pair 等权的行为均值不是购买概率；品类差异不是策略因果效果。'], e)


def explain_experiment(data):
    e = Evidence(data)
    alpha = POLICIES['significance_alpha']
    significant = data['p_value'] < alpha
    answer = ('模拟分组存在统计差异。' if significant else '模拟分组差异未达到显著性门槛。')
    answer += '不能据此证明购物车召回有效或无效。'
    facts = [
        f"Treatment {e.number('treatment/purchase_user_rate')}%；Control {e.number('control/purchase_user_rate')}%；p={e.number('p_value')}。",
        f"绝对 lift {e.number('absolute_lift_pp')} pp；{e.number(operation='policy',policy='confidence_level_pct',format_spec='.0f')}% CI [{e.number('ci_lower_pp')}, {e.number('ci_upper_pp')}] pp；MDE {e.number('mde_pp')} pp。",
        f"检验门槛 alpha={e.number(operation='policy',policy='significance_alpha')}。",
    ]
    contains_zero = data['ci_lower_pp'] <= 0 <= data['ci_upper_pp']
    ci_note = '置信区间包含零。' if contains_zero else '置信区间不包含零。'
    significance_note = '不显著不等于两组完全相同。' if not significant else '显著性不等于业务收益或真实策略因果效果。'
    return result(answer, facts, simulated_not_treatment_effect(data)+ci_note+significance_note,
                  ['预设观察窗口、样本量、主指标与护栏；记录真实送达和曝光后再评估召回。'],
                  ['MDE 是规划的检测能力，不是预期效果或扩量门槛。'], e)


EXPLAINERS = {'platform':explain_platform, 'growth':explain_growth, 'funnel':explain_funnel,
              'opportunity':explain_opportunity, 'experiment':explain_experiment}


def explain(intent, data):
    return EXPLAINERS[intent](data)


def explain_unavailable(intent):
    e = Evidence({})
    if intent == 'ambiguous':
        return result('问题涉及多个分析意图，请拆成单一主题问题。', [],
                      '当前原型不支持多工具编排，不能只回答其中一部分。', [], [], e)
    return result('无法判断：问题超出当前数据边界或支持范围。', [],
                  inactivity_not_churn()+' 无价格、收入、渠道或真实曝光数据，也无法推断这些指标或因果原因。',
                  [], ['没有调用工具，不提供未经查询支持的群体数值或实际活跃结论。'], e)
