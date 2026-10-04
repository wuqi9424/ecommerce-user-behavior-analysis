-- 中国电商平台用户转化漏斗分析（DuckDB）
-- 执行方式：在同一连接中按顺序执行整个文件；辅助表均为 TEMP，不修改源表。
-- 范围：[2017-11-25 00:00:00, 2017-12-04 00:00:00)，Asia/Shanghai。
-- 01_create_tables.sql 已将 event_time 转为上海本地 TIMESTAMP；不再次转换。
-- user_id 为空的记录不参与用户指标；item_id 为空不参与 user-item 指标。
-- 所有 *_pct 单位为百分比；分母为零时返回 NULL。使用精确计数。
-- buy 是购买行为事件，不是订单；本文件不计算事件比作为用户转化率。
-- 性能：先将大表聚合为用户-日标记，复用小表；严格漏斗先压缩用户-商品，
-- 再扫描意向与购买事件并做等值连接，避免原始事件之间多对多连接与全表排序。
-- 临时聚合可能占用较多内存/磁盘，运行环境应留出空间供 DuckDB spill。

SET TimeZone = 'Asia/Shanghai';

CREATE OR REPLACE TEMP TABLE funnel_user_day AS
SELECT user_id, CAST(event_time AS DATE) AS event_date,
       BOOL_OR(behavior_type = 'pv') AS has_pv,
       BOOL_OR(behavior_type = 'fav') AS has_fav,
       BOOL_OR(behavior_type = 'cart') AS has_cart,
       BOOL_OR(behavior_type = 'buy') AS has_buy
FROM user_behavior_clean
WHERE event_time >= TIMESTAMP '2017-11-25 00:00:00'
  AND event_time < TIMESTAMP '2017-12-04 00:00:00'
  AND user_id IS NOT NULL
  AND behavior_type IN ('pv', 'fav', 'cart', 'buy')
GROUP BY user_id, event_date;

-- 同时产生每日、整个观察期、两个对比时段的用户标记。
-- 同一用户跨日只在相应时段计一次；每日数据不能直接相加得到时段用户数。
CREATE OR REPLACE TEMP TABLE funnel_user_window AS
WITH expanded AS (
    SELECT d.*, w.window_type, w.window_label
    FROM funnel_user_day d
    CROSS JOIN LATERAL (VALUES
        ('overall', '2017-11-25_to_2017-12-03'),
        ('daily', CAST(d.event_date AS VARCHAR)),
        ('period', CASE WHEN d.event_date < DATE '2017-12-02'
                       THEN '2017-11-25_to_2017-12-01'
                       ELSE '2017-12-02_to_2017-12-03' END)
    ) w(window_type, window_label)
)
SELECT window_type, window_label, user_id,
       BOOL_OR(has_pv) AS has_pv, BOOL_OR(has_fav) AS has_fav,
       BOOL_OR(has_cart) AS has_cart, BOOL_OR(has_buy) AS has_buy
FROM expanded GROUP BY window_type, window_label, user_id;

CREATE OR REPLACE TEMP TABLE funnel_metrics AS
SELECT window_type, window_label,
       COUNT(*) AS active_users,
       COUNT(*) FILTER (WHERE has_pv) AS browsing_users,
       COUNT(*) FILTER (WHERE has_fav) AS favorite_users,
       COUNT(*) FILTER (WHERE has_cart) AS cart_users,
       COUNT(*) FILTER (WHERE has_fav OR has_cart) AS intent_users,
       COUNT(*) FILTER (WHERE has_buy) AS purchasing_users,
       COUNT(*) FILTER (WHERE has_pv AND (has_fav OR has_cart)) AS browse_intent_users,
       COUNT(*) FILTER (WHERE has_pv AND has_buy) AS browse_buy_users,
       COUNT(*) FILTER (WHERE has_pv AND (has_fav OR has_cart) AND has_buy)
           AS browse_intent_buy_users,
       COUNT(*) FILTER (WHERE has_pv AND has_buy AND NOT (has_fav OR has_cart))
           AS browse_buy_without_intent_users,
       COUNT(*) FILTER (WHERE has_buy AND NOT has_pv) AS buy_without_pv_users
FROM funnel_user_window GROUP BY window_type, window_label;

