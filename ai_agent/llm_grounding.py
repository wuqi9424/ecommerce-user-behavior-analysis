"""LLM-only source catalog and exact-format rounding normalization.

The frozen baseline numeric checker remains the final independent audit.
Unknown source IDs are never inferred or substituted.
"""
from copy import deepcopy
from decimal import Decimal
import json
import math
import re

from .evals.numeric_grounding import ALLOWED_FORMATS, source_value, NUMBER, strings


DISPLAY_CONTRACT = """numeric_evidence display contract:
- display 是纯数值 token，不包含 %、pp、空格或单位；正文使用完全相同的 token。
- format_spec 仅允许 .4f（四位）、.2f（两位）、.0f（整数）、,.0f（千分位整数）。
- 统一使用 Python format(source_or_derived_value, format_spec)，不自行乘除百分比尺度。
- _pct 和当前工具 _rate 字段是百分比尺度（比例以百分数表示，增长可超出通常区间），unit=percent；正文 token 后必须写 %。
- _pp 字段为百分点，unit=pp；正文 token 后写 pp 或 个百分点，不能写 %。
- p_value 和 significance_alpha 是无量纲 fraction，不是 percent；confidence_level_pct 是 percent。
- subtraction 的有序路径为 [被减数 A 的路径, 减数 B 的路径]，唯一约定为 A - B，即 paths[0] - paths[1]。时间变化 Final - Previous 使用 [final, previous]。不得忽略正负号或取绝对值。
- 新回答使用 subtraction，不使用旧 difference；旧 difference 仅兼容历史输出，仍保留第二项减第一项，绝不能混用两个 operation。
- pct_change 同样用 [基准, 比较]，计算 (paths[1]/paths[0]-1)*100；不得将下降幅度的绝对值冒充 signed change。
- 两个 percent 字段 subtraction 得到 pp；pct_change 得到 percent。计数与普通小数不加 % 或 pp。
- 明确的“下降/降低/减少/回落 X pp（或 %）”可用已经核验的负值 -X evidence；方向词、单位和展示精度必须精确对应，不得省略方向词，不得把上升或假设下降绑定到负值。优先用“有符号变化 -X pp”避免歧义。
- 区间可写 [下界, 上界]pp 或 [下界, 上界]%；末尾单位同时适用于两个边界，每个边界仍须各有自己的合法 evidence。无单位区间不合格。
- unit 允许 percent、pp、fraction、users、events、pairs、categories、items、days、events/user、events/pair、decimal、count、identifier。每个 entry 的 unit 必须与来源/派生单位一致，来源路径必须属于本轮实际执行的工具。
"""


class EvidenceValidationError(ValueError):
    def __init__(self,index,reason,diagnostic):
        super().__init__(f'entry {index}: {reason}')
        self.diagnostics=[dict(diagnostic,mismatch_reason=reason)]


def source_unit(path):
    field=path.rsplit('/',1)[-1]
    if field.endswith('_pp'): return 'pp'
    if field.endswith(('_pct','_rate')): return 'percent'
    if field=='p_value': return 'fraction'
    if field.endswith('_users') or field=='users': return 'users'
    if field.endswith('_events'): return 'events'
    if field.endswith('_pairs'): return 'pairs'
    if field.endswith('_categories'): return 'categories'
    if field.endswith('_items'): return 'items'
    if field.endswith('_days'): return 'days'
    return None  # No undocumented scaling/unit guess for an unknown field.


# A unit shared by a single explicit bracketed interval, not arbitrary nearby text.
INTERVAL=re.compile(r'(?P<open>\[|\(|（)\s*(?P<lower>'+NUMBER.pattern+
                    r')\s*[,，]\s*(?P<upper>'+NUMBER.pattern+
                    r')\s*(?P<close>\]|\)|）)\s*(?P<unit>pp\b|个百分点|%)')


def prose_unit(text, literal):
    suffix=text[literal.end():].lstrip()
    if suffix.startswith('%'): return 'percent'
    if re.match(r'pp\b|个百分点',suffix): return 'pp'
    for interval in INTERVAL.finditer(text):
        if {'[':']','(':')','（':'）'}[interval['open']]!=interval['close']: continue
        if float(interval['lower'].replace(',',''))>float(interval['upper'].replace(',','')): continue
        if literal.span() in (interval.span('lower'),interval.span('upper')):
            return 'percent' if interval['unit']=='%' else 'pp'
    return None


class ProseUnitError(ValueError):
    def __init__(self,reason,text,literal):
        super().__init__(reason)
        self.prose_context=text[max(0,literal.start()-80):literal.end()+80]


