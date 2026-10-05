-- 购物车召回提醒：A/B Test analysis pipeline simulation（DuckDB）
-- 历史数据没有真实提醒、触达/曝光或实验分组记录。本文件仅将历史用户固定分组，
-- 演示人群构建、平衡检查、结果聚合；不是历史真实实验，不估计提醒的因果效果。
-- 不伪造 treatment outcome；两组均使用原始历史结果，差异只是标签下的描述性差异。
-- 在同一只读连接顺序执行，辅助对象全为 TEMP，不修改源表。
-- event_time 已是 Asia/Shanghai 本地 TIMESTAMP，不重复转换。
-- 实验单位 user_id；仅纳入非空用户和 pv/fav/cart/buy。
-- Pre: [2017-11-25 00:00,2017-12-02 00:00)，Outcome: [2017-12-02 00:00,2017-12-04 00:00)。
-- 分组和 eligibility 均不依赖 Outcome；不复用九天分层标签，避免未来信息泄漏。
-- 主指标分母为所有已分组用户，不按 Outcome 活跃筛选，保持类 ITT 口径。
-- buy event 不是 order；前期无 buy 不代表用户此前从未购买。
-- 只有两天结果窗口且无触达数据，不能说明长期效果、可触达性或真实随机实验有效性。
-- 正式实验还需事先约定曝光/送达、样本量与 MDE、停止规则、护栏指标和观察期限。
-- 效率：Pre 大表一次用户聚合；Outcome 仅连接已聚合的一人一行 assignment。
SET TimeZone = 'Asia/Shanghai';

-- 集中配置半开窗口，查询过滤和窗口检查使用同一组参数。
CREATE OR REPLACE TEMP TABLE ab_windows AS
SELECT TIMESTAMP '2017-11-25' AS pre_start,
       TIMESTAMP '2017-12-02' AS pre_end,
       TIMESTAMP '2017-12-02' AS outcome_start,
       TIMESTAMP '2017-12-04' AS outcome_end;

-- 1. 仅用 Pre 数据聚合。category NULL 不纳入不同品类数。
-- pre_recency_days 锚定实验开始日 12/02：12/01 活跃为 1 天，11/30 为 2 天。
CREATE OR REPLACE TEMP TABLE ab_pre_features AS
SELECT user_id,
       COUNT(*) FILTER (WHERE behavior_type = 'pv') AS pre_pv_count,
       COUNT(*) FILTER (WHERE behavior_type = 'fav') AS pre_fav_count,
       COUNT(*) FILTER (WHERE behavior_type = 'cart') AS pre_cart_count,
       COUNT(*) FILTER (WHERE behavior_type = 'buy') AS pre_buy_count,
       COUNT(*) AS pre_total_events,
       COUNT(DISTINCT CAST(event_time AS DATE)) AS pre_active_days,
       COUNT(DISTINCT category_id) AS pre_category_count,
       MAX(CAST(event_time AS DATE)) AS pre_last_active_date,
       DATE_DIFF('day', MAX(CAST(event_time AS DATE)), DATE '2017-12-02') AS pre_recency_days,
       MIN(event_time) AS pre_min_event_time, MAX(event_time) AS pre_max_event_time
FROM user_behavior_clean
WHERE event_time >= (SELECT pre_start FROM ab_windows) AND event_time < (SELECT pre_end FROM ab_windows)
  AND user_id IS NOT NULL AND behavior_type IN ('pv','fav','cart','buy')
GROUP BY user_id;

CREATE OR REPLACE TEMP TABLE ab_eligible AS
SELECT * FROM ab_pre_features
WHERE pre_cart_count > 0 AND pre_buy_count = 0 AND pre_last_active_date >= DATE '2017-11-30';

-- 2. 固定版本盐 + BIGINT 用户 ID 的十进制字符串，MD5 最末十六进制位 0–7 为
-- Treatment，8–f 为 Control。每位均匀映射下分配概率为 50/50，不保证人数精确相等。
-- 不使用 random()/随版本可能变化的 hash()；保持盐、ID 表示及规则不变，
-- 用户独立固定归组，新增/删除其他用户不改变其组别。不得按结果换盐挑选 lift。
-- MD5 此处只用于分桶，不用于密码/安全用途；模拟标签不代表实际触达。
CREATE OR REPLACE TEMP TABLE ab_assignment AS
SELECT *, MD5('cart_recall_sim_v1:' || CAST(user_id AS VARCHAR)) AS assignment_digest,
       CASE WHEN RIGHT(MD5('cart_recall_sim_v1:' || CAST(user_id AS VARCHAR)), 1)
                      IN ('0','1','2','3','4','5','6','7')
            THEN 'Treatment' ELSE 'Control' END AS experiment_group
