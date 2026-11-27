# /// script
# requires-python = ">=3.11"
# dependencies = ["volcengine==1.0.229", "tos==2.9.3"]
# ///
"""Run bounded connection tests using the same media service credentials."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from model_api import NoRedirect


def check(app, values):
    kind = values.get('kind')
    if kind not in ('speech', 'avatar', 'storage', 'voice_slots'):
        raise ValueError('未知连接测试。')
    cfg = app.pc.load()
    connection = cfg['connections'].get('main', {})
    saved = app.pc.connection_keys(connection)
    fields = values.get('connection', {})
    if not isinstance(fields, dict):
        raise ValueError('连接配置格式不正确。')
    credentials = {}
    for name, old in zip(('api_key', 'ak', 'sk'), saved):
        submitted = fields.get(name, '')
        if not isinstance(submitted, str) or len(submitted) > 512 or any(c in submitted for c in '\r\n'):
            raise ValueError('服务凭据格式不正确。')
        credentials[name] = submitted.strip() or old
    module = app.skill / 'modules/digital-human'
    media = json.loads((module / 'config.json').read_text(encoding='utf-8'))
    if kind == 'speech':
        person = cfg['people'].get(values.get('person') or cfg.get('default_person'), {})
        provider = person.get('voice_provider') or 'volcengine'
        if values.get('provider', provider) != provider:
            raise ValueError('请先选择已绑定此平台音色的人物。')
        person_audio(app, cfg, person, '连接测试。', fields, credentials['api_key'])
        message = {'aliyun': '阿里百炼语音合成通过。', 'minimax': 'MiniMax 语音合成通过。'}
        return {'ok': True, 'message': message.get(provider, '语音连接正常，已用所选音色合成短句。')}
    if not credentials['ak'] or not credentials['sk']:
        raise ValueError('先在设置保存火山 Access Key ID 和 Secret Access Key。')
    media['avatar'].update(connection.get('avatar', {}))
    if kind == 'voice_slots':
        from voice_slots import project_name
        project = project_name(values.get('project') or connection.get('voice_project', 'default'))
        return run_signed(app, kind, module, {'ak': credentials['ak'], 'sk': credentials['sk'], 'project': project})
    task_id = None
    if kind == 'avatar':
        operation = media['avatar']['req_key']
        receipt_path = app.data / 'avatar-check.json'
        key_id = hashlib.sha256(credentials['ak'].encode()).hexdigest()
        task_id = values.get('taskId')
        if task_id is not None and not valid_task_id(task_id):
            raise ValueError('请提供真实的数字人任务编号。')
        if task_id is None and credentials['ak'] == saved[1]:
            task_id = recent_avatar_task(app, operation)
        if task_id is None:
            task_id = saved_avatar_task(receipt_path, key_id, operation)
        if task_id is None:
            raise ValueError('当前没有可用于测试的数字人任务记录。完成一次数字人制作后可在这里查询验证。')
    if kind == 'storage':
        storage = {**connection.get('storage', {})}
        for key in ('bucket', 'region', 'endpoint'):
            if isinstance(fields.get(key), str) and fields[key].strip():
                storage[key] = fields[key].strip()
        endpoint = str(storage.get('endpoint', '')).removeprefix('https://').rstrip('/')
        if not all(storage.get(key) for key in ('bucket', 'region', 'endpoint')):
            raise ValueError('请填写 TOS 存储桶、区域和 Endpoint。')
        if not re.fullmatch(r'tos-[a-z0-9-]+\.volces\.com', endpoint, re.I):
            raise ValueError('测试需要火山 TOS 的官方 Endpoint。')
        media['storage'] = {'provider': 'tos', 'endpoint': endpoint, 'region': storage['region'], 'bucket': storage['bucket']}
    response = run_signed(app, kind, module, {'media': media, 'ak': credentials['ak'],
                                              'sk': credentials['sk'], 'taskId': task_id})
    if kind == 'avatar':
        with app.transaction():
            app.pc.write(receipt_path, {'keyId': key_id, 'reqKey': operation, 'taskId': task_id}, private=True)
    return response


def run_signed(app, kind, module, payload):
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('本机缺少 uv，安装制作依赖后可测试签名接口。')
    command = [uv, 'run', '--python', '3.11', str(Path(__file__).resolve()), kind, str(module)]
    env = {**os.environ, 'UV_CACHE_DIR': str(app.data / 'uv-cache')}
    try:
        result = subprocess.run(command, input=json.dumps(payload), env=env,
                                capture_output=True, text=True, timeout=55)
    except OSError:
        raise ValueError('无法启动 uv，请检查本机制作依赖。') from None
    except subprocess.TimeoutExpired:
        raise ValueError('签名接口测试超时，请检查依赖下载和服务网络。') from None
    if result.returncode:
        if 'Failed to initialize cache' in result.stderr:
            raise ValueError('uv 无法写入工作台测试缓存，请检查项目目录权限。')
        raise ValueError('本机签名接口测试程序启动失败，请检查 Python 3.11 和服务依赖。')
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise ValueError('本机签名接口测试程序返回了无效结果。') from None
    if response.get('ok') is not True:
        raise ValueError(response['message'])
    return response


def valid_task_id(value):
    return isinstance(value, str) and re.fullmatch(r'\d{10,20}', value) is not None


def saved_avatar_task(path, key_id, operation):
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if isinstance(state, dict) and state.get('keyId') == key_id and state.get('reqKey') == operation:
        task_id = state.get('taskId')
        if valid_task_id(task_id):
            return task_id
    return None


def recent_avatar_task(app, operation):
    for row in reversed(app.queue.data()['items']):
        if row.get('kind') != 'video' or not row.get('job'):
            continue
        for path in Path(row['job']).glob('runs/avatar-*/run.json'):
            if not path.resolve().is_relative_to(app.project):
                continue
            try:
                state = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            ident = state.get('task_id', '')
            if state.get('config', {}).get('avatar', {}).get('req_key') == operation and valid_task_id(str(ident)):
                return str(ident)
    return None


PREVIEW_TEXT_LIMIT = 200
PREVIEW_AUDIO_LIMIT = 2_000_000


def person_audio(app, cfg, person, text, fields, main_key=None):
    if not person.get('speaker'):
        raise ValueError('请先为人物绑定音色。')
    provider = person.get('voice_provider') or 'volcengine'
    dialect = (person.get('voice_dialect') or '').strip() or None
    if provider in ('aliyun', 'minimax'):
        target = cfg['connections'].get(provider, {})
        raw = fields.get(provider + '_api_key', '')
        label = '阿里百炼' if provider == 'aliyun' else 'MiniMax'
        if not isinstance(raw, str) or len(raw) > 512 or any(c in raw for c in '\r\n'):
            raise ValueError(f'{label} API Key 格式不正确。')
        key = raw.strip() or app.pc.connection_keys(target)[0]
        if provider == 'aliyun':
            from aliyun_voice import synthesize
            workspace = person.get('aliyun_workspace') or fields.get('aliyun_workspace') or target.get('workspace')
            return synthesize(text, person['speaker'], person.get('aliyun_model'), person.get('speech_rate') or 0, key, workspace, dialect=dialect)[0]
        from minimax_voice import synthesize
        model = fields.get('minimax_model') or cfg.get('settings', {}).get('minimax_model', 'speech-2.8-hd')
        return synthesize(text, person['speaker'], model, person.get('speech_rate') or 0, key, dialect=dialect)[0]
    if dialect:
        from voice_dialects import validate_dialect
        validate_dialect(dialect, 'volcengine')
    connection_name = person.get('connection') or 'main'
    connection = cfg['connections'].get(connection_name, {})
    key = (main_key if connection_name == 'main' and main_key is not None
           else app.pc.connection_keys(connection)[0])
    if not key:
        raise ValueError('该人物绑定的语音连接缺少 API Key。')
    media = json.loads((app.skill / 'modules/digital-human/config.json').read_text(encoding='utf-8'))
    defaults = json.loads((app.skill / 'preset.json').read_text(encoding='utf-8'))['voice']
    voice = {**media['tts'], **defaults, **cfg.get('settings', {}).get('voice', {}), **connection.get('tts', {})}
    voice.update(endpoint='https://openspeech.bytedance.com/api/v3/tts/unidirectional',
                 speaker=person['speaker'], speech_rate=person.get('speech_rate', voice['speech_rate']),
                 custom_speaker_id=person.get('custom_speaker_id'))
    return speech(voice, key, text)


def preview_voice(app, ident, values):
    cfg, person = app.voice_training.load_person(ident)
    text = values.get('text', '')
    if not isinstance(text, str) or len(text.strip()) > PREVIEW_TEXT_LIMIT:
        raise ValueError(f'试听文案最多 {PREVIEW_TEXT_LIMIT} 个字符。')
    if not person.get('speaker'):
        raise ValueError('请先复刻或绑定音色，再试听。')
    expected = [person.get('voice_provider') or 'volcengine', person['speaker'],
                person.get('custom_speaker_id'), person.get('aliyun_model'),
                person.get('speech_rate'), person.get('voice_dialect') or '']
    if values.get('binding') != expected:
        raise ValueError('音色、方言或语速已更新，请刷新人物资料后再试听。')
    name = (person.get('name') or '你的数字人')[:12]
    text = text.strip() or f'大家好，我是{name}。很高兴和你见面。今天用这段话，给你听听我的声音和说话的节奏。'
    audio = person_audio(app, cfg, person, text, {})
    if not 0 < len(audio) <= PREVIEW_AUDIO_LIMIT:
        raise ValueError('试听音频为空或太长，请换一段较短的文案。')
    return audio


def speech(voice, key, text):
    request_params = {'speaker': voice['speaker'], 'model': voice['model'], 'text': text,
                      'audio_params': {'format': 'mp3', 'sample_rate': voice['sample_rate'],
                                       'speech_rate': voice['speech_rate'], 'bit_rate': voice['bit_rate']}}
    if voice.get('custom_speaker_id'):
        request_params['custom_speaker_id'] = voice['custom_speaker_id']
    request = urllib.request.Request(voice['endpoint'], json.dumps({
        'user': {'uid': 'montage-connection-test'}, 'req_params': request_params}).encode('utf-8'),
        {'Content-Type': 'application/json', 'X-Api-Key': key, 'X-Api-Resource-Id': voice['resource_id'],
         'X-Api-Request-Id': secrets.token_hex(16)})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
            messages = [json.loads(line) for line in response.read(PREVIEW_AUDIO_LIMIT * 2).splitlines() if line.strip()]
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError(f'语音服务返回 HTTP {code}，请检查 Key、资源和音色权限。') from None
    except (OSError, ValueError):
        raise ValueError('语音合成未取得有效响应，请检查网络和语音配置。') from None
    error = next((x.get('code') for x in messages if x.get('code') not in (None, 0, 20000000)), None)
    try:
        audio = b''.join(base64.b64decode(x['data'], validate=True) for x in messages if x.get('data'))
    except (ValueError, TypeError):
        raise ValueError('语音服务返回的音频数据无效。') from None
    if error is not None or not audio:
        details = f'（服务代码 {error}）' if isinstance(error, int) else ''
        raise ValueError('语音合成失败，请检查语音资源和音色绑定' + details + '。')
    return audio


def signed_probe(kind, module):
    values = json.load(sys.stdin)
    if kind == 'voice_slots':
        from voice_slots import signed_list
        try:
            return {'ok': True, 'slots': signed_list(values['ak'], values['sk'], values['project'])}
        except ValueError as error:
            return {'ok': False, 'message': str(error)}
        except OSError:
            return {'ok': False, 'message': '查询火山音色列表超时或网络中断。'}
    os.environ.update(VOLC_ACCESSKEY=values['ak'], VOLC_SECRETKEY=values['sk'], MONTAGE_MANAGED_CREDENTIALS='1')
    sys.path.insert(0, str(module / 'scripts'))
    from avatar_client import AvatarClient
    try:
        api = AvatarClient(values['media'])
        if kind == 'storage':
            api.storage_client().head_bucket(values['media']['storage']['bucket'])
            message = 'TOS 存储桶查询通过。已验证访问权限。'
        else:
            ident = values.get('taskId')
            if not valid_task_id(ident):
                return {'ok': False, 'message': '查询数字人接口需要真实任务编号。'}
            reply = api.query(ident)
            if reply.get('code') != 10000:
                code = reply.get('code')
                if code in (50500, 50501):
                    raise ValueError(f'数字人任务查询失败：服务暂未返回可验证的结果（{code}），请稍后重试。')
                suffix = f'（服务代码 {code}）' if isinstance(code, int) else ''
                raise ValueError('数字人任务查询失败，请检查 AK/SK 和接口权限' + suffix + '。')
            message = '数字人任务查询通过。已验证 AK/SK 与查询权限。'
        return {'ok': True, 'message': message}
    except ValueError as exc:
        if str(exc).startswith('数字人任务查询失败'):
            return {'ok': False, 'message': str(exc)}
    except Exception:
        pass
    return {'ok': False, 'message': '签名接口请求失败，请检查网络、区域及服务权限。'}


if __name__ == '__main__':
    print(json.dumps(signed_probe(sys.argv[1], Path(sys.argv[2])), ensure_ascii=False))
