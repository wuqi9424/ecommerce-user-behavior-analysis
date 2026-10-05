-- 中国电商平台：RF + 用户行为特征分层（DuckDB）
-- 不是标准 RFM：没有价格、金额，不计算 Monetary，不推断收入/利润/消费价值。
-- 时间：[2017-11-25 00:00:00, 2017-12-04 00:00:00)，Asia/Shanghai。
-- event_time 已转换为上海本地 TIMESTAMP。Recency 锚定 2017-12-03，绝非当前日期。
-- 只创建 TEMP 对象；同一连接按顺序运行；不修改源表。所有人数精确去重。
-- 不知道窗前历史，所谓未购买/仅浏览均限定本窗口；buy event 不是 order。
-- 各行为沿用 pv/fav/cart/buy；行为标记可重叠，不证明同商品、有序订单链路。
-- F 优先采用不同购买日期数，避免同日多条 buy 日志直接抬高“跨日频次”。
-- 规则阈值用 QUANTILE_DISC（实际整数值）；分位点上的并列全部纳入，
-- 因此“上四分位阈值以上”不保证恰好占 25%；阈值随数据变化，不是行业标准。
-- 九天窗口有左右截尾、样本选择与时间构成限制，晚进入用户观察机会更少。
-- “近期”“沉寂”“高频”仅为本窗口相对状态，不是长期生命周期或真实流失。
-- 运营建议是待验证假设：需结合品类、触达权限、成本与实验，不宣称因果效果。
-- 性能：用户-日、用户-商品、用户-品类分别聚合，避免大表多对多连接、
-- 避免同时构建多个巨大 COUNT DISTINCT 集合；辅助表可 spill，占用临时磁盘。
SET TimeZone = 'Asia/Shanghai';

-- 1. 用户-日行为汇总；排除空 user_id，仅纳入约定日期与四类行为。
CREATE OR REPLACE TEMP TABLE segmentation_user_day AS
SELECT user_id, CAST(event_time AS DATE) AS event_date,
       COUNT(*) AS total_events,
       COUNT(*) FILTER (WHERE behavior_type = 'pv') AS pv_events,
       COUNT(*) FILTER (WHERE behavior_type = 'cart') AS cart_events,
       COUNT(*) FILTER (WHERE behavior_type = 'fav') AS fav_events,
       COUNT(*) FILTER (WHERE behavior_type = 'buy') AS buy_event_count
FROM user_behavior_clean
WHERE event_time >= TIMESTAMP '2017-11-25' AND event_time < TIMESTAMP '2017-12-04'
  AND user_id IS NOT NULL AND behavior_type IN ('pv', 'fav', 'cart', 'buy')
GROUP BY user_id, event_date;

-- item/category 空值不计入去重实体数，但其事件仍计入行为总量。
CREATE OR REPLACE TEMP TABLE segmentation_item_metrics AS
WITH pairs AS (
    SELECT user_id, item_id,
           BOOL_OR(behavior_type = 'pv') AS has_pv,
           BOOL_OR(behavior_type = 'buy') AS has_buy
    FROM user_behavior_clean
    WHERE event_time >= TIMESTAMP '2017-11-25' AND event_time < TIMESTAMP '2017-12-04'
      AND user_id IS NOT NULL AND item_id IS NOT NULL AND behavior_type IN ('pv', 'buy')
    GROUP BY user_id, item_id
)
SELECT user_id, COUNT(*) FILTER (WHERE has_pv) AS pv_distinct_items,
       COUNT(*) FILTER (WHERE has_buy) AS buy_distinct_items
FROM pairs GROUP BY user_id;

CREATE OR REPLACE TEMP TABLE segmentation_category_metrics AS
WITH pairs AS (
    SELECT user_id, category_id, BOOL_OR(behavior_type = 'buy') AS has_buy
    FROM user_behavior_clean
    WHERE event_time >= TIMESTAMP '2017-11-25' AND event_time < TIMESTAMP '2017-12-04'
      AND user_id IS NOT NULL AND category_id IS NOT NULL
      AND behavior_type IN ('pv', 'fav', 'cart', 'buy')
    GROUP BY user_id, category_id
)
SELECT user_id, COUNT(*) AS distinct_categories,
       COUNT(*) FILTER (WHERE has_buy) AS buy_distinct_categories
FROM pairs GROUP BY user_id;

