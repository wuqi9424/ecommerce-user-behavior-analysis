"""Run real-database regression and real CLI demos; snapshots are assertions only."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from ai_agent.tools import (get_platform_overview, get_weekend_growth, get_funnel_comparison,
                            get_opportunity_analysis, get_ab_test_summary)
from ai_agent.tools._common import DATABASE, connection
from ai_agent.agent import detect_intent

CASES = [
 ('平台总览有哪些关键数字？','platform'),
 ('平台有多少用户和事件数？','platform'),
 ('为什么第二个周末流量增长了？','growth'),
 ('第二个周末流量上涨多少？','growth'),
 ('哪个环节转化下降？','funnel'),
 ('比较两个周末漏斗','funnel'),
 ('哪类用户最值得优先运营？','opportunity'),
 ('哪些用户值得召回？','opportunity'),
 ('A/B Test 显著吗？','experiment'),
 ('A/B Test 是否证明购物车召回有效？','experiment'),
 ('GMV 是多少？','unsupported'),
 ('删除数据库里的用户','unsupported'),
 ('周末增长和漏斗转化如何？','ambiguous'),
]

def digest():
    h=hashlib.sha256()
    with DATABASE.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def near(actual, expected, tolerance):
    assert math.isclose(actual,expected,rel_tol=0,abs_tol=tolerance),(actual,expected)

def main():
    before=digest()
    results={}
    for name,tool in [('platform',get_platform_overview),('growth',get_weekend_growth),
                      ('funnel',get_funnel_comparison),('opportunity',get_opportunity_analysis),
                      ('experiment',get_ab_test_summary)]:
        print(f'Running {name}',flush=True)
        results[name]=tool()
    p,g,f,o,e=[results[k] for k in ('platform','growth','funnel','opportunity','experiment')]
    assert p['total_events']==100095182
    assert p['total_users']==987991
    assert p['purchasing_users']==672404
    assert sum(p[k+'_events'] for k in ('pv','fav','cart','buy'))==p['total_events']
    previous,final=g['periods']
    assert previous['active_users']==864829 and final['active_users']==986594
    near(final['active_user_growth_pct'],14.08,.01)
    assert g['newly_observed_users']==19
    assert g['newly_observed_users']+g['previously_observed_users']==final['active_users']
    near(f['periods'][0]['pv_to_buy_rate'],18.2785,.00005)
    near(f['periods'][1]['pv_to_buy_rate'],17.1709,.00005)
    assert all(r['calendar_days']==2 and r['valid_browse_rate_days']==2 for r in f['periods'])
    assert o['opportunity_categories']==770
    assert o['total_categories']==9077 and o['eligible_categories']==1551
    p1=next(r for r in o['priorities'] if r['priority_segment']=='P1')
    assert p1['distinct_users']==349857 and p1['user_category_pairs']==658968
    near(e['treatment']['purchase_user_rate'],25.3933,.00005)
    near(e['control']['purchase_user_rate'],25.4770,.00005)
    near(e['p_value'],.6589,.00005)
    near(e['mde_pp'],.5341,.00005)
    assert e['experiment_type']=='simulated_assignment'
    # Prove the actual connection rejects persistent writes.
    with connection() as con:
        try: con.execute('CREATE TABLE __ai_agent_readonly_probe (id INTEGER)')
        except Exception as error:
            assert 'read-only' in str(error).lower(),str(error)
        else: raise AssertionError('Persistent write was permitted')
    for invalid in (0,-1,101,True,'5',1.5):
        try: get_opportunity_analysis(invalid)
        except ValueError: pass
        else: raise AssertionError(f'Accepted invalid top_n={invalid!r}')
    demos=[]
    for question,intent in CASES:
        assert detect_intent(question)==intent
        print(f'CLI {intent}: {question}',flush=True)
        completed=subprocess.run([sys.executable,'-m','ai_agent.agent',question],capture_output=True,text=True,check=False)
        assert completed.returncode == (2 if intent in ('unsupported','ambiguous') else 0)
        output=json.loads(completed.stdout)
        assert output['detected_intent']==intent
        assert all(k in output for k in ('tool_used','key_data','interpretation','caveats'))
        if intent in ('unsupported','ambiguous'): assert output['tool_used'] is None
        demos.append({'question':question,'intent':intent,'tool':output['tool_used'],'result':'PASS'})
    after=digest()
    assert before==after,'Database bytes changed'
    report={'status':'PASS','python':sys.version.split()[0], 'database_sha256_before':before,
            'database_sha256_after':after,'tool_results':results,'cli_cases':demos,
            'readonly_write_rejection':'PASS','top_n_validation':'PASS'}
    path=Path(__file__).with_name('last_run.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(f'PASS: 5 tools, {len(demos)} CLI cases, unchanged database; {path}')

if __name__=='__main__': main()
