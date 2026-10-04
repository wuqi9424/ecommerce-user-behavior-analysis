-- =========================================================
-- 03_platform_overview.sql
-- Project: E-commerce User Behavior Analysis
-- Purpose:
-- Understand overall platform scale, user activity,
-- behavior trends, purchase activity and time patterns.
--
-- Analysis table:
-- user_behavior_clean
--
-- Time window:
-- 2017-11-25 to 2017-12-03, Asia/Shanghai
-- =========================================================


-- =========================================================
-- 1. Overall platform scale
-- Exact number of users, items and categories
-- =========================================================

SELECT
    COUNT(*) AS total_behavior_events,
    COUNT(DISTINCT user_id) AS total_users,
    COUNT(DISTINCT item_id) AS total_items,
    COUNT(DISTINCT category_id) AS total_categories
FROM user_behavior_clean;


-- =========================================================
-- 2. Overall behavior summary
--
-- Note:
-- buy_to_pv_event_ratio is an event-level ratio.
-- It should NOT be interpreted as a user-level funnel conversion rate.
-- =========================================================

SELECT
    COUNT(*) FILTER (
        WHERE behavior_type = 'pv'
    ) AS pv_events,

    COUNT(*) FILTER (
        WHERE behavior_type = 'fav'
    ) AS fav_events,

    COUNT(*) FILTER (
        WHERE behavior_type = 'cart'
    ) AS cart_events,

    COUNT(*) FILTER (
        WHERE behavior_type = 'buy'
    ) AS buy_events,

    COUNT(DISTINCT user_id) FILTER (
        WHERE behavior_type = 'buy'
    ) AS purchasing_users,

    ROUND(
        COUNT(*) FILTER (WHERE behavior_type = 'buy')
        * 100.0
        /
        NULLIF(
            COUNT(*) FILTER (WHERE behavior_type = 'pv'),
            0
        ),
        2
    ) AS buy_to_pv_event_ratio_pct

FROM user_behavior_clean;


-- =========================================================
-- 3. Purchasing-user penetration
--
-- Percentage of observed users who made at least one purchase
-- during the analysis period.
-- =========================================================

SELECT
    COUNT(DISTINCT user_id) AS total_users,

    COUNT(DISTINCT user_id) FILTER (
        WHERE behavior_type = 'buy'
    ) AS purchasing_users,

    ROUND(
        COUNT(DISTINCT user_id) FILTER (
            WHERE behavior_type = 'buy'
        )
        * 100.0
        /
        COUNT(DISTINCT user_id),
        2
    ) AS purchasing_user_rate_pct

FROM user_behavior_clean;


-- =========================================================
-- 4. Daily platform overview
--
-- DAU = users with any recorded behavior on that day
-- Purchasing users = users with at least one buy event
-- =========================================================

SELECT
    CAST(event_time AS DATE) AS date,

    COUNT(*) AS total_events,

    COUNT(DISTINCT user_id) AS dau,

    COUNT(DISTINCT user_id) FILTER (
        WHERE behavior_type = 'buy'
    ) AS purchasing_users,

    COUNT(*) FILTER (
        WHERE behavior_type = 'pv'
    ) AS pv_events,

    COUNT(*) FILTER (
        WHERE behavior_type = 'fav'
    ) AS fav_events,

    COUNT(*) FILTER (
        WHERE behavior_type = 'cart'
    ) AS cart_events,

    COUNT(*) FILTER (
        WHERE behavior_type = 'buy'
    ) AS buy_events,

    ROUND(
        COUNT(*) * 1.0 /
        COUNT(DISTINCT user_id),
        2
    ) AS events_per_active_user

FROM user_behavior_clean

GROUP BY date
ORDER BY date;


-- =========================================================
-- 5. Daily purchasing-user rate
--
-- Measures the percentage of active users on each day
-- who generated at least one purchase event.
-- =========================================================

SELECT
    CAST(event_time AS DATE) AS date,

    COUNT(DISTINCT user_id) AS dau,

    COUNT(DISTINCT user_id) FILTER (
        WHERE behavior_type = 'buy'
    ) AS purchasing_users,

    ROUND(
        COUNT(DISTINCT user_id) FILTER (
            WHERE behavior_type = 'buy'
        )
        * 100.0
        /
        COUNT(DISTINCT user_id),
        2
    ) AS purchasing_user_rate_pct