CREATE OR REPLACE TEMP TABLE segmentation_features AS
WITH activity AS (
    SELECT user_id, COUNT(*) AS active_days,
           MIN(event_date) AS first_observed_active_date,
           MAX(event_date) AS last_active_date,
           SUM(total_events) AS total_events, SUM(pv_events) AS pv_events,
           SUM(cart_events) AS cart_events, SUM(fav_events) AS fav_events,
           SUM(buy_event_count) AS buy_event_count,
           COUNT(*) FILTER (WHERE buy_event_count > 0) AS buy_active_days,
           MAX(event_date) FILTER (WHERE buy_event_count > 0) AS last_purchase_date
    FROM segmentation_user_day GROUP BY user_id
)
SELECT a.*, DATE_DIFF('day', last_purchase_date, DATE '2017-12-03') AS recency_days,
       COALESCE(i.pv_distinct_items, 0) AS pv_distinct_items,
       COALESCE(i.buy_distinct_items, 0) AS buy_distinct_items,
       COALESCE(c.distinct_categories, 0) AS distinct_categories,
       COALESCE(c.buy_distinct_categories, 0) AS buy_distinct_categories,
       (pv_events > 0 AND cart_events = 0 AND fav_events = 0 AND buy_event_count = 0)
           AS is_browse_only,
       (cart_events > 0 AND buy_event_count = 0) AS is_cart_without_buy,
       (fav_events > 0 AND buy_event_count = 0) AS is_favorite_without_buy,
       (buy_event_count > 0) AS is_purchasing_user,
       (buy_active_days >= 2) AS is_cross_day_repeat_buyer,
       (active_days >= 2) AS is_multi_day_active
FROM activity a LEFT JOIN segmentation_item_metrics i USING (user_id)
LEFT JOIN segmentation_category_metrics c USING (user_id);

-- 2. 先输出分位分布，规则阈值每次动态计算；非购买用户 recency 为 NULL。
CREATE OR REPLACE TEMP TABLE segmentation_thresholds AS
SELECT QUANTILE_DISC(active_days, 0.75) AS all_active_days_q75,
       QUANTILE_DISC(active_days, 0.75) FILTER (WHERE NOT is_purchasing_user)
           AS nonbuyer_active_days_q75,
       QUANTILE_DISC(buy_active_days, 0.75) FILTER (WHERE is_purchasing_user)
           AS buyer_buy_days_q75,
       QUANTILE_DISC(recency_days, 0.5) FILTER (WHERE is_purchasing_user)
           AS buyer_recency_median,
       QUANTILE_DISC(recency_days, 0.75) FILTER (WHERE is_purchasing_user)
           AS buyer_recency_q75
FROM segmentation_features;
SELECT * FROM segmentation_thresholds;
SELECT QUANTILE_DISC(active_days, [0.25, 0.5, 0.75]) AS active_days_quartiles,
       QUANTILE_DISC(buy_active_days, [0.25, 0.5, 0.75]) FILTER (WHERE is_purchasing_user)
           AS buyer_buy_days_quartiles,
       QUANTILE_DISC(recency_days, [0.25, 0.5, 0.75]) FILTER (WHERE is_purchasing_user)
           AS buyer_recency_quartiles
FROM segmentation_features;

-- 3. 互斥 CASE，按以下优先级首次命中；未命中者有明确兜底。
-- 购买沉寂先于高频，避免用历史窗口内频次覆盖最近购买已较远的状态。
-- 再识别近期且高频且高活跃；不称高价值，因为没有 Monetary。
-- 高频至少两个购买日期（重复购买的定义），且达到购买日 Q75。
-- 非购买用户先识别活跃日达到非购买人群 Q75，再按加购、收藏分组。
-- “相对低活跃仅浏览”指低于该活跃阈值、无任何意向与购买，非绝对低活跃。
CREATE OR REPLACE TEMP TABLE segmentation_users AS
SELECT f.*,
       CASE
         WHEN is_purchasing_user AND recency_days > t.buyer_recency_q75
           THEN 'purchase_cooling'
         WHEN is_purchasing_user AND recency_days <= t.buyer_recency_median
              AND buy_active_days >= GREATEST(2, t.buyer_buy_days_q75)
              AND active_days >= t.all_active_days_q75
           THEN 'active_frequent_buyer'
         WHEN is_purchasing_user AND buy_active_days >= GREATEST(2, t.buyer_buy_days_q75)
           THEN 'frequent_buyer'
         WHEN is_purchasing_user AND recency_days <= t.buyer_recency_median
           THEN 'recent_buyer'
         WHEN is_purchasing_user THEN 'other_buyer'
         WHEN active_days >= t.nonbuyer_active_days_q75 THEN 'active_nonbuyer'
         WHEN is_cart_without_buy THEN 'cart_nonbuyer'
         WHEN is_favorite_without_buy THEN 'favorite_nonbuyer'
         ELSE 'lower_activity_browser'
       END AS segment_code
FROM segmentation_features f CROSS JOIN segmentation_thresholds t;

