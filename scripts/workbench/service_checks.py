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
        if not person.get('speaker') or values.get('provider', provider) != provider:
            raise ValueError('请先选择已绑定此平台音色的人物。')
        if provider == 'minimax':
            from minimax_voice import MODELS, synthesize
            raw = fields.get('minimax_api_key', '')
            if not isinstance(raw, str) or len(raw) > 512 or any(c in raw for c in '\r\n'):
                raise ValueError('MiniMax API Key 格式不正确。')
            key = raw.strip() or app.pc.connection_keys(cfg['connections'].get('minimax', {}))[0]
            model = fields.get('minimax_model') or cfg.get('settings', {}).get('minimax_model', 'speech-2.8-hd')
            if model not in MODELS:
                raise ValueError('请选择支持的 MiniMax 合成模型。')
            synthesize('连接测试。', person['speaker'], model, person.get('speech_rate', 0), key)
            return {'ok': True, 'message': 'MiniMax 语音合成通过。'}
        person_connection = person.get('connection')
        if person_connection and person_connection != 'main':
            connection = cfg['connections'].get(person_connection, {})
            credentials['api_key'] = app.pc.connection_keys(connection)[0]
        defaults = json.loads((app.skill / 'preset.json').read_text(encoding='utf-8'))['voice']
        voice = {**media['tts'], **defaults, **cfg.get('settings', {}).get('voice', {}), **connection.get('tts', {})}
        voice.update(endpoint='https://openspeech.bytedance.com/api/v3/tts/unidirectional',
                     speaker=person['speaker'], speech_rate=person.get('speech_rate', voice['speech_rate']),
                     custom_speaker_id=person.get('custom_speaker_id'))
        if not credentials['api_key']:
            raise ValueError('该人物绑定的语音连接缺少 API Key。')
        media['tts'] = voice
        return speech(media, credentials['api_key'])
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


def speech(media, key):
    voice = media['tts']
    request_params = {'speaker': voice['speaker'], 'model': voice['model'], 'text': '连接测试',
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
            messages = [json.loads(line) for line in response.read(400_000).splitlines() if line.strip()]
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError(f'语音服务返回 HTTP {code}，请检查 Key、资源和音色权限。') from None
    except (OSError, ValueError):
        raise ValueError('语音测试未取得有效响应，请检查网络和语音配置。') from None
    error = next((x.get('code') for x in messages if x.get('code') not in (None, 0, 20000000)), None)
    try:
        audio = b''.join(base64.b64decode(x['data'], validate=True) for x in messages if x.get('data'))
    except (ValueError, TypeError):
        raise ValueError('语音服务返回的音频数据无效。') from None
    if error is not None or not audio:
        details = f'（服务代码 {error}）' if isinstance(error, int) else ''
        raise ValueError('测试配音失败，请检查语音资源和音色绑定' + details + '。')
    return {'ok': True, 'message': '语音连接正常，已用所选音色合成短句。'}


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