FROM user_behavior_clean

GROUP BY date
ORDER BY date;


-- =========================================================
-- 6. Daily behavior composition
--
-- Used to check whether changes in total activity
-- are driven by browsing, cart, favorite or purchase behavior.
-- =========================================================

SELECT
    CAST(event_time AS DATE) AS date,

    behavior_type,

    COUNT(*) AS behavior_count,

    ROUND(
        COUNT(*) * 100.0
        /
        SUM(COUNT(*)) OVER (
            PARTITION BY CAST(event_time AS DATE)
        ),
        2
    ) AS daily_behavior_share_pct

FROM user_behavior_clean

GROUP BY
    CAST(event_time AS DATE),
    behavior_type

ORDER BY
    date,
    behavior_type;


-- =========================================================
-- 7. Hourly total activity
--
-- Used to identify the platform's daily activity pattern.
-- =========================================================

SELECT
    EXTRACT(HOUR FROM event_time) AS hour,

    COUNT(*) AS total_events,

    COUNT(DISTINCT user_id) AS active_users,

    ROUND(
        COUNT(*) * 1.0 /
        COUNT(DISTINCT user_id),
        2
    ) AS events_per_active_user

FROM user_behavior_clean

GROUP BY hour
ORDER BY hour;


-- =========================================================
-- 8. Hourly behavior breakdown
--
-- Shows when browsing, carting, favoriting and buying peak.
-- =========================================================

SELECT
    EXTRACT(HOUR FROM event_time) AS hour,

    COUNT(*) FILTER (
        WHERE behavior_type = 'pv'
    ) AS pv_events,

    COUNT(*) FILTER (
        WHERE behavior_type = 'fav'
    ) AS fav_events,

    COUNT(*) FILTER (
        WHERE behavior_type = 'cart'
    ) AS cart_events,

    COUNT(*) FILTER (
        WHERE behavior_type = 'buy'
    ) AS buy_events

FROM user_behavior_clean

GROUP BY hour
ORDER BY hour;


-- =========================================================
-- 9. Hourly purchasing-user activity
--
-- Counts unique users purchasing in each hour.
-- =========================================================

SELECT
    EXTRACT(HOUR FROM event_time) AS hour,

    COUNT(DISTINCT user_id) FILTER (
        WHERE behavior_type = 'buy'
    ) AS purchasing_users,

    COUNT(*) FILTER (
        WHERE behavior_type = 'buy'
    ) AS buy_events

FROM user_behavior_clean

GROUP BY hour
ORDER BY hour;


-- =========================================================
-- 10. Average number of behaviors per user
--
-- Helps describe overall user activity intensity.
-- =========================================================

WITH user_activity AS (

    SELECT
        user_id,

        COUNT(*) AS total_events,

        COUNT(*) FILTER (
            WHERE behavior_type = 'pv'
        ) AS pv_events,

        COUNT(*) FILTER (
            WHERE behavior_type = 'fav'
        ) AS fav_events,

        COUNT(*) FILTER (
            WHERE behavior_type = 'cart'
        ) AS cart_events,

        COUNT(*) FILTER (
            WHERE behavior_type = 'buy'
        ) AS buy_events

    FROM user_behavior_clean

    GROUP BY user_id
)

SELECT
    ROUND(AVG(total_events), 2) AS avg_events_per_user,
    ROUND(AVG(pv_events), 2) AS avg_pv_per_user,
    ROUND(AVG(fav_events), 2) AS avg_fav_per_user,
    ROUND(AVG(cart_events), 2) AS avg_cart_per_user,
    ROUND(AVG(buy_events), 2) AS avg_buy_per_user

FROM user_activity;


-- =========================================================
-- 11. Purchase frequency distribution
--
-- Only includes users who made at least one purchase.
-- This is NOT yet RFM segmentation.
-- =========================================================

WITH user_purchase_frequency AS (

    SELECT
        user_id,
        COUNT(*) AS purchase_count

    FROM user_behavior_clean

    WHERE behavior_type = 'buy'

    GROUP BY user_id
)