CREATE OR REPLACE TEMP VIEW funnel_rates AS
SELECT *,
       ROUND(100.0 * browse_intent_users / NULLIF(browsing_users, 0), 4)
           AS browse_to_intent_pct,
       ROUND(100.0 * browse_intent_buy_users / NULLIF(browse_intent_users, 0), 4)
           AS intent_to_buy_pct,
       ROUND(100.0 * browse_intent_buy_users / NULLIF(browsing_users, 0), 4)
           AS full_funnel_pct,
       ROUND(100.0 * browse_buy_users / NULLIF(browsing_users, 0), 4)
           AS browse_to_buy_pct,
       ROUND(100.0 * purchasing_users / NULLIF(active_users, 0), 4)
           AS purchasing_user_penetration_pct
FROM funnel_metrics;

-- 1. 整体行为覆盖：各类型至少发生一次的去重用户数，集合可以重叠。
SELECT active_users, browsing_users, favorite_users, cart_users,
       intent_users, purchasing_users
FROM funnel_metrics WHERE window_type = 'overall';

-- 2. 用户级集合漏斗：浏览用户 -> 浏览且收藏/加购 -> 三类行为均有。
-- 不约束商品或行为顺序；收藏、加购是并行意向，取并集。
-- browse_to_intent_pct = 浏览且意向用户 / 浏览用户。
-- intent_to_buy_pct = 三类行为用户 / 浏览且意向用户。
-- full_funnel_pct = 三类行为用户 / 浏览用户。
-- browse_to_buy_pct = 浏览且购买用户 / 浏览用户，允许没有中间意向行为。
-- purchasing_user_penetration_pct = 所有购买用户 / 所有活跃用户。
-- without_intent / without_pv 仅表示窗口内未观察到该行为，不能证明真实路径。
SELECT * EXCLUDE (window_type) FROM funnel_rates WHERE window_type = 'overall';

-- 3. 每日同日集合漏斗：每一步均限于当日，口径与上面一致。
-- 跨日购买不会记为浏览日的转化；不是按浏览日追踪的 cohort 漏斗。
-- 日期骨架确保无记录日期也输出，计数为 0，零分母比例为 NULL。
SELECT CAST(d.event_date AS DATE) AS event_date,
       COALESCE(m.active_users, 0) AS active_users,
       COALESCE(m.browsing_users, 0) AS browsing_users,
       COALESCE(m.intent_users, 0) AS intent_users,
       COALESCE(m.purchasing_users, 0) AS purchasing_users,
       COALESCE(m.browse_intent_users, 0) AS browse_intent_users,
       COALESCE(m.browse_intent_buy_users, 0) AS browse_intent_buy_users,
       COALESCE(m.browse_buy_users, 0) AS browse_buy_users,
       m.browse_to_intent_pct, m.intent_to_buy_pct, m.full_funnel_pct,
       m.browse_to_buy_pct, m.purchasing_user_penetration_pct
FROM generate_series(DATE '2017-11-25', DATE '2017-12-03', INTERVAL '1 day') d(event_date)
LEFT JOIN funnel_rates m ON m.window_type = 'daily'
 AND m.window_label = CAST(CAST(d.event_date AS DATE) AS VARCHAR)
ORDER BY event_date;

-- 4a. 分别在 7 日与 2 日窗口内重新去重，输出时段集合漏斗。
-- 窗口越长，用户越有机会完成多个行为，时段比例不能直接作效果提升结论。
-- 不同长度窗口的 period-level 用户去重比例受到观察窗口长度影响，
-- 因此保留为描述性指标，不用于直接比较转化表现。
-- period-level rate：时段内用户重新去重后的集合比例，不是每日比例的平均。
SELECT window_label AS period_label, active_users, browsing_users,
       browse_intent_users, browse_buy_users, browse_intent_buy_users, purchasing_users,
       browse_to_intent_pct AS period_level_browse_to_intent_pct,
       intent_to_buy_pct AS period_level_intent_to_buy_pct,
       full_funnel_pct AS period_level_full_funnel_pct,
       browse_to_buy_pct AS period_level_browse_to_buy_pct,
       purchasing_user_penetration_pct AS period_level_purchasing_user_penetration_pct
FROM funnel_rates WHERE window_type = 'period' ORDER BY window_label;

