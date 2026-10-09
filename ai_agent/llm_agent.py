"""Independent controlled LLM agent; does not use baseline routing or explanations."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from .tool_schemas import REGISTRY, INTENTS, provider_schemas, validate_arguments, strict_json
from .providers.base import ConfigurationError, ProviderError
from .providers.openai_responses import from_environment
from .evals.numeric_grounding import numeric_grounding_check
from .llm_grounding import source_catalog_prompt, normalize_rounded_evidence, independent_audit_interpretation, literal_failure_diagnostics, directional_prose_audit

ROOT=Path(__file__).resolve().parents[1]
PROMPT=Path(__file__).parent/'prompts/system_prompt.md'
MAX_TOOL_CALLS=3
FINAL_FIELDS={'status','detected_intents','answer','facts','analysis','candidate_actions','caveats','numeric_evidence'}


class AgentProtocolError(ValueError): pass
class ToolLimitError(ValueError): pass
class ToolExecutionError(RuntimeError): pass
class AnswerValidationError(ValueError):
    def __init__(self,message,diagnostics=None):
        super().__init__(message)
        self.diagnostics=diagnostics or []


def redact(value, secrets=()):
    if isinstance(value,str):
        for secret in secrets:
            if secret: value=value.replace(secret,'[REDACTED]')
        value=re.sub(r'(?i)\bauthorization\s*[:=]\s*[^\r\n]+','[REDACTED HEADER]',value)
        value=re.sub(r'(?i)\bbearer\s+[^\s\"\'<>;,]+','[REDACTED]',value)
        value=re.sub(r'(?i)[\"\']?(?:api[_-]?key|password|access[_-]?token|secret)[\"\']?\s*[:=]\s*(?:\"[^\"]*\"|\'[^\']*\'|[^\s,;<>]+)', '[REDACTED CREDENTIAL]',value)
        return re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[REDACTED]',value)
    if isinstance(value,list): return [redact(v,secrets) for v in value]
    if isinstance(value,dict):
        return {k: ('[REDACTED]' if str(k).casefold() in
                ('api_key','authorization','password','access_token','secret') else redact(v,secrets))
                for k,v in value.items()}
    return value


def validate_final(text, results):
    try: answer=strict_json(text)
    except (TypeError,ValueError): raise AnswerValidationError('Final answer must be valid JSON') from None
    if not isinstance(answer,dict) or set(answer)!=FINAL_FIELDS:
        raise AnswerValidationError('Final answer fields differ from the declared contract')
    if answer['status'] not in ('answered','unsupported','ambiguous'):
        raise AnswerValidationError('Invalid final status')
    intents=answer['detected_intents']
    if not isinstance(intents,list) or not intents or any(i not in {*INTENTS.values(),'unsupported','ambiguous'} for i in intents):
        raise AnswerValidationError('Invalid detected_intents')
    if len(set(intents))!=len(intents): raise AnswerValidationError('Duplicate detected_intents')
    for field in ('answer','analysis'):
        if not isinstance(answer[field],str) or len(answer[field])>16000:
            raise AnswerValidationError('Invalid explanation string')
    if not answer['answer'].strip(): raise AnswerValidationError('Empty answer')
    for field in ('facts','candidate_actions','caveats'):
        if not isinstance(answer[field],list) or any(not isinstance(v,str) for v in answer[field]):
            raise AnswerValidationError('Invalid explanation list')
    if answer['status']=='answered':
        if not results: raise AnswerValidationError('Answered project questions require a current tool result')
        actual={INTENTS[r['name']] for r in results}
        if set(intents)!=actual: raise AnswerValidationError('Declared intents do not match actual tool results')
    data={'calls':{r['call_id']:r['result'] for r in results}}
    try: answer=normalize_rounded_evidence(answer,data)
    except ValueError as error:
        raise AnswerValidationError('Explanation numeric grounding failed: '+str(error),getattr(error,'diagnostics',[])) from None
    interpretation=independent_audit_interpretation(answer)
    interpretation,presentation_bindings=directional_prose_audit(interpretation,answer['numeric_evidence'])
    grounding=numeric_grounding_check({'key_data':data,'interpretation':interpretation,'caveats':[]})
    if not grounding['passed']:
        missing={d['display'] for d in literal_failure_diagnostics(interpretation)}
        diagnostics=[d for d in literal_failure_diagnostics(answer) if d['display'] in missing]
        raise AnswerValidationError('Explanation numeric grounding failed: '+ '; '.join(grounding['errors']),diagnostics)
    if presentation_bindings: grounding['presentation_bindings']=presentation_bindings
    # No complete semantic judge: obvious positive causal assertions are blocked.
    body='\n'.join([answer['answer'],answer['analysis'],*answer['facts']])
    if re.search(r'\b(?:select[\s\S]{0,500}\bfrom|insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+table|create\s+table)\b',body,re.I):
        raise AnswerValidationError('Generated SQL is outside the answer contract')
    patterns=(r'(?:购物车)?召回(?:已经|已被证明|确实)(?:有效|无效)',
              r'(?:广告投放|促销)(?:导致了|造成了)(?:流量|增长)',
              r'P1\s*用户(?:一定|必然)(?:更容易)?购买',
              r'buy\s*(?:event)?\s*(?:就是|等于|=)\s*订单')
    for clause in re.split(r'[。；;，,\n]',body):
        for pattern in patterns:
            for match in re.finditer(pattern,clause,re.I):
                if not re.search(r'不能|无法|不是|不等于|不得',clause[:match.start()]):
                    raise AnswerValidationError('Unsupported positive causal or semantic claim')
    return answer,grounding


def run(question, provider, *, max_tool_calls=MAX_TOOL_CALLS, debug=False, repair_final_answer=False):
    """Execute an isolated single-question session with a fresh provider instance."""
    trace={'user_question':question,'status':'error','selected_tools':[], 'tool_arguments':[],
           'tool_results':[],'final_answer':None,'tool_call_count':0,
           'attempted_tool_call_count':0,'errors':[],
           'model_metadata':dict(provider.metadata),'model_rounds':[], 'agent_type':'controlled_llm_tool_calling'}
    secrets=getattr(provider,'redaction_secrets',())
    seen=set();pending=[];repair_feedback='';repair_used=False;repair_status=None
    try:
        if not isinstance(question,str) or not question.strip() or len(question)>10000:
            raise AgentProtocolError('Question must be nonempty and at most 10000 characters')
        if isinstance(max_tool_calls,bool) or not isinstance(max_tool_calls,int) or not 1<=max_tool_calls<=MAX_TOOL_CALLS:
            raise ToolLimitError('Maximum tool-call budget must be integer 1–3')
        system_prompt=PROMPT.read_text(encoding='utf-8')
        for _ in range(max_tool_calls+1+int(bool(repair_final_answer))):
            turn=provider.complete(question=question,system_prompt=source_catalog_prompt(system_prompt,trace['tool_results'])+repair_feedback,tools=provider_schemas(),
                                   tool_outputs=pending,allow_tools=not repair_used and trace['tool_call_count']<max_tool_calls)
            trace['model_rounds'].append(turn.metadata)
            pending=[]
            if turn.calls:
                trace['attempted_tool_call_count']+=len(turn.calls)
                trace['selected_tools'].extend(c.name for c in turn.calls)
                trace['tool_arguments'].extend({'call_id':c.call_id,'name':c.name,'arguments':c.arguments} for c in turn.calls)
                if trace['tool_call_count']+len(turn.calls)>max_tool_calls:
                    raise ToolLimitError('Tool-call budget exceeded; no calls from this batch executed')
                if repair_used: raise AgentProtocolError('Final-answer repair cannot call tools')
                # Validate the whole batch before executing any callable.
                prepared=[];batch_ids=set()
                for call in turn.calls:
                    if not isinstance(call.call_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',call.call_id):
                        raise AgentProtocolError('Invalid call id')
                    if call.call_id in seen or call.call_id in batch_ids:
                        raise AgentProtocolError('Duplicate/replayed call id')
                    args=validate_arguments(call.name,call.arguments)
                    batch_ids.add(call.call_id);prepared.append((call,args))
                for call,args in prepared:
                    seen.add(call.call_id);trace['tool_call_count']+=1
                    try: result=REGISTRY[call.name](**args)
                    except Exception as error:
                        trace['tool_results'].append({'call_id':call.call_id,'name':call.name,'arguments':args,
                                                      'error':{'type':type(error).__name__,'message':str(error)}})
                        raise ToolExecutionError(f'{call.name} failed: {type(error).__name__}: {error}') from None
                    # Ensure strict JSON serialization before sending data back to model.
                    encoded=json.dumps(result,ensure_ascii=False,allow_nan=False)
                    trace['tool_results'].append({'call_id':call.call_id,'name':call.name,'arguments':args,'result':result})
                    pending.append({'type':'function_call_output','call_id':call.call_id,'output':encoded})
                continue
            if not turn.final_text:
                raise AgentProtocolError('Model produced neither tools nor final JSON')
            try: answer,grounding=validate_final(turn.final_text,trace['tool_results'])
            except AnswerValidationError as error:
                if not repair_final_answer or repair_used or not str(error).startswith('Explanation numeric grounding failed:'):
                    raise
                repair_used=True
                repair_status=strict_json(turn.final_text)['status']
                feedback=redact({'error':str(error),'diagnostics':error.diagnostics},secrets)
                trace['answer_validation_attempts']=[feedback]
                repair_feedback='\n\n## 最终回答校验反馈（仅允许一次修正；禁止调用工具）\n'+json.dumps(feedback,ensure_ascii=False)+(
                    '\n上一份最终回答未通过校验。只使用已经返回的真实工具结果，重新输出完整最终 JSON；不得增加工具调用。'
                    '\n每种展示精度都需要独立 evidence；不同 display 不能借用同一记录。'
                    '\n有符号变化与正数下降幅度是不同计算；严格保留 declared operation 的方向及符号。'
                    '\n不要自动取绝对值或捏造来源。不能证明的数字应移除对应主张，保留有来源的事实与边界。'
                    '\n不能将 answered 改为 unsupported/ambiguous 来绕过校验。修正仍接受同样严格的全部校验。')
                continue
            if repair_used and answer['status']!=repair_status:
                raise AnswerValidationError('Final-answer repair must preserve status')
            trace['final_answer']=answer;trace['status']=answer['status'];trace['numeric_grounding']=grounding
            return redact(trace,secrets)
        raise ToolLimitError('Model-round limit reached without a final answer')
    except Exception as error:
        trace['errors'].append({'type':type(error).__name__,'message':str(error)})
        if debug and (isinstance(error,AnswerValidationError) or trace.get('answer_validation_attempts')):
            def compact(value):
                if isinstance(value,str): return value[:500]
                if isinstance(value,list): return [compact(v) for v in value[:10]]
                if isinstance(value,dict): return {k:compact(v) for k,v in list(value.items())[:20]}
                return value
            # Redact before truncating to avoid leaking a partial known key.
            trace['grounding_diagnostics']=compact(redact(getattr(error,'diagnostics',None) or trace.get('answer_validation_attempts',[{}])[0].get('diagnostics',[]),secrets))
        return redact(trace,secrets)


def missing_configuration(question, error):
    return {'user_question':question,'status':'configuration_missing','selected_tools':[],
            'tool_arguments':[],'tool_results':[],'final_answer':None,'tool_call_count':0,
            'errors':[{'type':type(error).__name__,'message':str(error)}],
            'model_metadata':{'provider':'openai' if os.environ.get('AI_AGENT_PROVIDER','openai')=='openai' else 'unsupported_provider','model':None},
            'agent_type':'controlled_llm_tool_calling'}


def save_trace(trace):
    directory=Path(__file__).parent/'runs'
    directory.mkdir(mode=0o700,exist_ok=True)
    path=directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream:
        os.chmod(path,0o600)
        json.dump(trace,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('question')
    parser.add_argument('--debug',action='store_true')
    parser.add_argument('--save-trace',action='store_true')
    args=parser.parse_args()
    try: trace=run(args.question,from_environment(),debug=args.debug,repair_final_answer=True)
    except ConfigurationError as error: trace=missing_configuration(args.question,error)
    if args.save_trace:
        try: save_trace(trace)
        except OSError:
            trace['errors'].append({'type':'TraceWriteError','message':'Could not save trace'})
            trace['status']='error'
    # Grounding failures need only failed evidence details, not huge result dumps.
    if args.debug and 'grounding_diagnostics' in trace:
        trace=dict(trace)
        trace['tool_result_summary']=[{'call_id':r['call_id'],'name':r['name']} for r in trace['tool_results']]
        trace.pop('tool_results')
    displayed=trace if args.debug else {key:trace[key] for key in
        ('status','selected_tools','tool_call_count','final_answer','errors','model_metadata')}
    print(json.dumps(displayed,ensure_ascii=False,indent=2,allow_nan=False))
    return 0 if trace['status']=='answered' else 2 if trace['status'] in ('unsupported','ambiguous') else 1


if __name__=='__main__': raise SystemExit(main())
