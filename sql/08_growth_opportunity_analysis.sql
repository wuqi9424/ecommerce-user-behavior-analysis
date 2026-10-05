-- 中国电商平台：用户 × 品类机会池（DuckDB）
-- 只读连接运行；只创建 TEMP 对象，不改变源表；同一连接按顺序执行。
-- event_time 已为 Asia/Shanghai 本地时间，范围 [2017-11-25,2017-12-04)。
-- 比较 11/25–11/26 与 12/02–12/03 两个等长周末，每个均为两天。
-- 品类匿名，无真实品类名；无 price/promotion/coupon/channel/order_id。
-- buy 是事件不是订单；这里没有金额、成本或真实触达，不能判断用户未买原因。
-- 本章是 12/03 结束后的机会识别，使用完整九天排除已购买组合；
-- 不能直接作为 SQL 07 在 12/02 启动的实验资格，否则会泄漏未来信息。
-- 策略须用之后真实线上 A/B Test 验证，不能用已有模拟实验宣称有效。
-- 各比例均为同品类、同周末去重用户集合交集/分母，单位 0–100%。
-- 收藏/加购为并集，不要求同商品或时间顺序，不代表严格购买路径。
-- 分母零、增长基期零时 NULL，不将新出现品类伪造为 100% 增长。
-- 用户可对应多个品类/优先层：互斥单位是 user-category，去重用户数不能跨层相加。
-- 只扫描大表一次按 user-category 压缩，再复用；避免事件级自连接。
SET TimeZone = 'Asia/Shanghai';

CREATE OR REPLACE TEMP TABLE growth_weekends AS
SELECT * FROM (VALUES
 ('previous_weekend', DATE '2017-11-25', DATE '2017-11-27'),
 ('final_weekend', DATE '2017-12-02', DATE '2017-12-04')
) t(period_label,start_date,end_exclusive);

-- 空用户/品类不进入机会分析。完整九天购买标记和两个周末计数在同一次扫描计算。
CREATE OR REPLACE TEMP TABLE growth_user_category AS
SELECT user_id,category_id,
 BOOL_OR(behavior_type='buy') AS has_window_buy,
 COUNT(*) FILTER (WHERE event_time<TIMESTAMP '2017-11-27' AND behavior_type='pv') AS previous_pv_count,
 COUNT(*) FILTER (WHERE event_time<TIMESTAMP '2017-11-27' AND behavior_type='fav') AS previous_fav_count,
 COUNT(*) FILTER (WHERE event_time<TIMESTAMP '2017-11-27' AND behavior_type='cart') AS previous_cart_count,
 COUNT(*) FILTER (WHERE event_time<TIMESTAMP '2017-11-27' AND behavior_type='buy') AS previous_buy_count,
 COUNT(*) FILTER (WHERE event_time>=TIMESTAMP '2017-12-02' AND behavior_type='pv') AS recent_pv_count,
 COUNT(*) FILTER (WHERE event_time>=TIMESTAMP '2017-12-02' AND behavior_type='fav') AS recent_fav_count,
 COUNT(*) FILTER (WHERE event_time>=TIMESTAMP '2017-12-02' AND behavior_type='cart') AS recent_cart_count,
 COUNT(*) FILTER (WHERE event_time>=TIMESTAMP '2017-12-02' AND behavior_type='buy') AS recent_buy_count,
 COUNT(*) FILTER (WHERE event_time>=TIMESTAMP '2017-12-02') AS recent_total_events,
 MAX(event_time) FILTER (WHERE event_time>=TIMESTAMP '2017-12-02') AS last_activity_time
FROM user_behavior_clean
WHERE event_time>=TIMESTAMP '2017-11-25' AND event_time<TIMESTAMP '2017-12-04'
 AND user_id IS NOT NULL AND category_id IS NOT NULL
 AND behavior_type IN ('pv','fav','cart','buy')
GROUP BY user_id,category_id;

