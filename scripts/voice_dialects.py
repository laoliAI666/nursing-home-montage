"""Dialect catalog, per-model support matrix, provider parameters, and script guidance."""
import re

DEFAULT_LABEL = '普通话（默认）'

DIALECTS = (
    {'id': 'yue', 'label': '广东话（粤语）', 'shortLabel': '广东话'},
    {'id': 'changsha', 'label': '长沙话（湖南话）', 'shortLabel': '长沙话'},
    {'id': 'chongqing', 'label': '重庆话', 'shortLabel': '重庆话'},
    {'id': 'shaanxi', 'label': '陕西话', 'shortLabel': '陕西话'},
)

DIALECT_IDS = tuple(item['id'] for item in DIALECTS)
DIALECT_MAP = {item['id']: item for item in DIALECTS}

ALIYUN_MODEL_SUPPORT = {
    'qwen-audio-3.1-tts-plus': ('yue', 'changsha', 'chongqing', 'shaanxi'),
    'cosyvoice-v3.5-flash': ('yue', 'changsha', 'shaanxi'),
}

MINIMAX_MODEL_SUPPORT = {
    'speech-2.8-hd': ('yue',),
    'speech-2.8-turbo': ('yue',),
}

ALIYUN_LANGUAGE_HINTS = {
    'qwen-audio-3.1-tts-plus': {
        'yue': ('zh-yue',),
        'changsha': ('zh-hn',),
        'chongqing': ('zh-cq',),
        'shaanxi': ('zh-sx',),
    },
}

ALIYUN_INSTRUCTIONS = {
    'cosyvoice-v3.5-flash': {
        'yue': '请用广东话表达。',
        'changsha': '请用湖南话表达。',
        'shaanxi': '请用陕西话表达。',
    },
}

MINIMAX_LANGUAGE_BOOST = {
    'yue': 'Chinese,Yue',
}

MODEL_HINTS = {
    'aliyun:qwen-audio-3.1-tts-plus': '当前模型支持全部 4 种方言（广东话、长沙话、重庆话、陕西话），保存后可用于试听与配音；选广东话时 AI 写口播稿也会自动按粤语口语习惯生成。',
    'aliyun:cosyvoice-v3.5-flash': '当前模型支持广东话、长沙话（按湖南话合成）、陕西话；暂不支持重庆话（已置灰）；选广东话时 AI 写口播稿也会自动按粤语口语习惯生成。',
    'minimax': 'MiniMax 当前仅原生支持广东话（粤语）；长沙话、重庆话、陕西话暂不支持（已置灰）；选广东话时 AI 写口播稿也会自动按粤语口语习惯生成。',
    'volcengine': '火山语音复刻暂不支持通过参数切换方言（以上 4 种已置灰）；如需方言请切换至阿里百炼，或直接上传方言录音复刻。',
}

CANTONESE_NARRATION_GUIDANCE = (
    '【广东话口播写法】本条数字人绑定了广东话（粤语）配音，请全程严格按地道广东话（粤语）口语习惯撰写口播台词，不要写普通话书面语直译：'
    '优先使用地道粤语口语词（如：系/唔系、同/同埋、呢度/呢间、畀、嘅、睇/睇下、讲/讲话、今日、喺、冇、咗、啲），'
    '避免“我是/和你/这段/给你/我的/很/没有/在”等普通话书面表达；'
    '机构正式名称、地名、数字、医疗及照护专名保持准确原文，不得生造或改写专名，也不要加任何括号或注音；'
    '若绑定了固定片尾钩子，最后一段也须在严格保留人物称呼、数字、引流关键词与原意的前提下，自然改写为地道广东话（粤语）口语'
    '（例如将“我是/带你看完/想要/在评论区/的”转为“我系/带你睇完/想/喺评论区/嘅”），不要生硬保留普通话书面语。'
)

_MANDARIN_HOOK_MARKERS = re.compile(
    r'我是|我们|这里|这儿|这家|这个|这段|给你|和你|我的|没有|看完|看看|想要|怎么|什么|为什么|哪里|在评论区|评论区里|喜欢的话|的(?=[，。！？!?,.\s]|$)'
)


