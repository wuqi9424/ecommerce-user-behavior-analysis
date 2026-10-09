"""Local portfolio UI; all analytics and validation remain in llm_agent."""
from pathlib import Path
import os
import sys

# Streamlit executes this file as a script; allow package imports from any cwd.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
from ai_agent import llm_agent
from ai_agent.providers.base import ConfigurationError

EXAMPLES = (
    '为什么第二个周末流量增长了？',
    '购买转化漏斗哪里出了问题？',
    '哪些用户最值得优先召回？',
    '哪些品类存在增长机会？',
    'A/B测试证明购物车召回有效吗？',
)


def demo_view(trace):
    """Allowlist display fields; never keep raw results or exception messages."""
    if trace.get('status') == 'configuration_missing':
        return {'error': '模型配置不可用，请检查当前环境的 OPENAI_API_KEY、OPENAI_MODEL 和 provider 设置。'}
    if trace.get('status') == 'error' or not trace.get('final_answer'):
        return {'error': '本次分析未能完成，可能是连接、数据工具或回答校验失败。请检查本地配置后重试。'}
    answer = trace['final_answer']
    fields = ('answer', 'facts', 'analysis', 'candidate_actions', 'caveats')
    view = {field: answer[field] for field in fields}
    view['process'] = {
        'detected_intents': answer['detected_intents'],
        'selected_tools': trace['selected_tools'],
        'tool_call_count': trace['tool_call_count'],
        'numeric_grounding': trace.get('numeric_grounding', {}).get('passed'),
    }
    return llm_agent.redact(view, (os.environ.get('OPENAI_API_KEY', ''),))


def query(question):
    """Use the existing controlled callable with the same policy as its CLI."""
    try:
        provider = llm_agent.from_environment()
        trace = llm_agent.run(question, provider, repair_final_answer=True)
    except ConfigurationError:
        return demo_view({'status': 'configuration_missing'})
    except Exception:
        # Never expose arbitrary exception strings, headers or tracebacks in UI.
        return demo_view({'status': 'error'})
    return demo_view(trace)


def choose_example(question):
    st.session_state['question'] = question
    st.session_state.pop('demo_result', None)


def main():
    st.set_page_config(page_title='E-commerce Analytics Copilot', page_icon=':material/analytics:')
    st.title('E-commerce Analytics Copilot')
    st.caption('基于 1 亿+ 电商行为日志的受控 AI Analytics Agent · Portfolio Demo')
    st.caption('Controlled Tool Calling · Read-only Analytics Tools · Numeric Grounding')
    st.caption('选择示例或输入问题，再点击「开始分析」。')
    for start in range(0, len(EXAMPLES), 2):
        columns = st.columns(2)
        for offset, example in enumerate(EXAMPLES[start:start + 2]):
            index = start + offset
            with columns[offset]:
                st.button(example, key=f'example_{index}', on_click=choose_example, args=(example,), width='stretch')
    with st.form('question_form'):
        question = st.text_area('你想了解什么？', key='question', max_chars=10000, height=80)
        submitted = st.form_submit_button('开始分析', type='primary')
    if submitted:
        st.session_state.pop('demo_result', None)
        if not question.strip():
            st.warning('请输入一个问题。')
        else:
            with st.spinner('正在分析，请稍候…'):
                st.session_state['demo_result'] = query(question.strip())
    render_result(st.session_state.get('demo_result'))
    st.caption('本项目基于匿名公开行为数据；无价格、GMV、真实渠道曝光或真实 treatment 数据。')


def render_result(view):
    if not view:
        return
    if 'error' in view:
        st.error(view['error'])
        return
    for field, label in (
        ('answer', '回答'), ('facts', '事实'), ('analysis', '分析'),
        ('candidate_actions', '候选行动'), ('caveats', '注意事项'),
    ):
        st.subheader(label)
        content = view[field]
        if isinstance(content, list):
            for item in content:
                st.markdown(f'- {item}')
            if not content:
                st.caption('暂无')
        else:
            st.markdown(content)
    with st.expander('查看分析过程', expanded=False):
        process = view['process']
        st.text('Detected intents: ' + ', '.join(process['detected_intents']))
        st.text('Selected tools: ' + (', '.join(process['selected_tools']) or '无'))
        st.text(f"Tool call count: {process['tool_call_count']}")
        passed = process['numeric_grounding']
        st.text('Numeric grounding: ' + ('通过' if passed is True else '未通过' if passed is False else '未执行'))


if __name__ == '__main__':
    main()
