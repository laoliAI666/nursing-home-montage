"""Beijing Aliyun Model Studio voice enrollment and non-streaming TTS."""
import base64
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

MODELS = ('qwen-audio-3.1-tts-plus', 'cosyvoice-v3.5-flash')
DEFAULT_MODEL = MODELS[0]


class Rejected(RuntimeError):
    """A definitive provider rejection, safe to retry after correction."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new_url):
        return None


def workspace_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,62}', value.strip()):
        raise ValueError('请填写阿里百炼北京区的业务空间 ID。')
    return value.strip().lower()


def model_id(value):
    if value not in MODELS:
        raise ValueError('请选择受支持的阿里百炼语音模型。')
    return value


def request(action, workspace, key, payload):
    host = workspace_id(workspace) + '.cn-beijing.maas.aliyuncs.com'
    if not key:
        raise ValueError('请先保存阿里百炼 API Key。')
    req = urllib.request.Request(
        f'https://{host}/api/v1/services/audio/tts/{action}',
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
    )
    result = None
    for attempt in range(3):
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=55) as response:
                result = json.loads(response.read(2_000_000))
            break
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code in (400, 401, 403, 404, 422, 429):
                raise Rejected(f'阿里百炼拒绝请求（HTTP {code}）。请检查业务空间、API Key、额度和音色。') from None
            if attempt < 2 and code in (408, 500, 502, 503, 504):
                time.sleep(1 + 2 * attempt)
                continue
            raise RuntimeError(f'阿里百炼请求中断（HTTP {code}）。') from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            if attempt < 2:
                time.sleep(1 + 2 * attempt)
                continue
            raise RuntimeError('阿里百炼请求连接超时或中断。') from None
    if not isinstance(result, dict) or not isinstance(result.get('output'), dict):
        code = result.get('code') if isinstance(result, dict) else None
        if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,64}', code):
            raise Rejected(f'阿里百炼拒绝请求（{code}）。')
        raise RuntimeError('阿里百炼返回了无法确认的结果。')
    return result


def enrollment(workspace, key, action, **fields):
    return request('customization', workspace, key, {
        'model': 'voice-enrollment', 'input': {'action': action, **fields}
    })['output']


def sample_audio(sample):
    with wave.open(str(sample)) as wav:
        if not 5 <= wav.getnframes() / wav.getframerate() <= 60 or wav.getsampwidth() != 2 or wav.getframerate() < 16000:
            raise ValueError('阿里百炼复刻需要 5–60 秒录音，建议使用 10–20 秒清晰单人朗读。')
    return 'data:audio/wav;base64,' + base64.b64encode(sample.read_bytes()).decode('ascii')


def create(workspace, key, model, prefix, audio):
    result = enrollment(workspace, key, 'create_voice', target_model=model_id(model), prefix=prefix, audio=audio)
    voice = result.get('voice_id')
    if not isinstance(voice, str) or not voice:
        raise RuntimeError('阿里百炼未返回音色 ID。')
    return voice


def find_voice(workspace, key, model, prefix):
    result = enrollment(workspace, key, 'list_voice', prefix=prefix, page_size=10, page_index=0)
    voices = [voice['voice_id'] for voice in result.get('voice_list', [])
              if voice.get('target_model') == model and isinstance(voice.get('voice_id'), str)
              and f'-{prefix}-' in voice['voice_id']]
    return voices[0] if len(voices) == 1 else None


def download_audio(url):
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme not in ('http', 'https') or not parts.hostname
            or parts.netloc != parts.hostname
            or not re.fullmatch(r'[a-z0-9-]+\.oss-cn-beijing\.aliyuncs\.com', parts.hostname)):
        raise RuntimeError('阿里百炼返回了非北京 OSS 的音频地址。')
    data = b''
    for attempt in range(3):
        try:
            with urllib.request.build_opener(NoRedirect()).open(parts._replace(scheme='https').geturl(), timeout=50) as response:
                data = response.read(32_000_001)
            break
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if attempt < 2 and code in (408, 500, 502, 503, 504):
                time.sleep(1 + 2 * attempt)
                continue
            raise RuntimeError(f'阿里百炼音频下载失败（HTTP {code}）。') from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            if attempt < 2:
                time.sleep(1 + 2 * attempt)
                continue
            raise RuntimeError('阿里百炼音频下载超时或中断。') from None
    if (len(data) > 32_000_000 or len(data) < 3
            or not (data.startswith(b'ID3') or data[0] == 255 and data[1] & 224 == 224)):
        raise RuntimeError('阿里百炼没有返回有效的 MP3。')
    return data


def synthesize(text, voice, model, speech_rate, key, workspace, dialect=None):
    target_model = model_id(model)
    payload_input = {
        'text': text, 'voice': voice, 'format': 'mp3', 'sample_rate': 24000,
        'rate': max(0.5, min(2.0, 1 + speech_rate / 100)),
    }
    if dialect:
        from voice_dialects import aliyun_instruction
        instruction = aliyun_instruction(dialect, target_model)
        if instruction:
            payload_input['instruction'] = instruction
    payload = {'model': target_model, 'input': payload_input}
    result = request('SpeechSynthesizer', workspace, key, payload)
    url = result['output'].get('audio', {}).get('url')
    if not isinstance(url, str) or not url:
        raise RuntimeError('阿里百炼未返回合成音频。')
    return download_audio(url), payload, result.get('request_id')