def validate_units(entry,paths,answer):
    unit=entry.get('unit')
    if not isinstance(unit,str) or not unit: raise ValueError('Missing unit')
    if unit not in {'percent','pp','fraction','users','events','pairs','categories','items','days',
                    'events/user','events/pair','decimal','count','identifier'}:
        raise ValueError('Unit is not on the display-contract allowlist')
    units=[source_unit(p) for p in paths]
    operation=entry['operation']
    if operation=='policy':
        expected_unit={'significance_alpha':'fraction','confidence_level_pct':'percent'}.get(entry.get('policy'))
    elif operation=='pct_change':
        if len(set(units))>1: raise ValueError('Derived operands have incompatible units')
        expected_unit='percent'
    elif operation in ('difference','subtraction'):
        if len(set(units))>1: raise ValueError('Derived operands have incompatible units')
        expected_unit='pp' if units and units[0]=='percent' else units[0] if units else None
    else: expected_unit=units[0] if units else None
    if expected_unit is not None and unit!=expected_unit:
        raise ValueError(f'Unit mismatch: source/operation requires {expected_unit}, declared {unit}')
    if expected_unit is None and unit in ('percent','pp','fraction'):
        raise ValueError('Unknown field cannot be assigned a percentage/fraction scale')
    display=entry.get('display')
    body={k:v for k,v in answer.items() if k!='numeric_evidence'}
    for text in strings(body):
        for match in NUMBER.finditer(text):
            if match.group(0)!=display: continue
            actual_unit=prose_unit(text,match)
            if unit in ('percent','pp') and actual_unit!=unit:
                raise ProseUnitError(f'Prose unit mismatch: requires {unit}, found {actual_unit or "missing"}',text,match)
            if unit not in ('percent','pp') and actual_unit in ('percent','pp'):
                raise ProseUnitError('Non-percentage source cannot use % or pp in prose',text,match)


def source_catalog_prompt(prompt, results):
    catalog=[{'call_id':row['call_id'],'tool_name':row['name'],
              'evidence_path_prefix':'calls/'+row['call_id']+'/'} for row in results]
    return prompt+'\n\n'+DISPLAY_CONTRACT+'\n\n## 本轮已执行来源（宿主动态生成）\n'+json.dumps(catalog,ensure_ascii=False)+(
        '\n只可引用清单中的真实 call_id；它们不是序号。不得猜测、缩写或用示例 ID 替代。'
        '\n每条 paths 以对应工具的 evidence_path_prefix 开头，再接该工具实际结果的字段路径。'
        '\n引用其他工具或不存在的字段无法通过校验。每个正文数字都必须有有效 evidence。'
        '\nvalue 优先保留来源或允许派生计算的完整精度；display 为该值按 format_spec 格式化后的文字，'
        '正文使用相同 display。如 value 也舍入，它必须严格等于该 display 对应的数值。')


def normalize_rounded_evidence(answer, data):
    """Accept only source-exact or exact formatted values; never a broad tolerance.

A rounded model value is replaced by the recomputed raw value for the existing
checker. Preserve model_value for audit. Display/path errors are never repaired.
"""
    normalized=deepcopy(answer)
    ledger=normalized.get('numeric_evidence')
    if not isinstance(ledger,list): return normalized  # Independent checker reports it.
    for index,entry in enumerate(ledger):
        diagnostic={'entry_index':index,'display':entry.get('display') if isinstance(entry,dict) else None,
                    'source_paths':entry.get('paths') if isinstance(entry,dict) else None,
                    'source_call_ids':[], 'resolved_source_numeric_values':[],
                    'declared_format':entry.get('format_spec') if isinstance(entry,dict) else None,
                    'declared_unit':entry.get('unit') if isinstance(entry,dict) else None,
                    'expected_display':None}
        try:
            if not isinstance(entry,dict): raise ValueError('Evidence must be an object')
            operation=entry['operation']
            if operation=='policy':
                validate_units(entry,[],normalized)
                continue  # Frozen checker validates policy value/format separately.
            paths=entry['paths']
            if not isinstance(paths,list) or any(not isinstance(p,str) for p in paths):
                raise ValueError('Evidence paths must be strings')
            if any(not p.startswith('calls/') for p in paths):
                raise ValueError('Evidence must reference an executed call')
            diagnostic['source_call_ids']=[p.split('/')[1] if p.startswith('calls/') else None for p in paths]
            operands=[source_value(data,path) for path in paths]
            diagnostic['resolved_source_numeric_values']=operands
            diagnostic['source_units']=[source_unit(p) for p in paths]
            if operation=='identity' and len(operands)==1: expected=operands[0]
            elif operation=='subtraction' and len(operands)==2: expected=operands[0]-operands[1]
            elif operation=='difference' and len(operands)==2: expected=operands[1]-operands[0]
            elif operation=='pct_change' and len(operands)==2 and operands[0]!=0:
                expected=(operands[1]/operands[0]-1)*100
            else: raise ValueError('Derivation is not on the allowlist')
            if not math.isfinite(expected): raise ValueError('Derived value is not finite')
            actual=entry['value']
            if isinstance(actual,bool) or not isinstance(actual,(int,float)) or not math.isfinite(actual):
                raise ValueError('Claimed number is not finite')
            spec=entry['format_spec']
            if spec not in ALLOWED_FORMATS: raise ValueError('Display format is not on the allowlist')
            display=entry['display']
            diagnostic['resolved_numeric_value']=expected
            diagnostic['expected_display']=format(expected,spec)
            if display!=diagnostic['expected_display']:
                raise ValueError('Display does not match source value and format')
            validate_units(entry,paths,normalized)
            if not math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-9):
                if Decimal(str(actual))!=Decimal(display.replace(',','')):
                    raise ValueError('Claimed number is neither source-exact nor its exact formatted value')
                entry['model_value']=actual
                entry['value']=expected
        except (KeyError,TypeError,ValueError,ZeroDivisionError) as error:
            if hasattr(error,'prose_context'): diagnostic['prose_context']=error.prose_context
            diagnostic['operation']=entry.get('operation') if isinstance(entry,dict) else None
            raise EvidenceValidationError(index,str(error),diagnostic) from None
    return normalized