CREATE OR REPLACE TEMP TABLE growth_category_weekend AS
WITH expanded AS (
 SELECT u.category_id,w.period_label,
 CASE WHEN w.period_label='previous_weekend' THEN previous_pv_count ELSE recent_pv_count END AS pv,
 CASE WHEN w.period_label='previous_weekend' THEN previous_fav_count ELSE recent_fav_count END AS fav,
 CASE WHEN w.period_label='previous_weekend' THEN previous_cart_count ELSE recent_cart_count END AS cart,
 CASE WHEN w.period_label='previous_weekend' THEN previous_buy_count ELSE recent_buy_count END AS buy
 FROM growth_user_category u CROSS JOIN growth_weekends w
), counts AS (
 SELECT category_id,period_label,
 COUNT(*) FILTER (WHERE pv>0) AS pv_users,
 COUNT(*) FILTER (WHERE fav>0 OR cart>0) AS intent_users,
 COUNT(*) FILTER (WHERE cart>0) AS cart_users,
 COUNT(*) FILTER (WHERE fav>0) AS fav_users,
 COUNT(*) FILTER (WHERE buy>0) AS buy_users,
 COUNT(*) FILTER (WHERE pv>0 AND (fav>0 OR cart>0)) AS pv_intent_users,
 COUNT(*) FILTER (WHERE (fav>0 OR cart>0) AND buy>0) AS intent_buy_users,
 COUNT(*) FILTER (WHERE pv>0 AND buy>0) AS pv_buy_users
 FROM expanded GROUP BY category_id,period_label
)
SELECT *,100.0*pv_intent_users/NULLIF(pv_users,0) AS pv_to_intent_user_rate,
 100.0*intent_buy_users/NULLIF(intent_users,0) AS intent_to_buy_user_rate,
 100.0*pv_buy_users/NULLIF(pv_users,0) AS pv_to_buy_user_rate
FROM counts;
-- intent_to_buy 在本章为意向且购买/全部意向，未额外要求 pv；
-- 因而不是 SQL 04 三层完整集合漏斗的中间转化指标，不应混用。

CREATE OR REPLACE TEMP TABLE growth_category_comparison AS
SELECT p.category_id,
 p.pv_users AS previous_pv_users,f.pv_users AS final_pv_users,
 p.intent_users AS previous_intent_users,f.intent_users AS final_intent_users,
 p.cart_users AS previous_cart_users,f.cart_users AS final_cart_users,
 p.fav_users AS previous_fav_users,f.fav_users AS final_fav_users,
 p.buy_users AS previous_buy_users,f.buy_users AS final_buy_users,
 p.pv_to_intent_user_rate AS previous_pv_to_intent_user_rate,
 f.pv_to_intent_user_rate AS final_pv_to_intent_user_rate,
 p.intent_to_buy_user_rate AS previous_intent_to_buy_user_rate,
 f.intent_to_buy_user_rate AS final_intent_to_buy_user_rate,
 p.pv_to_buy_user_rate AS previous_pv_to_buy_user_rate,
 f.pv_to_buy_user_rate AS final_pv_to_buy_user_rate,
 100.0*(f.pv_users-p.pv_users)/NULLIF(p.pv_users,0) AS pv_user_growth_pct,
 100.0*(f.intent_users-p.intent_users)/NULLIF(p.intent_users,0) AS intent_user_growth_pct,
 100.0*(f.buy_users-p.buy_users)/NULLIF(p.buy_users,0) AS buy_user_growth_pct,
 f.pv_to_buy_user_rate-p.pv_to_buy_user_rate AS pv_to_buy_rate_change_pp,
 f.intent_to_buy_user_rate-p.intent_to_buy_user_rate AS intent_to_buy_rate_change_pp
FROM growth_category_weekend p JOIN growth_category_weekend f USING(category_id)
WHERE p.period_label='previous_weekend' AND f.period_label='final_weekend'
 AND (p.pv_users+p.intent_users+p.buy_users+f.pv_users+f.intent_users+f.buy_users)>0;

-- 1. 全量品类结果保留，不直接给小品类排名；用户数跨品类不能相加为平台人数。
SELECT * FROM growth_category_comparison ORDER BY category_id;

