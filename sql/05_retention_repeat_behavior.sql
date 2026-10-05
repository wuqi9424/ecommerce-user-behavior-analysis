-- 中国电商平台：活跃留存、重复活跃与跨日重复购买行为（DuckDB）
-- 在同一连接按顺序执行；只创建 TEMP 对象，不修改 user_behavior_clean。
-- 上海本地时间范围：[2017-11-25 00:00:00, 2017-12-04 00:00:00)。
-- event_time 已是 Asia/Shanghai 的 TIMESTAMP，不再次转换。
-- 活跃 = pv/fav/cart/buy 任意行为；排除空 user_id，使用精确用户计数。
-- first_observed_active_date 是窗口内首次观测活跃日，不是注册日或获取日。
-- buy_event_count 是清洗表购买事件数，没有 order_id，不能称为订单数。
-- 仅九天，历史与后续不可见，存在左右截尾；不代表长期留存/真实长期复购率。
-- 所有 rate/share 单位均为 0–100 百分比；分母为零时 NULL。
-- 性能：仅扫描大表一次，后续复用用户-日聚合；LAG 只排序不同购买日期。
SET TimeZone = 'Asia/Shanghai';

CREATE OR REPLACE TEMP TABLE retention_user_day AS
SELECT user_id, CAST(event_time AS DATE) AS event_date,
       COUNT(*) FILTER (WHERE behavior_type = 'buy') AS buy_event_count
FROM user_behavior_clean
WHERE event_time >= TIMESTAMP '2017-11-25 00:00:00'
  AND event_time < TIMESTAMP '2017-12-04 00:00:00'
  AND user_id IS NOT NULL
  AND behavior_type IN ('pv', 'fav', 'cart', 'buy')
GROUP BY user_id, event_date;

CREATE OR REPLACE TEMP TABLE retention_user_summary AS
SELECT user_id, COUNT(*) AS active_days,
       MIN(event_date) AS first_observed_active_date,
       SUM(buy_event_count) AS buy_event_count,
       COUNT(*) FILTER (WHERE buy_event_count > 0) AS buy_active_days,
       MIN(event_date) FILTER (WHERE buy_event_count > 0) AS first_buy_date,
       MAX(event_date) FILTER (WHERE buy_event_count > 0) AS last_buy_date
FROM retention_user_day GROUP BY user_id;

-- 1a. 每个用户在整个九天内不同活跃日期数；分母为全体观测活跃用户。
SELECT active_days, COUNT(*) AS users,
       ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 4) AS user_share_pct
FROM retention_user_summary GROUP BY active_days ORDER BY active_days;

-- 1b. 用户等权平均/中位数，以及四个互斥分组占全体活跃用户的比例。
SELECT COUNT(*) AS observed_active_users,
       ROUND(AVG(active_days), 4) AS avg_active_days,
       MEDIAN(active_days) AS median_active_days,
       ROUND(100.0 * COUNT(*) FILTER (WHERE active_days = 1) / NULLIF(COUNT(*), 0), 4)
           AS active_1_day_share_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE active_days BETWEEN 2 AND 3) / NULLIF(COUNT(*), 0), 4)
           AS active_2_to_3_days_share_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE active_days BETWEEN 4 AND 6) / NULLIF(COUNT(*), 0), 4)
           AS active_4_to_6_days_share_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE active_days BETWEEN 7 AND 9) / NULLIF(COUNT(*), 0), 4)
           AS active_7_to_9_days_share_pct
FROM retention_user_summary;

