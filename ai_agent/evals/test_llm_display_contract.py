"""Display/unit diagnostics tests, with synthetic tools only."""
import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

from ai_agent.llm_agent import validate_final, AnswerValidationError, run, main
from ai_agent.evals.test_llm_control import ScriptedProvider, final
from ai_agent.providers.base import ModelTurn, ToolCall
from ai_agent.tool_schemas import REGISTRY

ID='call_dynamic_test_9f32'


def fixture(field,value,display,unit='percent',spec='.2f',prose=None):
    entry={'operation':'identity','paths':[f'calls/{ID}/{field}'],'policy':None,
           'value':value,'display':display,'format_spec':spec,'unit':unit}
    suffix='%' if unit=='percent' else 'pp' if unit=='pp' else ''
    text=final(('growth',),facts=[prose if prose is not None else f'观察值 {display}{suffix}。'],evidence=[entry])
    results=[{'call_id':ID,'name':'get_weekend_growth','result':{field:value}}]
    return text,results


class DisplayContractTests(unittest.TestCase):
    def test_percent_display_is_numeric_token_without_percent_suffix(self):
        text,results=fixture('active_user_growth_pct',14.079661990983183,'14.08')
        answer,grounding=validate_final(text,results)
        self.assertEqual(answer['numeric_evidence'][0]['display'],'14.08')
        self.assertTrue(grounding['passed'])
        text,results=fixture('active_user_growth_pct',14.079661990983183,'14.08%')
        with self.assertRaisesRegex(AnswerValidationError,'Display does not match'): validate_final(text,results)

    def test_percent_missing_prose_unit_fails(self):
        text,results=fixture('some_growth_pct',14.079661990983183,'14.08',prose='增长 14.08。')
        with self.assertRaisesRegex(AnswerValidationError,'requires percent'): validate_final(text,results)

    def test_pp_and_percent_source_units_cannot_be_swapped(self):
        for field,unit in [('rate_change_pp','percent'),('some_growth_pct','pp')]:
            text,results=fixture(field,1.23456,'1.23',unit=unit)
            with self.assertRaisesRegex(AnswerValidationError,'Unit mismatch'): validate_final(text,results)
        text,results=fixture('rate_change_pp',1.23456,'1.23',unit='pp')
        self.assertTrue(validate_final(text,results)[1]['passed'])
        text,results=fixture('rate_change_pp',1.23456,'1.23',unit='pp',prose='变化 1.23%。')
        with self.assertRaises(AnswerValidationError): validate_final(text,results)

    def test_integer_display(self):
        text,results=fixture('active_users',1234,'1,234',unit='users',spec=',.0f')
        self.assertTrue(validate_final(text,results)[1]['passed'])
        text,results=fixture('active_users',1234,'1234',unit='users',spec='.0f')
        self.assertTrue(validate_final(text,results)[1]['passed'])

    def test_decimal_display_and_fraction_not_percentage(self):
        text,results=fixture('events_per_active_user',1.234567,'1.2346',unit='events/user',spec='.4f')
        self.assertTrue(validate_final(text,results)[1]['passed'])
        text,results=fixture('p_value',0.6589,'0.6589',unit='fraction',spec='.4f')
        self.assertTrue(validate_final(text,results)[1]['passed'])
        text,results=fixture('p_value',0.6589,'0.6589',unit='percent',spec='.4f')
        with self.assertRaises(AnswerValidationError): validate_final(text,results)

    def test_wrong_rounding_and_hallucinated_value_still_fail(self):
        text,results=fixture('growth_pct',14.079661990983183,'14.07')
        with self.assertRaises(AnswerValidationError): validate_final(text,results)
        text,results=fixture('growth_pct',14.079661990983183,'14.08')
        payload=json.loads(text);payload['numeric_evidence'][0]['value']=999
        with self.assertRaises(AnswerValidationError): validate_final(json.dumps(payload),results)

    def test_diagnostic_reports_actual_failed_entry(self):
        text,results=fixture('growth_pct',14.079661990983183,'14.080',spec='.2f')
        with self.assertRaises(AnswerValidationError) as caught: validate_final(text,results)
        diagnostic=caught.exception.diagnostics[0]
        self.assertEqual(diagnostic['entry_index'],0)
        self.assertEqual(diagnostic['display'],'14.080')
        self.assertEqual(diagnostic['source_call_ids'],[ID])
        self.assertEqual(diagnostic['source_paths'],[f'calls/{ID}/growth_pct'])
        self.assertEqual(diagnostic['resolved_source_numeric_values'],[14.079661990983183])
        self.assertEqual(diagnostic['declared_format'],'.2f')
        self.assertEqual(diagnostic['declared_unit'],'percent')
        self.assertEqual(diagnostic['expected_display'],'14.08')
        self.assertIn('Display does not match',diagnostic['mismatch_reason'])

    def provider(self,text):
        return ScriptedProvider([ModelTurn(calls=[ToolCall(ID,'get_weekend_growth','{}')]),
                                 ModelTurn(final_text=text),ModelTurn(final_text=text)])

    def test_diagnostics_only_in_debug_and_secrets_redacted(self):
        text,results=fixture('growth_pct',14.079661990983183,'TEST_SECRET_DO_NOT_LOG Authorization: Bearer OTHER_SECRET')
        with patch.dict(REGISTRY,{'get_weekend_growth':lambda:results[0]['result']}):
            trace=run('test',self.provider(text),debug=True)
            ordinary=run('test',self.provider(text))
        diagnostic=json.dumps(trace['grounding_diagnostics'])
        self.assertNotIn('TEST_SECRET_DO_NOT_LOG',diagnostic)
        self.assertNotIn('OTHER_SECRET',diagnostic)
        self.assertNotIn('Authorization',diagnostic)
        self.assertNotIn('grounding_diagnostics',ordinary)

    def test_debug_cli_excludes_huge_tool_result_on_grounding_failure(self):
        text,results=fixture('growth_pct',14.079661990983183,'14.080')
        data=dict(results[0]['result'],huge_payload='DO_NOT_DUMP_THIS_RESULT'*1000)
        stream=io.StringIO()
        with patch.object(sys,'argv',['llm_agent','test','--debug']), \
             patch('ai_agent.llm_agent.from_environment',return_value=self.provider(text)), \
             patch.dict(REGISTRY,{'get_weekend_growth':lambda:data}),contextlib.redirect_stdout(stream):
            status=main()
        printed=json.loads(stream.getvalue())
        self.assertEqual(status,1)
        self.assertIn('grounding_diagnostics',printed)
        self.assertNotIn('tool_results',printed)
        self.assertNotIn('DO_NOT_DUMP_THIS_RESULT',stream.getvalue())
        self.assertEqual(printed['tool_result_summary'][0]['call_id'],ID)

    def test_frozen_baseline_hashes_unchanged(self):
        from ai_agent.evals.run_llm_evaluation import check_baseline_integrity
        check_baseline_integrity()

    def interval_fixture(self,unit='pp',suffix='pp',reverse=False,omit_upper=False):
        lower=-0.45524549512367796;upper=0.287845
        entries=[{'operation':'identity','paths':[f'calls/{ID}/{field}'],'policy':None,
                  'value':value,'display':display,'format_spec':'.2f','unit':unit}
                 for field,value,display in [('ci_lower_pp',lower,'-0.46'),('ci_upper_pp',upper,'0.29')]]
        if omit_upper: entries=entries[:1]
        prose=f'置信区间 [0.29, -0.46]{suffix}。' if reverse else f'置信区间 [-0.46, 0.29]{suffix}。'
        return final(('growth',),facts=[prose],evidence=entries),[
            {'call_id':ID,'name':'get_weekend_growth','result':{'ci_lower_pp':lower,'ci_upper_pp':upper}}]

    def test_ci_shared_pp_unit_accepts_both_grounded_endpoints(self):
        for suffix in ('pp',' 个百分点'):
            text,results=self.interval_fixture(suffix=suffix)
            self.assertTrue(validate_final(text,results)[1]['passed'])

    def test_ci_missing_or_wrong_shared_unit_still_fails(self):
        for suffix in ('','%'):
            text,results=self.interval_fixture(suffix=suffix)
            with self.assertRaises(AnswerValidationError) as caught: validate_final(text,results)
            self.assertIn('置信区间',caught.exception.diagnostics[0]['prose_context'])

    def test_shared_interval_does_not_exempt_unannotated_endpoint(self):
        text,results=self.interval_fixture(omit_upper=True)
        with self.assertRaisesRegex(AnswerValidationError,'Unannotated numeric literals'):
            validate_final(text,results)

    def test_reversed_interval_is_not_given_a_shared_unit(self):
        text,results=self.interval_fixture(reverse=True)
        with self.assertRaises(AnswerValidationError): validate_final(text,results)

    def test_interval_unit_does_not_apply_to_unrelated_number(self):
        text,results=self.interval_fixture()
        answer=json.loads(text);answer['facts'].append('另一个界限 -0.46。')
        with self.assertRaises(AnswerValidationError): validate_final(json.dumps(answer),results)

    def test_signed_difference_uses_baseline_then_comparison_without_auto_swap(self):
        paths=[f'calls/{ID}/periods/period_label=previous_weekend/intent_to_buy_rate',
               f'calls/{ID}/periods/period_label=final_weekend/intent_to_buy_rate']
        entry={'operation':'difference','paths':paths,'policy':None,'value':-2.0849,
               'display':'-2.0849','format_spec':'.4f','unit':'pp'}
        answer=json.loads(final(('growth',),facts=['变化 -2.0849pp。'],evidence=[entry]))
        results=[{'call_id':ID,'name':'get_weekend_growth','result':{'periods':[
            {'period_label':'previous_weekend','intent_to_buy_rate':24.2194},
            {'period_label':'final_weekend','intent_to_buy_rate':22.1345}]}}]
        self.assertTrue(validate_final(json.dumps(answer),results)[1]['passed'])
        answer['numeric_evidence'][0]['paths'].reverse()
        with self.assertRaises(AnswerValidationError) as caught: validate_final(json.dumps(answer),results)
        diagnostic=caught.exception.diagnostics[0]
        self.assertEqual(diagnostic['expected_display'],'2.0849')
        self.assertEqual(diagnostic['display'],'-2.0849')
        self.assertEqual(diagnostic['operation'],'difference')

    def test_subtraction_a_minus_b_preserves_order_and_sign(self):
        path_a=f'calls/{ID}/a_rate';path_b=f'calls/{ID}/b_rate'
        results=[{'call_id':ID,'name':'get_weekend_growth','result':{'a_rate':22.1345,'b_rate':24.2194}}]
        for paths,value,display in [([path_a,path_b],-2.0849,'-2.0849'),
                                    ([path_b,path_a],2.0849,'2.0849')]:
            entry={'operation':'subtraction','paths':paths,'policy':None,'value':value,
                   'display':display,'format_spec':'.4f','unit':'pp'}
            text=final(('growth',),facts=[f'变化 {display}pp。'],evidence=[entry])
            answer,check=validate_final(text,results)
            self.assertTrue(check['passed'])
            self.assertEqual(answer['numeric_evidence'][0]['operation'],'subtraction')
            self.assertEqual(answer['numeric_evidence'][0]['paths'],paths)
            # Reversing only the paths, keeping the signed claim, must fail.
            wrong=json.loads(text);wrong['numeric_evidence'][0]['paths']=list(reversed(paths))
            with self.assertRaises(AnswerValidationError): validate_final(json.dumps(wrong),results)

    def test_subtraction_audit_adapter_is_exact_and_non_mutating(self):
        from ai_agent.llm_grounding import independent_audit_interpretation
        paths=[f'calls/{ID}/a_rate',f'calls/{ID}/b_rate']
        answer={'numeric_evidence':[{'operation':'subtraction','paths':paths,'value':-5}]}
        audit=independent_audit_interpretation(answer)
        self.assertEqual(audit['numeric_evidence'][0]['operation'],'difference')
        self.assertEqual(audit['numeric_evidence'][0]['paths'],list(reversed(paths)))
        self.assertEqual(audit['numeric_evidence'][0]['value'],-5)
        self.assertEqual(answer['numeric_evidence'][0]['operation'],'subtraction')
        self.assertEqual(answer['numeric_evidence'][0]['paths'],paths)

    def test_confidence_level_and_shared_ci_unit_are_independent(self):
        text,results=self.interval_fixture(suffix=' pp')
        answer=json.loads(text)
        answer['facts']=['95% CI [-0.46, 0.29] pp。']
        answer['numeric_evidence'].append({'operation':'policy','paths':[],
            'policy':'confidence_level_pct','value':95,'display':'95','format_spec':'.0f','unit':'percent'})
        self.assertTrue(validate_final(json.dumps(answer),results)[1]['passed'])
        # CI scale does not inherit the percent sign from confidence level.
        answer['facts']=['95% CI [-0.46, 0.29] %。']
        with self.assertRaises(AnswerValidationError): validate_final(json.dumps(answer),results)

    def test_opportunity_user_pair_grounding_regression(self):
        data={'priorities':[{'priority_segment':'P1','distinct_users':349857,'user_category_pairs':658968}]}
        entries=[{'operation':'identity','paths':[f'calls/{ID}/priorities/priority_segment=P1/{field}'],
                  'policy':None,'value':value,'display':format(value,',.0f'),
                  'format_spec':',.0f','unit':unit}
                 for field,value,unit in [('distinct_users',349857,'users'),('user_category_pairs',658968,'pairs')]]
        text=final(('opportunity',),facts=['P1 有 349,857 个去重用户，658,968 个用户品类对。'],evidence=entries)
        results=[{'call_id':ID,'name':'get_opportunity_analysis','result':data}]
        self.assertTrue(validate_final(text,results)[1]['passed'])


if __name__=='__main__': unittest.main()
