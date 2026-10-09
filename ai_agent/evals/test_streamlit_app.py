"""Offline UI integration; no live API or database calls."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from ai_agent import llm_agent
from ai_agent.streamlit_app import demo_view, query, EXAMPLES
from ai_agent.evals.test_llm_control import ScriptedProvider, call, final
from ai_agent.providers.base import ModelTurn, ConfigurationError

APP = Path(__file__).resolve().parents[1] / 'streamlit_app.py'


def trace(status='answered'):
    return {
        'status': status, 'selected_tools': ['get_weekend_growth'], 'tool_call_count': 1,
        'numeric_grounding': {'passed': True},
        'final_answer': {'answer': '回答示例', 'facts': ['事实示例'], 'analysis': '分析示例',
                         'candidate_actions': ['行动示例'], 'caveats': ['边界示例'],
                         'detected_intents': ['growth'], 'numeric_evidence': ['PRIVATE_SOURCE']},
        'tool_results': ['PRIVATE_RAW'], 'errors': [{'message': 'Authorization: PRIVATE_KEY'}],
    }


class StreamlitChecks(unittest.TestCase):
    def test_projection_does_not_mutate_trace_or_expose_internal_fields(self):
        original = trace()
        before = copy.deepcopy(original)
        view = demo_view(original)
        self.assertEqual(original, before)
        self.assertEqual(set(view), {'answer', 'facts', 'analysis', 'candidate_actions', 'caveats', 'process'})
        self.assertNotIn('PRIVATE', json.dumps(view))

    def test_error_and_exception_messages_never_displayed(self):
        self.assertNotIn('PRIVATE', json.dumps(demo_view(trace('error'))))
        with patch.object(llm_agent, 'from_environment', side_effect=RuntimeError('Authorization: PRIVATE_KEY')):
            self.assertNotIn('PRIVATE', json.dumps(query('问题')))
        with patch.object(llm_agent, 'from_environment', side_effect=ConfigurationError('PRIVATE_KEY')):
            self.assertIn('OPENAI_MODEL', query('问题')['error'])

    def test_known_secret_redacted_in_answer(self):
        original = trace()
        original['final_answer']['answer'] = 'PRIVATE_KEY'
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'PRIVATE_KEY'}):
            self.assertEqual(demo_view(original)['answer'], '[REDACTED]')

    def test_ui_callable_matches_existing_run_policy(self):
        def provider():
            return ScriptedProvider([call(), ModelTurn(final_text=final())])
        with patch.dict(llm_agent.REGISTRY, {'get_platform_overview': lambda: {'events': 1}}):
            expected = llm_agent.run('平台规模？', provider(), repair_final_answer=True)
            instance = provider()
            with patch.object(llm_agent, 'from_environment', return_value=instance):
                self.assertEqual(query('平台规模？'), demo_view(expected))
            self.assertEqual(len(instance.requests), 2)

    def test_initial_example_blank_and_rerun_do_not_call_agent(self):
        with patch.object(llm_agent, 'from_environment') as factory:
            app = AppTest.from_file(str(APP)).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.title[0].value, 'E-commerce Analytics Copilot')
            captions = [item.value for item in app.caption]
            self.assertIn('基于 1 亿+ 电商行为日志的受控 AI Analytics Agent · Portfolio Demo', captions)
            self.assertIn('Controlled Tool Calling · Read-only Analytics Tools · Numeric Grounding', captions)
            self.assertIn('本项目基于匿名公开行为数据；无价格、GMV、真实渠道曝光或真实 treatment 数据。', captions)
            self.assertEqual(len(app.get('column')), 6)
            for index, example in enumerate(EXAMPLES):
                app.button(key=f'example_{index}').click().run()
                self.assertEqual(app.text_area[0].value, example)
            app.text_area[0].set_value('   ')
            app.button[-1].click().run()
            self.assertTrue(app.warning)
            factory.assert_not_called()

    def test_submit_render_and_rerun_no_duplicate_request(self):
        with patch.object(llm_agent, 'from_environment', return_value=object()), patch.object(llm_agent, 'run', return_value=trace()) as run:
            app = AppTest.from_file(str(APP)).run()
            app.text_area[0].set_value('  增长？  ')
            app.button[-1].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(run.call_args.args[0], '增长？')
            self.assertEqual(run.call_args.kwargs, {'repair_final_answer': True})
            self.assertEqual(len(app.subheader), 5)
            self.assertEqual(app.expander[0].label, '查看分析过程')
            self.assertFalse(app.expander[0].proto.expanded)
            self.assertFalse(app.json)
            self.assertNotIn('PRIVATE', str(app))
            app.run()
            run.assert_called_once()

    def test_error_page_has_no_internal_trace(self):
        with patch.object(llm_agent, 'from_environment', return_value=object()), patch.object(llm_agent, 'run', return_value=trace('error')):
            app = AppTest.from_file(str(APP)).run()
            app.text_area[0].set_value('问题')
            app.button[-1].click().run()
            self.assertTrue(app.error)
            self.assertFalse(app.exception)
            self.assertFalse(app.expander)
            self.assertNotIn('PRIVATE', str(app))

    def test_refusal_and_clarification_render_normally(self):
        for status in ('unsupported', 'ambiguous'):
            with self.subTest(status=status), patch.object(llm_agent, 'from_environment', return_value=object()), patch.object(llm_agent, 'run', return_value=trace(status)):
                app = AppTest.from_file(str(APP)).run()
                app.text_area[0].set_value('问题')
                app.button[-1].click().run()
                self.assertFalse(app.error)
                self.assertEqual(len(app.subheader), 5)


if __name__ == '__main__':
    unittest.main()
