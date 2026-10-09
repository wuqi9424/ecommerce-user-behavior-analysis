"""OpenAI Responses HTTP adapter; standard library, no SDK dependency.

Official schema reference:
https://developers.openai.com/api/docs/guides/function-calling
"""
import json
import os
import re
import urllib.request
import urllib.error
from .base import ConfigurationError, ProviderError, ModelTurn, ToolCall


ERROR_BODY_LIMIT=65_536
ERROR_SUMMARY_LIMIT=1_500


def _safe_error_text(value, secrets):
    # Redact before truncation so a cutoff cannot expose a partial credential.
    text=str(value)
    for secret in secrets:
        if secret: text=text.replace(secret,'[REDACTED]')
    text=re.sub(r'(?i)\bauthorization\s*[:=]\s*[^\r\n]+','[REDACTED HEADER]',text)
    text=re.sub(r'(?i)\bbearer\s+[^\s\"\'<>;,]+','[REDACTED]',text)
    text=re.sub(r'\bsk-[A-Za-z0-9_*\-]+','[REDACTED]',text)
    text=re.sub(r'(?i)[\"\']?(?:api[_-]?key|password|access[_-]?token|secret)[\"\']?\s*[:=]\s*(?:\"[^\"]*\"|\'[^\']*\'|[^\s,;<>]+)',
                '[REDACTED CREDENTIAL]',text)
    # One printable line; JSON encoding below escapes any remaining controls.
    return ' '.join(text.split())


def _http_failure(status, response, secrets):
    try:
        raw=response.read(ERROR_BODY_LIMIT+1)
    except Exception:
        return f'OpenAI HTTP request failed (status {status}); response body could not be read'
    truncated=len(raw)>ERROR_BODY_LIMIT
    body=raw[:ERROR_BODY_LIMIT].decode('utf-8',errors='replace')
    try: parsed=json.loads(body)
    except (ValueError,TypeError): parsed=None
    error=parsed.get('error') if isinstance(parsed,dict) else None
    fields={}
    if isinstance(error,dict):
        for name in ('type','code','param','message'):
            value=error.get(name)
            if value is None or isinstance(value,(str,int,float,bool)):
                if name in error:
                    fields[name]=None if value is None else _safe_error_text(value,secrets)[:500]
    if fields:
        summary='error='+json.dumps(fields,ensure_ascii=False)
    else:
        summary='body='+json.dumps(_safe_error_text(body,secrets),ensure_ascii=False)
    if len(summary)>ERROR_SUMMARY_LIMIT:
        summary=summary[:ERROR_SUMMARY_LIMIT]+' [truncated]'
    if truncated: summary+=' [body read limit reached]'
    return f'OpenAI HTTP request failed (status {status}); '+summary


class OpenAIResponses:
    def __init__(self, *, api_key:str, model:str, timeout=45, max_output_tokens=5000):
        if not api_key or not model:
            raise ConfigurationError('Set OPENAI_API_KEY and OPENAI_MODEL in the process environment')
        self._api_key=api_key
        self.model=model
        self.timeout=timeout
        self.max_output_tokens=max_output_tokens
        self._history=[]
        self.metadata={'provider':'openai','requested_model':model,'transport':'responses_http'}
        self.redaction_secrets=(api_key,)

    def complete(self, *, question, system_prompt, tools, tool_outputs, allow_tools):
        if not self._history:
            self._history=[{'role':'user','content':question}]
        self._history.extend(tool_outputs)
        payload={'model':self.model,'instructions':system_prompt,'input':self._history,
                 'tools':tools,'tool_choice':'auto' if allow_tools else 'none',
                 'parallel_tool_calls':False,'store':False,
                 'include':['reasoning.encrypted_content'],
                 'max_output_tokens':self.max_output_tokens}
        # Function arguments are constrained by strict tool schemas, not JSON mode.
        # Final text follows the existing prompt contract and is independently checked
        # by llm_agent.validate_final; no API-level text format is requested here.
        request=urllib.request.Request('https://api.openai.com/v1/responses',
                data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8'),
                headers={'Authorization':'Bearer '+self._api_key,'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(request,timeout=self.timeout) as response:
                status=getattr(response,'status',None)
                if isinstance(status,int) and not 200<=status<300:
                    raise ProviderError(_http_failure(status,response,self.redaction_secrets))
                raw=response.read(2_000_001)
            if len(raw)>2_000_000: raise ProviderError('Provider response exceeds size limit')
            parsed=json.loads(raw)
        except urllib.error.HTTPError as error:
            try: diagnostic=_http_failure(error.code,error,self.redaction_secrets)
            finally: error.close()
            raise ProviderError(diagnostic) from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise ProviderError('OpenAI transport failed; check network and configuration') from None
        except (ValueError,TypeError):
            raise ProviderError('Provider returned invalid JSON') from None
        if parsed.get('status')!='completed':
            raise ProviderError('Provider response was not completed; refusing truncated output')
        output=parsed.get('output',[])
        self._history.extend(output)  # Preserve reasoning and function items for stateless continuation.
        calls=[];text=[]
        for item in output:
            if item.get('type')=='function_call':
                calls.append(ToolCall(item.get('call_id',''),item.get('name',''),item.get('arguments','')))
            elif item.get('type')=='message':
                for part in item.get('content',[]):
                    if part.get('type')=='output_text': text.append(part['text'])
                    elif part.get('type')=='refusal': raise ProviderError('Model refused the request')
            elif item.get('type')!='reasoning':
                raise ProviderError('Unexpected provider item; only function calls/messages/reasoning allowed')
        return ModelTurn(calls=calls,final_text=''.join(text) or None,
                         metadata={'provider':'openai','model':parsed.get('model',self.model),
                                   'response_id':parsed.get('id'),'usage':parsed.get('usage',{})})


def from_environment():
    provider=os.environ.get('AI_AGENT_PROVIDER','openai')
    if provider!='openai': raise ConfigurationError('Configured provider is unsupported; implement the Provider protocol first')
    return OpenAIResponses(api_key=os.environ.get('OPENAI_API_KEY',''),model=os.environ.get('OPENAI_MODEL',''))