-- 4b. average daily rate（每日比例算术均值）；日均指标：固定除以 7/2，缺失日期按零计；比例为每日比例算术均值，
-- 不是时段转化率，也不是用户加权平均。零分母日期比例为 NULL，AVG 不纳入。
-- 两段人群、星期构成不同；变化是描述性对比，不能归因为活动或运营策略。
WITH calendar AS (
    SELECT CAST(event_date AS DATE) AS event_date,
           CASE WHEN event_date < DATE '2017-12-02' THEN '2017-11-25_to_2017-12-01'
                ELSE '2017-12-02_to_2017-12-03' END AS period_label
    FROM generate_series(DATE '2017-11-25', DATE '2017-12-03', INTERVAL '1 day') d(event_date)
)
SELECT c.period_label, COUNT(*) AS calendar_days,
       ROUND(AVG(COALESCE(m.browsing_users, 0)), 2) AS avg_daily_browsing_users,
       ROUND(AVG(COALESCE(m.browse_intent_users, 0)), 2) AS avg_daily_browse_intent_users,
       ROUND(AVG(COALESCE(m.purchasing_users, 0)), 2) AS avg_daily_purchasing_users,
       ROUND(AVG(COALESCE(m.browse_intent_buy_users, 0)), 2) AS avg_daily_full_funnel_users,
       COUNT(m.browse_to_buy_pct) AS valid_browse_rate_days,
       COUNT(m.intent_to_buy_pct) AS valid_intent_rate_days,
       ROUND(AVG(100.0 * m.browse_intent_users / NULLIF(m.browsing_users, 0)), 4)
           AS avg_daily_browse_to_intent_pct,
       ROUND(AVG(100.0 * m.browse_intent_buy_users / NULLIF(m.browse_intent_users, 0)), 4)
           AS avg_daily_intent_to_buy_pct,
       ROUND(AVG(100.0 * m.browse_intent_buy_users / NULLIF(m.browsing_users, 0)), 4)
           AS avg_daily_full_funnel_pct,
       ROUND(AVG(100.0 * m.browse_buy_users / NULLIF(m.browsing_users, 0)), 4)
           AS avg_daily_browse_to_buy_pct
FROM calendar c LEFT JOIN funnel_rates m ON m.window_type = 'daily'
 AND m.window_label = CAST(c.event_date AS VARCHAR)
GROUP BY c.period_label ORDER BY c.period_label;

-- 4c. 等长周末稳健性对比：两个窗口均为连续两天（周六、周日）。
-- 复用每日聚合，不再次扫描原始大表；控制窗口长度和星期构成，仍非因果检验。
-- 日均意向用户：每日发生 fav 或 cart 的去重用户数的均值，不要求浏览。
-- 日均浏览且意向用户另列，作为集合漏斗中间层，避免与所有意向用户混淆。
-- 每日浏览->意向 = 当日浏览且意向用户 / 当日浏览用户。
-- 每日意向->购买 = 当日浏览且意向且购买用户 / 当日浏览且意向用户。
-- 每日浏览->购买 = 当日浏览且购买用户 / 当日浏览用户。
-- 每日完整集合漏斗 = 当日浏览且意向且购买用户 / 当日浏览用户。
-- 以上均为无序集合关系，不要求同商品或严格时间顺序，不是严格购买路径。
-- average daily rate 为每日比例等权算术均值；先按未舍入比例平均再舍入。
-- 四天日期骨架保证每个窗口恰有两天；缺失日人数为 0，比例零分母为 NULL。
-- 比例均值不纳入 NULL 日期，valid_*_rate_days 展示实际纳入的天数。
WITH weekend_calendar AS (
    SELECT w.period_label, CAST(d.event_date AS DATE) AS event_date
    FROM (VALUES
        ('previous_weekend', DATE '2017-11-25', DATE '2017-11-26'),
        ('final_weekend', DATE '2017-12-02', DATE '2017-12-03')
    ) w(period_label, start_date, end_date)
    CROSS JOIN LATERAL generate_series(w.start_date, w.end_date, INTERVAL '1 day') d(event_date)
)
SELECT c.period_label, MIN(c.event_date) AS start_date, MAX(c.event_date) AS end_date,
       COUNT(*) AS calendar_days,
       ROUND(AVG(COALESCE(m.browsing_users, 0)), 2) AS avg_daily_browsing_users,
       ROUND(AVG(COALESCE(m.intent_users, 0)), 2) AS avg_daily_intent_users,
       ROUND(AVG(COALESCE(m.browse_intent_users, 0)), 2) AS avg_daily_browse_intent_users,
       ROUND(AVG(COALESCE(m.purchasing_users, 0)), 2) AS avg_daily_purchasing_users,
       COUNT(m.browse_to_buy_pct) AS valid_browse_rate_days,
       COUNT(m.intent_to_buy_pct) AS valid_intent_rate_days,
       ROUND(AVG(100.0 * m.browse_intent_users / NULLIF(m.browsing_users, 0)), 4)
           AS avg_daily_browse_to_intent_pct,
       ROUND(AVG(100.0 * m.browse_intent_buy_users / NULLIF(m.browse_intent_users, 0)), 4)
           AS avg_daily_intent_to_buy_pct,
       ROUND(AVG(100.0 * m.browse_buy_users / NULLIF(m.browsing_users, 0)), 4)
           AS avg_daily_browse_to_buy_pct,
       ROUND(AVG(100.0 * m.browse_intent_buy_users / NULLIF(m.browsing_users, 0)), 4)
           AS avg_daily_full_funnel_pct
