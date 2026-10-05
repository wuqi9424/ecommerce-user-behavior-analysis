"""Export compact, aggregated datasets for Power BI and Tableau."""

from __future__ import annotations

import math
from pathlib import Path
from statistics import NormalDist
import sys

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data/processed/ecommerce.duckdb"
OUTPUT_DIR = ROOT / "dashboard/data"


def run_sql_until_cleanup(connection: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Run a verified SQL module but retain its TEMP tables for export."""
    for statement in connection.extract_statements(path.read_text(encoding="utf-8")):
        if statement.query.strip().upper().startswith("DROP "):
            break
        connection.execute(statement)


def export_csv(frame: pd.DataFrame, filename: str) -> tuple[str, int, int, int]:
    path = OUTPUT_DIR / filename
    frame.to_csv(path, index=False, encoding="utf-8", date_format="%Y-%m-%d")
    size = path.stat().st_size
    print(f"SUCCESS {filename}: {len(frame):,} rows x {len(frame.columns)} columns ({size:,} bytes)")
    return filename, len(frame), len(frame.columns), size


def power_for_effect(n_per_arm: int, baseline: float, uplift: float) -> float:
    normal = NormalDist()
    z_critical = normal.inv_cdf(0.975)
    alternative = baseline + uplift
    mean_rate = (baseline + alternative) / 2
    critical = z_critical * math.sqrt(2 * mean_rate * (1 - mean_rate) / n_per_arm)
    alternative_se = math.sqrt(
        (baseline * (1 - baseline) + alternative * (1 - alternative)) / n_per_arm
    )
    return normal.cdf((-critical - uplift) / alternative_se) + normal.cdf(
        (uplift - critical) / alternative_se
    )


def calculate_ab_statistics(metrics: pd.DataFrame) -> dict[str, float]:
    values = metrics.set_index("experiment_group")
    n_t, n_c = int(values.loc["Treatment", "assigned_users"]), int(values.loc["Control", "assigned_users"])
    x_t, x_c = int(values.loc["Treatment", "purchase_users"]), int(values.loc["Control", "purchase_users"])
    p_t, p_c = x_t / n_t, x_c / n_c
    difference = p_t - p_c
    pooled = (x_t + x_c) / (n_t + n_c)
    null_se = math.sqrt(pooled * (1 - pooled) * (1 / n_t + 1 / n_c))
    p_value = math.erfc(abs(difference / null_se) / math.sqrt(2))
    ci_se = math.sqrt(p_t * (1 - p_t) / n_t + p_c * (1 - p_c) / n_c)
    low, high = 1e-10, min(0.2, (1 - p_c) * 0.9)
    for _ in range(70):
        middle = (low + high) / 2
        if power_for_effect(min(n_t, n_c), p_c, middle) >= 0.80:
            high = middle
        else:
            low = middle
    return {
        "absolute_lift_pp": difference * 100,
        "relative_lift_pct": (p_t / p_c - 1) * 100,
        "p_value": p_value,
        "ci_lower_pp": (difference - 1.96 * ci_se) * 100,
        "ci_upper_pp": (difference + 1.96 * ci_se) * 100,
        "mde_pp": high * 100,
    }


def main() -> int:
    if not DATABASE.is_file():
        print(f"ERROR: DuckDB database not found: {DATABASE.relative_to(ROOT)}", file=sys.stderr)
        return 1
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exported: list[tuple[str, int, int, int]] = []
    connection = None
    try:
        connection = duckdb.connect(str(DATABASE), read_only=True)
        connection.execute("SET TimeZone='Asia/Shanghai'")
        connection.execute("SET memory_limit='3GB'")
        connection.execute("SET threads=4")

        daily = connection.execute("""
            SELECT CAST(event_time AS DATE) AS date,
                   COUNT(DISTINCT user_id) AS active_users,
                   COUNT(*) AS total_events,
                   COUNT(*) FILTER (WHERE behavior_type='pv') AS pv_events,
                   COUNT(*) FILTER (WHERE behavior_type='fav') AS fav_events,
                   COUNT(*) FILTER (WHERE behavior_type='cart') AS cart_events,
                   COUNT(*) FILTER (WHERE behavior_type='buy') AS buy_events,
                   COUNT(DISTINCT user_id) FILTER (WHERE behavior_type='buy') AS purchasing_users,
                   ROUND(100.0*COUNT(DISTINCT user_id) FILTER (WHERE behavior_type='buy')/
                         NULLIF(COUNT(DISTINCT user_id),0),4) AS purchasing_user_rate,
                   ROUND(COUNT(*)*1.0/NULLIF(COUNT(DISTINCT user_id),0),4) AS events_per_active_user
            FROM user_behavior_clean
            WHERE event_time>=TIMESTAMP '2017-11-25' AND event_time<TIMESTAMP '2017-12-04'
            GROUP BY date ORDER BY date
        """).fetchdf()
        exported.append(export_csv(daily, "daily_metrics.csv"))

        weekend = connection.execute("""
            WITH periods AS (
              SELECT * FROM (VALUES
                ('Previous Weekend',DATE '2017-11-25',DATE '2017-11-26'),
                ('Final Weekend',DATE '2017-12-02',DATE '2017-12-03')
              ) p(weekend_period,start_date,end_date)
            ), summary AS (
              SELECT p.weekend_period,p.start_date,p.end_date,
                     COUNT(DISTINCT b.user_id) AS active_users,COUNT(*) AS total_events,
                     COUNT(*) FILTER(WHERE behavior_type='pv') AS pv_events,
                     COUNT(*) FILTER(WHERE behavior_type='fav') AS fav_events,
                     COUNT(*) FILTER(WHERE behavior_type='cart') AS cart_events,
                     COUNT(*) FILTER(WHERE behavior_type='buy') AS buy_events
              FROM periods p JOIN user_behavior_clean b
                ON b.event_time>=CAST(p.start_date AS TIMESTAMP)
               AND b.event_time<CAST(p.end_date+1 AS TIMESTAMP)
              GROUP BY p.weekend_period,p.start_date,p.end_date
            ), metrics AS (
              SELECT *,total_events*1.0/active_users AS events_per_active_user,
                     pv_events*1.0/active_users AS pv_per_active_user,
                     fav_events*1.0/active_users AS fav_per_active_user,
                     cart_events*1.0/active_users AS cart_per_active_user,
                     buy_events*1.0/active_users AS buy_per_active_user
              FROM summary
            ), baseline AS (
              SELECT active_users AS baseline_active_users,total_events AS baseline_total_events
              FROM metrics WHERE weekend_period='Previous Weekend'
            )
            SELECT m.*,CASE WHEN weekend_period='Final Weekend' THEN
                       100.0*(active_users-baseline_active_users)/baseline_active_users END AS active_user_growth_pct,
                     CASE WHEN weekend_period='Final Weekend' THEN
                       100.0*(total_events-baseline_total_events)/baseline_total_events END AS total_event_growth_pct
            FROM metrics m CROSS JOIN baseline ORDER BY start_date
        """).fetchdf()
        exported.append(export_csv(weekend, "weekend_comparison.csv"))

        funnel = connection.execute("""
            WITH user_day AS (
              SELECT user_id,CAST(event_time AS DATE) AS event_date,
                     BOOL_OR(behavior_type='pv') AS has_pv,
                     BOOL_OR(behavior_type IN ('fav','cart')) AS has_intent,
                     BOOL_OR(behavior_type='buy') AS has_buy
              FROM user_behavior_clean
              WHERE event_time>=TIMESTAMP '2017-11-25' AND event_time<TIMESTAMP '2017-12-04'
              GROUP BY user_id,event_date
            ), daily AS (
              SELECT event_date,COUNT(*) FILTER(WHERE has_pv) AS pv_users,
                     COUNT(*) FILTER(WHERE has_intent) AS intent_users,
                     COUNT(*) FILTER(WHERE has_buy) AS buy_users,
                     COUNT(*) FILTER(WHERE has_pv AND has_intent) AS pv_intent_users,
                     COUNT(*) FILTER(WHERE has_pv AND has_buy) AS pv_buy_users,
                     COUNT(*) FILTER(WHERE has_pv AND has_intent AND has_buy) AS full_users
              FROM user_day GROUP BY event_date
            ), periods AS (
              SELECT * FROM (VALUES
                ('Previous Weekend',DATE '2017-11-25',DATE '2017-11-26'),
                ('Final Weekend',DATE '2017-12-02',DATE '2017-12-03')
              ) p(weekend_period,start_date,end_date)
            )
            SELECT p.weekend_period,ROUND(AVG(d.pv_users),2) AS avg_pv_users,
                   ROUND(AVG(d.intent_users),2) AS avg_intent_users,
                   ROUND(AVG(d.buy_users),2) AS avg_buy_users,
                   ROUND(AVG(100.0*d.pv_intent_users/NULLIF(d.pv_users,0)),4) AS pv_to_intent_rate,
                   ROUND(AVG(100.0*d.full_users/NULLIF(d.pv_intent_users,0)),4) AS intent_to_buy_rate,
                   ROUND(AVG(100.0*d.pv_buy_users/NULLIF(d.pv_users,0)),4) AS pv_to_buy_rate,
                   ROUND(AVG(100.0*d.full_users/NULLIF(d.pv_users,0)),4) AS full_funnel_rate
            FROM periods p JOIN daily d ON d.event_date BETWEEN p.start_date AND p.end_date
            GROUP BY p.weekend_period,p.start_date ORDER BY p.start_date
        """).fetchdf()
        exported.append(export_csv(funnel, "funnel_comparison.csv"))

        run_sql_until_cleanup(connection, ROOT / "sql/08_growth_opportunity_analysis.sql")
        priorities = connection.execute("""
            SELECT priority_segment,
              CASE priority_segment WHEN 'P1' THEN 'Recent cart; no nine-day buy in target category'
                WHEN 'P2' THEN 'No recent cart; recent favorite; no nine-day buy in target category'
                ELSE 'No recent cart/favorite; recent PV at or above browse-only Q75; no nine-day buy in target category' END AS segment_definition,
              COUNT(*) AS user_category_pairs,COUNT(DISTINCT user_id) AS distinct_users,
              ROUND(AVG(recent_pv_count),4) AS avg_pv,ROUND(AVG(recent_fav_count),4) AS avg_fav,
              ROUND(AVG(recent_cart_count),4) AS avg_cart,
              CASE priority_segment WHEN 'P1' THEN 'Cart recall / benefit-price reminder'
                WHEN 'P2' THEN 'Favorite reminder / content reinforcement'
                ELSE 'Recommendation / interest cultivation' END AS recommended_strategy
            FROM growth_combined_pool GROUP BY priority_segment ORDER BY priority_segment
        """).fetchdf()
        exported.append(export_csv(priorities, "user_priority_segments.csv"))

        categories = connection.execute("""
            SELECT category_id,previous_pv_users,final_pv_users,ROUND(pv_user_growth_pct,4) AS pv_user_growth_pct,
                   previous_intent_users,final_intent_users,ROUND(intent_user_growth_pct,4) AS intent_user_growth_pct,
                   previous_buy_users,final_buy_users,ROUND(buy_user_growth_pct,4) AS buy_user_growth_pct,
                   ROUND(previous_pv_to_buy_user_rate,4) AS previous_pv_to_buy_rate,
                   ROUND(final_pv_to_buy_user_rate,4) AS final_pv_to_buy_rate,
                   ROUND(pv_to_buy_rate_change_pp,4) AS pv_to_buy_rate_change_pp,
                   ROUND(previous_intent_to_buy_user_rate,4) AS previous_intent_to_buy_rate,
                   ROUND(final_intent_to_buy_user_rate,4) AS final_intent_to_buy_rate,
                   ROUND(intent_to_buy_rate_change_pp,4) AS intent_to_buy_rate_change_pp,
                   high_intent_no_buy_users AS high_intent_nonbuyer_users
            FROM growth_candidate_categories ORDER BY opportunity_rank
        """).fetchdf()
        exported.append(export_csv(categories, "opportunity_categories.csv"))

        run_sql_until_cleanup(connection, ROOT / "sql/07_ab_test_design.sql")
        ab = connection.execute("""
            SELECT experiment_group,experiment_users AS assigned_users,purchase_users,
                   ROUND(purchase_rate*100,4) AS purchase_user_rate,
                   ROUND(active_rate*100,4) AS outcome_active_rate,
                   ROUND(avg_buy_events_per_user,6) AS avg_buy_events_per_user
            FROM ab_group_metrics
            ORDER BY CASE experiment_group WHEN 'Treatment' THEN 1 ELSE 2 END
        """).fetchdf()
        for column, value in calculate_ab_statistics(ab).items():
            ab[column] = round(value, 6)
        exported.append(export_csv(ab, "ab_test_summary.csv"))

        expected = {
            "final_active_users": int(weekend.loc[weekend.weekend_period.eq("Final Weekend"), "active_users"].iloc[0]),
            "previous_active_users": int(weekend.loc[weekend.weekend_period.eq("Previous Weekend"), "active_users"].iloc[0]),
            "opportunity_categories": len(categories),
            "p1_pairs": int(priorities.loc[priorities.priority_segment.eq("P1"), "user_category_pairs"].iloc[0]),
            "p1_users": int(priorities.loc[priorities.priority_segment.eq("P1"), "distinct_users"].iloc[0]),
        }
        required = {"final_active_users": 986594, "previous_active_users": 864829,
                    "opportunity_categories": 770, "p1_pairs": 658968, "p1_users": 349857}
        if expected != required:
            raise ValueError(f"Validation mismatch: actual={expected}, expected={required}")
        rates = ab.set_index("experiment_group")["purchase_user_rate"]
        if not math.isclose(rates["Treatment"], 25.3933) or not math.isclose(rates["Control"], 25.4770):
            raise ValueError(f"A/B rate mismatch: {rates.to_dict()}")
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()

    print("\nExport summary")
    for filename, rows, columns, size in exported:
        print(f"- {filename}: {rows:,} rows, {columns} columns, {size:,} bytes")
    print("Validation passed: all required reference metrics match the verified SQL results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