-- 中文业务说明及待验证动作。所有标签均限定九天窗口。
CREATE OR REPLACE TEMP TABLE segmentation_catalog AS
SELECT * FROM (VALUES
 (1, 'active_frequent_buyer', '高活跃高频购买用户',
  '近期且高频、活跃日达到全体 Q75；属于行为表现突出群体，不代表高消费价值',
  '重点维护；会员权益测试；个性化推荐；新品优先触达'),
 (2, 'frequent_buyer', '高频重复购买用户',
  '购买日达到购买人群 Q75、至少两天；排除已命中的沉寂与高活跃高频群体',
  '关联推荐；组合购测试；忠诚度运营'),
 (3, 'recent_buyer', '近期购买用户',
  '新近度不超过购买人群中位数；排除更高优先级群体',
  '购买后推荐；交叉销售；二次购买引导'),
 (4, 'purchase_cooling', '购买沉寂观察用户',
  '新近度严格高于购买人群 Q75；可能仍在浏览，不能直接判定流失',
  '先检查最近活跃及品类周期；测试召回优惠、内容触达与商品推荐'),
 (5, 'other_buyer', '其他已购买用户',
  '其余购买用户，避免强行赋予近期或流失标签',
  '按品类与意向补充分析；常规推荐与购买后维护'),
 (6, 'active_nonbuyer', '高活跃未购买用户',
  '未观察到购买，活跃日达到非购买人群 Q75；可能同时加购/收藏',
  '分析高浏览低购买品类；优化转化；个性化推荐与优惠券测试'),
 (7, 'cart_nonbuyer', '加购未购买用户',
  '未购买且有加购，未达到高活跃层；可以同时收藏',
  '加购召回；限时优惠测试；在商品信息可用时提供价格/库存提醒'),
 (8, 'favorite_nonbuyer', '收藏未购买用户',
  '未购买、有收藏、无加购，未达到高活跃层',
  '兴趣内容与收藏商品推荐；轻量触达测试'),
 (9, 'lower_activity_browser', '相对低活跃仅浏览用户',
  '未购买、无加购收藏且活跃日低于非购买人群 Q75',
  '低成本唤醒；兴趣探索；控制触达频率，避免过度营销')
) t(sort_order, segment_code, segment_name, behavior_description, suggested_actions);
SELECT * FROM segmentation_catalog ORDER BY sort_order;

-- 4. 全量用户特征保存在 TEMP segmentation_users 中；前 20 行仅为明细预览。
-- 如需全量明细，在清理段前 SELECT * FROM segmentation_users。
SELECT * FROM segmentation_users ORDER BY user_id LIMIT 20;

-- 行为标记可以重叠；它们不是最终互斥分群。
SELECT COUNT(*) AS total_users,
       COUNT(*) FILTER (WHERE is_browse_only) AS browse_only_users,
       COUNT(*) FILTER (WHERE is_cart_without_buy) AS cart_without_buy_users,
       COUNT(*) FILTER (WHERE is_favorite_without_buy) AS favorite_without_buy_users,
       COUNT(*) FILTER (WHERE is_purchasing_user) AS purchasing_users,
       COUNT(*) FILTER (WHERE is_cross_day_repeat_buyer) AS repeat_purchase_users,
       COUNT(*) FILTER (WHERE is_multi_day_active) AS multi_day_active_users
FROM segmentation_users;

-- 5. 群体汇总：各平均值按用户等权；分母为群体人数。
-- 非购买群体购买次数/购买日数为 0，recency 不适用，AVG(NULL) 返回 NULL。
-- 目录 LEFT JOIN 确保零人数群体仍显示，空群体均值 NULL。
CREATE OR REPLACE TEMP TABLE segmentation_summary AS
SELECT c.sort_order, c.segment_code, c.segment_name, COUNT(u.user_id) AS users,
       ROUND(100.0 * COUNT(u.user_id) / NULLIF((SELECT COUNT(*) FROM segmentation_users), 0), 4)
           AS user_share_pct,
       ROUND(AVG(u.active_days), 4) AS avg_active_days,
       ROUND(AVG(u.buy_event_count), 4) AS avg_buy_event_count,
       ROUND(AVG(u.buy_active_days), 4) AS avg_buy_active_days,
       ROUND(AVG(u.pv_events), 4) AS avg_pv_events,
       ROUND(AVG(u.cart_events), 4) AS avg_cart_events,
       ROUND(AVG(u.fav_events), 4) AS avg_fav_events,
       ROUND(AVG(u.recency_days), 4) AS avg_recency_days,
       ROUND(AVG(u.pv_distinct_items), 4) AS avg_pv_distinct_items,
       ROUND(AVG(u.buy_distinct_items), 4) AS avg_buy_distinct_items,
       ROUND(AVG(u.buy_distinct_categories), 4) AS avg_buy_distinct_categories,
       ROUND(AVG(DATE_DIFF('day', u.last_active_date, DATE '2017-12-03')), 4)
           AS avg_days_since_last_active