-- 2/3. 先构建 D0–D7 长表。留存是指定第 k 天有行为，非累计/连续留存，
-- 不要求中间每天活跃，因此留存曲线不必单调递减。
-- cohort 分母固定为窗口内首次观测日的用户数；日期骨架保留零用户 cohort。
-- 观测假设：源日志覆盖整个约定日期范围。没有记录不自动证明日志完整。
CREATE OR REPLACE TEMP TABLE retention_cells AS
WITH calendar AS (
    SELECT CAST(d AS DATE) AS cohort_date
    FROM generate_series(DATE '2017-11-25', DATE '2017-12-03', INTERVAL '1 day') t(d)
), cohorts AS (
    SELECT first_observed_active_date AS cohort_date, COUNT(*) AS cohort_users
    FROM retention_user_summary GROUP BY first_observed_active_date
), observed AS (
    SELECT s.first_observed_active_date AS cohort_date,
           DATE_DIFF('day', s.first_observed_active_date, d.event_date) AS retention_day,
           COUNT(*) AS retained_users
    FROM retention_user_day d JOIN retention_user_summary s USING (user_id)
    GROUP BY cohort_date, retention_day
)
SELECT c.cohort_date, COALESCE(h.cohort_users, 0) AS cohort_users,
       CAST(k.retention_day AS INTEGER) AS retention_day,
       c.cohort_date + CAST(k.retention_day AS INTEGER) <= DATE '2017-12-03' AS is_observable,
       CASE WHEN c.cohort_date + CAST(k.retention_day AS INTEGER) <= DATE '2017-12-03'
            THEN COALESCE(o.retained_users, 0) END AS retained_users,
       CASE WHEN c.cohort_date + CAST(k.retention_day AS INTEGER) <= DATE '2017-12-03'
            THEN ROUND(100.0 * COALESCE(o.retained_users, 0) / NULLIF(h.cohort_users, 0), 4)
       END AS retention_rate
FROM calendar c CROSS JOIN range(8) k(retention_day)
LEFT JOIN cohorts h USING (cohort_date)
LEFT JOIN observed o ON o.cohort_date = c.cohort_date AND o.retention_day = k.retention_day;

-- 2. 次日留存：排除 12/03 cohort，其 D1 位于观察期外。
-- 首日 cohort 包含大量既有用户，不能解释为新用户留存。
SELECT cohort_date, cohort_users, retained_users AS d1_retained_users,
       retention_rate AS d1_retention_rate
FROM retention_cells WHERE retention_day = 1 AND is_observable ORDER BY cohort_date;

-- 3a. 长表同时给出人数、比例、可观测标记，供后续图表使用。
-- 不完整后续窗口人数/比例均为 NULL；可观测但没人返回时人数为 0。
SELECT * FROM retention_cells ORDER BY cohort_date, retention_day;

-- 3b. D0–D7 百分比矩阵，分母为各 cohort 用户数。
-- NULL 是 unavailable（或 cohort 无用户），不是 0% 或流失。
SELECT cohort_date, MAX(cohort_users) AS cohort_users,
       MAX(retention_rate) FILTER (WHERE retention_day = 0) AS d0,
       MAX(retention_rate) FILTER (WHERE retention_day = 1) AS d1,
       MAX(retention_rate) FILTER (WHERE retention_day = 2) AS d2,
       MAX(retention_rate) FILTER (WHERE retention_day = 3) AS d3,
       MAX(retention_rate) FILTER (WHERE retention_day = 4) AS d4,
       MAX(retention_rate) FILTER (WHERE retention_day = 5) AS d5,
       MAX(retention_rate) FILTER (WHERE retention_day = 6) AS d6,
       MAX(retention_rate) FILTER (WHERE retention_day = 7) AS d7
FROM retention_cells GROUP BY cohort_date ORDER BY cohort_date;

-- 4a. 每个购买用户的指标，保存在 TEMP view 中，避免直接输出几十万行。
-- 如需明细，在清理段之前执行 SELECT * FROM retention_purchase_users ORDER BY user_id;
-- first/last_buy_date 同样只是窗口内首次/末次购买日期。
CREATE OR REPLACE TEMP VIEW retention_purchase_users AS
SELECT user_id, buy_event_count, buy_active_days, first_buy_date, last_buy_date
FROM retention_user_summary WHERE buy_active_days > 0;

-- 展示前 20 位用户作明细预览，不作为全体统计的样本依据。
SELECT * FROM retention_purchase_users ORDER BY user_id LIMIT 20;

