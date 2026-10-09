"""LLM-only grounding regressions with synthetic results; no API or DB access."""
import json
import unittest
from uuid import uuid4
from unittest.mock import patch

from ai_agent.llm_agent import validate_final, AnswerValidationError, run
from ai_agent.llm_grounding import source_catalog_prompt
from ai_agent.providers.base import ToolCall, ModelTurn
from ai_agent.tool_schemas import REGISTRY
from ai_agent.evals.test_llm_control import ScriptedProvider, final

GROWTH={'active_user_growth_pct':14.079661990983183,
        'total_event_growth_pct':30.142451823373296}


def evidence(call_id,field,value,display):
    return {'operation':'identity','paths':[f'calls/{call_id}/{field}'],'policy':None,
            'value':value,'display':display,'format_spec':'.2f','unit':'percent'}


def result(call_id,data=None,name='get_weekend_growth'):
    return {'call_id':call_id,'name':name,'result':GROWTH if data is None else data}


def growth_final(entries,facts=None,intents=('growth',)):
    return final(intents,facts=facts or ['活跃用户增长 14.08%，总事件增长 30.14%。'],evidence=entries)


class GroundingTests(unittest.TestCase):
    def entries(self,call_id,rounded=False):
        return [evidence(call_id,field,float(display) if rounded else GROWTH[field],display)
                for field,display in [('active_user_growth_pct','14.08'),('total_event_growth_pct','30.14')]]

    def test_dynamic_ids_and_high_precision_values_ground_rounded_prose(self):
        for _ in range(2):
            call_id='call_'+uuid4().hex
            answer,grounding=validate_final(growth_final(self.entries(call_id)),[result(call_id)])
            self.assertTrue(grounding['passed'])
            self.assertEqual(answer['numeric_evidence'][0]['paths'],[f'calls/{call_id}/active_user_growth_pct'])

    def test_nonexistent_placeholder_id_is_not_remapped(self):
        actual_id='call_'+uuid4().hex
        with self.assertRaisesRegex(AnswerValidationError,'call_1'):
            validate_final(growth_final(self.entries('call_1')),[result(actual_id)])

    def test_rounded_evidence_values_are_recomputed_with_audit_value(self):
        call_id='call_'+uuid4().hex
        answer,grounding=validate_final(growth_final(self.entries(call_id,True)),[result(call_id)])
        self.assertTrue(grounding['passed'])
        for record,(field,raw) in zip(answer['numeric_evidence'],GROWTH.items()):
            self.assertEqual(record['value'],raw)
            self.assertEqual(record['model_value'],float(record['display']))

    def test_wrong_display_or_nearby_claim_does_not_pass(self):
        call_id='call_'+uuid4().hex
        for value,display in [(14.09,'14.09'),(14.081,'14.08'),(999,'14.08')]:
            entries=[evidence(call_id,'active_user_growth_pct',value,display)]
            with self.assertRaises(AnswerValidationError):
                validate_final(growth_final(entries,[f'活跃用户增长 {display}%。']),[result(call_id)])

    def test_missing_annotation_and_extra_hallucinated_number_still_fail(self):
        call_id='call_'+uuid4().hex
        for facts,entries in [(['活跃用户增长 14.08%。'],[]),
                              (['活跃用户增长 14.08%，另增长 999%。'],self.entries(call_id)[:1])]:
            with self.assertRaisesRegex(AnswerValidationError,'Unannotated numeric literals'):
                validate_final(growth_final(entries,facts),[result(call_id)])

    def test_multi_call_does_not_borrow_field_value_from_another_tool(self):
        growth_id='call_'+uuid4().hex;platform_id='call_'+uuid4().hex
        results=[result(growth_id),result(platform_id,{'active_user_growth_pct':80},'get_platform_overview')]
        # Same field label, wrong call result: cannot use the growth number there.
        bad=evidence(platform_id,'active_user_growth_pct',14.08,'14.08')
        with self.assertRaises(AnswerValidationError):
            validate_final(growth_final([bad],['增长 14.08%。'],('growth','platform')),results)
        # Missing field is not searched for or borrowed from another result.
        missing=evidence(platform_id,'total_event_growth_pct',30.14,'30.14')
        with self.assertRaises(AnswerValidationError):
            validate_final(growth_final([missing],['增长 30.14%。'],('growth','platform')),results)

    def test_run_supplies_cumulative_actual_source_catalog_before_final(self):
        growth_id='call_'+uuid4().hex;platform_id='call_'+uuid4().hex
        entries=self.entries(growth_id)
        provider=ScriptedProvider([
            ModelTurn(calls=[ToolCall(growth_id,'get_weekend_growth','{}')]),
            ModelTurn(calls=[ToolCall(platform_id,'get_platform_overview','{}')]),
            ModelTurn(final_text=growth_final(entries,intents=('growth','platform')))])
        with patch.dict(REGISTRY,{'get_weekend_growth':lambda:dict(GROWTH),'get_platform_overview':lambda:{}}):
            trace=run('平台规模及周末增长',provider)
        self.assertEqual(trace['status'],'answered')
        self.assertEqual(trace['tool_call_count'],2)
        first=provider.requests[0]['system_prompt']
        self.assertNotIn(growth_id,first)
        second=provider.requests[1]
        self.assertIn(f'calls/{growth_id}/',second['system_prompt'])
        self.assertEqual(second['tool_outputs'][0]['call_id'],growth_id)
        last=provider.requests[2]['system_prompt']
        self.assertIn(growth_id,last);self.assertIn(platform_id,last)
        self.assertNotIn('"call_1"',last)

    def test_prompt_source_catalog_does_not_use_sequence_aliases(self):
        call_id='call_'+uuid4().hex
        prompt=source_catalog_prompt('Contract',[result(call_id)])
        self.assertIn('get_weekend_growth',prompt)
        self.assertIn('calls/'+call_id+'/',prompt)
        self.assertNotIn('"call_1"',prompt)

    def test_allowed_derivation_with_rounded_value_still_checks_operands(self):
        call_id='call_'+uuid4().hex
        entries=[{'operation':'pct_change','paths':[f'calls/{call_id}/before',f'calls/{call_id}/after'],
                  'policy':None,'value':14.08,'display':'14.08','format_spec':'.2f','unit':'percent'}]
        raw={'before':864829,'after':986594}
        answer,grounding=validate_final(growth_final(entries,['增长 14.08%。']),[result(call_id,raw)])
        self.assertTrue(grounding['passed'])
        self.assertAlmostEqual(answer['numeric_evidence'][0]['value'],GROWTH['active_user_growth_pct'])
        entries[0]['paths'].reverse()
        with self.assertRaises(AnswerValidationError):
            validate_final(growth_final(entries,['增长 14.08%。']),[result(call_id,raw)])

    def test_frozen_baseline_hashes_unchanged(self):
        from ai_agent.evals.run_llm_evaluation import check_baseline_integrity
        check_baseline_integrity()


if __name__=='__main__': unittest.main()