SELECT
    purchase_count,
    COUNT(*) AS users,

    ROUND(
        COUNT(*) * 100.0
        /
        SUM(COUNT(*)) OVER (),
        2
    ) AS user_share_pct

FROM user_purchase_frequency

GROUP BY purchase_count
ORDER BY purchase_count;


-- =========================================================
-- 12. Weekday versus weekend daily averages
-- =========================================================

WITH daily_metrics AS (

    SELECT
        CAST(event_time AS DATE) AS date,

        CASE
            WHEN EXTRACT(DOW FROM event_time) IN (0, 6)
                THEN 'Weekend'
            ELSE 'Weekday'
        END AS day_type,

        COUNT(*) AS total_events,

        COUNT(DISTINCT user_id) AS active_users,

        COUNT(*) FILTER (
            WHERE behavior_type = 'buy'
        ) AS buy_events,

        COUNT(DISTINCT user_id) FILTER (
            WHERE behavior_type = 'buy'
        ) AS purchasing_users

    FROM user_behavior_clean

    GROUP BY date, day_type
)

SELECT
    day_type,

    COUNT(*) AS number_of_days,

    ROUND(AVG(total_events), 0) AS avg_daily_events,

    ROUND(AVG(active_users), 0) AS avg_daily_active_users,

    ROUND(AVG(buy_events), 0) AS avg_daily_buy_events,

    ROUND(AVG(purchasing_users), 0) AS avg_daily_purchasing_users

FROM daily_metrics

GROUP BY day_type
ORDER BY day_type;

-- =========================================================
-- 13. Investigate activity increase on Dec 2 and Dec 3
--
-- Compare average daily behavior volume in:
--
-- Baseline period:
-- 2017-11-25 to 2017-12-01
--
-- High-activity period:
-- 2017-12-02 to 2017-12-03
-- =========================================================

WITH daily_behavior AS (

    SELECT
        CAST(event_time AS DATE) AS date,
        behavior_type,
        COUNT(*) AS behavior_count

    FROM user_behavior_clean

    GROUP BY
        date,
        behavior_type
),

period_summary AS (

    SELECT
        behavior_type,

        AVG(
            CASE
                WHEN date BETWEEN DATE '2017-11-25'
                              AND DATE '2017-12-01'
                THEN behavior_count
            END
        ) AS baseline_avg_daily_events,

        AVG(
            CASE
                WHEN date BETWEEN DATE '2017-12-02'
                              AND DATE '2017-12-03'
                THEN behavior_count
            END
        ) AS final_two_days_avg_events

    FROM daily_behavior

    GROUP BY behavior_type
)

SELECT
    behavior_type,

    ROUND(
        baseline_avg_daily_events,
        0
    ) AS baseline_avg_daily_events,

    ROUND(
        final_two_days_avg_events,
        0
    ) AS final_two_days_avg_events,

    ROUND(
        (
            final_two_days_avg_events
            -
            baseline_avg_daily_events
        )
        * 100.0
        /
        NULLIF(
            baseline_avg_daily_events,
            0
        ),
        2
    ) AS uplift_pct

FROM period_summary

ORDER BY uplift_pct DESC;


-- =========================================================
-- 14. Overall activity increase on Dec 2 and Dec 3
-- =========================================================

WITH daily_total AS (

    SELECT
        CAST(event_time AS DATE) AS date,
        COUNT(*) AS total_events

    FROM user_behavior_clean

    GROUP BY date
),

period_summary AS (

    SELECT

        AVG(
            CASE
                WHEN date BETWEEN DATE '2017-11-25'
                              AND DATE '2017-12-01'
                THEN total_events
            END
        ) AS baseline_avg_daily_events,

        AVG(
            CASE
                WHEN date BETWEEN DATE '2017-12-02'
                              AND DATE '2017-12-03'
                THEN total_events
            END
        ) AS final_two_days_avg_events

    FROM daily_total
)

SELECT
    ROUND(
        baseline_avg_daily_events,
        0
    ) AS baseline_avg_daily_events,

    ROUND(
        final_two_days_avg_events,
        0
    ) AS final_two_days_avg_events,

    ROUND(
        (
            final_two_days_avg_events
            -
            baseline_avg_daily_events
        )
        * 100.0
        /
        baseline_avg_daily_events,
        2
    ) AS overall_uplift_pct

FROM period_summary;