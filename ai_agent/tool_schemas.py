"""Provider-independent schemas and a closed dispatch registry."""
from copy import deepcopy
import json
from . import tools

REGISTRY = {name: getattr(tools, name) for name in (
    'get_platform_overview','get_weekend_growth','get_funnel_comparison',
    'get_opportunity_analysis','get_ab_test_summary')}
INTENTS = dict(zip(REGISTRY, ('platform','growth','funnel','opportunity','experiment')))

DESCRIPTIONS = {
 'get_platform_overview': ('platform：九天整体平台事件、活跃/总用户、商品和品类、四类行为与购买用户。',
   'active_users=total_users（九天任意行为去重用户）；buy_events 是事件不是订单。',
   '公开样本，非平台全量；没有 GMV/价格。'),
 'get_weekend_growth': ('growth：两个等长周末的用户规模、事件量、人均强度和观测用户结构；描述性 bridge。',
   '两天内去重用户；人均事件=两天事件/两天全部活跃用户；Final 相对 Previous 增长。',
   'newly_observed 不是新注册；无渠道/活动/曝光，不能识别因果增长来源。'),
 'get_funnel_comparison': ('funnel：两等长周末用户级集合漏斗、日均人数、环节比例变化。',
   '每日未舍入集合比例的等权均值；意向→购买=浏览且意向且购买/浏览且意向；百分比尺度。',
   '非事件比、非两天重新去重率、非同商品有序路径；规模增加和比例下降可同时发生。'),
 'get_opportunity_analysis': ('opportunity：机会品类总数、P1/P2/P3 规则候选池及按高意向未买规模排序的 Top N 匿名品类。',
   'P1 近期 cart；P2 无 cart、有 fav；P3 无 cart/fav、PV 达 Q75；完整九天目标品类无 buy。',
   '优先级不是购买概率；人数不能跨层相加；均值按 user-category pair 等权。category_id 匿名；品类意向→购买分母为全部意向。仅用于观察窗口结束后的实验。'),
 'get_ab_test_summary': ('experiment：历史模拟固定分桶，两组分配人数、购买用户率、lift、p-value、CI、MDE。',
   '结果窗口 buy 用户/全部 assigned_users；百分比尺度，lift/CI/MDE 为 pp；资格只用前七天。',
   'simulated_assignment，没有真实 treatment/exposure，不能证明召回有效或无效；不显著不等于相同；MDE 不是预期 uplift。'),
}

SCHEMAS = []
for name, (description, semantics, caveats) in DESCRIPTIONS.items():
    parameters = {'type':'object','properties':{},'required':[],'additionalProperties':False}
    if name == 'get_opportunity_analysis':
        parameters.update(properties={'top_n':{'type':'integer','minimum':1,'maximum':100,
                                             'description':'必须明确提供整数；未指定 Top N 时选择 5。'}},required=['top_n'])
    SCHEMAS.append({'name':name,'description':description,'parameters':parameters,
                    'metric_semantics':semantics,'caveats':caveats})


class ToolValidationError(ValueError):
    pass


def strict_json(text):
    def unique(pairs):
        output={}
        for key,value in pairs:
            if key in output: raise ToolValidationError('Duplicate JSON key')
            output[key]=value
        return output
    def invalid_constant(value):
        raise ToolValidationError('Nonfinite JSON number')
    return json.loads(text,object_pairs_hook=unique,parse_constant=invalid_constant)


def validate_arguments(name, arguments):
    if name not in REGISTRY:
        raise ToolValidationError('Unknown tool: only the five analytics functions are permitted')
    if isinstance(arguments,str):
        if len(arguments)>4096: raise ToolValidationError('Tool arguments too large')
        try: arguments=strict_json(arguments)
        except (ValueError,TypeError) as error: raise ToolValidationError('Invalid tool argument JSON') from error
    if not isinstance(arguments,dict): raise ToolValidationError('Arguments must be a JSON object')
    if name=='get_opportunity_analysis':
        if set(arguments)!={'top_n'}: raise ToolValidationError('Only required top_n is allowed')
        value=arguments['top_n']
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=100:
            raise ToolValidationError('top_n must be integer 1–100')
    elif arguments:
        raise ToolValidationError('This tool accepts no user parameters')
    return deepcopy(arguments)


def provider_schemas():
    return [{'type':'function','name':s['name'],'strict':True,
             'description':s['description']+' Metric semantics: '+s['metric_semantics']+' Caveats: '+s['caveats'],
             'parameters':deepcopy(s['parameters'])} for s in SCHEMAS]
