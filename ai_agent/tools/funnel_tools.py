from ._common import connection, rows, prepare, metadata

WEEKEND_QUERY = "WITH weekend_calendar AS (\n    SELECT w.period_label, CAST(d.event_date AS DATE) AS event_date\n    FROM (VALUES\n        ('previous_weekend', DATE '2017-11-25', DATE '2017-11-26'),\n        ('final_weekend', DATE '2017-12-02', DATE '2017-12-03')\n    ) w(period_label, start_date, end_date)\n    CROSS JOIN LATERAL generate_series(w.start_date, w.end_date, INTERVAL '1 day') d(event_date)\n)\nSELECT c.period_label, MIN(c.event_date) AS start_date, MAX(c.event_date) AS end_date,\n       COUNT(*) AS calendar_days,\n       ROUND(AVG(COALESCE(m.browsing_users, 0)), 2) AS avg_daily_browsing_users,\n       ROUND(AVG(COALESCE(m.intent_users, 0)), 2) AS avg_daily_intent_users,\n       ROUND(AVG(COALESCE(m.browse_intent_users, 0)), 2) AS avg_daily_browse_intent_users,\n       ROUND(AVG(COALESCE(m.purchasing_users, 0)), 2) AS avg_daily_purchasing_users,\n       COUNT(m.browse_to_buy_pct) AS valid_browse_rate_days,\n       COUNT(m.intent_to_buy_pct) AS valid_intent_rate_days,\n       ROUND(AVG(100.0 * m.browse_intent_users / NULLIF(m.browsing_users, 0)), 4)\n           AS avg_daily_browse_to_intent_pct,\n       ROUND(AVG(100.0 * m.browse_intent_buy_users / NULLIF(m.browse_intent_users, 0)), 4)\n           AS avg_daily_intent_to_buy_pct,\n       ROUND(AVG(100.0 * m.browse_buy_users / NULLIF(m.browsing_users, 0)), 4)\n           AS avg_daily_browse_to_buy_pct,\n       ROUND(AVG(100.0 * m.browse_intent_buy_users / NULLIF(m.browsing_users, 0)), 4)\n           AS avg_daily_full_funnel_pct\nFROM weekend_calendar c LEFT JOIN funnel_rates m ON m.window_type = 'daily'\n AND m.window_label = CAST(c.event_date AS VARCHAR)\nGROUP BY c.period_label ORDER BY start_date;"

def get_funnel_comparison():
    aliases = {'avg_daily_browsing_users':'avg_pv_users', 'avg_daily_intent_users':'avg_intent_users',
      'avg_daily_purchasing_users':'avg_buy_users', 'avg_daily_browse_to_intent_pct':'pv_to_intent_rate',
      'avg_daily_intent_to_buy_pct':'intent_to_buy_rate', 'avg_daily_browse_to_buy_pct':'pv_to_buy_rate',
      'avg_daily_full_funnel_pct':'full_funnel_rate'}
    with connection() as con:
        prepare(con, '04_conversion_funnel.sql', stop_before='funnel_item_bounds')
        periods = [{aliases.get(k,k):v for k,v in r.items()} for r in rows(con, WEEKEND_QUERY)]
    return {'periods': periods, 'metadata': metadata('sql/04_conversion_funnel.sql'),
      'definitions': {'aggregation': '两天每日未舍入用户比例等权均值，最后舍入四位；零分母不纳入',
        'avg_intent_users': '每日 fav/cart 并集用户均值，不要求 pv',
        'pv_to_intent_rate': '浏览且意向 / 浏览',
        'intent_to_buy_rate': '浏览且意向且购买 / 浏览且意向',
        'pv_to_buy_rate': '浏览且购买 / 浏览',
        'full_funnel_rate': '浏览且意向且购买 / 浏览',
        'path': '同日无序用户集合，不要求同商品或严格时间顺序；不是事件比'}}
