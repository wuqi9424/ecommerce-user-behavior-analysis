from ._common import connection, rows, metadata

def get_platform_overview():
    with connection() as con:
        data = rows(con, """
        SELECT COUNT(*) AS total_events, COUNT(DISTINCT user_id) AS total_users,
          COUNT(DISTINCT user_id) AS active_users, COUNT(DISTINCT item_id) AS items,
          COUNT(DISTINCT category_id) AS categories,
          COUNT(*) FILTER(WHERE behavior_type='pv') AS pv_events,
          COUNT(*) FILTER(WHERE behavior_type='fav') AS fav_events,
          COUNT(*) FILTER(WHERE behavior_type='cart') AS cart_events,
          COUNT(*) FILTER(WHERE behavior_type='buy') AS buy_events,
          COUNT(DISTINCT user_id) FILTER(WHERE behavior_type='buy') AS purchasing_users
        FROM user_behavior_clean
        """)[0]
    return {**data, 'metadata': metadata('sql/03_platform_overview.sql'),
            'definitions': {'active_users': '九天内任意行为的去重用户，与 total_users 相同',
                            'buy_events': '购买行为事件数，不是订单数'}}
