"""MiniMax mainland Speech API client for cloning and MP3 synthesis."""
import json
import re
import urllib.error
import urllib.request
import uuid
import wave

API = 'https://api.minimax.cn/v1/'
MODELS = ('speech-2.8-hd', 'speech-2.8-turbo')


class Rejected(RuntimeError):
    """The provider explicitly declined the operation."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(action, key, payload, content_type='application/json'):
    if not key:
        raise ValueError('请在语音设置保存 MiniMax API Key。')
    body = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, dict) else payload
    req = urllib.request.Request(API + action, data=body, method='POST',
                                 headers={'Content-Type': content_type, 'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=90) as response:
            result = json.loads(response.read(32_000_000))
    except urllib.error.HTTPError as error:
        error.close()
        if error.code in (400, 401, 403, 422, 429):
            raise Rejected(f'MiniMax 拒绝了请求（HTTP {error.code}）。') from None
        raise RuntimeError(f'MiniMax 请求中断（HTTP {error.code}）。') from None
    metadata = result.get('base_resp', {})
    code = metadata.get('status_code')
    if type(code) is not int:
        raise RuntimeError('MiniMax 返回了无法确认的响应。')
    if code:
        raise Rejected(f'MiniMax 拒绝了请求（代码 {code}）。')
    return result


def upload(sample, key):
    with wave.open(str(sample)) as wav:
        seconds = wav.getnframes() / wav.getframerate()
    if not 10 <= seconds <= 300:
        raise ValueError('MiniMax 复刻需要 10 秒至 5 分钟的录音。')
    fence = uuid.uuid4().hex
    lead = (
        f'--{fence}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nvoice_clone\r\n'
        f'--{fence}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\n'
        'Content-Type: audio/wav\r\n\r\n'
    ).encode()
    body = lead + sample.read_bytes() + f'\r\n--{fence}--\r\n'.encode()
    result = request('files/upload', key, body, f'multipart/form-data; boundary={fence}')
    file_id = result.get('file', {}).get('file_id')
    if not re.fullmatch(r'[0-9]+', str(file_id or '')):
        raise RuntimeError('MiniMax 未返回可用的录音文件 ID。')
    return int(file_id)


def clone(file_id, voice_id, key):
    result = request('voice_clone', key, {'file_id': file_id, 'voice_id': voice_id})
    if result.get('input_sensitive'):
        raise Rejected('MiniMax 未接受这段录音，请更换录音。')
    return result


def synthesize(text, voice_id, model, speech_rate, key, rules=None):
    if model not in MODELS:
        raise ValueError('请选择受支持的 MiniMax 语音模型。')
    speed = max(0.5, min(2.0, 1 + speech_rate / 100))
    payload = {'model': model, 'text': text, 'stream': False, 'output_format': 'hex',
               'voice_setting': {'voice_id': voice_id, 'speed': speed, 'vol': 1, 'pitch': 0},
               'audio_setting': {'format': 'mp3', 'sample_rate': 32000, 'bitrate': 128000, 'channel': 1}}
    if rules and rules.get('tone'):
        payload['pronunciation_dict'] = {'tone': rules['tone']}
    response = request('t2a_v2', key, payload)
    try:
        data = bytes.fromhex(response['data']['audio'])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('MiniMax 未返回有效的 MP3 音频。') from None
    if not data:
        raise RuntimeError('MiniMax 返回了空音频。')
    return data, payload, response.get('trace_id')
