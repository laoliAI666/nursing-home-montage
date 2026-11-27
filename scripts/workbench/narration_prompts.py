"""Text-only writing inputs for the workbench's one-minute narration workflow."""
from random import Random


COMPLETE_NARRATION_POLICY = 2
DEFAULT_CTA = '邀请有意了解参观的家属留言。'


def outline_omission_limit(policy, fact_count, rules=None):
    if policy != COMPLETE_NARRATION_POLICY:
        return 0
    limit = 2
    if isinstance(rules, dict):
        from text_styles import extract_rules
        style_rules, _ = extract_rules(rules)
        limit = style_rules['body']['omissionLimit']
    return min(limit, max(0, fact_count - 1))


OUTLINE_RULES = (
    '根据机构原文全文，梳理每条综合介绍都要讲到的核心事实。逐段核对家属了解机构、照护或居住服务时有用的信息。'
    '优先保留明确的用户要求、机构定位与探望交通、照护方式和特色、居住和活动、日常生活服务、医护和就医等原文实际存在的核心主题。'
    '逐段审视全文，保留各类核心服务。把不同服务方式合在同一项，把同类空间或设施概括为一项。'
    '评价、疗效、开业年份、规模数字、完整门牌、周边资源清单、服务流程和设施举例保留在原文供写稿核对，'
    '除非用户明确要求或它就是本机构核心服务的唯一说明。区位保留地名和关键交通事实，特色服务保留名称和适用对象。'
    '每项 text 只写一句或半句实际必须说出的关键信息。把原文细节留给单条稿件选用。'
    '全部 text 合计控制在 outlineUnits 个可读字内。篇幅紧时压缩各项措辞和可选细节，保留各类核心服务。'
    '一个要点只能引用一条原句，出处不同时可以拆项。'
    '返回 {"facts":[{"topic":"主题","text":"必须讲到的关键信息","evidenceId":"e1"}],"question":null}。'
)

COMPACT_OUTLINE = (
    '从候选事实制作一分钟养老机构介绍的必讲清单。你在编一个简短的关键词索引，text 只写极简短语，不写完整句。'
    '保留家属理解机构和服务的核心信息。候选中有机构所在地、探望交通时，分别留一个最短事实；'
    '机构定位的宣传描述也要省掉。有不同照护和生活服务时，保留不同服务形式和必要正式名称。'
    '有居住与活动时概括空间用途，原文既有室内也有室外活动时，两类都覆盖，各留最多一个例子。'
    '有特色服务时保留名称和适用人群，具体方法留作单篇例子。医护安排保留院内支持、就医方式等能区分的服务。'
    '删掉开业时间、规模数字、地址门牌、就医时效、宣传、疗效、流程和多个并列例子。'
    '用户明确点名要求的事实照常保留。相关内容可拆可合，每项只引用一条候选的 evidenceId。'
    '空间不用列出所有房间。多种核心服务用顿号并列，不添加宣传或效果。所有 text 合计以 targetUnits 为目标，必须在 maxUnits 内。'
    '返回 {"facts":[{"topic":"短主题","text":"必须说出的关键词组","evidenceId":"e1"}]}。'
)

NARRATION_SYSTEM = (
    '为长辈和家属写一条中文养老机构介绍口播，让第一次听到这家机构的人也能听懂。\n\n'
    '开头最多两句，按「本条变化参数」切入，并自然交代机构名称。开头第 1 句作为数字人出场句，控制在 15–30 字（约 5–8 秒自然说完，不要写得过长）。完整机构名只说一次。开头已经交代的事实，正文接着往下讲。\n\n'
    '口播台词严禁使用任何括号（含中英文括号）。若机构名称或原文中带有括号说明（例如「椿萱茂（重庆回兴）老年公寓」中的「（重庆回兴）」），口播直接省略括号及括号内文字（只念「椿萱茂老年公寓」），需要交代地名或补充信息时用正常语句自然说出。\n\n'
    '逐项覆盖「必讲要点」中的关键信息。相关内容可以合在一句或一段，每个事实讲清一次。'
    '全文只扩展一个重要细节，原文中的其余细节用来核对。'
    '「展开重点」可以用一个有原文依据的细节讲具体。结尾用一句话完成「咨询引导」；若「咨询引导」标明为该数字人绑定的固定片尾钩子，最后一段必须原样使用该钩子全文（一字不改），并将其字数计入全文总字数范围内。\n\n'
    '机构原文是事实依据。用日常的话说清具体安排，保留正式名称、适用对象和服务条件。'
    '省去宣传评价和未经核实的效果承诺。\n\n'
    '全文含开头、正文和结尾，须落在用户给定的字数范围内。标点和空格不计，数字按实际读法计。'
    '超长时，删掉重复表述、额外例子和修饰语。偏短时，把已有的一个要点解释清楚。'
    '交稿前逐项检查关键信息和全文长度。\n\n'
    '只返回一个 JSON 对象，格式为 {"paragraphs":["开头","正文段","结尾"]}。'
    '正文可以有多段，每项只写实际要念的话。'
)

