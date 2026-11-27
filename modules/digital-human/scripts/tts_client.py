"""Explicit cloned-speaker TTS; no voice training or client-side time stretching."""
import base64
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from common import sanitized, tts_key, write_json


def parse_audio(raw):
    chunks, errors = [], []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        message = json.loads(line)
        code = message.get("code")
        if code not in (None, 0, 20000000):
            errors.append({"code": code, "message": message.get("message")})
        if message.get("data"):
            chunks.append(base64.b64decode(message["data"], validate=True))
    if errors or not chunks:
        raise RuntimeError("TTS 合成失败：" + json.dumps(errors, ensure_ascii=False))
    return b"".join(chunks)


def speech_key(settings):
    if settings.get('provider') == 'minimax':
        key = os.environ.get('MINIMAX_TTS_API_KEY', '').strip()
        if not key:
            raise ValueError('请在工作台保存 MiniMax API Key。')
        return key
    if settings.get('provider') == 'aliyun':
        key = os.environ.get('DASHSCOPE_API_KEY', '').strip()
        if not key:
            raise ValueError('请在工作台保存阿里百炼 API Key。')
        return key
    return tts_key()


def synthesize(text, cfg, directory, revision=0):
    settings = cfg["tts"]
    key = speech_key(settings)
    provider = settings.get('provider')
    if provider in ('minimax', 'aliyun'):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
        dialect = settings.get('dialect') or None
        if provider == 'aliyun':
            from aliyun_voice import synthesize as provider_synthesize
            extra = settings['workspace']
        else:
            from minimax_voice import synthesize as provider_synthesize
            extra = settings.get('pronunciation_dict')
        data, payload, trace = provider_synthesize(text, settings['speaker'], settings['model'],
                                                  settings['speech_rate'], key, extra, dialect=dialect)
        directory = Path(directory)
        write_json(directory / f"tts-request-{revision}.json", payload)
        output = directory / f"narration-{revision}.mp3"
        output.write_bytes(data)
        result = {'file': str(output), 'provider': provider, 'speaker': settings['speaker'],
                  'resource_id': settings['model'], 'speech_rate': settings['speech_rate'],
                  'logid': trace, 'revision': revision}
        if dialect:
            result['dialect'] = dialect
        return result
    if settings.get('dialect'):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
        from voice_dialects import validate_dialect
        validate_dialect(settings['dialect'], 'volcengine')
    payload = {"user": {"uid": "project-text-to-digital-human"}, "req_params": {
        "text": text, "speaker": settings["speaker"], "model": settings["model"],
        "audio_params": {"format": "mp3", "sample_rate": settings["sample_rate"],
                         "speech_rate": settings["speech_rate"], "bit_rate": settings["bit_rate"]},
        "additions": json.dumps({"pronunciation_dict": settings["pronunciation_dict"]}, ensure_ascii=False)}}
    if settings.get("custom_speaker_id"):payload["req_params"]["custom_speaker_id"]=settings["custom_speaker_id"]
    directory = Path(directory)
    write_json(directory / f"tts-request-{revision}.json", payload)
    data, logid = None, None
    for attempt in range(3):
        request = urllib.request.Request(settings["endpoint"], data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Api-Key": key,
                     "X-Api-Resource-Id": settings["resource_id"], "X-Api-Request-Id": str(uuid.uuid4())})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                logid = response.headers.get("X-Tt-Logid")
            data = parse_audio(raw)
            break
        except urllib.error.HTTPError as error:
            code = error.code
            body = sanitized(error.read().decode(errors="replace"), (key,))
            if code in (408, 429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1 + 2 * attempt)
                continue
            raise RuntimeError(f"TTS HTTP {code}: {body}") from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            if attempt < 2:
                time.sleep(1 + 2 * attempt)
                continue
            raise RuntimeError(sanitized(str(error), (key,))) from None
        except Exception as error:
            raise RuntimeError(sanitized(str(error), (key,))) from None
    output = directory / f"narration-{revision}.mp3"
    output.write_bytes(data)
    return {"file": str(output), "speaker": settings["speaker"], "resource_id": settings["resource_id"],
            "speech_rate": settings["speech_rate"], "logid": logid, "revision": revision}