FROM weekend_calendar c LEFT JOIN funnel_rates m ON m.window_type = 'daily'
 AND m.window_label = CAST(c.event_date AS VARCHAR)
GROUP BY c.period_label ORDER BY start_date;

-- 5. 严格 user-item 漏斗：窗口内存在 t_pv < t_intent < t_buy。
-- 时间精度为秒：同秒行为不推断顺序，不算严格后续步骤。
-- 允许跨日；只要求落在九天观察期内，无额外归因时限，不能解释为因果。
-- 观察期之前的浏览/意向及结束后的购买不可见，存在边界截断。
-- 显式依次寻找首次浏览、浏览后的首次意向、意向后的首次购买。
-- 从首次浏览出发足以判断路径是否存在；更晚浏览的合法后续也晚于首次浏览。
-- 仅用三种行为各自的首次时间会漏掉“先购买、后浏览、再购买”等合法路径。
CREATE OR REPLACE TEMP TABLE funnel_item_bounds AS
SELECT user_id, item_id,
       MIN(event_time) AS first_pv_time
FROM user_behavior_clean
WHERE event_time >= TIMESTAMP '2017-11-25 00:00:00'
  AND event_time < TIMESTAMP '2017-12-04 00:00:00'
  AND user_id IS NOT NULL AND item_id IS NOT NULL
  AND behavior_type = 'pv'
GROUP BY user_id, item_id;

CREATE OR REPLACE TEMP TABLE funnel_item_intent AS
SELECT e.user_id, e.item_id, MIN(e.event_time) AS first_intent_after_pv
FROM user_behavior_clean e
JOIN funnel_item_bounds b ON e.user_id = b.user_id AND e.item_id = b.item_id
WHERE e.behavior_type IN ('fav', 'cart')
  AND e.event_time >= TIMESTAMP '2017-11-25 00:00:00'
  AND e.event_time < TIMESTAMP '2017-12-04 00:00:00'
  AND e.event_time > b.first_pv_time
GROUP BY e.user_id, e.item_id;

-- 同一用户-商品先聚合意向，再连接购买；每个购买事件只对应一个聚合行。
CREATE OR REPLACE TEMP TABLE funnel_item_purchase AS
SELECT e.user_id, e.item_id,
       MIN(e.event_time) AS first_buy_after_pv,
       MIN(e.event_time) FILTER (WHERE e.event_time > i.first_intent_after_pv)
           AS first_buy_after_intent
FROM user_behavior_clean e
JOIN funnel_item_bounds b USING (user_id, item_id)
LEFT JOIN funnel_item_intent i USING (user_id, item_id)
WHERE e.behavior_type = 'buy'
  AND e.event_time >= TIMESTAMP '2017-11-25 00:00:00'
  AND e.event_time < TIMESTAMP '2017-12-04 00:00:00'
  AND e.event_time > b.first_pv_time
GROUP BY e.user_id, e.item_id;