-- 4b. 四个互斥购买活跃日分组，分母为窗口内所有购买用户；非订单频次。
WITH buckets AS (
    SELECT * FROM (VALUES (1, '1'), (2, '2'), (3, '3'), (4, '4+')) t(sort_order, buy_active_days_group)
), counts AS (
    SELECT LEAST(buy_active_days, 4) AS sort_order, COUNT(*) AS users
    FROM retention_purchase_users GROUP BY sort_order
)
SELECT b.buy_active_days_group, COALESCE(c.users, 0) AS users,
       ROUND(100.0 * COALESCE(c.users, 0) /
             NULLIF((SELECT COUNT(*) FROM retention_purchase_users), 0), 4) AS user_share_pct
FROM buckets b LEFT JOIN counts c USING (sort_order) ORDER BY b.sort_order;

-- 5. observed_repeat_purchase_user_rate = 至少两个不同购买日期的用户 / 所有购买用户。
-- 单日多个 buy 不算跨日重复购买；不是复购订单率或真实长期复购率。
CREATE OR REPLACE TEMP TABLE retention_repeat_summary AS
SELECT COUNT(*) AS purchasing_users,
       COUNT(*) FILTER (WHERE buy_active_days >= 2) AS repeat_purchase_users,
       ROUND(100.0 * COUNT(*) FILTER (WHERE buy_active_days >= 2) / NULLIF(COUNT(*), 0), 4)
           AS observed_repeat_purchase_user_rate
FROM retention_purchase_users;
SELECT * FROM retention_repeat_summary;

-- 6. 相邻不同购买日期间隔；一个用户 n 个购买日期贡献 n-1 个间隔。
-- 以间隔数为分母，每个间隔等权，并非每个用户等权；仅包含窗口内已观测的间隔。
-- 无后续购买的等待期不在样本中，不能用于估计长期购买周期。
CREATE OR REPLACE TEMP TABLE retention_buy_intervals AS
WITH lagged AS (
    SELECT user_id, event_date AS buy_date,
           LAG(event_date) OVER (PARTITION BY user_id ORDER BY event_date) AS previous_buy_date
    FROM retention_user_day WHERE buy_event_count > 0
)
SELECT user_id, previous_buy_date, buy_date,
       DATE_DIFF('day', previous_buy_date, buy_date) AS interval_days
FROM lagged WHERE previous_buy_date IS NOT NULL;

SELECT interval_days, COUNT(*) AS interval_count,
       ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 4) AS share_pct
FROM retention_buy_intervals GROUP BY interval_days ORDER BY interval_days;
SELECT COUNT(*) AS observed_intervals, COUNT(DISTINCT user_id) AS users_with_intervals,
       ROUND(AVG(interval_days), 4) AS avg_interval_days,
       MEDIAN(interval_days) AS median_interval_days
FROM retention_buy_intervals;

-- 7. 等长周末：只比较当期观测行为，不比较需要未来数据的 D1/D2 留存。
-- active_both_days_users/period_active_users：当期两天均活跃的用户占比。
-- repeat_purchase_users/purchasing_users：当期两天均购买的用户占比。
-- 两段均为两天周末，仍可能存在人群差异，描述性变化不能证明因果。
WITH weekend_users AS (
    SELECT CASE WHEN event_date <= DATE '2017-11-26' THEN 'previous_weekend'
                ELSE 'final_weekend' END AS period_label,
           user_id, COUNT(*) AS active_days,
           COUNT(*) FILTER (WHERE buy_event_count > 0) AS buy_active_days
    FROM retention_user_day
    WHERE event_date BETWEEN DATE '2017-11-25' AND DATE '2017-11-26'
       OR event_date BETWEEN DATE '2017-12-02' AND DATE '2017-12-03'
    GROUP BY period_label, user_id
), windows AS (
    SELECT * FROM (VALUES
        ('previous_weekend', DATE '2017-11-25', DATE '2017-11-26'),
        ('final_weekend', DATE '2017-12-02', DATE '2017-12-03')
    ) t(period_label, start_date, end_date)
)
SELECT w.period_label, w.start_date, w.end_date, 2 AS calendar_days,
       COUNT(u.user_id) AS period_active_users,
       ROUND(COALESCE(SUM(u.active_days), 0) / 2.0, 2) AS avg_daily_active_users,
       ROUND(COALESCE(SUM(u.buy_active_days), 0) / 2.0, 2) AS avg_daily_purchasing_users,
       COUNT(u.user_id) FILTER (WHERE u.active_days = 2) AS active_both_days_users,
       ROUND(100.0 * COUNT(u.user_id) FILTER (WHERE u.active_days = 2) /
             NULLIF(COUNT(u.user_id), 0), 4) AS observed_both_days_active_user_rate,
       COUNT(u.user_id) FILTER (WHERE u.buy_active_days > 0) AS purchasing_users,
       COUNT(u.user_id) FILTER (WHERE u.buy_active_days = 2) AS repeat_purchase_users,
       ROUND(100.0 * COUNT(u.user_id) FILTER (WHERE u.buy_active_days = 2) /
             NULLIF(COUNT(u.user_id) FILTER (WHERE u.buy_active_days > 0), 0), 4)
           AS observed_repeat_purchase_user_rate
