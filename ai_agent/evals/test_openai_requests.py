"""Offline Responses payload regressions; no API or business tool calls."""
import io
import json
import unittest
from unittest.mock import patch

from ai_agent.providers.openai_responses import OpenAIResponses
from ai_agent.tool_schemas import provider_schemas
from ai_agent.llm_agent import validate_final


class RequestTests(unittest.TestCase):
    def test_function_call_without_json_input_uses_only_tool_schemas(self):
        payloads=[]
        tools=provider_schemas()
        def transport(request,**kwargs):
            payload=json.loads(request.data)
            payloads.append(payload)
            self.assertNotIn('text',payload)
            self.assertNotIn('json',json.dumps(payload['input']).lower())
            self.assertEqual(payload['instructions'],'Select an analytics function.')
            self.assertEqual(payload['tools'],tools)
            self.assertTrue(all(tool['strict'] for tool in payload['tools']))
            self.assertEqual(payload['tool_choice'],'auto')
            self.assertFalse(payload['parallel_tool_calls'])
            self.assertFalse(payload['store'])
            return io.BytesIO(json.dumps({'status':'completed','id':'resp_test','model':'test-model',
                'output':[{'type':'function_call','call_id':'call_1','name':'get_weekend_growth',
                           'arguments':'{}'}]}).encode())
        provider=OpenAIResponses(api_key='test-only-key',model='test-model')
        with patch('urllib.request.urlopen',side_effect=transport):
            turn=provider.complete(question='为什么第二个周末流量增长了？',
                system_prompt='Select an analytics function.',tools=tools,tool_outputs=[],allow_tools=True)
        self.assertEqual(turn.calls[0].name,'get_weekend_growth')
        self.assertEqual(turn.calls[0].arguments,'{}')
        self.assertEqual(turn.calls[0].call_id,'call_1')
        self.assertEqual(len(payloads),1)

    def test_tool_result_continuation_and_final_text_parse_unchanged(self):
        provider=OpenAIResponses(api_key='test-only-key',model='test-model')
        tools=provider_schemas()
        answer={'status':'answered','detected_intents':['growth'],'answer':'本轮工具支持描述性分析。',
                'facts':['增长原因不能仅凭观察数据确认。'],'analysis':'相关不等于因果。',
                'candidate_actions':[],'caveats':['无法确认营销因果效果。'],'numeric_evidence':[]}
        outputs=[{'type':'function_call_output','call_id':'call_1','output':'{}'}]
        responses=[{'status':'completed','output':[{'type':'function_call','call_id':'call_1',
                    'name':'get_weekend_growth','arguments':'{}'}]},
                   {'status':'completed','output':[{'type':'message','content':[
                    {'type':'output_text','text':json.dumps(answer,ensure_ascii=False)}]}]}]
        with patch('urllib.request.urlopen',side_effect=[io.BytesIO(json.dumps(r).encode()) for r in responses]) as http:
            provider.complete(question='周末增长',system_prompt='Existing contract',tools=tools,
                              tool_outputs=[],allow_tools=True)
            final=provider.complete(question='周末增长',system_prompt='Existing contract',tools=tools,
                                    tool_outputs=outputs,allow_tools=False)
        payload=json.loads(http.call_args.args[0].data)
        self.assertNotIn('text',payload)
        self.assertEqual(payload['tool_choice'],'none')
        self.assertEqual(payload['tools'],tools)
        self.assertEqual(payload['input'][-1],outputs[0])
        self.assertEqual(payload['input'][-2]['call_id'],'call_1')
        self.assertEqual(final.calls,[])
        self.assertEqual(json.loads(final.final_text),answer)
        validated,grounding=validate_final(final.final_text,
            [{'name':'get_weekend_growth','call_id':'call_1','result':{}}])
        self.assertEqual(validated,answer)
        self.assertTrue(grounding['passed'])


if __name__=='__main__': unittest.main()