FROM ab_eligible;

SELECT 'cart_recall_sim_v1; MD5(canonical BIGINT user_id); last hex digit 0-7 Treatment, 8-f Control'
           AS assignment_method,
       experiment_group, COUNT(*) AS eligible_users,
       ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 4) AS assignment_share_pct
FROM ab_assignment GROUP BY experiment_group ORDER BY experiment_group;

-- 3. 人数差与 50/50 偏离，仅为诊断，不要求固定哈希人数完全相等。
SELECT COUNT(*) AS eligible_users,
       COUNT(*) FILTER (WHERE experiment_group='Treatment') AS treatment_users,
       COUNT(*) FILTER (WHERE experiment_group='Control') AS control_users,
       ABS(COUNT(*) FILTER (WHERE experiment_group='Treatment') -
           COUNT(*) FILTER (WHERE experiment_group='Control')) AS absolute_user_count_difference,
       ROUND(ABS(COUNT(*) FILTER (WHERE experiment_group='Treatment') - COUNT(*)/2.0)
             *100.0/NULLIF(COUNT(*),0),4) AS treatment_deviation_from_50_percentage_points
FROM ab_assignment;

-- Balance 各特征均值差为 Treatment-Control 的绝对值；SMD = 均值差 /
-- sqrt((Treatment 样本方差 + Control 样本方差)/2)，另给绝对 SMD。
-- 两组都有至少两人且零方差、均值相同时 SMD=0；无法计算时 NULL。
-- 小 SMD 只是已观测特征的平衡诊断，不证明所有变量/未观测因素完全一致。
-- 不使用 p>0.05 宣称一致；没有真正 treatment，平衡不证明业务实验有效。
CREATE OR REPLACE TEMP TABLE ab_balance AS
WITH long_features AS (
    SELECT a.experiment_group, f.metric, f.feature_value
    FROM ab_assignment a CROSS JOIN LATERAL (VALUES
       ('pre_pv_count',CAST(a.pre_pv_count AS DOUBLE)),
       ('pre_fav_count',CAST(a.pre_fav_count AS DOUBLE)),
       ('pre_cart_count',CAST(a.pre_cart_count AS DOUBLE)),
       ('pre_total_events',CAST(a.pre_total_events AS DOUBLE)),
       ('pre_active_days',CAST(a.pre_active_days AS DOUBLE)),
       ('pre_category_count',CAST(a.pre_category_count AS DOUBLE)),
       ('pre_recency_days',CAST(a.pre_recency_days AS DOUBLE))
    ) f(metric,feature_value)
), statistics AS (
    SELECT metric,
       AVG(feature_value) FILTER (WHERE experiment_group='Treatment') AS treatment_mean,
       AVG(feature_value) FILTER (WHERE experiment_group='Control') AS control_mean,
       VAR_SAMP(feature_value) FILTER (WHERE experiment_group='Treatment') AS treatment_variance,
       VAR_SAMP(feature_value) FILTER (WHERE experiment_group='Control') AS control_variance
    FROM long_features GROUP BY metric
), scaled AS (
    SELECT *, SQRT((treatment_variance+control_variance)/2.0) AS pooled_sd FROM statistics
)
SELECT metric, treatment_mean, control_mean, ABS(treatment_mean-control_mean) AS absolute_difference,
       CASE WHEN pooled_sd > 0 THEN (treatment_mean-control_mean)/pooled_sd
            WHEN pooled_sd = 0 AND treatment_mean=control_mean THEN 0 END AS smd
FROM scaled;
SELECT metric, ROUND(treatment_mean,4) AS treatment_mean, ROUND(control_mean,4) AS control_mean,
       ROUND(absolute_difference,4) AS absolute_difference,
       ROUND(smd,6) AS smd, ROUND(ABS(smd),6) AS absolute_smd
FROM ab_balance ORDER BY metric;

-- 4. Outcome 全部限于后两天。仅扫描已分组用户的结果，不回流到 eligibility。
CREATE OR REPLACE TEMP TABLE ab_outcome_observed AS
SELECT e.user_id, COUNT(*) AS outcome_total_events,
       COUNT(*) FILTER (WHERE e.behavior_type='buy') AS outcome_buy_events,
       COUNT(DISTINCT CAST(e.event_time AS DATE)) AS outcome_active_days,
       MIN(e.event_time) FILTER (WHERE e.behavior_type='buy') AS first_outcome_buy_time,
       MIN(e.event_time) AS outcome_min_event_time, MAX(e.event_time) AS outcome_max_event_time