FROM segmentation_catalog c LEFT JOIN segmentation_users u USING (segment_code)
GROUP BY c.sort_order, c.segment_code, c.segment_name;
SELECT * FROM segmentation_summary ORDER BY sort_order;

-- 6. 独立参照计算：按源表用户-日购买标记，沿用 05 的跨日重复购买定义。
-- 另外输出前章已验证快照供人工核对：购买 672404，重复购买 370274，总用户 987991。
-- 不将快照硬编码为动态规则；数据更新时参照重算仍有效，历史数值应重新核验。
CREATE OR REPLACE TEMP TABLE segmentation_reference AS
WITH days AS (
    SELECT user_id, CAST(event_time AS DATE) AS event_date,
           BOOL_OR(behavior_type = 'buy') AS has_buy
    FROM user_behavior_clean
    WHERE event_time >= TIMESTAMP '2017-11-25' AND event_time < TIMESTAMP '2017-12-04'
      AND user_id IS NOT NULL AND behavior_type IN ('pv', 'fav', 'cart', 'buy')
    GROUP BY user_id, event_date
), users AS (
    SELECT user_id, COUNT(*) FILTER (WHERE has_buy) AS buy_days FROM days GROUP BY user_id
)
SELECT COUNT(*) AS total_users,
       COUNT(*) FILTER (WHERE buy_days > 0) AS purchasing_users,
       COUNT(*) FILTER (WHERE buy_days >= 2) AS repeat_purchase_users FROM users;
SELECT * FROM segmentation_reference;

-- 7. 所有异常数量应为 0；合法行为多寡不设任意“最大购买次数”上限。
SELECT 'duplicate_segment_users' AS check_name, COUNT(*) AS invalid_rows
FROM (SELECT user_id FROM segmentation_users GROUP BY user_id HAVING COUNT(*) <> 1)
UNION ALL
SELECT 'missing_or_unknown_segment', COUNT(*) FROM segmentation_users u
LEFT JOIN segmentation_catalog c USING (segment_code) WHERE c.segment_code IS NULL
UNION ALL
SELECT 'segment_total_mismatch', ABS((SELECT SUM(users) FROM segmentation_summary) -
                                     (SELECT COUNT(*) FROM segmentation_features))
UNION ALL
SELECT 'invalid_recency', COUNT(*) FROM segmentation_users
WHERE (is_purchasing_user AND (recency_days IS NULL OR recency_days NOT BETWEEN 0 AND 8
       OR last_purchase_date IS NULL))
   OR (NOT is_purchasing_user AND (recency_days IS NOT NULL OR last_purchase_date IS NOT NULL))
UNION ALL
SELECT 'invalid_frequency', COUNT(*) FROM segmentation_users
WHERE buy_event_count < 0 OR buy_active_days NOT BETWEEN 0 AND 9
   OR buy_active_days > active_days OR buy_event_count < buy_active_days
   OR buy_distinct_items < 0 OR buy_distinct_items > buy_event_count
   OR buy_distinct_categories < 0 OR buy_distinct_categories > buy_event_count
   OR buy_distinct_categories > distinct_categories
   OR ((buy_event_count = 0) <> (buy_active_days = 0))
UNION ALL
SELECT 'invalid_activity', COUNT(*) FROM segmentation_users
WHERE active_days NOT BETWEEN 1 AND 9 OR total_events <> pv_events + cart_events + fav_events + buy_event_count
   OR total_events < active_days OR pv_distinct_items > pv_events OR distinct_categories > total_events
UNION ALL
SELECT 'reference_total_mismatch', ABS((SELECT COUNT(*) FROM segmentation_users) -
                                     (SELECT total_users FROM segmentation_reference))
UNION ALL
SELECT 'purchasing_users_mismatch', ABS((SELECT COUNT(*) FROM segmentation_users WHERE is_purchasing_user) -
                                      (SELECT purchasing_users FROM segmentation_reference))
UNION ALL
SELECT 'repeat_purchase_users_mismatch', ABS((SELECT COUNT(*) FROM segmentation_users WHERE is_cross_day_repeat_buyer) -
                                           (SELECT repeat_purchase_users FROM segmentation_reference));

DROP TABLE IF EXISTS segmentation_reference;
DROP TABLE IF EXISTS segmentation_summary;
DROP TABLE IF EXISTS segmentation_catalog;
DROP TABLE IF EXISTS segmentation_users;
DROP TABLE IF EXISTS segmentation_thresholds;
DROP TABLE IF EXISTS segmentation_features;
DROP TABLE IF EXISTS segmentation_category_metrics;
DROP TABLE IF EXISTS segmentation_item_metrics;
DROP TABLE IF EXISTS segmentation_user_day;
