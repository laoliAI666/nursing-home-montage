"""Dialect catalog, per-model support matrix, and provider parameter mapping."""

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

ALIYUN_INSTRUCTIONS = {
    'qwen-audio-3.1-tts-plus': {
        'yue': '请用广东话朗读。',
        'changsha': '请用长沙话朗读。',
        'chongqing': '请用重庆话朗读。',
        'shaanxi': '请用陕西话朗读。',
    },
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
    'aliyun:qwen-audio-3.1-tts-plus': '当前模型支持全部 4 种方言（广东话、长沙话、重庆话、陕西话），保存后可用于试听与配音。',
    'aliyun:cosyvoice-v3.5-flash': '当前模型支持广东话、长沙话（按湖南话合成）、陕西话；暂不支持重庆话（已置灰）。',
    'minimax': 'MiniMax 当前仅原生支持广东话（粤语）；长沙话、重庆话、陕西话暂不支持（已置灰）。',
    'volcengine': '火山语音复刻暂不支持通过参数切换方言（以上 4 种已置灰）；如需方言请切换至阿里百炼，或直接上传方言录音复刻。',
}


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


def aliyun_instruction(dialect, model):
    code = validate_dialect(dialect, 'aliyun', model)
    if not code:
        return None
    return ALIYUN_INSTRUCTIONS[model][code]


def minimax_language_boost(dialect, model=None):
    code = validate_dialect(dialect, 'minimax', model)
    if not code:
        return None
    return MINIMAX_LANGUAGE_BOOST[code]


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
