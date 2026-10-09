"""Compare the same Golden Set without altering deterministic baseline artifacts."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

from ai_agent.llm_agent import run, save_trace
from ai_agent.providers.base import ConfigurationError
from ai_agent.providers.openai_responses import from_environment
from ai_agent.tool_schemas import INTENTS
from .run_evaluation import fact_check, forbidden_hits, number_check, metric

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def load(name): return json.loads((HERE/name).read_text(encoding='utf-8'))
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def check_baseline_integrity():
    protected=load('llm_baseline_manifest.json')['protected_files']
    changed=[p for p,h in protected.items() if digest(ROOT/p)!=h]
    if changed: raise RuntimeError('Frozen baseline changed: '+', '.join(changed))
    return protected


def verify_baseline():
    """Run original evaluation, capture fresh evidence, restore frozen report/results."""
    paths=[HERE/'evaluation_results_after.json',HERE/'evaluation_report.md']
    saved={p:p.read_bytes() for p in paths}
    try:
        result=subprocess.run([sys.executable,'-m','ai_agent.evals.run_evaluation'],
                              cwd=ROOT,capture_output=True,text=True)
        if result.returncode:
            raise RuntimeError('Baseline regression failed; see original evaluation output: '+result.stdout[-1500:])
        fresh=load('evaluation_results_after.json')
        if fresh['metrics']['Overall Case Pass Rate']['passed']!=37:
            raise RuntimeError('Baseline did not retain 37/37')
        save_trace({'kind':'baseline_verification','result':fresh})
        return fresh
    finally:
        for p,data in saved.items(): p.write_bytes(data)
        check_baseline_integrity()


def project(trace, expected_tool):
    answer=trace.get('final_answer') or {}
    intents=answer.get('detected_intents',[])
    actual=intents[0] if len(intents)==1 else 'ambiguous' if len(intents)>1 else 'error'
    matching=[r['result'] for r in trace.get('tool_results',[])
              if r.get('name')==expected_tool and 'result' in r]
    return {'detected_intent':actual,'status':trace['status'],
            'key_data':matching[0] if matching else {},
            'interpretation':{k:v for k,v in answer.items() if k not in ('status','detected_intents')},
            'caveats':answer.get('caveats',[])}


def numeric_display_coverage(trace, tool, spec):
    """Require a displayed, grounded value from the task's actual tool, not just raw data."""
    ids={r['call_id'] for r in trace.get('tool_results',[]) if r.get('name')==tool and 'result' in r}
    for evidence in (trace.get('final_answer') or {}).get('numeric_evidence',[]):
        try:
            display=float(evidence['display'].replace(',',''))
            relevant=any(any(path.startswith('calls/'+id+'/') for id in ids) for path in evidence['paths'])
            if relevant and math.isfinite(display) and abs(display-spec['value'])<=spec['abs_tolerance']:
                return True
        except (KeyError,ValueError,TypeError):
            continue
    return False


