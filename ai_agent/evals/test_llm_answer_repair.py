"""Single final-answer repair must retain all numeric/tool protections."""
import json
import unittest
from unittest.mock import patch
from ai_agent.llm_agent import run, validate_final, AnswerValidationError
from ai_agent.evals.test_llm_control import ScriptedProvider, final
from ai_agent.providers.base import ModelTurn, ToolCall
from ai_agent.tool_schemas import REGISTRY

ID='call_live_dynamic_fixture'
RAW=12.345678


def evidence(display,spec):
    return {'operation':'identity','paths':[f'calls/{ID}/growth_pct'],'policy':None,
            'value':RAW,'display':display,'format_spec':spec,'unit':'percent'}


def answer(complete=False):
    entries=[evidence('12.3457','.4f')]
    if complete: entries.append(evidence('12.35','.2f'))
    return final(('growth',),facts=['增长 12.3457%，概述为 12.35%。'],evidence=entries)


class RepairTests(unittest.TestCase):
    def test_unannotated_diagnostic_identifies_actual_prose_span(self):
        with self.assertRaises(AnswerValidationError) as caught:
            validate_final(answer(),[{'call_id':ID,'name':'get_weekend_growth','result':{'growth_pct':RAW}}])
        d=caught.exception.diagnostics[0]
        self.assertEqual(d['display'],'12.35')
        self.assertEqual(d['prose_field'],'facts')
        self.assertIn('概述为 12.35%',d['prose_context'])
        self.assertEqual(d['annotated_displays'],['12.3457'])

    def test_one_repair_can_annotate_second_precision_without_tools(self):
        provider=ScriptedProvider([ModelTurn(calls=[ToolCall(ID,'get_weekend_growth','{}')]),
                                  ModelTurn(final_text=answer()),ModelTurn(final_text=answer(True))])
        with patch.dict(REGISTRY,{'get_weekend_growth':lambda:{'growth_pct':RAW}}):
            trace=run('增长',provider,repair_final_answer=True)
        self.assertEqual(trace['status'],'answered')
        self.assertEqual(trace['tool_call_count'],1)
        self.assertEqual(trace['errors'],[])
        self.assertEqual(len(trace['answer_validation_attempts']),1)
        self.assertFalse(provider.requests[-1]['allow_tools'])
        self.assertIn('12.35',provider.requests[-1]['system_prompt'])
        self.assertTrue(trace['numeric_grounding']['passed'])

    def test_second_invalid_answer_fails_without_another_retry(self):
        provider=ScriptedProvider([ModelTurn(calls=[ToolCall(ID,'get_weekend_growth','{}')]),
                                  ModelTurn(final_text=answer()),ModelTurn(final_text=answer())])
        with patch.dict(REGISTRY,{'get_weekend_growth':lambda:{'growth_pct':RAW}}):
            trace=run('增长',provider,repair_final_answer=True,debug=True)
        self.assertEqual(trace['errors'][0]['type'],'AnswerValidationError')
        self.assertEqual(len(provider.requests),3)
        self.assertIsNone(trace['final_answer'])
        self.assertEqual(trace['grounding_diagnostics'][0]['display'],'12.35')

    def test_repair_tool_attempt_never_executes(self):
        provider=ScriptedProvider([ModelTurn(calls=[ToolCall(ID,'get_weekend_growth','{}')]),
                                  ModelTurn(final_text=answer()),ModelTurn(calls=[ToolCall('extra','get_platform_overview','{}')])])
        with patch.dict(REGISTRY,{'get_weekend_growth':lambda:{'growth_pct':RAW},
                                 'get_platform_overview':lambda:self.fail('extra tool executed')}):
            trace=run('增长',provider,repair_final_answer=True)
        self.assertEqual(trace['errors'][0]['type'],'AgentProtocolError')
        self.assertEqual(trace['tool_call_count'],1)

    def test_repair_cannot_bypass_numeric_checks_by_refusing(self):
        provider=ScriptedProvider([ModelTurn(calls=[ToolCall(ID,'get_weekend_growth','{}')]),
                                  ModelTurn(final_text=answer()),ModelTurn(final_text=final(('unsupported',),'unsupported'))])
        with patch.dict(REGISTRY,{'get_weekend_growth':lambda:{'growth_pct':RAW}}):
            trace=run('增长',provider,repair_final_answer=True)
        self.assertEqual(trace['errors'][0]['type'],'AnswerValidationError')
        self.assertIn('preserve status',trace['errors'][0]['message'])

    def test_repair_hallucinated_claim_remains_rejected(self):
        bad=json.loads(answer(True));bad['numeric_evidence'][1]['value']=999
        provider=ScriptedProvider([ModelTurn(calls=[ToolCall(ID,'get_weekend_growth','{}')]),
                                  ModelTurn(final_text=answer()),ModelTurn(final_text=json.dumps(bad))])
        with patch.dict(REGISTRY,{'get_weekend_growth':lambda:{'growth_pct':RAW}}):
            trace=run('增长',provider,repair_final_answer=True)
        self.assertEqual(trace['errors'][0]['type'],'AnswerValidationError')

    def test_repair_after_three_tools_does_not_expand_tool_budget(self):
        provider=ScriptedProvider([ModelTurn(calls=[ToolCall(call_id,'get_weekend_growth','{}')])
            for call_id in (ID,'call_second_fixture','call_third_fixture')]+[
            ModelTurn(final_text=answer()),ModelTurn(final_text=answer(True))])
        with patch.dict(REGISTRY,{'get_weekend_growth':lambda:{'growth_pct':RAW}}):
            trace=run('增长',provider,repair_final_answer=True)
        self.assertEqual(trace['status'],'answered')
        self.assertEqual(trace['tool_call_count'],3)
        self.assertEqual(len(provider.requests),5)
        self.assertFalse(provider.requests[-2]['allow_tools'])
        self.assertFalse(provider.requests[-1]['allow_tools'])


if __name__=='__main__': unittest.main()
