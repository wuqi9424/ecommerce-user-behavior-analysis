from ._common import connection, rows, prepare, metadata

def get_opportunity_analysis(top_n=5):
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= 100:
        raise ValueError('top_n must be an integer between 1 and 100')
    with connection() as con:
        prepare(con, '08_growth_opportunity_analysis.sql')
        totals = rows(con, """SELECT
          (SELECT COUNT(*) FROM growth_category_comparison) AS total_categories,
          (SELECT COUNT(*) FROM growth_volume_filtered) AS eligible_categories,
          (SELECT COUNT(*) FROM growth_candidate_categories) AS opportunity_categories""")[0]
        priorities = rows(con, """SELECT priority_segment, COUNT(*) AS user_category_pairs,
          COUNT(DISTINCT user_id) AS distinct_users, ROUND(AVG(recent_pv_count),4) AS avg_pv,
          ROUND(AVG(recent_fav_count),4) AS avg_fav, ROUND(AVG(recent_cart_count),4) AS avg_cart
          FROM growth_combined_pool GROUP BY priority_segment ORDER BY priority_segment""")
        categories = rows(con, """SELECT opportunity_rank, category_id,
          previous_pv_users, final_pv_users, previous_intent_users, final_intent_users,
          previous_buy_users, final_buy_users, pv_user_growth_pct,intent_user_growth_pct,
          buy_user_growth_pct,intent_to_buy_rate_change_pp,pv_to_buy_rate_change_pp,
          high_intent_no_buy_users AS high_intent_nonbuyer_users
          FROM growth_candidate_categories ORDER BY opportunity_rank LIMIT ?""", [top_n])
        thresholds = rows(con, 'SELECT * FROM growth_thresholds')[0]
        thresholds.update(rows(con, 'SELECT high_pv_count FROM growth_browse_threshold')[0])
    return {**totals, 'priorities': priorities, 'top_categories': categories, 'thresholds': thresholds,
      'metadata': metadata('sql/08_growth_opportunity_analysis.sql'),
      'definitions': {'pool_scope': '仅机会品类子池；近期行为 12/02–12/03，完整九天目标品类无 buy',
        'P1':'有近期 cart', 'P2':'无近期 cart、有 fav',
        'P3':'无近期 cart/fav、近期 pv 达纯浏览未买 pair Q75',
        'averages': '按 user-category pair 等权，非按用户等权',
        'distinct_users':'同用户可跨品类跨层，人数不得跨层相加',
        'category_id':'匿名 ID，无法判断真实商品品类',
        'ranking':'按高意向未买人数、Final PV 人数、category_id 排名，偏向大品类',
        'intent_to_buy':'同品类意向且购买 / 全部意向；与 SQL 04 嵌套分母不同',
        'timing':'仅可用于 12/03 结束后的运营，不能用于 12/02 历史实验选人'}}