-- 2. 先输出规模分布，再按固定 Q75 规则筛选，规则不参考增长或下降结果。
-- 用两个周末较小的 PV/Intent 人数衡量可比较规模，排除基期没有该分母的品类。
-- 最小 PV 与最小 Intent 各达到其正值品类分布 Q75 才参与排名，
-- 兼顾流量和意向分母；是运营规模筛选，不是显著性检验，仍可能存在噪音。
SELECT period_label,COUNT(*) AS categories,
 QUANTILE_DISC(pv_users,[0.25,0.5,0.75,0.9]) AS pv_user_quantiles,
 QUANTILE_DISC(intent_users,[0.25,0.5,0.75,0.9]) AS intent_user_quantiles
FROM growth_category_weekend
WHERE pv_users+intent_users+buy_users>0 GROUP BY period_label ORDER BY period_label;

CREATE OR REPLACE TEMP TABLE growth_thresholds AS
SELECT QUANTILE_DISC(LEAST(previous_pv_users,final_pv_users),0.75)
 FILTER (WHERE previous_pv_users>0 AND final_pv_users>0) AS min_pv_users,
 QUANTILE_DISC(LEAST(previous_intent_users,final_intent_users),0.75)
 FILTER (WHERE previous_intent_users>0 AND final_intent_users>0) AS min_intent_users
FROM growth_category_comparison;
SELECT *, 'Q75 of positive minimum weekend users; both PV and Intent required' AS volume_rule
FROM growth_thresholds;
CREATE OR REPLACE TEMP TABLE growth_volume_filtered AS
SELECT c.* FROM growth_category_comparison c CROSS JOIN growth_thresholds t
WHERE LEAST(previous_pv_users,final_pv_users)>=t.min_pv_users
 AND LEAST(previous_intent_users,final_intent_users)>=t.min_intent_users;
SELECT * FROM growth_volume_filtered ORDER BY category_id;

-- 3. 未买组合：完整九天内该品类没有 buy；不排除用户在其他品类购买。
CREATE OR REPLACE TEMP TABLE growth_no_buy_pairs AS
SELECT user_id,category_id,recent_pv_count,recent_fav_count,recent_cart_count,
 recent_total_events,last_activity_time
FROM growth_user_category WHERE NOT has_window_buy AND recent_total_events>0;

-- P3 只在无意向且未买、至少一次近期浏览的组合中取浏览次数 Q75。
-- 同分位并列全部纳入，不保证恰好占 25%；事件数仅用于参与强度，不作转化率。
CREATE OR REPLACE TEMP TABLE growth_browse_threshold AS
SELECT QUANTILE_DISC(recent_pv_count,[0.25,0.5,0.75,0.9]) AS pv_count_quantiles,
 QUANTILE_DISC(recent_pv_count,0.75) AS high_pv_count,
 COUNT(*) AS browse_only_no_buy_pairs
FROM growth_no_buy_pairs WHERE recent_cart_count=0 AND recent_fav_count=0 AND recent_pv_count>0;
SELECT *,'Q75 among recent browse-only pairs with no nine-day category buy' AS p3_rule
FROM growth_browse_threshold;

CREATE OR REPLACE TEMP TABLE growth_opportunity_pool AS
WITH prioritized AS (
 SELECT p.*,CASE WHEN recent_cart_count>0 THEN 'P1'
 WHEN recent_fav_count>0 THEN 'P2'
 WHEN recent_pv_count>0 AND recent_pv_count>=t.high_pv_count THEN 'P3' END AS priority_segment
 FROM growth_no_buy_pairs p CROSS JOIN growth_browse_threshold t
)
SELECT * FROM prioritized WHERE priority_segment IS NOT NULL;