def normalize_dialect(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('配音方言参数格式不正确。')
    cleaned = value.strip().lower()
    if not cleaned:
        return ''
    if cleaned not in DIALECT_MAP:
        raise ValueError(f'暂不支持该配音方言：{value.strip()}。目前仅支持广东话、长沙话、重庆话、陕西话或普通话。')
    return cleaned


def dialect_label(value, *, short=False):
    code = normalize_dialect(value)
    if not code:
        return '普通话' if short else DEFAULT_LABEL
    item = DIALECT_MAP[code]
    return item['shortLabel'] if short else item['label']


def supported_dialects(provider, model=None):
    prov = (provider or 'volcengine').strip().lower()
    if prov == 'aliyun':
        return ALIYUN_MODEL_SUPPORT.get(model or '', ())
    if prov == 'minimax':
        if model:
            return MINIMAX_MODEL_SUPPORT.get(model, ())
        return ('yue',)
    return ()


def validate_dialect(value, provider, model=None):
    code = normalize_dialect(value)
    if not code:
        return ''
    allowed = supported_dialects(provider, model)
    if code not in allowed:
        label = DIALECT_MAP[code]['label']
        raise ValueError(f'当前所选语音模型不支持「{label}」，请改选支持的方言或恢复普通话。')
    return code


def aliyun_language_hints(dialect, model):
    code = validate_dialect(dialect, 'aliyun', model)
    if not code:
        return None
    hints = ALIYUN_LANGUAGE_HINTS.get(model, {}).get(code)
    return list(hints) if hints else None


def aliyun_instruction(dialect, model):
    code = validate_dialect(dialect, 'aliyun', model)
    if not code:
        return None
    return ALIYUN_INSTRUCTIONS.get(model, {}).get(code)


def aliyun_synthesis_params(dialect, model):
    code = validate_dialect(dialect, 'aliyun', model)
    if not code:
        return {}
    hints = aliyun_language_hints(code, model)
    if hints:
        return {'language_hints': hints}
    instruction = aliyun_instruction(code, model)
    if instruction:
        return {'instruction': instruction}
    return {}


def minimax_language_boost(dialect, model=None):
    code = validate_dialect(dialect, 'minimax', model)
    if not code:
        return None
    return MINIMAX_LANGUAGE_BOOST[code]


def preview_text(name, dialect=None):
    speaker_name = (name or '你的数字人')[:12]
    code = normalize_dialect(dialect)
    if code == 'yue':
        return f'大家好，我系{speaker_name}。好高兴同你见面。今日用呢段话，畀你听下我把声同讲话嘅节奏。'
    return f'大家好，我是{speaker_name}。很高兴和你见面。今天用这段话，给你听听我的声音和说话的节奏。'


def narration_dialect_guidance(dialect):
    code = normalize_dialect(dialect)
    if code == 'yue':
        return CANTONESE_NARRATION_GUIDANCE
    return ''


def adapts_closing_hook_for_dialect(dialect):
    """Return True when a person's bound closing hook should be adapted into dialect speech."""
    return normalize_dialect(dialect) == 'yue'


def cantonese_hook_adaptation_issue(actual_text, bound_hook, dialect):
    """Guardrail for Cantonese closing-hook adaptation: preserve numerals and avoid verbatim Mandarin copy."""
    if not bound_hook or not adapts_closing_hook_for_dialect(dialect):
        return ''
    actual = str(actual_text or '').strip()
    source = str(bound_hook or '').strip()
    if not actual:
        return '最后一段片尾钩子不能为空，请按绑定片尾钩子转为地道广东话（粤语）口语。'
    expected_nums = re.findall(r'\d+', source)
    missing_nums = [num for num in dict.fromkeys(expected_nums) if num not in actual]
    if missing_nums:
        return (
            f'最后一段片尾钩子遗漏了原钩子中的数字（{"、".join(missing_nums)}），'
            '请保留全部原数字、人物称呼与引流关键词，并保持地道广东话（粤语）口语。'
        )
    if re.sub(r'\s+', '', actual) == re.sub(r'\s+', '', source) and _MANDARIN_HOOK_MARKERS.search(source):
        return (
            '最后一段片尾钩子仍直接照抄了普通话原文，未转为地道广东话（粤语）口语。'
            '请在严格保留人物称呼、数字、引流关键词与原意的前提下，将最后一段自然改写为粤语口语'
            '（如将“我是/看完/想要/在/的”转为“我系/睇完/想/喺/嘅”）。'
        )
    return ''


def public_catalog():
    return {
        'defaultLabel': DEFAULT_LABEL,
        'options': [dict(item) for item in DIALECTS],
        'support': {
            'aliyun': {k: list(v) for k, v in ALIYUN_MODEL_SUPPORT.items()},
            'minimax': {k: list(v) for k, v in MINIMAX_MODEL_SUPPORT.items()},
            'volcengine': [],
        },
        'hints': dict(MODEL_HINTS),
    }
