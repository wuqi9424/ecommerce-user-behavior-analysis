-- =========================================================
-- 02_data_quality_checks.sql
-- Purpose:
-- Validate the cleaned user behavior dataset.
-- =========================================================

-- 1. Overall dataset size and time range
SELECT
    COUNT(*) AS total_rows,
    MIN(event_time) AS start_time,
    MAX(event_time) AS end_time
FROM user_behavior_clean;


-- 2. Missing value checks
SELECT
    SUM(CASE WHEN user_id IS NULL THEN 1 ELSE 0 END) AS null_user_id,
    SUM(CASE WHEN item_id IS NULL THEN 1 ELSE 0 END) AS null_item_id,
    SUM(CASE WHEN category_id IS NULL THEN 1 ELSE 0 END) AS null_category_id,
    SUM(CASE WHEN behavior_type IS NULL THEN 1 ELSE 0 END) AS null_behavior_type,
    SUM(CASE WHEN event_time IS NULL THEN 1 ELSE 0 END) AS null_event_time
FROM user_behavior_clean;


-- 3. Behavior type distribution
SELECT
    behavior_type,
    COUNT(*) AS behavior_count,
    ROUND(
        COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (),
        2
    ) AS percentage
FROM user_behavior_clean
GROUP BY behavior_type
ORDER BY behavior_count DESC;


-- 4. Approximate number of users, items and categories
SELECT
    approx_count_distinct(user_id) AS approx_users,
    approx_count_distinct(item_id) AS approx_items,
    approx_count_distinct(category_id) AS approx_categories
FROM user_behavior_clean;


-- 5. Daily behavior volume
SELECT
    CAST(event_time AS DATE) AS date,
    COUNT(*) AS behavior_count
FROM user_behavior_clean
GROUP BY date
ORDER BY date;

-- 6. Raw behavior type validation
SELECT
    behavior_type,
    COUNT(*) AS behavior_count
FROM raw_user_behavior
GROUP BY behavior_type
ORDER BY behavior_count DESC;


-- 7. Exact duplicate record check
SELECT
    COUNT(*) AS duplicate_groups,
    SUM(record_count - 1) AS duplicate_rows
FROM (
    SELECT
        user_id,
        item_id,
        category_id,
        behavior_type,
        timestamp,
        COUNT(*) AS record_count
    FROM user_behavior_clean
    GROUP BY
        user_id,
        item_id,
        category_id,
        behavior_type,
        timestamp
    HAVING COUNT(*) > 1
);

-- 8. Inspect exact duplicate records
SELECT
    user_id,
    item_id,
    category_id,
    behavior_type,
    timestamp,
    event_time,
    COUNT(*) AS record_count
FROM user_behavior_clean
GROUP BY
    user_id,
    item_id,
    category_id,
    behavior_type,
    timestamp,
    event_time
HAVING COUNT(*) > 1
ORDER BY record_count DESC, event_time
LIMIT 100;


-- 9. Duplicate records by behavior type
SELECT
    behavior_type,
    SUM(record_count - 1) AS duplicate_rows
FROM (
    SELECT
        user_id,
        item_id,
        category_id,
        behavior_type,
        timestamp,
        COUNT(*) AS record_count
    FROM user_behavior_clean
    GROUP BY
        user_id,
        item_id,
        category_id,
        behavior_type,
        timestamp
    HAVING COUNT(*) > 1
)
GROUP BY behavior_type
ORDER BY duplicate_rows DESC;