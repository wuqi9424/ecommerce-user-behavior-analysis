"""Counterfactual relationship and provenance tests, no database required."""
import unittest

from ai_agent.reasoning.diagnostic_rules import (Evidence, absolute_vs_relative,
    compare_adjacent_rates, explain_funnel,
    explain_experiment, explain_unavailable, scale_not_priority)
from ai_agent.evals.numeric_grounding import numeric_grounding_check


class ReasoningChecks(unittest.TestCase):
    def test_absolute_and_relative_can_move_in_opposite_directions(self):
        self.assertIn('购买人数增加', absolute_vs_relative(100, 140, 20, 15))
        self.assertIn('购买人数下降', absolute_vs_relative(140, 100, 15, 20))
        self.assertIn('比例增加', absolute_vs_relative(140, 100, 15, 20))
        self.assertIn('持平', absolute_vs_relative(100, 100, 20, 20))
        self.assertIn('无法完整比较', absolute_vs_relative(100, None, 20, 15))

    def test_adjacent_step_winner_reverses_and_ties(self):
        before = {'pv_to_intent_rate':40,'intent_to_buy_rate':20}
        self.assertIn('意向→购买的百分点降幅更大',compare_adjacent_rates(before,{'pv_to_intent_rate':39,'intent_to_buy_rate':17}))
        self.assertIn('浏览→意向的百分点降幅更大',compare_adjacent_rates(before,{'pv_to_intent_rate':36,'intent_to_buy_rate':19}))
        self.assertIn('降幅相同',compare_adjacent_rates(before,{'pv_to_intent_rate':38,'intent_to_buy_rate':18}))
        self.assertIn('均未',compare_adjacent_rates(before,{'pv_to_intent_rate':41,'intent_to_buy_rate':21}))
        self.assertIn('缺失',compare_adjacent_rates(before,{'pv_to_intent_rate':None,'intent_to_buy_rate':18}))

    def test_largest_segment_is_data_driven(self):
        rows=[{'priority_segment':'P1','distinct_users':40},{'priority_segment':'P3','distinct_users':20}]
        self.assertIn('P1 的去重用户规模最大',scale_not_priority(rows))
        rows[1]['distinct_users']=80
        self.assertIn('P3 的去重用户规模最大',scale_not_priority(rows))
        self.assertIn('规模不等于优先级',scale_not_priority(rows))

    def test_missing_funnel_rates_are_not_invented(self):
        data={'periods':[{'period_label':label,'avg_buy_users':None,'pv_to_buy_rate':None}
                         for label in ('previous_weekend','final_weekend')]}
        output=explain_funnel(data)
        self.assertIn('无法完整比较',output['answer'])
        self.assertEqual(output['numeric_evidence'],[])

    def test_unsupported_stays_conditional_and_without_population_numbers(self):
        output=explain_unavailable('unsupported')
        self.assertIn('若最后一天',output['analysis'])
        self.assertIn('未返回',output['analysis'])
        self.assertIn('不能据此判定流失',output['analysis'])
        self.assertEqual(output['facts'],[])
        self.assertEqual(output['numeric_evidence'],[])

    def fixture(self):
        data={'previous':100,'final':125}
        evidence=Evidence(data)
        actual=evidence.number(operation='pct_change',paths=['previous','final'])
        return {'key_data':data,'interpretation':{'facts':[f'增长 {actual}%。'],
                'numeric_evidence':evidence.entries},'caveats':[]}

    def test_valid_derived_number_is_grounded(self):
        self.assertTrue(numeric_grounding_check(self.fixture())['passed'])

    def test_invented_prose_number_is_rejected(self):
        output=self.fixture();output['interpretation']['facts'].append('用户增长 999%。')
        self.assertFalse(numeric_grounding_check(output)['passed'])

    def test_attached_chinese_number_is_not_silently_skipped(self):
        output=self.fixture();output['interpretation']['facts'].append('购买人数增加999人。')
        self.assertFalse(numeric_grounding_check(output)['passed'])

    def test_false_ledger_value_is_rejected(self):
        output=self.fixture();entry=output['interpretation']['numeric_evidence'][0]
        entry['value']=99;entry['display']='99.0000'
        output['interpretation']['facts']=['增长 99.0000%。']
        self.assertFalse(numeric_grounding_check(output)['passed'])

    def test_bad_source_and_unknown_operation_are_rejected(self):
        for key,value in [('paths',['missing','final']),('operation','predict_probability')]:
            output=self.fixture();output['interpretation']['numeric_evidence'][0][key]=value
            self.assertFalse(numeric_grounding_check(output)['passed'])

    def test_unknown_policy_and_missing_ledger_are_rejected(self):
        output=self.fixture();entry=output['interpretation']['numeric_evidence'][0]
        entry.update(operation='policy',paths=[],policy='assumed_uplift')
        self.assertFalse(numeric_grounding_check(output)['passed'])
        self.assertFalse(numeric_grounding_check({'interpretation':{'facts':['增长 25%。']}})['passed'])

    def test_source_mutation_breaks_grounding(self):
        output=self.fixture();output['key_data']['final']=150
        self.assertFalse(numeric_grounding_check(output)['passed'])

    def test_policy_value_is_independently_checked(self):
        e=Evidence({});display=e.number(operation='policy',policy='significance_alpha')
        output={'key_data':{},'interpretation':{'facts':[f'alpha={display}'], 'numeric_evidence':e.entries}}
        self.assertTrue(numeric_grounding_check(output)['passed'])
        e.entries[0]['value']=.10
        self.assertFalse(numeric_grounding_check(output)['passed'])

    def test_real_explanation_depends_on_changed_counts(self):
        data={'periods':[{'period_label':'previous_weekend','avg_buy_users':100,'pv_to_buy_rate':20,
                         'pv_to_intent_rate':40,'intent_to_buy_rate':20},
                        {'period_label':'final_weekend','avg_buy_users':140,'pv_to_buy_rate':15,
                         'pv_to_intent_rate':39,'intent_to_buy_rate':17}]}
        self.assertIn('购买人数增加',explain_funnel(data)['answer'])
        data['periods'][1]['avg_buy_users']=80
        self.assertIn('购买人数下降',explain_funnel(data)['answer'])
        output={'key_data':data,'interpretation':explain_funnel(data),'caveats':[]}
        self.assertTrue(numeric_grounding_check(output)['passed'])

    def test_significance_does_not_become_treatment_effect(self):
        data={'experiment_type':'simulated_assignment','p_value':.001,
              'treatment':{'purchase_user_rate':30},'control':{'purchase_user_rate':25},
              'absolute_lift_pp':5,'ci_lower_pp':1,'ci_upper_pp':9,'mde_pp':.5}
        output=explain_experiment(data)
        self.assertIn('存在统计差异',output['answer'])
        self.assertIn('不能据此证明',output['answer'])
        data['p_value']=.9;data['ci_lower_pp']=-1
        self.assertIn('未达到',explain_experiment(data)['answer'])
        self.assertIn('包含零',explain_experiment(data)['analysis'])


if __name__=='__main__':
    unittest.main()
