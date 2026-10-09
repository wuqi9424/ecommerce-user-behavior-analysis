"""Offline adversarial self-tests of the independent v2 evaluation view."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from .llm_v2_predicates import concept_check
from .run_llm_evaluation_v2 import (HERE, load, evaluate, grounding_check, numeric_fact,
                                  set_check, overall, protected_hashes, report)

class EvaluationV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved=load(HERE/'llm_results_latest.json') if (HERE/'llm_results_latest.json').exists() else None
        cls.cases=load(HERE/'evaluation_cases.json')['cases']
        cls.values=load(HERE/'expected_values.json')['values']
        cls.profiles=load(HERE/'llm_v2_contracts.json')['cases']
    def trace(self,id):
        # Synthetic protocol fixtures: self-tests do not depend on a model's wording.
        def evidence(call,path,value,display,unit,fmt='.2f'):
            return dict(operation='identity',paths=[f'calls/{call}/{path}'],policy=None,
                        value=value,display=display,unit=unit,format_spec=fmt)
        calls=[];entries=[];text='';definitions={}
        if id=='direct_03':
            calls=[dict(call_id='call_dynamic_growth',name='get_weekend_growth',result={'periods':[
                {'period_label':'previous_weekend','active_users':864829},
                {'period_label':'final_weekend','active_users':986594}]})]
            entries=[evidence('call_dynamic_growth','periods/period_label=final_weekend/active_users',986594,'986594','users','.0f')];text='活跃用户为 986594 人。'
        elif id=='direct_06':
            calls=[dict(call_id='call_dynamic_experiment',name='get_ab_test_summary',result={'treatment':{'purchase_user_rate':25.3933029896693}})]
            entries=[evidence('call_dynamic_experiment','treatment/purchase_user_rate',25.3933029896693,'25.39','percent')];text='购买用户率为 25.39%。'
        elif id=='direct_08':
            calls=[dict(call_id='call_dynamic_experiment',name='get_ab_test_summary',result={'absolute_lift_pp':-0.08367741777225435})]
            entries=[evidence('call_dynamic_experiment','absolute_lift_pp',-0.08367741777225435,'-0.0837','pp','.4f')];text='变化 -0.0837 pp。'
        elif id=='causal_04':
            calls=[dict(call_id='call_dynamic_opportunity',name='get_opportunity_analysis',result={'definitions':{'priority':'P1 不是购买概率模型'}})]
            text='P1 不是购买概率模型。'
        elif id=='multi_intent_01':
            calls=[dict(call_id='call_dynamic_platform',name='get_platform_overview',result={'total_users':987991}),
                   dict(call_id='call_dynamic_experiment',name='get_ab_test_summary',result={'p_value':0.658927128926678})]
            entries=[evidence('call_dynamic_experiment','p_value',0.658927128926678,'0.6589','fraction','.4f')];text='p 值为 0.6589。'
        else: raise AssertionError(id)
        answer=dict(answer=text,facts=[],analysis='',candidate_actions=[],caveats=[],numeric_evidence=entries)
        return dict(final_answer=answer,tool_results=calls,numeric_grounding={'passed':True})
    def check(self,text,concept): return concept_check({'answer':text},concept)['passed']
    def test_multi_intent_exact_set(self):
        self.assertTrue(set_check(['platform','experiment'],['experiment','platform'])['exact_match'])
        self.assertFalse(set_check(['platform','experiment'],['ambiguous'])['exact_match'])
    def test_missing_and_optional_tools(self):
        c=set_check(['funnel'],['funnel','growth'],['growth'])
        self.assertFalse(c['exact_match']);self.assertTrue(c['required_set_pass']);self.assertEqual(c['reasonable_extra'],['growth'])
        self.assertEqual(set_check(['growth'],['funnel'])['missing'],['growth'])
    def test_buy_synonyms(self):
        for text in ['buy event ≠ order','购买行为事件不等于订单数']:
            self.assertTrue(self.check(text,'buy_not_order'))
    def test_exposure_synonyms(self):
        for text in ['没有真实 treatment / exposure','没有真实 treatment 或 exposure']:
            self.assertTrue(self.check(text,'no_exposure'))
    def test_wrong_negation(self):
        for text in ['购买行为事件等于订单数','购买行为事件不是不等于订单数']:
            self.assertFalse(self.check(text,'buy_not_order'))
        self.assertFalse(self.check('不是没有真实 treatment 或 exposure','no_exposure'))
    def test_contradiction(self):
        self.assertFalse(self.check('购买行为事件不等于订单数。购买行为事件就是订单数。','buy_not_order'))
    def test_local_negation_cannot_hide_later_assertion(self):
        self.assertFalse(self.check('P1 不是购买概率模型，但P1用户必然更容易购买。','priority_not_probability'))
        self.assertFalse(self.check('category_id 是匿名标识，但匿名标识可以对应真实类别名称。','anonymous'))

    def test_definitions_not_prose(self):
        trace=self.trace('causal_04');trace['final_answer'].update(answer='待解释',facts=[],analysis='',caveats=[],candidate_actions=[])
        self.assertFalse(concept_check(trace['final_answer'],'priority_not_probability')['passed'])
        # The actual tool still contains the definitions, but they cannot satisfy prose.
        self.assertTrue(trace['tool_results'][0]['result']['definitions'])
    def test_pp_percent_mismatch(self):
        trace=self.trace('direct_08');self.assertTrue(grounding_check(trace)['passed'])
        trace['final_answer']['numeric_evidence'][0]['unit']='percent'
        self.assertFalse(grounding_check(trace)['passed'])
    def test_pp_prose_percent_mismatch(self):
        trace=self.trace('direct_08');trace['final_answer']['answer']=trace['final_answer']['answer'].replace('pp','%')
        self.assertFalse(grounding_check(trace)['passed'])
    def test_missing_display_separate_from_grounding(self):
        trace=self.trace('direct_03');g=grounding_check(trace)
        self.assertTrue(g['passed'])
        check=numeric_fact(trace,self.values['previous_weekend_active_users'],g['passed'],True)
        self.assertFalse(check['passed']);self.assertEqual(check['failure_kind'],'missing_numeric_display')
    def test_rounded_precision_not_hallucination(self):
        trace=self.trace('direct_06');self.assertTrue(grounding_check(trace)['passed'])
        self.assertTrue(numeric_fact(trace,self.values['treatment_purchase_rate'],True)['passed'])
        check=numeric_fact(trace,self.values['treatment_purchase_rate'],True,True)
        self.assertFalse(check['passed']);self.assertEqual(check['failure_kind'],'display_precision')
    def test_hallucinated_value(self):
        trace=self.trace('direct_06');trace['final_answer']['numeric_evidence'][0]['value']=999
        self.assertFalse(grounding_check(trace)['passed'])
    def test_dynamic_call_id_no_placeholder(self):
        trace=self.trace('direct_06');self.assertTrue(grounding_check(trace)['passed'])
        trace['final_answer']['numeric_evidence'][0]['paths'][0]='calls/call_1/treatment/purchase_user_rate'
        self.assertFalse(grounding_check(trace)['passed'])
    def test_exact_field_not_similar_number(self):
        trace=self.trace('direct_06');self.assertFalse(numeric_fact(trace,self.values['control_purchase_rate'],True)['passed'])
    def test_cross_tool_binding(self):
        trace=self.trace('multi_intent_01')
        e=next(e for e in trace['final_answer']['numeric_evidence'] if e['paths'] and e['paths'][0].endswith('/p_value'))
        platform=next(r['call_id'] for r in trace['tool_results'] if r['name']=='get_platform_overview')
        e['paths']=[f'calls/{platform}/p_value'];self.assertFalse(grounding_check(trace)['passed'])
    def test_absolute_relative_requires_both(self):
        self.assertTrue(self.check('购买事件增至更多。意向购买比例下降。','absolute_relative'))
        self.assertFalse(self.check('购买事件增至更多。','absolute_relative'))
    def test_unknown_predicate_fails_closed(self):
        with self.assertRaises(ValueError): self.check('任意文字','unknown')
    def evaluation(self,saved=None,profiles=None):
        if self.saved is None: self.skipTest('Saved run unavailable; protocol self-tests remain independent')
        # Any accidental model/network/database work makes these tests fail.
        with patch('urllib.request.urlopen',side_effect=AssertionError('Network forbidden')), patch('ai_agent.llm_agent.run',side_effect=AssertionError('Agent execution forbidden')), patch('duckdb.connect',side_effect=AssertionError('Database forbidden')):
            return evaluate(saved or self.saved,self.cases,self.values,profiles or self.profiles)
    def test_boundary_independent_of_facts(self):
        profiles=deepcopy(self.profiles);profiles['boundary_01']['semantic_facts']=['priority_not_probability']
        result=self.evaluation(profiles=profiles);row=next(r for r in result['cases'] if r['id']=='boundary_01')
        self.assertFalse(row['architecture_aware_case_pass']);self.assertTrue(all(b['passed'] for b in row['boundary_rules']))
        self.assertEqual(result['architecture_aware_metrics']['boundary_compliance']['passed'],12)
    def test_forbidden_zero_is_good(self):
        result=self.evaluation();metrics=result['architecture_aware_metrics']
        self.assertEqual(metrics['forbidden_claim_cases'],0);self.assertEqual(metrics['forbidden_claim_rate'],0.0)
        self.assertNotIn('passed',{'forbidden_claim_cases':metrics['forbidden_claim_cases']})
    def test_overall_independent_legacy_blanket_facts(self):
        if self.saved is None: self.skipTest('Saved run unavailable')
        saved=deepcopy(self.saved)
        for r in saved['cases']: r['passed']=False;r['fact_checks']=[{'passed':False}]*100
        result=self.evaluation(saved=saved)
        row=next(r for r in result['cases'] if r['id']=='direct_01')
        self.assertTrue(row['architecture_aware_case_pass']);self.assertFalse(row['legacy_strict_case_pass'])
    def test_source_accuracy_does_not_mix_causal_claims(self):
        trace=self.trace('direct_06');trace['final_answer']['answer']+=' 购物车召回已经有效。'
        self.assertTrue(grounding_check(trace)['passed'])
        # Source grounding is deliberately not a causal compliance score.

    def test_boundary_metrics_do_not_hide_forbidden(self):
        self.assertFalse(self.check('不显著就等于两组相同。','not_same'))
    def test_frozen_hashes_unchanged(self):
        if self.saved is None: self.skipTest('Saved run unavailable')
        before=protected_hashes(HERE/'llm_results_latest.json');self.evaluation()
        self.assertEqual(before,protected_hashes(HERE/'llm_results_latest.json'))
    def test_saved_grounding_not_blindly_trusted(self):
        trace=self.trace('direct_06');trace['final_answer']['answer']+=' 999999999'
        self.assertTrue(trace['numeric_grounding']['passed']);self.assertFalse(grounding_check(trace)['passed'])
    def test_profile_ids_and_question_integrity(self):
        if self.saved is None: self.skipTest('Saved run unavailable')
        saved=deepcopy(self.saved);saved['cases'][0]['trace']['user_question']='other'
        with self.assertRaises(ValueError): self.evaluation(saved=saved)
    def test_offline_full_run_and_report(self):
        result=self.evaluation();self.assertEqual(result['new_api_requests'],0)
        self.assertEqual(result['architecture_aware_metrics']['numeric_grounding_source_accuracy']['passed'],37)
        self.assertIn('Forbidden Claim Rate',report(result))

if __name__=='__main__': unittest.main()