-- PV -> Buy 是两步包含式路径：不要求 Intent，也不排除期间发生 Intent。
-- PV -> Intent -> Buy 是其子集，两者不可相加；不能将前者解释为跳过意向。
-- 额外给出 pv_buy_without_full_path_pairs/users：完成两步但未完成三步的集合差。
-- 用户差集按用户计算：只要任何商品完成三步，就不属于用户差集。
-- pair 指标按去重用户-商品计数，并非用户率；user 指标要求至少一个
-- 商品完成对应路径。用户漏斗各级可由不同商品满足，但完整路径必须同商品。
-- browsing_pairs/users：有浏览的用户-商品对数/用户数。
-- browse_intent_pairs/users：有严格“浏览后意向”路径的对数/用户数。
-- full_funnel_pairs/users：有严格三步路径的对数/用户数。
-- browse_buy_pairs/users：有严格“浏览后购买”路径的对数/用户数，意向可省略。
-- pair/user 比例分别使用对应对数/用户数：browse_to_intent = 意向路径/浏览，
-- intent_to_buy = 三步路径/意向路径，full_funnel = 三步路径/浏览，
-- browse_to_buy = 两步购买路径/浏览。用户只计一次，不按商品数加权。
WITH counts AS (
    SELECT COUNT(*) AS browsing_pairs,
           COUNT(i.first_intent_after_pv) AS browse_intent_pairs,
           COUNT(*) FILTER (WHERE p.first_buy_after_intent IS NOT NULL) AS full_funnel_pairs,
           COUNT(*) FILTER (WHERE p.first_buy_after_pv IS NOT NULL) AS browse_buy_pairs,
           COUNT(DISTINCT b.user_id) AS browsing_users,
           COUNT(DISTINCT b.user_id) FILTER (WHERE i.first_intent_after_pv IS NOT NULL)
               AS browse_intent_users,
           COUNT(DISTINCT b.user_id) FILTER (WHERE p.first_buy_after_intent IS NOT NULL)
               AS full_funnel_users,
           COUNT(DISTINCT b.user_id) FILTER (WHERE p.first_buy_after_pv IS NOT NULL)
               AS browse_buy_users
    FROM funnel_item_bounds b LEFT JOIN funnel_item_intent i USING (user_id, item_id)
    LEFT JOIN funnel_item_purchase p USING (user_id, item_id)
)
SELECT *,
       browse_buy_pairs - full_funnel_pairs AS pv_buy_without_full_path_pairs,
       browse_buy_users - full_funnel_users AS pv_buy_without_full_path_users,
       ROUND(100.0 * browse_intent_pairs / NULLIF(browsing_pairs, 0), 4) AS pair_browse_to_intent_pct,
       ROUND(100.0 * full_funnel_pairs / NULLIF(browse_intent_pairs, 0), 4) AS pair_intent_to_buy_pct,
       ROUND(100.0 * full_funnel_pairs / NULLIF(browsing_pairs, 0), 4) AS pair_full_funnel_pct,
       ROUND(100.0 * browse_buy_pairs / NULLIF(browsing_pairs, 0), 4) AS pair_browse_to_buy_pct,
       ROUND(100.0 * browse_intent_users / NULLIF(browsing_users, 0), 4) AS user_browse_to_intent_pct,
       ROUND(100.0 * full_funnel_users / NULLIF(browse_intent_users, 0), 4) AS user_intent_to_buy_pct,
       ROUND(100.0 * full_funnel_users / NULLIF(browsing_users, 0), 4) AS user_full_funnel_pct,
       ROUND(100.0 * browse_buy_users / NULLIF(browsing_users, 0), 4) AS user_browse_to_buy_pct
FROM counts;

-- 6a. 严格路径时间检查：应返回 0。
SELECT COUNT(*) AS invalid_strict_paths
FROM funnel_item_purchase p
JOIN funnel_item_bounds b USING (user_id, item_id)
LEFT JOIN funnel_item_intent i USING (user_id, item_id)
WHERE p.first_buy_after_pv <= b.first_pv_time
   OR (p.first_buy_after_intent IS NOT NULL AND
       (i.first_intent_after_pv IS NULL
        OR i.first_intent_after_pv <= b.first_pv_time
        OR p.first_buy_after_intent <= i.first_intent_after_pv
        OR p.first_buy_after_intent < p.first_buy_after_pv));

-- 6b. 集合漏斗一致性检查：应返回 0，不代表所有数据质量问题均已排除。
SELECT COUNT(*) AS invalid_funnel_windows FROM funnel_metrics
WHERE browse_intent_buy_users > browse_intent_users
   OR browse_intent_users > browsing_users
   OR browse_intent_buy_users > browse_buy_users
   OR browse_buy_users > purchasing_users
   OR purchasing_users > active_users;

DROP VIEW IF EXISTS funnel_rates;
DROP TABLE IF EXISTS funnel_item_purchase;
DROP TABLE IF EXISTS funnel_item_intent;
DROP TABLE IF EXISTS funnel_item_bounds;
DROP TABLE IF EXISTS funnel_metrics;
DROP TABLE IF EXISTS funnel_user_window;
DROP TABLE IF EXISTS funnel_user_day;
