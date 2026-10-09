from ._common import connection, rows, prepare, metadata

def get_weekend_growth():
    with connection() as con:
        prepare(con, '09_growth_decomposition.sql')
        periods = rows(con, """SELECT *,total_events*1.0/active_users AS events_per_active_user,
          pv_events*1.0/active_users AS pv_per_active_user,
          fav_events*1.0/active_users AS fav_per_active_user,
          cart_events*1.0/active_users AS cart_per_active_user,
          buy_events*1.0/active_users AS buy_per_active_user
          FROM gd_summary ORDER BY period_label DESC""")
        bridge = rows(con, 'SELECT * FROM gd_bridge ORDER BY metric')
        cohorts = rows(con, 'SELECT * FROM gd_cohorts ORDER BY observed_group')
    previous, final = periods
    for period in periods:
        period['active_user_growth_pct'] = None if period is previous else (final['active_users']/previous['active_users']-1)*100
        period['total_event_growth_pct'] = None if period is previous else (final['total_events']/previous['total_events']-1)*100
    groups = {r['observed_group']: r['users'] for r in cohorts}
    return {'periods': periods, **groups, 'cohorts': cohorts, 'descriptive_bridge': bridge,
      'metadata': metadata('sql/09_growth_decomposition.sql'),
      'definitions': {'previous_weekend': '2017-11-25–2017-11-26（两天去重）',
        'final_weekend': '2017-12-02–2017-12-03（两天去重）',
        'newly_observed_users': 'Final 活跃用户中九天首次观测日期不早于 12/02；不是新注册用户',
        'intensity': '两天事件数 / 两天全部活跃用户；不是日均',
        'bridge': '先规模后强度的描述性拆解，顺序影响交互项归属；无法归因活动或渠道'}}
