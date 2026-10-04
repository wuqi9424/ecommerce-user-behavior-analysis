-- =========================================================
-- 01_create_tables.sql
-- Purpose:
-- Create the raw view and cleaned analysis table
-- for the Taobao user behavior dataset.
-- =========================================================

-- Raw data view
CREATE OR REPLACE VIEW raw_user_behavior AS
SELECT
    user_id,
    item_id,
    category_id,
    behavior_type,
    timestamp
FROM read_csv(
    'data/raw/UserBehavior.csv',
    header = false,
    columns = {
        'user_id': 'BIGINT',
        'item_id': 'BIGINT',
        'category_id': 'BIGINT',
        'behavior_type': 'VARCHAR',
        'timestamp': 'BIGINT'
    }
);

-- Clean analysis table
CREATE OR REPLACE TABLE user_behavior_clean AS
SELECT
    user_id,
    item_id,
    category_id,
    behavior_type,
    timestamp,
    to_timestamp(timestamp) AT TIME ZONE 'Asia/Shanghai' AS event_time
FROM raw_user_behavior
WHERE timestamp BETWEEN
    epoch(TIMESTAMPTZ '2017-11-25 00:00:00+08:00')
    AND
    epoch(TIMESTAMPTZ '2017-12-03 23:59:59+08:00')
AND behavior_type IN ('pv', 'fav', 'cart', 'buy');

-- Remove exact duplicate events.
-- Assumption:
-- Rows with identical user, item, category, behavior and timestamp
-- are treated as duplicate event logs and only one is retained.

DELETE FROM user_behavior_clean
WHERE rowid IN (
    SELECT rowid
    FROM (
        SELECT
            rowid,
            ROW_NUMBER() OVER (
                PARTITION BY
                    user_id,
                    item_id,
                    category_id,
                    behavior_type,
                    timestamp
                ORDER BY rowid
            ) AS rn
        FROM user_behavior_clean
    )
    WHERE rn > 1
);