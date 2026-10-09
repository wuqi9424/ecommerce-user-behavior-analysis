"""Signed change vs explicit decline presentation, strictly source checked."""
import json
import unittest
from uuid import uuid4
from ai_agent.llm_agent import validate_final, AnswerValidationError
from ai_agent.evals.test_llm_control import final


class DirectionTests(unittest.TestCase):
    def fixture(self,prose,unit='pp',amount=2.123456,display='-2.12',spec='.2f'):
        id='call_'+uuid4().hex
        field='conversion_rate' if unit=='pp' else 'total_events'
        entries=[{'operation':'subtraction','paths':[f'calls/{id}/final/{field}',f'calls/{id}/previous/{field}'],
                  'policy':None,'value':-amount,'display':display,'format_spec':spec,'unit':unit}]
        data={'final':{field:10},'previous':{field:10+amount}}
        results=[{'call_id':id,'name':'get_funnel_comparison','result':data}]
        return final(('funnel',),facts=[prose],evidence=entries),results

    def test_decline_is_bound_to_verified_negative_evidence_without_editing_prose(self):
        text,results=self.fixture('较前期下降 2.12 个百分点。')
        answer,check=validate_final(text,results)
        self.assertTrue(check['passed'])
        self.assertEqual(answer['facts'],['较前期下降 2.12 个百分点。'])
        self.assertEqual(answer['numeric_evidence'][0]['display'],'-2.12')
        proof=check['presentation_bindings'][0]
        self.assertEqual(proof['prose_display'],'2.12')
        self.assertEqual(proof['signed_display'],'-2.12')
        self.assertEqual(proof['source_paths'],answer['numeric_evidence'][0]['paths'])

    def test_supported_decline_words(self):
        for cue in ('下降','下降了','降低','减少','回落'):
            text,results=self.fixture(f'较前期{cue} 2.12pp。')
            self.assertTrue(validate_final(text,results)[1]['passed'])

    def test_increase_or_directionless_token_does_not_use_negative_entry(self):
        for prose in ('上升 2.12pp。','变化 2.12pp。','数值 2.12pp。'):
            text,results=self.fixture(prose)
            with self.assertRaises(AnswerValidationError): validate_final(text,results)

    def test_negated_hypothetical_or_denied_decline_is_not_an_observed_change(self):
        for prose in ('未下降 2.12pp。','不下降 2.12pp。','并未下降 2.12pp。',
                      '如果下降 2.12pp。','可能下降 2.12pp。','预计下降 2.12pp。',
                      '下降 2.12pp 的说法不成立。'):
            text,results=self.fixture(prose)
            with self.assertRaises(AnswerValidationError): validate_final(text,results)

    def test_magnitude_precision_and_unit_must_match_exactly(self):
        for prose in ('下降 2.13pp。','下降 2.123pp。','下降 2.12%。','下降 2.12。'):
            text,results=self.fixture(prose)
            with self.assertRaises(AnswerValidationError): validate_final(text,results)

    def test_swapping_operands_with_negative_claim_still_fails(self):
        text,results=self.fixture('下降 2.12pp。')
        answer=json.loads(text);answer['numeric_evidence'][0]['paths'].reverse()
        with self.assertRaises(AnswerValidationError): validate_final(json.dumps(answer),results)

    def test_hallucinated_ledger_value_does_not_gain_a_presentation_binding(self):
        text,results=self.fixture('下降 2.12pp。')
        answer=json.loads(text);answer['numeric_evidence'][0]['value']=-999
        with self.assertRaises(AnswerValidationError): validate_final(json.dumps(answer),results)

    def test_other_unannotated_numbers_remain_failures(self):
        text,results=self.fixture('下降 2.12pp，另有 999 人。')
        with self.assertRaisesRegex(AnswerValidationError,'999'): validate_final(text,results)

    def test_three_distinct_decline_entries_replay_generic_pattern(self):
        id='call_'+uuid4().hex
        entries=[];before={};after={};clauses=[]
        for field,previous,current,display in [('first_rate',30,27.876544,'-2.12'),
                                               ('second_rate',45,44.345678,'-0.65'),
                                               ('third_rate',18,16.888888,'-1.11')]:
            before[field]=previous;after[field]=current
            entries.append({'operation':'subtraction','paths':[f'calls/{id}/after/{field}',f'calls/{id}/before/{field}'],
                'policy':None,'value':current-previous,'display':display,'format_spec':'.2f','unit':'pp'})
            clauses.append(f'{field} 下降 {display[1:]} 个百分点。')
        text=final(('funnel',),facts=clauses,evidence=entries)
        results=[{'call_id':id,'name':'get_funnel_comparison','result':{'before':before,'after':after}}]
        self.assertEqual(len(validate_final(text,results)[1]['presentation_bindings']),3)


if __name__=='__main__': unittest.main()
