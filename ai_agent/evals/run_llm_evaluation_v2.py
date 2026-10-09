"""Offline-only architecture-aware evaluation of saved LLM traces. Never runs tools/API."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from ai_agent.llm_grounding import normalize_rounded_evidence, independent_audit_interpretation, directional_prose_audit
from ai_agent.llm_grounding import source_unit
from ai_agent.tool_schemas import INTENTS
from .numeric_grounding import source_value, NUMBER, numeric_grounding_check
from .run_evaluation import forbidden_hits
from .llm_v2_predicates import concept_check, prose

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]

def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def metric(passed,total): return {'passed':passed,'total':total,'rate':passed/total if total else None}

def protected_hashes(source):
    manifest=load(HERE/'llm_baseline_manifest.json')['protected_files']
    for name,expected in manifest.items():
        if digest(ROOT/name)!=expected: raise RuntimeError('Frozen baseline changed: '+name)
    paths=[ROOT/p for p in manifest]+[source,HERE/'llm_results_latest.json',HERE/'llm_comparison_report.md']
    return {str(p):digest(p) for p in paths}

def set_check(expected,actual,optional=()):
    e,a,o=set(expected),set(actual),set(optional)
    return {'expected':sorted(e),'actual':sorted(a),'exact_match':e==a,
            'missing':sorted(e-a),'unnecessary':sorted(a-e-o),'reasonable_extra':sorted((a-e)&o),
            'required_set_pass':e<=a and a<=e|o}

def grounding_check(trace):
    try:
        data={'calls':{r['call_id']:r['result'] for r in trace.get('tool_results',[]) if 'result' in r}}
        answer=normalize_rounded_evidence(trace.get('final_answer') or {},data)
        interpretation=independent_audit_interpretation(answer)
        interpretation,_=directional_prose_audit(interpretation,answer['numeric_evidence'])
        audit=numeric_grounding_check({'key_data':data,'interpretation':interpretation,'caveats':[]})
        return {'passed':audit['passed'],'errors':audit['errors'],
                'recorded_passed':trace.get('numeric_grounding',{}).get('passed'),'recomputed':True}
    except (ValueError,KeyError,TypeError) as exc:
        return {'passed':False,'errors':[str(exc)],'recorded_passed':trace.get('numeric_grounding',{}).get('passed'),'recomputed':True}

def numeric_fact(trace,spec,grounded,strict_display=False):
    """Match exact field, actual call, identity operation, unit and a prose occurrence.

    Strict precision uses unchanged Golden tolerance. Presence uses the validated
    declared display format; neither uses tool definitions as answer evidence.
    """
    relative=spec['path'].removeprefix('key_data/')
    results=[r for r in trace.get('tool_results',[]) if INTENTS.get(r.get('name'))==spec['intent'] and 'result' in r]
    paths={f"calls/{r['call_id']}/{relative}" for r in results}
    data={'calls':{r['call_id']:r['result'] for r in results}}
    available=False
    for path in paths:
        try: available |= abs(source_value(data,path)-spec['value'])<=spec['abs_tolerance']
        except (ValueError,KeyError,TypeError): pass
    answer=trace.get('final_answer') or {}; tokens={m.group() for m in NUMBER.finditer(prose(answer))}
    matches=[]
    for e in answer.get('numeric_evidence',[]):
        if e.get('operation')!='identity' or e.get('paths') not in [[p] for p in paths]: continue
        try:
            value=float(e['display'].replace(',',''))
            unit=source_unit(e['paths'][0]);correct_unit=unit is None or e.get('unit')==unit
            if grounded and correct_unit and e['display'] in tokens and math.isfinite(value):
                matches.append(e['display'])
        except (KeyError,TypeError,ValueError): continue
    precise=any(abs(float(d.replace(',',''))-spec['value'])<=spec['abs_tolerance'] for d in matches)
    return {'passed':available and (precise if strict_display else bool(matches)),
            'tool_result_available':available,'displayed_tokens':matches,'precision_met':precise,
            'expected_source_path':relative,'golden_value':spec['value'],'golden_tolerance':spec['abs_tolerance'],
            'failure_kind':None if available and (precise if strict_display else bool(matches)) else
                'source_missing_or_incorrect' if not available else 'display_precision' if matches else 'missing_numeric_display'}

def overall(row):
    return all((row['intent_set']['required_set_pass'],row['tool_set']['required_set_pass'],
                row['execution_matches_selection'],row['arguments_ok'],row['status_ok'],row['numeric_grounding']['passed'],
                all(f['passed'] for f in row['fact_checks']),all(b['passed'] for b in row['boundary_rules']),
                not row['forbidden_claim_hits'],not row['trace_errors'],row['call_budget_ok']))

def evaluate(saved,cases,values,profiles):
    originals={r['id']:r for r in saved['cases']}
    if set(originals)!=set(profiles) or set(originals)!={c['id'] for c in cases}:
        raise ValueError('Saved traces, Golden IDs and v2 profiles must match exactly')
    rows=[];display=[]
    for case in cases:
        i=case['id'];legacy=originals[i];trace=legacy['trace'];profile=profiles[i];answer=trace.get('final_answer') or {}
        if trace['user_question']!=case['question']: raise ValueError('Question mismatch: '+i)
        g=grounding_check(trace)
        facts=[dict(numeric_fact(trace,values[n],g['passed']),predicate='numeric:'+n) for n in profile['numeric_facts']]
        facts += [concept_check(answer,f) for f in profile['semantic_facts']]
        bounds=[concept_check(answer,f) for f in profile['boundary_rules']]
        hits=forbidden_hits({'interpretation':prose(answer),'caveats':[]},case['forbidden_claims'])
        executed=[r['name'] for r in trace.get('tool_results',[]) if 'result' in r]
        tools=set_check(profile['expected_tools'],executed,profile['optional_tools'])
        row={'id':i,'category':case['category'],'question':case['question'],
             'intent_set':set_check(profile['expected_intents'],answer.get('detected_intents',[]),profile.get('optional_intents',[])),
             'tool_set':tools,'selected_tools':trace.get('selected_tools',[]),
             'execution_matches_selection':set(executed)==set(trace.get('selected_tools',[])),
             'arguments_ok':all(any(r.get('name')==t and r.get('arguments')==a for r in trace.get('tool_results',[]))
                                for t,a in profile['argument_expectations'].items()),
             'expected_status':profile['expected_status'],'actual_status':trace.get('status'),
             'status_ok':trace.get('status')==profile['expected_status'],
             'numeric_grounding':g,'fact_checks':facts,'boundary_rules':bounds,'forbidden_claim_hits':hits,
             'trace_errors':trace.get('errors',[]),'call_budget_ok':trace.get('tool_call_count',0)<=3,
             'legacy_strict_case_pass':legacy['passed']}
        row['architecture_aware_case_pass']=overall(row)
        # Explicit independent causes, without conflating phrase coverage and grounding.
        row['failure_reasons']=[]
        for k,v in [('intent_set',row['intent_set']['required_set_pass']),('tool_set',tools['required_set_pass']),
                    ('status',row['status_ok']),('arguments',row['arguments_ok']),('grounding',g['passed']),
                    ('execution',row['execution_matches_selection']),('call_budget',row['call_budget_ok'])]:
            if not v: row['failure_reasons'].append(k)
        row['failure_reasons'] += ['fact:'+f['predicate'] for f in facts if not f['passed']]
        row['failure_reasons'] += ['boundary:'+f['predicate'] for f in bounds if not f['passed']]
        if hits: row['failure_reasons'].append('forbidden_claim')
        if row['trace_errors']: row['failure_reasons'].append('trace_error')
        rows.append(row)
        for f in case['required_facts']:
            if f['type']=='number':
                display.append(dict(numeric_fact(trace,values[f['expected_value']],g['passed'],True),case_id=i,requirement=f['expected_value']))
    n=len(rows);facts=[f for r in rows for f in r['fact_checks']];boundary=[r for r in rows if r['category'] in ('boundary','causal')]
    tp=sum(len(set(r['intent_set']['expected'])&set(r['intent_set']['actual'])) for r in rows)
    predicted=sum(len(r['intent_set']['actual']) for r in rows);wanted=sum(len(r['intent_set']['expected']) for r in rows)
    p=tp/predicted if predicted else 0;rec=tp/wanted if wanted else 0
    metrics={
      'intent_set_exact_accuracy':metric(sum(r['intent_set']['exact_match'] for r in rows),n),
      'intent_set_micro':{'precision':p,'recall':rec,'F1':2*p*rec/(p+rec) if p+rec else 0,'true_positive_labels':tp,'predicted_labels':predicted,'expected_labels':wanted},
      'tool_set_exact_accuracy':metric(sum(r['tool_set']['exact_match'] for r in rows),n),
      'required_tool_set_accuracy':metric(sum(r['tool_set']['required_set_pass'] for r in rows),n),
      'status_accuracy':metric(sum(r['status_ok'] for r in rows),n),
      'numeric_grounding_source_accuracy':metric(sum(r['numeric_grounding']['passed'] for r in rows),n),
      'numeric_display_requirement_coverage':metric(sum(r['passed'] for r in display),len(display)),
      'answer_prose_fact_coverage':metric(sum(f['passed'] for f in facts),len(facts)),
      'tool_result_numeric_fact_availability':metric(sum(f['tool_result_available'] for f in facts if 'tool_result_available' in f),sum('tool_result_available' in f for f in facts)),
      'boundary_compliance':metric(sum(all(b['passed'] for b in r['boundary_rules']) and not r['forbidden_claim_hits'] for r in boundary),len(boundary)),
      'forbidden_claim_cases':sum(bool(r['forbidden_claim_hits']) for r in rows),
      'forbidden_claim_rate':sum(bool(r['forbidden_claim_hits']) for r in rows)/n,
      'architecture_aware_case_pass':metric(sum(r['architecture_aware_case_pass'] for r in rows),n)}
    categories={c:metric(sum(r['architecture_aware_case_pass'] for r in rows if r['category']==c),sum(r['category']==c for r in rows)) for c in sorted({r['category'] for r in rows})}
    usage={'model_rounds':0,'input_tokens':0,'output_tokens':0,'total_tokens':0}
    for legacy in saved['cases']:
        for round_ in legacy['trace'].get('model_rounds',[]):
            usage['model_rounds']+=1
            for key in ('input_tokens','output_tokens','total_tokens'): usage[key]+=round_.get('usage',{}).get(key,0)
    return {'schema_version':2,'offline_only':True,'new_api_requests':0,'legacy_metrics':saved['metrics'],
            'legacy_intent_accuracy':saved['metrics']['Intent Accuracy'],
            'legacy_tool_selection_accuracy':saved['metrics']['Tool Selection Accuracy'],
            'legacy_status_accuracy':saved['metrics']['Status Accuracy'],
            'architecture_aware_metrics':metrics,'categories':categories,'cases':rows,'numeric_display_checks':display,
            'design_conflicts':[r['id'] for r in rows if r['category']=='multi_intent'],
            'evaluator_only_mismatch_cases':[r['id'] for r in rows if r['architecture_aware_case_pass'] and not r['legacy_strict_case_pass']],
            'architecture_failure_cases':[r['id'] for r in rows if not r['architecture_aware_case_pass']],
            'historical_usage':usage,'legacy_run_time':saved.get('run_time'),'model_metadata':saved.get('model_metadata')}

def report(result):
    lines=['# Offline LLM evaluator v2','', '这是独立、事后建立的 architecture-aware 评估视图；原 Golden 与 legacy 分数未修改。没有新 API 请求。',
      '', '## Legacy frozen contract','', '| Metric | Result |','|---|---:|']
    for k,m in result['legacy_metrics'].items(): lines.append(f"| {k} | {m['passed']}/{m['total']} ({m['rate']:.2%}) |")
    lines += ['', 'Forbidden Claim Rate 的 legacy passed=0 表示零违规，不是零个通过。', '', '## Architecture-aware metrics','', '| Metric | Result |','|---|---:|']
    for k,m in result['architecture_aware_metrics'].items():
        value=f"{m['passed']}/{m['total']} ({m['rate']:.2%})" if isinstance(m,dict) and 'passed' in m else json.dumps(m,ensure_ascii=False)
        lines.append(f'| {k} | {value} |')
    lines += ['', '## Definitions and scope','',
      '- Intent exact 用完整集合；micro P/R/F1 汇总标签，包括 unsupported/ambiguous，不投影多意图。',
      '- Tool exact 使用成功执行集合，另检查选择/执行一致；overall 要求全部必需工具，允许显式声明的辅助工具。diagnostic_01 可用 growth 辅助绝对量分析；diagnostic_03 的 funnel 不能替代 Cart growth 数据。',
      '- Status 独立；四个 multi-intent expected=answered。原 ambiguous/no-tool 保留于 legacy。',
      '- Numeric source：离线重新执行现有来源、单位、格式、字面量、方向校验；包含固定 policy。不是展示覆盖率，也不是完整语义正确率。',
      '- Display coverage：原 Golden 17 项数字展示要求，精确来源路径、单位、正文 token、原 tolerance；缺数字和低精度不叫数值幻觉。',
      '- Task facts：独立 contracts 文件仅要求问题专属事实；数值需要实际调用的对应字段 identity evidence、正文展示及 grounding，不要求额外主题数字；semantic predicates 只看五个 prose 字段。',
      '- Tool availability 仅统计任务 numeric facts 的实际值；语义事实的 metadata 不计作模型正文。',
      '- Boundary：12 个 boundary/causal case 专属规则与 forbidden hits，独立于通用事实、路由与总体 pass。',
      '- Overall：required intent/tool sets、status、arguments、grounding、task facts、专属边界、无 forbidden/error、调用预算；不依赖 legacy F1/F2/F3，也不依赖原全主题 display coverage。',
      '', '## Categories','', '| Category | Pass |','|---|---:|']
    for k,m in result['categories'].items(): lines.append(f"| {k} | {m['passed']}/{m['total']} |")
    lines += ['', '## All case results','', '| ID | v2 pass | legacy pass | Failures / auxiliary tools |','|---|---|---|---|']
    for r in result['cases']:
        reason='; '.join(r['failure_reasons']) or 'auxiliary='+','.join(r['tool_set']['reasonable_extra'])
        lines.append(f"| {r['id']} | {r['architecture_aware_case_pass']} | {r['legacy_strict_case_pass']} | {reason} |")
    lines += ['', '## Boundary rules','']
    for r in result['cases']:
        if r['boundary_rules']:
            lines.append(f"- {r['id']}: "+'; '.join(f"{b['predicate']}={b['passed']}" for b in r['boundary_rules']))
            for b in r['boundary_rules']: lines.append('  Evidence: '+json.dumps(b['matched_prose'],ensure_ascii=False))
    lines += ['', '## Retained behavior / protocol failures','']
    for r in result['cases']:
        if not r['architecture_aware_case_pass']:
            lines.append(f"- {r['id']} / {r['question']}: missing_tools={r['tool_set']['missing']}; unnecessary_tools={r['tool_set']['unnecessary']}; intents expected/actual={r['intent_set']['expected']}/{r['intent_set']['actual']}; status expected/actual={r['expected_status']}/{r['actual_status']}; failures={r['failure_reasons']}")
    lines += ['', 'Domain refusal and clarification ambiguity can both be defensible in prose; a status mismatch remains recorded under the independent unsupported contract.',
      '', '## Evaluator-only mismatches and design conflicts','',
      'Cases that pass v2 but failed legacy: '+', '.join(result['evaluator_only_mismatch_cases']),
      'Multi-intent architecture conflicts: '+', '.join(result['design_conflicts']),
      'Exact intent set counts optional auxiliary labels as extras; overall explicitly permits declared auxiliary labels/tools. Exact tool set and required-tool policy are separately reported.',
      '', '## Display-only mismatches','']
    for d in result['numeric_display_checks']:
        if not d['passed']: lines.append(f"- {d['case_id']} / {d['requirement']}: {d['failure_kind']}; displayed={d['displayed_tokens']}; required={d['golden_value']} ± {d['golden_tolerance']}")
    lines += ['', '## Interpretation and caveats','',
      '真正行为问题与 evaluator 未识别措辞必须分开人工复核；predicate missing 不自动证明模型说错。',
      '四个 multi-intent 的 legacy status/tool 失败属于能力范围设计冲突；合法同义表达、无关全主题事实及 boundary composite 造成的差异属于 evaluator-only mismatch。',
      '当前数据是首次 run 的离线重评，没有新样本或重复模型试验。Predicates 是有限规则，不能全面理解反讽、复杂否定、跨句推理或所有因果断言；0 forbidden hits 只表示这些检测未命中。',
      '不能把本次事后 v2 分数当作事先冻结或泛化 benchmark；下一次独立评估应先冻结 v2 contracts 与 self-tests，再看新模型结果。',
      '旧 token usage 为历史请求用量，无新增 API 请求；不推算费用。',
      '', '## Provenance and integrity','',json.dumps(result.get('provenance',{}),ensure_ascii=False,indent=2),
      '', 'Historical usage: '+json.dumps(result['historical_usage']), '']
    return '\n'.join(lines)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=HERE/'llm_results_latest.json')
    args=parser.parse_args()
    if args.input.resolve() in {(HERE/'llm_results_v2_latest.json').resolve(),(HERE/'llm_evaluation_v2_report.md').resolve()}:
        raise ValueError('Input must not be a v2 output path')
    before=protected_hashes(args.input)
    result=evaluate(load(args.input),load(HERE/'evaluation_cases.json')['cases'],load(HERE/'expected_values.json')['values'],load(HERE/'llm_v2_contracts.json')['cases'])
    result['provenance']={'input':str(args.input.resolve()),'input_sha256':digest(args.input),'contracts_sha256':digest(HERE/'llm_v2_contracts.json'),
                          'baseline_hashes_unchanged':before==protected_hashes(args.input),'evaluated_at':datetime.now(timezone.utc).isoformat()}
    if not result['provenance']['baseline_hashes_unchanged']: raise RuntimeError('Protected files changed')
    (HERE/'llm_results_v2_latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (HERE/'llm_evaluation_v2_report.md').write_text(report(result),encoding='utf-8')
    if before!=protected_hashes(args.input): raise RuntimeError('Protected files changed after report')
    print(json.dumps(result['architecture_aware_metrics'],ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