FROM user_behavior_clean e JOIN ab_assignment a USING(user_id)
WHERE e.event_time >= (SELECT outcome_start FROM ab_windows) AND e.event_time < (SELECT outcome_end FROM ab_windows)
  AND e.behavior_type IN ('pv','fav','cart','buy')
GROUP BY e.user_id;

-- 所有 assignment 左连接保留：没有 Outcome 事件者按日志中未观测行为计 0。
-- 假设整个 Outcome 窗口日志覆盖完整；缺日志不能自动当作无行为。
-- time_to_first_buy 单位小时，自 12/02 00:00 起，不是距前期加购时间。
-- 仅购买者非 NULL；未买者在 48 小时行政右截尾，不填 0、不冒充完整购买等待期。
-- 给出 followup_hours 和 buy_time_censored；不据购买者均值比较整体购买速度。
CREATE OR REPLACE TEMP TABLE ab_user_results AS
SELECT a.*, COALESCE(o.outcome_total_events,0)>0 AS outcome_active,
       COALESCE(o.outcome_buy_events,0)>0 AS outcome_buy,
       COALESCE(o.outcome_buy_events,0) AS outcome_buy_events,
       COALESCE(o.outcome_active_days,0) AS outcome_active_days,
       o.first_outcome_buy_time,
       EPOCH(o.first_outcome_buy_time - (SELECT outcome_start FROM ab_windows))/3600.0 AS time_to_first_buy,
       48.0 AS followup_hours,
       o.first_outcome_buy_time IS NULL AS buy_time_censored,
       o.outcome_min_event_time, o.outcome_max_event_time
FROM ab_assignment a LEFT JOIN ab_outcome_observed o USING(user_id);
-- 明细预览不是抽样统计；全量可在清理前读取 ab_user_results。
SELECT user_id, experiment_group, outcome_active, outcome_buy, outcome_buy_events,
       outcome_active_days, time_to_first_buy, followup_hours, buy_time_censored
FROM ab_user_results ORDER BY user_id LIMIT 20;

-- 5/7. 主指标与辅助指标。内部 rate 为 0–1；输出 purchase_user_rate、
-- outcome_active_rate 均为百分比（0–100），avg_buy_events_per_user 为事件/用户。
CREATE OR REPLACE TEMP TABLE ab_group_metrics AS
SELECT g.experiment_group, COUNT(r.user_id) AS experiment_users,
       COUNT(r.user_id) FILTER (WHERE r.outcome_buy) AS purchase_users,
       COUNT(r.user_id) FILTER (WHERE r.outcome_buy)*1.0/NULLIF(COUNT(r.user_id),0) AS purchase_rate,
       COUNT(r.user_id) FILTER (WHERE r.outcome_active)*1.0/NULLIF(COUNT(r.user_id),0) AS active_rate,
       SUM(r.outcome_buy_events)*1.0/NULLIF(COUNT(r.user_id),0) AS avg_buy_events_per_user
FROM (VALUES ('Treatment'),('Control')) g(experiment_group)
LEFT JOIN ab_user_results r USING(experiment_group) GROUP BY g.experiment_group;
SELECT experiment_group, experiment_users, purchase_users,
       ROUND(purchase_rate*100,4) AS purchase_user_rate,
       ROUND(active_rate*100,4) AS outcome_active_rate,
       ROUND(avg_buy_events_per_user,6) AS avg_buy_events_per_user
FROM ab_group_metrics ORDER BY experiment_group;

-- 6. Lift 从未舍入的率计算；Control 零购买率时 relative_lift_pct 为 NULL。
-- 主指标按全部分组用户计算；辅助指标不替代主指标。
SELECT t.experiment_users AS treatment_users, c.experiment_users AS control_users,
       t.purchase_users AS treatment_purchase_users, c.purchase_users AS control_purchase_users,
       ROUND(t.purchase_rate*100,4) AS treatment_purchase_user_rate,
       ROUND(c.purchase_rate*100,4) AS control_purchase_user_rate,
       ROUND((t.purchase_rate-c.purchase_rate)*100,6) AS absolute_lift_percentage_points,
       ROUND((t.purchase_rate/NULLIF(c.purchase_rate,0)-1)*100,6) AS relative_lift_pct,
       'SIMULATION ONLY: historical outcomes under deterministic labels; no real reminder effect'
           AS interpretation
