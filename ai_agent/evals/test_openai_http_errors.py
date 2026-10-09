"""Offline HTTP diagnostics tests; no API calls or database access."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from ai_agent.providers.base import ProviderError
from ai_agent.providers.openai_responses import OpenAIResponses, ERROR_BODY_LIMIT
from ai_agent.llm_agent import run

KEY='test-only-provider-key-never-log'


def http_error(body, status=400):
    return urllib.error.HTTPError('https://api.openai.com/v1/responses',status,
                                  'Bad request',{'Authorization':'Bearer '+KEY},io.BytesIO(body))


class HTTPDiagnosticsTests(unittest.TestCase):
    def provider(self): return OpenAIResponses(api_key=KEY,model='test-only-model')

    def request(self,provider):
        return provider.complete(question='test',system_prompt='JSON',tools=[],tool_outputs=[],allow_tools=True)

    def test_json_400_exposes_all_error_fields(self):
        body=json.dumps({'error':{'type':'invalid_request_error','code':'unsupported_value',
                                  'param':'text.format','message':'Unsupported format for this request.'}}).encode()
        with patch('urllib.request.urlopen',side_effect=http_error(body)):
            with self.assertRaises(ProviderError) as caught: self.request(self.provider())
        diagnostic=str(caught.exception)
        for expected in ('status 400','invalid_request_error','unsupported_value','text.format','Unsupported format'):
            self.assertIn(expected,diagnostic)
        self.assertNotIn('Authorization',diagnostic)
        self.assertNotIn(KEY,diagnostic)

    def test_non_json_body_has_bounded_summary_and_no_credentials(self):
        body=('upstream proxy failed\nAuthorization: Bearer '+KEY+'\napi_key="OTHER_SECRET"\n'+
              'x'*70_000).encode()
        with patch('urllib.request.urlopen',side_effect=http_error(body,502)):
            with self.assertRaises(ProviderError) as caught: self.request(self.provider())
        diagnostic=str(caught.exception)
        self.assertIn('status 502',diagnostic)
        self.assertIn('upstream proxy failed',diagnostic)
        self.assertIn('truncated',diagnostic)
        self.assertLess(len(diagnostic),1700)
        for secret in (KEY,'OTHER_SECRET','Authorization','Bearer'):
            self.assertNotIn(secret,diagnostic)

    def test_json_echoed_key_and_sensitive_extra_fields_never_reach_trace(self):
        body=json.dumps({'error':{'type':'invalid_request_error','code':None,'param':None,
            'message':f'Rejected key {KEY}; password="OTHER_SECRET"',
            'secret':'EXTRA_SECRET'},'Authorization':'Bearer '+KEY}).encode()
        with patch('urllib.request.urlopen',side_effect=http_error(body)):
            trace=run('test',self.provider())
        logged=json.dumps(trace)
        self.assertIn('Rejected key',logged)
        self.assertIn('status 400',logged)
        for secret in (KEY,'OTHER_SECRET','EXTRA_SECRET','Authorization'):
            self.assertNotIn(secret,logged)

    def test_explicit_non_2xx_response_is_diagnosed(self):
        class Response(io.BytesIO): status=503
        body=json.dumps({'error':{'message':'Service temporarily unavailable','code':'server_error'}}).encode()
        with patch('urllib.request.urlopen',return_value=Response(body)):
            with self.assertRaises(ProviderError) as caught: self.request(self.provider())
        self.assertIn('status 503',str(caught.exception))
        self.assertIn('Service temporarily unavailable',str(caught.exception))

    def test_empty_and_unreadable_body_keep_http_status(self):
        with patch('urllib.request.urlopen',side_effect=http_error(b'')):
            with self.assertRaises(ProviderError) as caught: self.request(self.provider())
        self.assertIn('status 400',str(caught.exception))
        error=http_error(b'')
        error.read=lambda count: (_ for _ in ()).throw(OSError(KEY))
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaises(ProviderError) as caught: self.request(self.provider())
        self.assertIn('could not be read',str(caught.exception))
        self.assertNotIn(KEY,str(caught.exception))


if __name__=='__main__': unittest.main()