-- 4. 机会品类：流量和意向均增长、购买增速落后至少一个、至少一种集合比例下降。
-- 无基期购买时增长为 NULL，不纳入该增长比较；低于另一增长使用 OR 明确解释。
-- 排名按九天品类未买、近期有意向的组合人数降序，再按流量规模、品类 ID 排序。
-- 没有加权得分；这个规模不是预期增量购买数，更不是预期收益。
CREATE OR REPLACE TEMP TABLE growth_candidate_categories AS
WITH opportunity_counts AS (
 SELECT category_id,COUNT(*) FILTER (WHERE priority_segment IN ('P1','P2')) AS high_intent_no_buy_users,
 COUNT(*) FILTER (WHERE priority_segment='P1') AS p1_users,
 COUNT(*) FILTER (WHERE priority_segment='P2') AS p2_users,
 COUNT(*) FILTER (WHERE priority_segment='P3') AS p3_users
 FROM growth_opportunity_pool GROUP BY category_id
), candidates AS (
 SELECT c.*,COALESCE(o.high_intent_no_buy_users,0) AS high_intent_no_buy_users,
 COALESCE(o.p1_users,0) AS p1_users,COALESCE(o.p2_users,0) AS p2_users,COALESCE(o.p3_users,0) AS p3_users
 FROM growth_volume_filtered c LEFT JOIN opportunity_counts o USING(category_id)
 WHERE final_pv_users>previous_pv_users AND final_intent_users>previous_intent_users
 AND (buy_user_growth_pct<pv_user_growth_pct OR buy_user_growth_pct<intent_user_growth_pct)
 AND (pv_to_buy_rate_change_pp<0 OR intent_to_buy_rate_change_pp<0)
)
SELECT ROW_NUMBER() OVER(ORDER BY high_intent_no_buy_users DESC,final_pv_users DESC,category_id) AS opportunity_rank,*
FROM candidates;
SELECT * FROM growth_candidate_categories ORDER BY opportunity_rank;

-- 5. 结合用户与品类，两种 pool_scope 分开输出完整池和机会品类子池。
-- 平均次数与最近活跃按 pair 等权，不是按用户等权；最近活跃小时自 12/04 00:00 计算。
-- Final 最后一天活跃占比：pair 的 last_activity_time >= 12/03。
CREATE OR REPLACE TEMP TABLE growth_combined_pool AS
SELECT p.*,c.opportunity_rank FROM growth_opportunity_pool p JOIN growth_candidate_categories c USING(category_id);
WITH expanded AS (
 SELECT 'all_categories' AS pool_scope,* EXCLUDE(opportunity_rank) FROM
 (SELECT p.*,NULL::BIGINT AS opportunity_rank FROM growth_opportunity_pool p)
 UNION ALL
 SELECT 'candidate_categories',* EXCLUDE(opportunity_rank) FROM growth_combined_pool
), scopes AS (
 SELECT * FROM (VALUES ('all_categories'),('candidate_categories')) t(pool_scope)
), priorities AS (
 SELECT * FROM (VALUES ('P1'),('P2'),('P3')) t(priority_segment)
)
SELECT s.pool_scope,k.priority_segment,COUNT(e.user_id) AS user_category_pairs,
 COUNT(DISTINCT e.user_id) AS unique_users,COUNT(DISTINCT e.category_id) AS categories,
 ROUND(AVG(e.recent_pv_count),4) AS avg_recent_pv,
 ROUND(AVG(e.recent_fav_count),4) AS avg_recent_fav,
 ROUND(AVG(e.recent_cart_count),4) AS avg_recent_cart,
 ROUND(AVG(EPOCH(TIMESTAMP '2017-12-04'-e.last_activity_time)/3600.0),4) AS avg_hours_since_last_activity,
 ROUND(100.0*COUNT(e.user_id) FILTER(WHERE e.last_activity_time>=TIMESTAMP '2017-12-03')/
       NULLIF(COUNT(e.user_id),0),4) AS active_on_final_day_pair_pct
FROM scopes s CROSS JOIN priorities k LEFT JOIN expanded e
 ON e.pool_scope=s.pool_scope AND e.priority_segment=k.priority_segment
GROUP BY s.pool_scope,k.priority_segment ORDER BY s.pool_scope,k.priority_segment;

