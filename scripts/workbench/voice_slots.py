"""Read the provider's prepaid voice inventory and describe local reservations."""
import json
import re
import time
import urllib.error
import urllib.request

HOST = 'open.volcengineapi.com'
CONSOLE = 'https://console.volcengine.com/speech/new/overview'


def project_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value):
        raise ValueError('请填写火山控制台左上角的项目名称。')
    return value


def signed_list(ak, sk, project):
    from volcengine.auth.SignerV4 import SignerV4
    from volcengine.base.Request import Request
    from volcengine.Credentials import Credentials

    rows = []
    for page in range(1, 101):
        request = Request()
        request.schema, request.method, request.host, request.path = 'https', 'POST', HOST, '/'
        request.query = {'Action': 'BatchListMegaTTSTrainStatus', 'Version': '2025-05-21'}
        request.headers = {'Host': HOST, 'Content-Type': 'application/json'}
        body = {'ProjectName': project_name(project), 'PageNumber': page, 'PageSize': 100}
        request.body = json.dumps(body, separators=(',', ':'))
        SignerV4.sign(request, Credentials(ak, sk, 'speech_saas_prod', 'cn-beijing'))
        from model_api import NoRedirect
        signed = urllib.request.Request(request.build(), request.body.encode(), request.headers, method='POST')
        try:
            with urllib.request.build_opener(NoRedirect()).open(signed, timeout=12) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            error.close()
            raise ValueError(f'火山返回 HTTP {error.code}，请检查 AK/SK 的音色列表权限和项目名称。') from None
        result = data.get('Result')
        if not isinstance(result, dict) or data.get('ResponseMetadata', {}).get('Error'):
            raise ValueError('火山未返回音色槽位，请检查项目名称、服务和读取权限。')
        batch = result.get('Statuses') or []
        rows.extend(batch)
        total = result.get('TotalCount')
        if (type(total) is int and len(rows) >= total) or len(batch) < 100:
            return summarize(rows)
    raise ValueError('火山音色列表未能完整读取，请稍后重试。')


def summarize(rows):
    status_text = {'Unknown': '空槽位', 'Training': '复刻中', 'Success': '已有音色',
                   'Active': '已锁定', 'Expired': '已过期', 'Reclaimed': '已回收'}
    now = time.time() * 1000
    slots = {}
    for row in rows:
        ident = row.get('SpeakerID')
        if not isinstance(ident, str) or not re.fullmatch(r'S_[A-Za-z0-9_-]+', ident):
            continue
        state = row.get('State')
        remaining = row.get('AvailableTrainingTimes')
        valid = not row.get('ExpireTime') or row['ExpireTime'] > now
        allowed = valid and type(remaining) is int and remaining > 0 and state in ('Unknown', 'Success')
        slots[ident] = {'id': ident, 'status': status_text.get(state, '不可用') if valid else '已过期',
                        'empty': allowed and state == 'Unknown', 'selectable': allowed,
                        'remaining': remaining if type(remaining) is int else None}
    return sorted(slots.values(), key=lambda x: (not x['empty'], not x['selectable'], x['id']))