FROM ab_group_metrics t CROSS JOIN ab_group_metrics c
WHERE t.experiment_group='Treatment' AND c.experiment_group='Control';

-- 8. 异常数量应为 0。事件时间界限 + 构建依赖隔离检查不能证明原始日志完全无遗漏。
SELECT 'invalid_eligibility' AS check_name, COUNT(*) AS invalid_rows FROM ab_eligible
WHERE pre_cart_count<=0 OR pre_buy_count<>0 OR pre_last_active_date<DATE '2017-11-30'
UNION ALL SELECT 'duplicate_assignment',COUNT(*) FROM
 (SELECT user_id FROM ab_assignment GROUP BY user_id HAVING COUNT(*)<>1)
UNION ALL SELECT 'assignment_population_mismatch',ABS((SELECT COUNT(*) FROM ab_assignment)-(SELECT COUNT(*) FROM ab_eligible))
UNION ALL SELECT 'invalid_assignment_label',COUNT(*) FROM ab_assignment
WHERE experiment_group NOT IN ('Treatment','Control') OR experiment_group IS NULL
UNION ALL SELECT 'nonrepeatable_assignment',COUNT(*) FROM ab_assignment
WHERE experiment_group <> CASE WHEN RIGHT(MD5('cart_recall_sim_v1:'||CAST(user_id AS VARCHAR)),1)
 IN ('0','1','2','3','4','5','6','7') THEN 'Treatment' ELSE 'Control' END
 OR assignment_digest<>MD5('cart_recall_sim_v1:'||CAST(user_id AS VARCHAR))
UNION ALL SELECT 'pre_window_leakage',COUNT(*) FROM ab_pre_features
WHERE pre_min_event_time<TIMESTAMP '2017-11-25' OR pre_max_event_time>=TIMESTAMP '2017-12-02'
 OR pre_last_active_date<>CAST(pre_max_event_time AS DATE)
 OR pre_recency_days<>DATE_DIFF('day',pre_last_active_date,DATE '2017-12-02')
UNION ALL SELECT 'outcome_window_violation',COUNT(*) FROM ab_outcome_observed
WHERE outcome_min_event_time<TIMESTAMP '2017-12-02' OR outcome_max_event_time>=TIMESTAMP '2017-12-04'
UNION ALL SELECT 'invalid_window_configuration',COUNT(*) FROM ab_windows
WHERE pre_start>=pre_end OR outcome_start>=outcome_end OR pre_end>outcome_start
UNION ALL SELECT 'eligibility_feature_mismatch',COUNT(*) FROM (
 SELECT * FROM ab_eligible
 EXCEPT
 SELECT * FROM ab_pre_features WHERE pre_cart_count>0 AND pre_buy_count=0
 AND pre_last_active_date>=DATE '2017-11-30'
)
UNION ALL SELECT 'invalid_outcome',COUNT(*) FROM ab_user_results
WHERE outcome_buy_events<0 OR outcome_active_days NOT BETWEEN 0 AND 2
 OR outcome_buy<>(outcome_buy_events>0) OR (outcome_buy AND NOT outcome_active)
 OR outcome_active<>(outcome_active_days>0)
 OR (outcome_buy AND (time_to_first_buy IS NULL OR time_to_first_buy<0 OR time_to_first_buy>=48))
 OR (NOT outcome_buy AND time_to_first_buy IS NOT NULL)
UNION ALL SELECT 'outcome_population_mismatch',ABS((SELECT COUNT(*) FROM ab_user_results)-(SELECT COUNT(*) FROM ab_assignment))
UNION ALL SELECT 'invalid_purchase_rate',COUNT(*) FROM ab_group_metrics
WHERE purchase_rate NOT BETWEEN 0 AND 1 OR purchase_users>experiment_users
 OR (experiment_users>0 AND purchase_rate IS NULL);

DROP TABLE IF EXISTS ab_group_metrics;
DROP TABLE IF EXISTS ab_user_results;
DROP TABLE IF EXISTS ab_outcome_observed;
DROP TABLE IF EXISTS ab_balance;
DROP TABLE IF EXISTS ab_assignment;
DROP TABLE IF EXISTS ab_eligible;
DROP TABLE IF EXISTS ab_pre_features;
DROP TABLE IF EXISTS ab_windows;
