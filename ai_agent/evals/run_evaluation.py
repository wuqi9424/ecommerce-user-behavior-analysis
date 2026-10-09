"""Deterministic golden evaluation. No model, judge, framework or database writes."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
from functools import wraps
import hashlib
import json
import math
import io
import unittest
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_agent import agent
from ai_agent.tools import _common
from .numeric_grounding import numeric_grounding_check

HERE = Path(__file__).resolve().parent


def read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def at(data, path):
    for segment in path.split('/'):
        if isinstance(data, list):
            if '=' in segment:
                key, expected = segment.split('=', 1)
                matches = [row for row in data if str(row.get(key)) == expected]
                if len(matches) != 1:
                    raise ValueError(f'Selector {segment} matched {len(matches)} rows')
                data = matches[0]
            else:
                data = data[int(segment)]
        else:
            data = data[segment]
    return data


def text_of(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return '\n'.join(text_of(v) for v in value.values())
    if isinstance(value, list):
        return '\n'.join(text_of(v) for v in value)
    return str(value)


def number_check(output, spec):
    try:
        actual = at(output, spec['path'])
        passed = (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                  and math.isfinite(actual)
                  and abs(actual - spec['value']) <= spec['abs_tolerance'])
        return {'passed': passed, 'actual': actual, 'expected': spec['value'],
                'abs_tolerance': spec['abs_tolerance'], 'path': spec['path']}
    except (KeyError, IndexError, ValueError, TypeError) as error:
        return {'passed': False, 'error': str(error), 'path': spec['path']}


def fact_check(output, fact, values):
    if fact['type'] == 'number':
        return number_check(output, values[fact['expected_value']])
    scope = output if fact['scope'] == 'response' else output.get(fact['scope'], {})
    if fact['type'] == 'text':
        body = text_of(scope).casefold()
        return {'passed': any(term.casefold() in body for term in fact['any_of']),
                'scope': fact['scope'], 'any_of': fact['any_of']}
    if fact['type'] == 'sections':
        return {'passed': isinstance(scope, dict) and all(scope.get(k) for k in fact['fields']),
                'required_sections': fact['fields']}
    raise ValueError(f"Unknown fact type: {fact['type']}")


def forbidden_hits(output, claims):
    # Check assertions in the explanation/caveats, not definitions or echoed input.
    body = text_of([output.get('interpretation', {}), output.get('caveats', [])])
    hits = []
    for clause in re.split(r'[。！？；;，,\n]', body):
        for claim in claims:
            for match in re.finditer(claim['pattern'], clause, re.I):
                prefix = clause[:match.start()]
                if re.search(r'不能|不等于|无法|不得|不要|不应|不是|尚未|未能', prefix):
                    continue
                hits.append({'id': claim['id'], 'claim': match.group(0)})
    return hits


def validate_set(cases, values):
    ids = [c['id'] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate case ids')
    required = {'id', 'category', 'question', 'expected_intent', 'expected_tool',
                'expected_status', 'required_facts', 'forbidden_claims', 'notes'}
    for case in cases:
        if not required <= case.keys():
            raise ValueError(f"Missing fields: {case.get('id')}")
        intent = case['expected_intent']
        if intent not in {*agent.TOOLS, 'unsupported', 'ambiguous'}:
            raise ValueError(f'Unknown golden intent: {intent}')
        expected_status = intent if intent in ('unsupported', 'ambiguous') else 'answered'
        if case['expected_status'] != expected_status:
            raise ValueError(f"Inconsistent status: {case['id']}")
        expected_tool = agent.TOOLS[intent].__name__ if intent in agent.TOOLS else None
        if case['expected_tool'] != expected_tool:
            raise ValueError(f"Inconsistent tool: {case['id']}")
        for fact in case['required_facts']:
            if fact['type'] == 'number' and values[fact['expected_value']]['intent'] != intent:
                raise ValueError(f"Number from another tool: {case['id']}")
        for claim in case['forbidden_claims']:
            re.compile(claim['pattern'])


def failure_paths():
    outcomes = []
    def record(name, passed, detail):
        outcomes.append({'id': name, 'passed': passed, 'detail': detail})
    with TemporaryDirectory(prefix='agent-eval-') as directory:
        missing = Path(directory) / 'missing.duckdb'
        with patch.object(_common, 'DATABASE', missing):
            try:
                agent.TOOLS['platform']()
            except FileNotFoundError as error:
                record('missing_database_api', not missing.exists(), str(error))
            except Exception as error:
                record('missing_database_api', False, repr(error))
            else:
                record('missing_database_api', False, 'No error raised')
        # Child process overrides only the internal database path: no rename/deletion.
        script = ("from pathlib import Path; import sys; from ai_agent.tools import _common; "
                  "from ai_agent import agent; _common.DATABASE=Path(sys.argv[1]); "
                  "sys.argv=['agent','平台总览']; raise SystemExit(agent.main())")
        cli = subprocess.run([sys.executable, '-c', script, str(missing)],
                             cwd=_common.ROOT, text=True, capture_output=True)
        try:
            error = json.loads(cli.stderr)
            passed = cli.returncode == 1 and not cli.stdout and error['error'] == 'FileNotFoundError'
        except (ValueError, KeyError):
            passed = False
        record('missing_database_cli', passed,
               {'returncode': cli.returncode, 'stderr': cli.stderr, 'file_created': missing.exists()})
    for invalid in (0, 101, 1.5, '5', True):
        with patch('ai_agent.tools.opportunity_tools.connection', side_effect=AssertionError('DB accessed')):
            try:
                agent.TOOLS['opportunity'](invalid)
            except ValueError as error:
                record(f'top_n_api_{invalid!r}', True, str(error))
            except Exception as error:
                record(f'top_n_api_{invalid!r}', False, repr(error))
            else:
                record(f'top_n_api_{invalid!r}', False, 'No error raised')
    for invalid, code in [('0', 1), ('101', 1), ('1.5', 2)]:
        cli = subprocess.run([sys.executable, '-m', 'ai_agent.agent', '机会品类', '--top-n', invalid],
                             cwd=_common.ROOT, capture_output=True, text=True)
        record(f'top_n_cli_{invalid}', cli.returncode == code and bool(cli.stderr) and not cli.stdout,
               {'returncode': cli.returncode, 'stderr': cli.stderr})
    for name, question in [('write_request', '删除数据库里的用户'), ('unknown_intent', '你好，请帮我写一首诗')]:
        def banned(*args, **kwargs):
            raise AssertionError('A tool was called for an unsupported request')
        with patch.dict(agent.TOOLS, {k: banned for k in agent.TOOLS}):
            result = agent.analyze(question)
        cli = subprocess.run([sys.executable, '-m', 'ai_agent.agent', question],
                             cwd=_common.ROOT, capture_output=True, text=True)
        try:
            response = json.loads(cli.stdout)
            passed = (cli.returncode == 2 and response.get('detected_intent') == 'unsupported'
                      and response.get('tool_used') is None and '无法判断' in text_of(response))
        except ValueError:
            passed = False
        record(name, passed and result['tool_used'] is None,
               {'returncode': cli.returncode, 'meaning': 'explicit unsupported response; nonzero because no answer/tool execution'})
    return outcomes


def evaluator_self_checks(cases, values):
    claims = cases[0]['forbidden_claims']
    positive = ['buy event = order', 'newly observed 用户就是新增注册用户',
                '长期复购率为 55%', '购物车召回已经有效', '广告投放导致了流量增长',
                'GMV 为 100', 'P1 用户一定更容易购买']
    negative = ['不能说 buy event 就是订单', '首次观测不等于注册用户',
                '不能证明购物车召回已经有效', '无法判断广告投放导致了流量增长']
    checks = [bool(forbidden_hits({'interpretation': {'answer': s}}, claims)) for s in positive]
    checks += [not forbidden_hits({'interpretation': {'answer': s}}, claims) for s in negative]
    spec = {'path': 'x', 'value': 100, 'abs_tolerance': .01}
    checks += [not number_check({'x': 101}, spec)['passed'],
               not number_check({'x': True}, spec)['passed'],
               not number_check({'x': float('nan')}, spec)['passed'],
               not number_check({}, spec)['passed'],
               not fact_check({}, {'type':'text','scope':'interpretation','any_of':['必须存在']}, values)['passed']]
    if not all(checks):
        raise AssertionError(f'Evaluator self-check failed: {checks}')
    return len(checks)


def metric(passed, total):
    return {'passed': passed, 'total': total, 'rate': passed / total if total else None}


def report_markdown(result, before):
    lines = ['# Golden Evaluation Report', '',
             f"运行时间（带时区）：{result['run_time']}。Python {result['python']}，DuckDB {result['duckdb_version']}。",
             '', f"Golden cases：**{result['case_count']}**；分类：", '', '| Category | Cases |', '|---|---:|']
    lines += [f'| {k} | {v} |' for k, v in result['categories'].items()]
    lines += ['', '## Metrics', '', '| Metric | Count / Total | Result |', '|---|---:|---:|']
    for name, item in result['metrics'].items():
        rate = 'N/A' if item['rate'] is None else f"{item['rate']:.2%}"
        lines.append(f"| {name} | {item['passed']} / {item['total']} | {rate} |")
    lines += ['', 'Numeric Accuracy 分母为 17 个全量 ground truth 检查加各 case 的数值要求；'
              'Required Fact Coverage 按原子事实检查计数。Boundary Compliance 为 boundary/causal cases '
              '的状态、工具调用、边界说明与禁断言全部通过比例；Forbidden Claim Rate 为出现至少一个禁断言的 case 比例。'
              '整体 case 要求意图、工具、状态、全部 required facts、禁断言及新增数值 grounding 均通过。', '', '## Failed golden cases', '']
    failed = [c for c in result['cases'] if not c['passed']]
    if not failed:
        lines.append('无。此结果仅适用于冻结测试集，不能推断任意问题均正确。')
    for case in failed:
        lines += [f"- **{case['id']}**：{case['question']}",
                  f"  意图/工具/状态：{case['intent_ok']}/{case['tool_ok']}/{case['status_ok']}；"
                  f"缺失事实：{json.dumps([f for f in case['fact_checks'] if not f['passed']], ensure_ascii=False)}；"
                  f"禁断言：{case['forbidden_hits']}；数值 grounding：{case['numeric_grounding']}。"]
    lines += ['', '## Failure paths', '']
    lines += [f"- {f['id']}：{'PASS' if f['passed'] else 'FAIL'}。{json.dumps(f['detail'], ensure_ascii=False)}"
              for f in result['failure_paths']]
    lines += ['', 'unsupported/ambiguous 输出明确结构化状态，CLI 返回 2；执行失败返回 1，argparse 非整数也返回 2。'
              '没有重命名、删除或写入数据库；缺失数据库使用内部路径替换测试。', '', '## Before / after', '']
    if before:
        lines += [f"本轮 explanation 修复前：{before['metrics']['Overall Case Pass Rate']['passed']}/{before['case_count']} = {before['metrics']['Overall Case Pass Rate']['rate']:.2%}；修复后：{result['metrics']['Overall Case Pass Rate']['passed']}/{result['case_count']} = {result['metrics']['Overall Case Pass Rate']['rate']:.2%}。", '']
        lines += ['| Metric | Before | After |', '|---|---:|---:|']
        for name, item in result['metrics'].items():
            lines.append(f"| {name} | {before['metrics'][name]['rate']:.2%} | {item['rate']:.2%} |")
        changed = [(b, a) for b, a in zip(before['cases'], result['cases'])
                   if (b['actual_intent'], b['passed']) != (a['actual_intent'], a['passed'])]
        lines += ['', '路由、状态和 CLI 退出码保持不变。改进仅来自通用 explanation reasoning；变化的 case：']
        lines += [f"- {a['id']}：intent {b['actual_intent']} → {a['actual_intent']}；pass {b['passed']} → {a['passed']}。"
                  for b, a in changed]
    else:
        lines.append('未提供同一 Golden Set 的修复前运行记录。')
    lines += ['', '## Provenance and integrity', '',
              f"- 数据库前后 SHA-256 一致：{result['database_unchanged']}。",
              f"- 工具源文件与修复前一致：{result['tools_unchanged_from_before']}。",
              f"- 评估器自检：{result['evaluator_self_checks']} 项通过。",
              '- 运行中每个真实工具查询一次；每个问题经过真实 analyze 路由与解释层，工具调用由 spy 记录。'
              '同一运行的重复工具请求复用深拷贝结果，仅在评估器内；不读取 last_run.json 作答案缓存。'
              '输出 JSON 保存每个 case 的实际响应、检查细节、源码及评估集哈希。',
              '', '## Deterministic baseline limitations', '',
              '关键词路由不能充分理解否定、复杂语义或任意中英混合；固定解释模板可能没有回应诊断问题。'
              '精确事实检查只覆盖预设路径与短语，禁止断言正则仅覆盖已列举措辞，0% 命中不能证明没有幻觉。'
              '原 Numeric Accuracy 仍按结构化字段计数，保证与之前分母一致。新增轻量检查核验 explanation 的数值来源、派生公式、格式与字面覆盖；它不全面验证单位语义或数字所关联的自然语言主张。'
              '不把这些限制包装成已经解决，也不放宽事实要求以提高 pass rate。',
              '', '## Next: LLM Tool Calling evaluation', '',
              '可使用同一 Golden Set 比较 semantic routing、真实工具选择/参数、numeric grounding、'
              'boundary compliance 与 hallucination。接入时保持外部工具白名单，记录调用轨迹，'
              '新增自然语言数字与来源核验、prompt injection、工具失败传播、重复运行稳定性。'
              '当前已建立可冻结的有限测试集 baseline；100% 仅指此集。后续接入采用同一测试集比较，保持白名单与工具失败显式传播。本轮不接模型或自由 SQL。', '']
    grounding = result['explanation_numeric_grounding']
    ready = (all(c['passed'] for c in result['cases']) and all(f['passed'] for f in result['failure_paths'])
             and result['reasoning_regressions']['passed'] and grounding['passed']==grounding['total'])
    lines += ['', '## Generic reasoning improvements', '',
              '完整修改前定位见 [explanation_failure_analysis.md](explanation_failure_analysis.md)。', '',
              '- diagnostic_01：Absolute vs Relative Change 使用日均购买人数与比例关系，区分规模与承接。',
              '- diagnostic_02：只比较相邻漏斗步骤的百分点差，避免把完整路径作为第三个相邻环节；处理并列、缺失与无下降。',
              '- diagnostic_03：Association ≠ Causality 区分事件强度、潜在意愿与因果证据。',
              '- diagnostic_04 / diagnostic_05 / causal_04：Scale ≠ Priority 与 Priority ≠ Probability 同时解释三层行为规则、规模和候选动作，不保证购买。',
              '- causal_06：Inactivity ≠ Churn 只给通用条件边界；保持 unsupported / 零调用，不编造沉寂群体最后一天活跃人数。',
              '- experiment：依据 simulated_assignment 分开处理统计显著性与真实 treatment effect；p-value/CI 改变时结论随之改变。',
              '', '## Explanation numeric grounding', '',
              f"新增数值来源检查：{grounding['passed']}/{grounding['total']} cases；检查 {grounding['checked_literals']} 个数值字面量、{grounding['checked_entries']} 条来源记录。",
              '展示数值来自工具字段路径，或白名单的 difference / pct_change；仅允许 alpha 与 CI 水平两个明确统计设置。评估器独立重算，拒绝无来源数值、伪造来源值与未使用的记录。',
              '不修改原 Numeric Accuracy 的分母。标识符 P1/P2/P3/Q75 不当成数值主张；相同数值可对应不同语义，当前检查不能证明单位和每句话的因果关系正确。',
              f"额外反事实 reasoning / grounding 回归：{result['reasoning_regressions']['tests_run']} tests，pass={result['reasoning_regressions']['passed']}。原 16 个评估器自检与 12 个失败路径仍独立执行。",
              '', '## Baseline freeze decision', '',
              ('当前完整 case、数值来源与回归通过，建议冻结 deterministic baseline。' if ready else '仍存在失败，暂不冻结；保留真实失败继续定位。')+'冻结的是当前有限窗口和白名单工具上的回归基准，不是任意问题均正确的保证。',
              'protected_files_unchanged 核对本轮修改前 tools、evaluation_cases、expected_values 文件哈希；未修改数据库或任何工具查询，也未调整 expected facts / forbidden claims。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['before', 'after'], default='after')
    parser.add_argument('--comparison', default='evaluation_results_explanation_before.json',
                        help='Saved pre-explanation baseline; does not alter the Golden Set')
    args = parser.parse_args()
    cases = read('evaluation_cases.json')['cases']
    values = read('expected_values.json')['values']
    validate_set(cases, values)
    self_checks = evaluator_self_checks(cases, values)
    database_before = digest(_common.DATABASE)
    originals = dict(agent.TOOLS)
    tool_hashes = {p.name: digest(p) for p in sorted((_common.ROOT/'ai_agent/tools').glob('*.py'))}
    snapshots = {}
    for intent, tool in originals.items():
        print(f'Querying real tool: {tool.__name__}', flush=True)
        snapshots[intent] = tool()
    numeric = {name: number_check({'key_data': snapshots[spec['intent']]}, spec)
               for name, spec in values.items()}
    calls = []
    def wrap(intent):
        @wraps(originals[intent])
        def tool(*args, **kwargs):
            calls.append(originals[intent].__name__)
            return deepcopy(snapshots[intent])
        return tool
    evaluated = []
    with patch.dict(agent.TOOLS, {i: wrap(i) for i in originals}):
        for case in cases:
            calls.clear()
            try:
                output = agent.analyze(case['question'])
                actual_intent = output.get('detected_intent')
                # Legacy MVP lacks status; derive only for before comparison and record origin.
                actual_status = output.get('status', actual_intent if actual_intent in ('unsupported','ambiguous') else 'answered')
                fact_checks = [fact_check(output, f, values) for f in case['required_facts']]
                forbidden = forbidden_hits(output, case['forbidden_claims'])
                grounding = numeric_grounding_check(output)
                intent_ok = actual_intent == case['expected_intent']
                expected_calls = [case['expected_tool']] if case['expected_tool'] else []
                tool_ok = output.get('tool_used') == case['expected_tool'] and calls == expected_calls
                status_ok = actual_status == case['expected_status']
                passed = intent_ok and tool_ok and status_ok and all(f['passed'] for f in fact_checks) and not forbidden and grounding['passed']
                evaluated.append({**case, 'actual_intent': actual_intent, 'actual_status': actual_status,
                                  'status_inferred': 'status' not in output, 'calls': list(calls),
                                  'intent_ok': intent_ok, 'tool_ok': tool_ok, 'status_ok': status_ok,
                                  'fact_checks': fact_checks, 'forbidden_hits': forbidden, 'numeric_grounding': grounding,
                                  'passed': passed, 'output': output})
            except Exception as error:
                evaluated.append({**case, 'actual_intent': 'error', 'actual_status': 'error',
                                  'calls': list(calls), 'intent_ok': False, 'tool_ok': False, 'status_ok': False,
                                  'fact_checks': [{'passed':False,'error':str(error)} for _ in case['required_facts']],
                                  'forbidden_hits': [], 'numeric_grounding': {'passed':False,'errors':[repr(error)],'checked_literals':0,'checked_entries':0}, 'passed': False, 'error': repr(error)})
    failures = failure_paths()
    database_after = digest(_common.DATABASE)
    if database_before != database_after:
        raise RuntimeError('Database changed during evaluation')
    all_facts = [f for c in evaluated for f in c['fact_checks']]
    case_numbers = [f for c in evaluated for spec, f in zip(c['required_facts'], c['fact_checks']) if spec['type']=='number']
    numbers = list(numeric.values()) + case_numbers
    boundary = [c for c in evaluated if c['category'] in ('boundary','causal')]
    metrics = {
        'Intent Accuracy': metric(sum(c['intent_ok'] for c in evaluated),len(cases)),
        'Tool Selection Accuracy': metric(sum(c['tool_ok'] for c in evaluated),len(cases)),
        'Status Accuracy': metric(sum(c['status_ok'] for c in evaluated),len(cases)),
        'Numeric Accuracy': metric(sum(n['passed'] for n in numbers),len(numbers)),
        'Required Fact Coverage': metric(sum(f['passed'] for f in all_facts),len(all_facts)),
        'Boundary Compliance': metric(sum(c['passed'] for c in boundary),len(boundary)),
        'Forbidden Claim Rate': metric(sum(bool(c['forbidden_hits']) for c in evaluated),len(cases)),
        'Overall Case Pass Rate': metric(sum(c['passed'] for c in evaluated),len(cases)),
    }
    before_path = HERE/args.comparison
    before = read(before_path.name) if args.phase=='after' and before_path.exists() else None
    manifest = read('explanation_baseline_manifest.json')
    protected = {path: digest(_common.ROOT/path)==expected
                 for path,expected in manifest['protected_files'].items()}
    if not all(protected.values()):
        raise RuntimeError(f'Protected tools / Golden Set changed: {protected}')
    test_stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName('ai_agent.evals.test_reasoning')
    reasoning_tests = unittest.TextTestRunner(stream=test_stream).run(suite)
    import duckdb
    from zoneinfo import ZoneInfo
    result = {'schema_version':1, 'phase':args.phase,
              'run_time':datetime.now(ZoneInfo('Australia/Melbourne')).isoformat(),
              'python':sys.version.split()[0], 'duckdb_version':duckdb.__version__,
              'case_count':len(cases), 'categories':dict(Counter(c['category'] for c in cases)),
              'metrics':metrics, 'numeric_ground_truth':numeric, 'cases':evaluated,
              'comparison_baseline':args.comparison,
              'protected_files_unchanged':protected,
              'explanation_numeric_grounding': {**metric(sum(c['numeric_grounding']['passed'] for c in evaluated),len(cases)),
                  'checked_literals':sum(c['numeric_grounding']['checked_literals'] for c in evaluated),
                  'checked_entries':sum(c['numeric_grounding']['checked_entries'] for c in evaluated)},
              'reasoning_regressions':{'passed':reasoning_tests.wasSuccessful(), 'tests_run':reasoning_tests.testsRun,
                                       'output':test_stream.getvalue()},
              'failure_paths':failures,'evaluator_self_checks':self_checks,
              'database_sha256_before':database_before,'database_sha256_after':database_after,
              'database_unchanged':database_before==database_after,
              'tool_source_sha256':tool_hashes,
              'tools_unchanged_from_before':tool_hashes==before['tool_source_sha256'] if before else None,
              'artifact_sha256':{p: digest(_common.ROOT/p) for p in
                  ('ai_agent/agent.py','ai_agent/evals/evaluation_cases.json','ai_agent/evals/expected_values.json','ai_agent/evals/run_evaluation.py',
                   'ai_agent/reasoning/__init__.py','ai_agent/reasoning/diagnostic_rules.py',
                   'ai_agent/evals/numeric_grounding.py','ai_agent/evals/test_reasoning.py')}}
    path = HERE/f'evaluation_results_{args.phase}.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    if args.phase=='after':
        (HERE/'evaluation_report.md').write_text(report_markdown(result,before),encoding='utf-8')
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
    print(f'Results: {path}')
    return 0 if all(c['passed'] for c in evaluated) and all(n['passed'] for n in numbers) and all(f['passed'] for f in failures) and reasoning_tests.wasSuccessful() else 1

if __name__ == '__main__':
    raise SystemExit(main())