NARRATION_REPAIR_SYSTEM = (
    '局部修订一条给长辈和家属听的养老机构口播。只改指定段落，其他段落原样保留。'
    '「必须保留」里的要求必须落实到实际念出的台词。改开头时须在第 1 段自然说出机构名称（省略名称中的括号及括号内文字），第 1 句控制在 15–30 字，'
    '即使上一稿漏了也要补回；正文和结尾不重复院名。全稿严禁出现任何括号或括号注释。'
    '按替换段落的字数目标改写，替换段落合计不得超出给定范围。工作台会把改后的段落替回全文计数。'
    '继续覆盖本条必讲清单，保留适用对象、服务条件和本条切入方式。'
    '超长时先压缩重复、次要例子和修饰语，不能用删除必须保留项来凑字数。偏短时解释一个有原文依据的要点。'
    '直接介绍具体安排，把核验备注留在后台。结尾保留一个咨询动作；若绑定了固定片尾钩子，最后一段原样保留不得改写。'
    '只返回 {"edits":[{"index":2,"text":"修订段落"}]}，段落编号从 1 起，每个指定编号返回一次。'
)


def variations(facts, task_id, count, rules=None):
    """Use distinct opening topics first, with stable combinations on retry."""
    topics = list(dict.fromkeys(row['topic'] for row in facts))
    styles = ('家属提问', '从具体安排讲起', '直接介绍')
    if isinstance(rules, dict):
        from text_styles import extract_rules
        style_rules, _ = extract_rules(rules)
        configured = tuple(style_rules['opening'].get('openingStyles') or ())
        if configured:
            styles = configured
    rng = Random(task_id)
    rng.shuffle(topics)
    options = {}
    for topic in topics:
        options[topic] = [
            {'开场切入点': topic, '开场写法': style, '展开重点': focus}
            for style in styles
            for focus in topics
        ]
        rng.shuffle(options[topic])
    ordered = [choice for round_choices in zip(*(options[topic] for topic in topics))
               for choice in round_choices]
    return [ordered[i % len(ordered)] for i in range(count)]


def user_prompt(context, facts, budget, variation, preference, feedback, *, closing_hook=None, rules=None):
    lower, upper = budget['minUnits'], budget['maxUnits']
    default_cta = DEFAULT_CTA
    if isinstance(rules, dict):
        from text_styles import extract_rules
        style_rules, _ = extract_rules(rules)
        default_cta = style_rules['ending'].get('defaultCta') or DEFAULT_CTA
    cta_guide = (
        f'固定片尾钩子（最后一段必须原样使用，一字不改）：{closing_hook}'
        if closing_hook else default_cta
    )
    value = {
        '机构名称': context['institutionName'],
        '字数要求': {'目标': (lower + upper) // 2, '最少': lower, '最多': upper},
        '必讲要点': [{'主题': fact['topic'], '必须讲到的关键信息': fact['text'],
                     '对应原文': {'文件': fact['source'], '段落': fact['paragraph']}} for fact in facts],
        '本条变化参数': variation,
        '咨询引导': cta_guide,
        '机构原文全文': context['sources'],
    }
    answer = context.get('answer') or {}
    if any((context.get('requirements'), preference, answer.get('reply'), feedback)):
        value['补充要求'] = {'任务': context.get('requirements', ''), '本条': preference,
                           '答复': answer.get('reply'), '上次提示': feedback}
    return value


def edit_lengths(sizes, targets, budget):
    """Give the writer the size of the merged replacements and each target."""
    lower, upper = budget['minUnits'], budget['maxUnits']
    fixed = sum(size for index, size in enumerate(sizes) if index not in targets)
    current = sum(sizes[index] for index in targets)
    minimum = max(len(targets), lower - fixed)
    maximum = max(minimum, upper - fixed)
    wanted = min(max((lower + upper) // 2 - fixed, minimum), maximum)
    allocated = 0
    rows = []
    for offset, index in enumerate(targets):
        remaining = len(targets) - offset - 1
        share = wanted - allocated if not remaining else round(wanted * sizes[index] / max(current, 1))
        share = min(max(share, 1), wanted - allocated - remaining)
        allocated += share
        rows.append({'段号': index + 1, '现有字数': sizes[index], '目标字数': share})
    return {'合计目标': wanted, '合计最少': minimum, '合计最多': maximum, '各段': rows}