def score(traces, cases, values):
    checked=[]
    for case,trace in zip(cases,traces):
        output=project(trace,case['expected_tool'])
        checks=[fact_check(output,f,values) for f in case['required_facts']]
        for fact,check in zip(case['required_facts'],checks):
            if fact['type']=='number':
                shown=numeric_display_coverage(trace,case['expected_tool'],values[fact['expected_value']])
                check['displayed_in_answer']=shown
                check['passed']=check['passed'] and shown
        forbidden=forbidden_hits(output,case['forbidden_claims'])
        executed=[r['name'] for r in trace['tool_results'] if 'result' in r]
        expected=[case['expected_tool']] if case['expected_tool'] else []
        intent_ok=output['detected_intent']==case['expected_intent']
        tool_ok=trace['selected_tools']==expected and executed==expected
        status_ok=trace['status']==case['expected_status']
        grounded=trace.get('numeric_grounding',{}).get('passed',False)
        checked.append({'id':case['id'],'question':case['question'],'category':case['category'],
                        'intent_ok':intent_ok,'tool_ok':tool_ok,'status_ok':status_ok,
                        'fact_checks':checks,'forbidden_hits':forbidden,'numeric_grounding':grounded,
                        'passed':intent_ok and tool_ok and status_ok and all(f['passed'] for f in checks)
                                 and not forbidden and grounded and not trace['errors'], 'trace':trace})
    # Match baseline Numeric Accuracy denominator: 17 run-level ground-truth checks
    # plus 17 original case requirements. Only actual LLM-invoked tool results used.
    numbers=[]
    inverse={v:k for k,v in INTENTS.items()}
    for name,spec in values.items():
        matches=[r['result'] for t in traces for r in t['tool_results']
                 if r.get('name')==inverse[spec['intent']] and 'result' in r]
        numbers.append(number_check({'key_data':matches[0] if matches else {}},spec))
    numbers += [check for case,row in zip(cases,checked)
                for fact,check in zip(case['required_facts'],row['fact_checks']) if fact['type']=='number']
    facts=[f for row in checked for f in row['fact_checks']]
    boundary=[r for r in checked if r['category'] in ('boundary','causal')]
    metrics={
      'Intent Accuracy':metric(sum(r['intent_ok'] for r in checked),len(checked)),
      'Tool Selection Accuracy':metric(sum(r['tool_ok'] for r in checked),len(checked)),
      'Status Accuracy':metric(sum(r['status_ok'] for r in checked),len(checked)),
      'Numeric Accuracy':metric(sum(r['passed'] for r in numbers),len(numbers)),
      'Required Fact Coverage':metric(sum(f['passed'] for f in facts),len(facts)),
      'Boundary Compliance':metric(sum(r['passed'] for r in boundary),len(boundary)),
      'Forbidden Claim Rate':metric(sum(bool(r['forbidden_hits']) for r in checked),len(checked)),
      'Overall Case Pass Rate':metric(sum(r['passed'] for r in checked),len(checked)),
    }
    multi=[]
    supplement=load('llm_multi_intent_expectations.json')
    expectations=supplement['cases']
    for row in checked:
        if row['id'] not in expectations: continue
        trace=row['trace'];expected=expectations[row['id']]
        executed=[r['name'] for r in trace['tool_results'] if 'result' in r]
        tool_ok=set(trace['selected_tools'])==set(expected) and set(executed)==set(expected) and len(executed)==len(expected)
        task_facts=[]
        for tool in expected:
            intent=INTENTS[tool]
            # Supplement: same factual requirements as existing direct task cases,
            # not baseline's "ask one topic" response requirement.
            representative=next(c for c in cases if c['category']=='direct' and c['expected_intent']==intent)
            output=project(trace,tool)
            for fact in representative['required_facts']:
                check=fact_check(output,fact,values)
                if fact['type']=='number':
                    shown=numeric_display_coverage(trace,tool,values[fact['expected_value']])
                    check['displayed_in_answer']=shown
                    check['passed']=check['passed'] and shown
                task_facts.append(check)
        wanted=supplement.get('argument_expectations',{}).get(row['id'],{})
        argument_ok=all(any(r.get('name')==name and r.get('arguments')==arguments for r in trace['tool_results'])
                        for name,arguments in wanted.items())
        passed=(tool_ok and argument_ok and trace['status']=='answered' and trace['tool_call_count']<=3
                and all(f['passed'] for f in task_facts) and row['numeric_grounding']
                and not row['forbidden_hits'] and not trace['errors'])
        multi.append({'id':row['id'],'expected_tools':expected,'tool_ok':tool_ok,'argument_ok':argument_ok,
                      'task_fact_checks':task_facts,'passed':passed})
    return {'metrics':metrics,'cases':checked,'numeric_checks':numbers,
            'explanation_grounding':metric(sum(r['numeric_grounding'] for r in checked),len(checked)),
            'multi_intent_supplement':{'score':metric(sum(r['passed'] for r in multi),len(multi)),'cases':multi}}


