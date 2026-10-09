import math
from statistics import NormalDist
from ._common import connection, rows, prepare, metadata

def power_for_effect(n_per_arm: int, baseline: float, uplift: float) -> float:
    normal = NormalDist()
    z_critical = normal.inv_cdf(0.975)
    alternative = baseline + uplift
    mean_rate = (baseline + alternative) / 2
    critical = z_critical * math.sqrt(2 * mean_rate * (1 - mean_rate) / n_per_arm)
    alternative_se = math.sqrt(
        (baseline * (1 - baseline) + alternative * (1 - alternative)) / n_per_arm
    )
    return normal.cdf((-critical - uplift) / alternative_se) + normal.cdf(
        (uplift - critical) / alternative_se
    )

def get_ab_test_summary():
    with connection() as con:
        prepare(con, '07_ab_test_design.sql')
        groups = rows(con, """SELECT experiment_group,experiment_users AS assigned_users,
          purchase_users,purchase_rate*100 AS purchase_user_rate FROM ab_group_metrics""")
    arms = {g['experiment_group'].lower(): {k:v for k,v in g.items() if k!='experiment_group'} for g in groups}
    t, c = arms['treatment'], arms['control']
    nt, nc, xt, xc = t['assigned_users'], c['assigned_users'], t['purchase_users'], c['purchase_users']
    if min(nt,nc) <= 0: raise ValueError('Empty experiment arm')
    pt, pc = xt/nt, xc/nc
    difference = pt-pc
    pooled = (xt+xc)/(nt+nc)
    null_se = math.sqrt(pooled*(1-pooled)*(1/nt+1/nc))
    if null_se == 0 or not 0 < pc < 1: raise ValueError('Degenerate rates; z-test/MDE undefined')
    p_value = math.erfc(abs(difference/null_se)/math.sqrt(2))
    ci_se = math.sqrt(pt*(1-pt)/nt+pc*(1-pc)/nc)
    low, high = 1e-10, min(0.2,(1-pc)*0.9)
    if power_for_effect(min(nt,nc),pc,high)<0.80: raise ValueError('MDE outside search interval')
    for _ in range(70):
        middle = (low+high)/2
        if power_for_effect(min(nt,nc),pc,middle)>=0.80: high=middle
        else: low=middle
    return {**arms, 'absolute_lift_pp': difference*100,
      'relative_lift_pct': (pt/pc-1)*100, 'p_value':p_value,
      'ci_lower_pp':(difference-1.96*ci_se)*100, 'ci_upper_pp':(difference+1.96*ci_se)*100,
      'mde_pp':high*100, 'experiment_type':'simulated_assignment',
      'caveat':'没有真实 treatment / exposure，不能解释为购物车召回真实因果效果，不能证明召回有效或无效。',
      'metadata':metadata('sql/07_ab_test_design.sql','dashboard/export_bi_data.py'),
      'definitions':{'purchase_user_rate':'12/02–12/03 至少一次 buy 用户 / 全部 assigned_users，百分比',
        'eligibility':'仅 11/25–12/01 有 cart、无 buy、最后活跃不早于 11/30；MD5 固定盐分组',
        'statistics':'双侧 pooled 两比例 z 检验；unpooled Wald 95% CI；alpha .05',
        'mde':'control 基线、power .80、1:1、较小组人数；正向 MDE，不是预期效果'}}
