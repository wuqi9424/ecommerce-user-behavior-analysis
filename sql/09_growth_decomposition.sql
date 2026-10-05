-- 等长周末增长来源拆解（DuckDB，只读连接；辅助对象仅 TEMP）
-- 时间为已转换的 Asia/Shanghai event_time。buy 是事件，不是订单。
-- 周末用户在两天内去重，人均次数=两天事件总量/两天活跃用户；不是日均 DAU 口径。
-- first_observed_date 只基于九天，首次观测不是新增注册或真正获客。
-- 无 acquisition channel/campaign/promotion/traffic source/exposure log，不能判断增长原因。
-- 描述性 bridge：先规模后强度，交互项归入强度；换顺序会改变分量，非唯一因果贡献。
SET TimeZone='Asia/Shanghai';
CREATE OR REPLACE TEMP TABLE gd_windows AS
SELECT * FROM (VALUES
 ('previous_weekend',DATE '2017-11-25',DATE '2017-11-27'),
 ('final_weekend',DATE '2017-12-02',DATE '2017-12-04')
) t(period_label,start_date,end_exclusive);
-- 大表一次按用户压缩，同时取 first_observed_date 和各周末行为数。
CREATE OR REPLACE TEMP TABLE gd_users AS
SELECT user_id,MIN(CAST(event_time AS DATE)) AS first_observed_date,
 COUNT(*) FILTER(WHERE event_time<TIMESTAMP '2017-11-27') AS previous_total_events,
 COUNT(*) FILTER(WHERE event_time<TIMESTAMP '2017-11-27' AND behavior_type='pv') AS previous_pv_events,
 COUNT(*) FILTER(WHERE event_time<TIMESTAMP '2017-11-27' AND behavior_type='fav') AS previous_fav_events,
 COUNT(*) FILTER(WHERE event_time<TIMESTAMP '2017-11-27' AND behavior_type='cart') AS previous_cart_events,
 COUNT(*) FILTER(WHERE event_time<TIMESTAMP '2017-11-27' AND behavior_type='buy') AS previous_buy_events,
 COUNT(*) FILTER(WHERE event_time>=TIMESTAMP '2017-12-02') AS final_total_events,
 COUNT(*) FILTER(WHERE event_time>=TIMESTAMP '2017-12-02' AND behavior_type='pv') AS final_pv_events,
 COUNT(*) FILTER(WHERE event_time>=TIMESTAMP '2017-12-02' AND behavior_type='fav') AS final_fav_events,
 COUNT(*) FILTER(WHERE event_time>=TIMESTAMP '2017-12-02' AND behavior_type='cart') AS final_cart_events,
 COUNT(*) FILTER(WHERE event_time>=TIMESTAMP '2017-12-02' AND behavior_type='buy') AS final_buy_events
FROM user_behavior_clean
WHERE event_time>=TIMESTAMP '2017-11-25' AND event_time<TIMESTAMP '2017-12-04'
GROUP BY user_id;
CREATE OR REPLACE TEMP TABLE gd_summary AS
SELECT 'previous_weekend' AS period_label,COUNT(*) AS active_users,SUM(previous_total_events) AS total_events,SUM(previous_pv_events) AS pv_events,SUM(previous_fav_events) AS fav_events,SUM(previous_cart_events) AS cart_events,SUM(previous_buy_events) AS buy_events FROM gd_users WHERE previous_total_events>0
UNION ALL
SELECT 'final_weekend' AS period_label,COUNT(*) AS active_users,SUM(final_total_events) AS total_events,SUM(final_pv_events) AS pv_events,SUM(final_fav_events) AS fav_events,SUM(final_cart_events) AS cart_events,SUM(final_buy_events) AS buy_events FROM gd_users WHERE final_total_events>0;
-- 1. 总量与两天人均强度。四类行为强度统一以全部活跃用户为分母。
SELECT *,total_events*1.0/active_users AS events_per_active_user,
 pv_events*1.0/active_users AS pv_per_active_user,
 fav_events*1.0/active_users AS fav_per_active_user,
 cart_events*1.0/active_users AS cart_per_active_user,
 buy_events*1.0/active_users AS buy_per_active_user
FROM gd_summary ORDER BY period_label DESC;
CREATE OR REPLACE TEMP TABLE gd_long AS
SELECT period_label,active_users,metric,event_count FROM gd_summary
UNPIVOT(event_count FOR metric IN(total_events,pv_events,fav_events,cart_events,buy_events));
-- 2. E1-E0 = (U1-U0)*I0 + U1*(I1-I0)。不舍入中间计算，保留顺序说明。
CREATE OR REPLACE TEMP TABLE gd_bridge AS
WITH paired AS (
 SELECT p.metric,p.active_users AS previous_users,f.active_users AS final_users,
 p.event_count AS previous_events,f.event_count AS final_events,
 p.event_count*1.0/p.active_users AS previous_intensity,
 f.event_count*1.0/f.active_users AS final_intensity
 FROM gd_long p JOIN gd_long f USING(metric)
 WHERE p.period_label='previous_weekend' AND f.period_label='final_weekend'
)
SELECT *,100.0*(final_users-previous_users)/previous_users AS user_growth_pct,
 100.0*(final_events-previous_events)/NULLIF(previous_events,0) AS event_growth_pct,
 100.0*(final_intensity/NULLIF(previous_intensity,0)-1) AS intensity_growth_pct,
 final_users*previous_intensity AS counterfactual_events,
 (final_users-previous_users)*previous_intensity AS scale_bridge_events,
 final_users*(final_intensity-previous_intensity) AS intensity_bridge_events,
 final_events-previous_events AS observed_event_change