FROM windows w LEFT JOIN weekend_users u USING (period_label)
GROUP BY w.period_label, w.start_date, w.end_date ORDER BY w.start_date;

-- 8. 一致性检查：每项应为 0。检查逻辑一致性不等于证明日志没有遗漏。
SELECT 'invalid_retention_rate' AS check_name, COUNT(*) AS invalid_rows
FROM retention_cells WHERE retention_rate < 0 OR retention_rate > 100
UNION ALL
SELECT 'retained_exceeds_cohort', COUNT(*) FROM retention_cells
WHERE retained_users < 0 OR retained_users > cohort_users
UNION ALL
SELECT 'invalid_observation_window', COUNT(*) FROM retention_cells
WHERE is_observable <> (cohort_date + retention_day <= DATE '2017-12-03')
   OR (NOT is_observable AND (retained_users IS NOT NULL OR retention_rate IS NOT NULL))
   OR (is_observable AND retained_users IS NULL)
   OR (is_observable AND cohort_users > 0 AND retention_rate IS NULL)
   OR (cohort_users = 0 AND retention_rate IS NOT NULL)
UNION ALL
SELECT 'invalid_d0', COUNT(*) FROM retention_cells
WHERE retention_day = 0 AND (retained_users <> cohort_users OR (cohort_users > 0 AND retention_rate <> 100))
UNION ALL
SELECT 'invalid_user_days', COUNT(*) FROM retention_user_summary
WHERE active_days NOT BETWEEN 1 AND 9 OR buy_active_days NOT BETWEEN 0 AND 9
   OR buy_active_days > active_days OR buy_event_count < buy_active_days
UNION ALL
SELECT 'invalid_repeat_summary', COUNT(*) FROM retention_repeat_summary
WHERE repeat_purchase_users > purchasing_users
   OR observed_repeat_purchase_user_rate NOT BETWEEN 0 AND 100
UNION ALL
SELECT 'invalid_purchase_interval', COUNT(*) FROM retention_buy_intervals
WHERE interval_days NOT BETWEEN 1 AND 8
UNION ALL
SELECT 'interval_count_mismatch',
       CASE WHEN (SELECT COUNT(*) FROM retention_buy_intervals) <>
                 (SELECT COALESCE(SUM(buy_active_days - 1), 0) FROM retention_purchase_users)
            THEN 1 ELSE 0 END
UNION ALL
SELECT 'cohort_total_mismatch',
       CASE WHEN (SELECT SUM(cohort_users) FROM retention_cells WHERE retention_day = 0) <>
                 (SELECT COUNT(*) FROM retention_user_summary) THEN 1 ELSE 0 END;

DROP TABLE IF EXISTS retention_buy_intervals;
DROP TABLE IF EXISTS retention_repeat_summary;
DROP VIEW IF EXISTS retention_purchase_users;
DROP TABLE IF EXISTS retention_cells;
DROP TABLE IF EXISTS retention_user_summary;
DROP TABLE IF EXISTS retention_user_day;