def write_report(baseline, llm, config_error=None):
    lines=['# Rule-based vs Controlled LLM Comparison','',
           '原 Golden Set：37 cases；原 expected_intent/tool/status/facts/forbidden claims 未改变。',
           '', '| Metric | Frozen rule-based baseline | Real LLM |','|---|---:|---:|']
    for name,b in baseline['metrics'].items():
        value=f"{llm['metrics'][name]['rate']:.2%}" if llm else 'N/A — 未运行'
        lines.append(f"| {name} | {b['rate']:.2%} | {value} |")
    if not llm:
        lines += ['', '## Execution status','', '没有真实 LLM 执行结果，也没有 LLM 成绩。',
                  '配置检查：'+(config_error or '本次仅核验 baseline，未请求 LLM。'),
                  '离线 ScriptedProvider 只测试协议与控制层，不计入此表；不能据此声称模型理解、回答或 multi-intent 质量已验证。']
    else:
        lines += ['', '## Real run details','',
                  f"Provider/model：{json.dumps(llm['model_metadata'],ensure_ascii=False)}。",
                  f"错误 cases：{sum(bool(r['trace']['errors']) for r in llm['cases'])}；数值来源通过率：{llm['explanation_grounding']['rate']:.2%}。",
                  '失败：']
        lines += [f"- {r['id']}：{r['question']}；intent/tool/status={r['intent_ok']}/{r['tool_ok']}/{r['status_ok']}；errors={r['trace']['errors']}"
                  for r in llm['cases'] if not r['passed']]
    lines += ['', 'LLM 数值 case 检查额外要求指定数字展示于最终回答的有效 numeric_evidence；原 baseline 数值成绩不追溯改变。', '', '## Multi-intent comparability','',
              '原严格 37-case 分数仍要求四个 multi-intent cases 返回 ambiguous；LLM 若成功多工具回答，在该严格标准下会失败。这是能力范围差异，不能改写 baseline 历史成绩。',
              '单独 supplement 为这四个 ID 声明预期工具组合，要求 answered、各任务事实、数值来源和边界合规，最多三次调用。它不改变原 Golden Set，也不混入原 overall score。']
    if llm:
        lines.append(f"Supplement pass：{llm['multi_intent_supplement']['score']['rate']:.2%}。")
    else: lines.append('Supplement：N/A；尚无真实模型运行。')
    lines += ['', '## What to assess next','',
              '真实配置后重点比较 paraphrase、中英混合、错误前提、多意图与自然解释；逐项查看错工具、错参数、来源缺失、数值幻觉、因果越界与数据边界回退，不只看总分。',
              '数值来源核验可阻止无来源字面量，却不能全面判断单位语义或同值不同义；禁断言正则仅覆盖有限措辞。没有真实模型结果时，不能推断 LLM 已带来这些收益或没有这些风险。',
              '', '## Integrity','',
              'llm_baseline_manifest.json 冻结 agent.py、reasoning、tools、原评估器、Golden Set、expected_values 和历史 baseline report/results。每次比较前检查文件哈希。',
              '可用 --verify-baseline 实际重跑原评估器；程序保留新结果为本轮验证证据，并恢复原冻结 report/results 字节，避免重写历史。',
              '真实 LLM trace/results 为动态数据，保存在 runs/ 或 llm_results*.json，均已忽略；日志不保存 key。', '']
    (HERE/'llm_comparison_report.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('baseline','llm','compare'),default='compare')
    parser.add_argument('--verify-baseline',action='store_true')
    parser.add_argument('--save-traces',action='store_true')
    args=parser.parse_args()
    check_baseline_integrity()
    baseline=verify_baseline() if args.mode=='baseline' or args.verify_baseline else load('evaluation_results_after.json')
    cases=load('evaluation_cases.json')['cases'];values=load('expected_values.json')['values']
    if args.mode=='baseline':
        write_report(baseline,None)
        print(json.dumps({'mode':'baseline','overall':baseline['metrics']['Overall Case Pass Rate'],'llm_executed':False},indent=2))
        return 0
    try: from_environment()  # Validate configuration without sending any request.
    except ConfigurationError as error:
        write_report(baseline,None,str(error))
        print(json.dumps({'status':'configuration_missing','llm_executed':False,'error':str(error)},ensure_ascii=False))
        return 1
    traces=[]
    for case in cases:
        print('LLM case: '+case['id'],flush=True)
        trace=run(case['question'],from_environment(),repair_final_answer=True)
        traces.append(trace)
        if args.save_traces: save_trace(trace)
    result=score(traces,cases,values)
    result.update({'real_api_attempted':True,'real_llm_run':any(t.get('model_rounds') for t in traces),'run_time':datetime.now(ZoneInfo('Australia/Melbourne')).isoformat(),
                   'model_metadata':traces[0]['model_metadata'],
                   'golden_set_sha256':digest(HERE/'evaluation_cases.json'),
                   'expected_values_sha256':digest(HERE/'expected_values.json')})
    check_baseline_integrity()
    (HERE/'llm_results_latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    write_report(baseline,result if result['real_llm_run'] else None,
                 None if result['real_llm_run'] else '所有 API 请求均未返回完成的模型 turn；不把传输失败当成真实模型质量成绩。')
    print(json.dumps(result['metrics'],ensure_ascii=False,indent=2))
    return 0 if all(r['passed'] for r in result['cases']) else 1


if __name__=='__main__': raise SystemExit(main())