FROM paired;
SELECT * FROM gd_bridge ORDER BY metric;
-- 3. Final 用户结构及事件贡献；购买用户率=至少一次 buy/该组全部用户。
CREATE OR REPLACE TEMP TABLE gd_cohorts AS
SELECT CASE WHEN first_observed_date>=DATE '2017-12-02' THEN 'newly_observed_users'
 ELSE 'previously_observed_users' END AS observed_group,
 COUNT(*) AS users,SUM(final_total_events) AS total_events,
 SUM(final_pv_events) AS pv_events,SUM(final_fav_events) AS fav_events,
 SUM(final_cart_events) AS cart_events,SUM(final_buy_events) AS buy_events,
 SUM(final_total_events)*1.0/COUNT(*) AS events_per_user,
 COUNT(*) FILTER(WHERE final_buy_events>0) AS purchasing_users,
 100.0*COUNT(*) FILTER(WHERE final_buy_events>0)/COUNT(*) AS purchasing_user_rate,
 100.0*COUNT(*)/(SELECT active_users FROM gd_summary WHERE period_label='final_weekend') AS user_share_pct,
 100.0*SUM(final_total_events)/(SELECT total_events FROM gd_summary WHERE period_label='final_weekend') AS event_share_pct
FROM gd_users WHERE final_total_events>0 GROUP BY observed_group;
SELECT * FROM gd_cohorts ORDER BY observed_group;
-- 4. 质量检查；异常数正常为 0。浮点 bridge 残差允许 0.000001 个事件。
SELECT 'invalid_window_length' AS check_name,COUNT(*) AS invalid_rows FROM gd_windows
WHERE DATE_DIFF('day',start_date,end_exclusive)<>2
UNION ALL SELECT 'overlapping_windows',COUNT(*) FROM gd_windows p JOIN gd_windows f
ON p.period_label='previous_weekend' AND f.period_label='final_weekend'
WHERE p.end_exclusive>f.start_date
UNION ALL SELECT 'cohort_population_mismatch',CAST((SELECT SUM(users) FROM gd_cohorts)<>
(SELECT active_users FROM gd_summary WHERE period_label='final_weekend') AS BIGINT)
UNION ALL SELECT 'cohort_event_mismatch',CAST((SELECT SUM(total_events) FROM gd_cohorts)<>
(SELECT total_events FROM gd_summary WHERE period_label='final_weekend') AS BIGINT)
UNION ALL SELECT 'invalid_behavior_sum',COUNT(*) FROM gd_summary
WHERE pv_events+fav_events+cart_events+buy_events<>total_events
UNION ALL SELECT 'invalid_cohort_behavior_sum',COUNT(*) FROM gd_cohorts
WHERE pv_events+fav_events+cart_events+buy_events<>total_events
UNION ALL SELECT 'invalid_user_rates',COUNT(*) FROM gd_cohorts
WHERE purchasing_user_rate NOT BETWEEN 0 AND 100 OR user_share_pct NOT BETWEEN 0 AND 100
OR purchasing_users>users
UNION ALL SELECT 'invalid_bridge_identity',COUNT(*) FROM gd_bridge
WHERE ABS(scale_bridge_events+intensity_bridge_events-observed_event_change)>0.000001
UNION ALL SELECT 'null_user',COUNT(*) FROM gd_users WHERE user_id IS NULL
UNION ALL SELECT 'invalid_first_observed_date',COUNT(*) FROM gd_users
WHERE first_observed_date<DATE '2017-11-25' OR first_observed_date>DATE '2017-12-03';
DROP TABLE gd_cohorts;
DROP TABLE gd_bridge;
DROP TABLE gd_long;
DROP TABLE gd_summary;
DROP TABLE gd_users;
DROP TABLE gd_windows;

-- 实测业务结论（当前数据库）：
-- 1. 两天活跃 864,829→986,594（+14.08%），总事件 +30.14%，人均 24.38→27.81（+14.08%）；
--    规模与强度共同扩张，不能描述为单用户强度没有增长；应同时优化参与与购买承接。
-- 2. 固定先规模 bridge：规模增量约 296.86 万，强度增量约 338.68 万；
--    两者都重要，顺序决定交互项归属，不将此解释为精确因果贡献率。
-- 3. PV/Fav 的两个分量相近；Cart 强度分量更大，Buy 规模分量略大；
--    四类人均均增长，加购参与提升较明显，值得结合既有品类机会测试购买承接。
-- 4. Final 首次观测仅 19 人（0.001926%），贡献 201 事件；应关注此前已观测用户参与，
--    不能将样本增长理解为真实获客成功。19 人购买用户率 57.89% 仅是小样本描述。
-- 限制：九天左右截尾、公开样本覆盖未知、没有渠道/广告/活动/曝光数据，不能推断原因。