def independent_audit_interpretation(answer):
    """Adapt A-B to the frozen checker's B-A representation for its independent audit.

This affects only the audit copy, never model evidence, displayed signs, or trace
paths. No inferred order or numeric repair: reverse exactly two declared paths.
"""
    interpretation=deepcopy({k:v for k,v in answer.items() if k not in ('status','detected_intents')})
    for entry in interpretation.get('numeric_evidence',[]):
        if entry.get('operation')=='subtraction':
            entry['operation']='difference'
            entry['paths']=list(reversed(entry['paths']))
    return interpretation


def literal_failure_diagnostics(answer):
    """Locate unannotated literal spans; never infer an operand or repair a sign."""
    displays={e.get('display') for e in answer.get('numeric_evidence',[]) if isinstance(e,dict) and isinstance(e.get('display'),str)}
    diagnostics=[]
    for field,value in answer.items():
        if field=='numeric_evidence': continue
        for text in strings(value):
            for match in NUMBER.finditer(text):
                if match.group(0) not in displays:
                    diagnostics.append({'kind':'unannotated_numeric_literal','display':match.group(0),
                        'prose_field':field,'prose_span':list(match.span()),
                        'prose_context':text[max(0,match.start()-80):match.end()+80],
                        'mismatch_reason':'No valid evidence has this exact display',
                        'annotated_displays':sorted(displays)})
    return diagnostics[:10]


DECLINE=re.compile(r'(?P<cue>下降|降低|减少|回落)(?:了)?\s*(?P<number>'+NUMBER.pattern+
                   r')\s*(?P<unit>pp\b|个百分点|%)')


def directional_prose_audit(interpretation, validated_evidence):
    """Bind explicit decline magnitude to an already verified signed negative entry.

Only the audit copy gets a signed literal. Source operands, values, operations,
model prose and model ledger are unchanged. This is not a blanket sign exemption.
"""
    audited=deepcopy(interpretation)
    bindings=[]
    displays={e['display'] for e in validated_evidence}

    def visit(value,field):
        if isinstance(value,list): return [visit(v,f'{field}/{i}') for i,v in enumerate(value)]
        if not isinstance(value,str): return value
        edits=[]
        for match in DECLINE.finditer(value):
            number=match['number']
            if number.startswith(('-','+')) or number in displays: continue
            prefix=re.split(r'[。；;\n]',value[:match.start()])[-1]
            if re.search(r'没有|并未|未曾|不会|不能|无法|未下降|不下降|假设|如果|可能|预计|预期|目标|是否|(?:未|不|无)\s*$',prefix): continue
            suffix_clause=re.split(r'[。；;\n]',value[match.end():])[0]
            if re.search(r'不成立|不正确|是错误|不属实|未发生',suffix_clause): continue
            unit='percent' if match['unit']=='%' else 'pp'
            candidates=[(i,e) for i,e in enumerate(validated_evidence)
                        if e['unit']==unit and e['value']<0 and e['display']=='-'+number
                        and e['operation'] in ('identity','subtraction','difference','pct_change')]
            if not candidates: continue
            # All candidates have been recomputed against real paths already.
            index,entry=candidates[0]
            signed=entry['display']
            edits.append((match.start(),match.end(),'有符号变化 '+signed+match['unit']))
            bindings.append({'prose_field':field,'prose_span':list(match.span('number')),
                             'prose_display':number,'direction':match['cue'],'unit':unit,
                             'evidence_index':index,'signed_display':signed,
                             'source_paths':list(entry['paths']), 'source_value':entry['value']})
        for start,end,replacement in reversed(edits): value=value[:start]+replacement+value[end:]
        return value

    for field,value in audited.items():
        if field!='numeric_evidence': audited[field]=visit(value,field)
    return audited,bindings