SELECT (SELECT COUNT(*) FROM growth_category_comparison) AS all_categories,
 (SELECT COUNT(*) FROM growth_volume_filtered) AS volume_filtered_categories,
 (SELECT COUNT(*) FROM growth_candidate_categories) AS candidate_categories,
 COUNT(*) AS combined_pairs,COUNT(DISTINCT user_id) AS combined_unique_users
FROM growth_combined_pool;
-- 全量机会池保留为 TEMP 表，明细只预览；需导出可在清理前读取。
SELECT * FROM growth_combined_pool ORDER BY priority_segment,opportunity_rank,user_id LIMIT 20;

-- P1：购物车召回、权益/价格提醒候选；P2：收藏提醒、内容强化候选；
-- P3：推荐优化、兴趣培育候选。需补实际品类、实时商品及触达数据后设计真实 A/B Test。
-- 当前不知道为什么未买；用户可能在其他平台买、窗后买或尚未形成购买需求。
-- 品类用户构成变化、多个品类同时筛选、两天购买滞后都可能影响比例；
-- 排名不证明承接问题显著，更不证明某种营销动作有效。

-- 6. 所有异常数应为 0。
SELECT 'existing_category_buy_in_pool' AS check_name,COUNT(*) AS invalid_rows
FROM growth_opportunity_pool p JOIN growth_user_category u USING(user_id,category_id) WHERE u.has_window_buy
UNION ALL SELECT 'duplicate_priority_pair',COUNT(*) FROM
 (SELECT user_id,category_id FROM growth_opportunity_pool GROUP BY user_id,category_id HAVING COUNT(*)<>1)
UNION ALL SELECT 'invalid_priority',COUNT(*) FROM growth_opportunity_pool p CROSS JOIN growth_browse_threshold t
WHERE priority_segment NOT IN ('P1','P2','P3')
 OR (priority_segment='P1' AND recent_cart_count<=0)
 OR (priority_segment='P2' AND (recent_cart_count<>0 OR recent_fav_count<=0))
 OR (priority_segment='P3' AND (recent_cart_count<>0 OR recent_fav_count<>0 OR recent_pv_count<t.high_pv_count))
UNION ALL SELECT 'invalid_rates',COUNT(*) FROM growth_category_weekend
WHERE pv_to_intent_user_rate NOT BETWEEN 0 AND 100 OR intent_to_buy_user_rate NOT BETWEEN 0 AND 100
 OR pv_to_buy_user_rate NOT BETWEEN 0 AND 100
 OR pv_intent_users>pv_users OR intent_buy_users>intent_users OR pv_buy_users>pv_users
UNION ALL SELECT 'invalid_weekend_length',COUNT(*) FROM growth_weekends
WHERE DATE_DIFF('day',start_date,end_exclusive)<>2
 OR EXTRACT(DOW FROM start_date)<>6 OR EXTRACT(DOW FROM end_exclusive-1)<>0
UNION ALL SELECT 'invalid_recent_pair',COUNT(*) FROM growth_opportunity_pool
WHERE recent_total_events<>recent_pv_count+recent_fav_count+recent_cart_count
 OR last_activity_time<TIMESTAMP '2017-12-02' OR last_activity_time>=TIMESTAMP '2017-12-04'
UNION ALL SELECT 'combined_not_candidate',COUNT(*) FROM growth_combined_pool p
LEFT JOIN growth_candidate_categories c USING(category_id) WHERE c.category_id IS NULL;

DROP TABLE IF EXISTS growth_combined_pool;
DROP TABLE IF EXISTS growth_candidate_categories;
DROP TABLE IF EXISTS growth_opportunity_pool;
DROP TABLE IF EXISTS growth_browse_threshold;
DROP TABLE IF EXISTS growth_no_buy_pairs;
DROP TABLE IF EXISTS growth_volume_filtered;
DROP TABLE IF EXISTS growth_thresholds;
DROP TABLE IF EXISTS growth_category_comparison;
DROP TABLE IF EXISTS growth_category_weekend;
DROP TABLE IF EXISTS growth_user_category;
DROP TABLE IF EXISTS growth_weekends;
