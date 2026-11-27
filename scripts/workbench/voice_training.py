"""Background voice registration and polling for the local workbench."""
import base64
import re
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path
from urllib.parse import urlsplit

import voice_clone
import minimax_voice
import aliyun_voice


MAX_AUDIO_BYTES = 10_000_000
AUDIO_FORMATS = {'.wav': 'wav', '.mp3': 'mp3', '.m4a': 'mov', '.aac': 'aac', '.flac': 'flac', '.ogg': 'ogg'}
ACTIVE = {'queued', 'checking', 'submitting', 'training'}
UNSENT = {'prepared', 'workbench_queued'}
PERSON_ID = r'[A-Za-z0-9_-]{1,80}'


def managed_postpaid(person, record):
    return (
        person.get('speaker') == record.get('speaker_id') == 'custom_speaker_id'
        and bool(person.get('custom_speaker_id'))
        and person['custom_speaker_id'] == record.get('custom_speaker_id')
        and record.get('connection', person.get('connection')) == person.get('connection')
    )


def voice_binding(person):
    return {field: person.get(field) for field in ('speaker', 'custom_speaker_id', 'voice_provider', 'connection', 'aliyun_model', 'aliyun_workspace')}


def matches_binding(person, snapshot):
    return all(person.get(field) == value for field, value in snapshot.items())


def can_replace_with_minimax(person):
    return bool(person.get('speaker') and (person.get('voice_provider') or 'volcengine') == 'volcengine')


def retryable_registration(record):
    return (record.get('phase') == 'retryable'
            or (record.get('status') == 3 and record.get('workbench', {}).get('state') == 'failed'))


def voice_http_status(message):
    # The shared voice client preserves the HTTP status in its sanitized error.
    match = re.search(r'声音服务HTTP (\d{3})；', message)
    return int(match[1]) if match else None


def permission_message(status, record):
    if status == 401:
        return '火山未接受当前语音 API Key。请在工作台设置里更新后，再点「复刻」。'
    if record.get('speaker_id') == 'custom_speaker_id':
        return '请在当前语音 API Key 所属的火山项目开通「声音复刻模型 2.0」和「后付费音色」，然后再点「复刻」。'
    return '请确认当前语音 API Key 所属的火山项目已开通声音复刻，并且可以使用所选音色，然后再点「复刻」。'


class VoiceTraining:
    default_mode = 'prepaid'

    def __init__(self, app, read, write, run):
        self.app, self.pc, self.read, self.write, self.run = app, app.pc, read, write, run
        self.worker = None

    def folder(self, ident):
        if not re.fullmatch(PERSON_ID, ident):
            raise ValueError('数字人标识无效。')
        root = self.pc.directory().resolve()
        folder = (root / 'people' / ident).resolve()
        if not folder.is_relative_to(root):
            raise ValueError('人物目录不在当前项目中。')
        return folder

    def path(self, ident):
        return self.folder(ident) / 'voice-training.json'

    def public(self, person):
        record = self.read(self.path(person['id']), {})
        state = record.get('workbench', {})
        phase = record.get('phase')
        sample = self.pc.asset_path(person.get('sample'))
        replacement = self.pc.asset_path(record.get('replacementSample'))
        has_replacement = bool(replacement and replacement.is_file())
        bound = bool(person.get('speaker'))
        retraining = bool(record.get('retraining'))
        managed = managed_postpaid(person, record)
        switching = bool(record.get('previousBinding'))
        state_name = state.get('state') or ('paused' if phase else 'idle')
        if bound and not switching and (not managed or not state.get('state')):
            state_name = 'ready'
        can_retrain = (managed and phase == 'ready'
                       and record.get('status') != 4 and state.get('state') not in ACTIVE)
        retryable = not bound and retryable_registration(record)
        can_query = bool(phase and phase not in UNSENT and not retryable and (not bound or retraining))
        has_sample = bool(sample and sample.is_file())
        demo = record.get('demo', {})
        replace_postpaid = not bound and record.get('speaker_id') == 'custom_speaker_id' and state_name not in ACTIVE
        needs_slot = not bound and (
            not phase or phase in UNSENT or retryable or replace_postpaid)
        message = state.get('message', '')
        http_status = state.get('httpStatus') or voice_http_status(message)
        needs_permission = state_name in ('failed', 'paused') and http_status in (401, 403)
        ali_started = record.get('provider') == 'aliyun' and phase != 'ready'
        ali_resuming = ali_started and phase in ('submitted', 'submission_unknown', 'confirmed')
        ali_sample = self.pc.asset_path(record.get('aliyunSample'))
        aliyun_clone = {
            'started': ali_started, 'model': record.get('model') if ali_started else None,
            'hasSample': has_sample or bool(ali_sample and ali_sample.is_file()),
            'canUpload': state_name not in ACTIVE and not ali_resuming and (not can_query or needs_slot or ali_started),
            'canStart': state_name not in ACTIVE and (not can_query or needs_slot or ali_started),
            'resuming': ali_resuming,
        }
        minimax_switch = None
        if can_replace_with_minimax(person):
            staged = self.pc.asset_path(record.get('minimaxSample'))
            mini_started = switching and record.get('provider') == 'minimax'
            can_edit = state_name not in ACTIVE and not ali_resuming and (
                not mini_started or phase in UNSENT or phase == 'retryable')
            minimax_switch = {
                'started': mini_started,
                'hasSample': has_sample or bool(staged and staged.is_file()),
                'canUpload': can_edit,
                'canStart': can_edit or (mini_started and phase == 'confirmed' and state_name not in ACTIVE),
            }
        return {
            'state': state_name,
            'message': permission_message(http_status, record) if needs_permission else message,
            'hasSample': has_sample,
            'canUpload': can_retrain or (not bound and (not phase or phase in UNSENT or retryable)
                                         and state.get('state') not in ACTIVE),
            'canRetry': retryable and has_sample,
            'needsPermission': needs_permission,
            'canStart': can_query or (not bound and has_sample),
            'canQuery': can_query,
            'needsSlot': needs_slot,
            'attemptProvider': record.get('provider', 'volcengine') if phase else None,
            'minimaxSwitch': minimax_switch,
            'aliyunClone': aliyun_clone,
            'replacePostpaid': replace_postpaid,
            'canRetrain': can_retrain,
            'hasReplacement': can_retrain and has_replacement,
            'demoAudio': demo.get('url', '') if not retraining and demo.get('expiresAt', 0) > time.time() else '',
        }

    def active(self, ident):
        return self.read(self.path(ident), {}).get('workbench', {}).get('state') in ACTIVE

    def load_person(self, ident):
        self.folder(ident)
        cfg = self.pc.load()
        person = cfg['people'].get(ident)
        if not person:
            raise ValueError('找不到该数字人。')
        return cfg, person

    def sample(self, person):
        path = self.pc.asset_path(person.get('sample'))
        if not path or not path.is_file():
            raise ValueError('请先上传这位数字人的录音。')
        if not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
            raise ValueError('复刻用录音须大于 0 且不超过 10 MB。')
        try:
            with wave.open(str(path)) as audio:
                if not audio.getnframes():
                    raise ValueError('录音文件为空。')
        except (wave.Error, EOFError):
            raise ValueError('工作台无法读取这段 WAV 录音，请重新上传。') from None
        return path

    def attach(self, ident, filename, data, provider=None):
        suffix = Path(filename).suffix.lower()
        if suffix not in self.app.people.AUDIO or not 0 < len(data) <= MAX_AUDIO_BYTES:
            raise ValueError('请选择不超过 10 MB 的 WAV、MP3、M4A、AAC、FLAC 或 OGG 录音。')
        cfg, person = self.load_person(ident)
        def upload_field(person):
            status = self.public(person)
            if provider == 'aliyun':
                return 'aliyunSample' if status['aliyunClone']['canUpload'] else None
            if provider == 'minimax' and status['minimaxSwitch'] is not None:
                return 'minimaxSample' if status['minimaxSwitch']['canUpload'] else None
            if status['canUpload']:
                return 'replacementSample' if person.get('speaker') else 'sample'

        field = upload_field(person)
        if not field:
            raise ValueError('这位数字人已有绑定的音色或正在查询复刻结果。')
        with tempfile.TemporaryDirectory(prefix='voice-', dir=self.app.data) as directory:
            source = Path(directory) / ('upload' + suffix)
            source.write_bytes(data)
            sample = source.with_name('sample.wav')
            self.normalize_sample(source, sample)
            with self.app.transaction():
                cfg, person = self.load_person(ident)
                if upload_field(person) != field:
                    raise ValueError('这位数字人的复刻状态已变化，请刷新后查看。')
                folder = self.folder(ident)
                folder.mkdir(parents=True, exist_ok=True)
                destination = folder / ('sample-' + uuid.uuid4().hex[:12] + '.wav')
                sample.replace(destination)
                relative_sample = str(destination.relative_to(self.pc.directory().resolve()))
                try:
                    if field != 'sample':
                        path = self.path(ident)
                        record = self.read(path, {})
                        record[field] = relative_sample
                        self.write(path, record)
                    else:
                        person['sample'] = relative_sample
                        self.pc.write(self.pc.directory() / 'project.json', cfg)
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                return self.public(person)

    def normalize_sample(self, source, sample):
        result = self.run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                           '-f', AUDIO_FORMATS[source.suffix], '-i', source, '-map', '0:a:0',
                           '-ar', '24000', '-ac', '1', '-c:a', 'pcm_s16le', '-fs',
                           str(MAX_AUDIO_BYTES + 1), sample], capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError('无法读取这个录音文件，请重新选择。')
        self.sample({'sample': str(sample)})

    def reserve(self, cfg, person, connection):
        mode = connection.get('clone_mode') or self.default_mode
        if mode == 'postpaid':
            return {'speaker_id': 'custom_speaker_id', 'custom_speaker_id': 'montage_' + uuid.uuid4().hex}
        if mode != 'prepaid':
            raise ValueError('请先在设置 → 制作服务中选择复刻方式。')
        used = {p.get('speaker') for p in cfg['people'].values()}
        for other in cfg['people']:
            if other != person['id']:
                used.add(self.read(self.path(other), {}).get('speaker_id'))
        speaker = next((s for s in connection.get('speaker_pool', []) if s and s not in used and s != 'custom_speaker_id'), None)
        if not speaker:
            raise ValueError('当前没有空闲预付费音色槽位，请在制作服务中填写未占用的槽位 ID。')
        return {'speaker_id': speaker}

    def available_slots(self, ident=None, project=None):
        from service_checks import check
        slots = check(self.app, {'kind': 'voice_slots', 'project': project})['slots']
        cfg = self.pc.load()
        owners = {person['speaker']: person['name'] for person in cfg['people'].values()
                  if person.get('speaker') and (person.get('voice_provider') or 'volcengine') == 'volcengine'}
        busy = self.reserved_slots(cfg, ident)
        return [{**slot, 'owner': owners.get(slot['id']), 'empty': slot['empty'] and slot['id'] not in owners,
                 'selectable': slot['selectable'] and slot['id'] not in busy,
                 'status': '本机复刻中' if slot['id'] in busy else slot['status']} for slot in slots]

    def reserved_slots(self, cfg, ident):
        reserved = set()
        for other in cfg['people']:
            if other == ident:
                continue
            record = self.read(self.path(other), {})
            if self.active(other) or (record.get('phase') in ('submission_unknown', 'submitted')
                                      and record.get('workbench', {}).get('state') == 'paused'):
                reserved.add(record.get('speaker_id'))
        return reserved

    def select_slot(self, cfg, ident, slots, speaker, overwrite):
        reserved = self.reserved_slots(cfg, ident)
        owners = {p['speaker']: p['id'] for p in cfg['people'].values()
                  if p.get('speaker') and (p.get('voice_provider') or 'volcengine') == 'volcengine'}
        options = [slot for slot in slots if slot['selectable'] and slot['id'] not in reserved]
        if speaker:
            choice = next((slot for slot in options if slot['id'] == speaker), None)
            if choice is None:
                raise ValueError('这个槽位现在不可用，请刷新后选另一个。')
        else:
            choice = next((slot for slot in options if slot['empty'] and slot['id'] not in owners), None)
            if choice is None:
                raise ValueError('没有可用空槽位，请选择一个可以覆盖的槽位，或前往火山购买。')
        current = owners.get(choice['id'])
        covers = not choice['empty'] or bool(current)
        if covers and overwrite is not True:
            raise ValueError('继续会覆盖原音色，请先确认。')
        return {'speaker_id': choice['id'], 'overwrite': covers, 'slotOwner': current}

    def requested_provider(self, cfg, person, provider):
        if provider not in (None, 'minimax', 'volcengine', 'aliyun'):
            raise ValueError('请选择支持的语音平台。')
        return provider or person.get('voice_provider') or cfg.get('settings', {}).get('voice_provider', 'volcengine')

    def start_minimax_switch(self, cfg, person, path, record):
        options = self.public(person)['minimaxSwitch']
        if not options or not options['canStart']:
            raise ValueError('请先完成正在处理的复刻。若 MiniMax 上次提交未确认，请核对控制台。')
        phase = record.get('phase') if options['started'] else None
        if phase != 'confirmed':
            key = self.pc.connection_keys(cfg['connections'].get('minimax', {}))[0]
            if not key:
                raise ValueError('请先在语音合成与复刻设置保存 MiniMax API Key。')
            self.sample({'sample': record.get('minimaxSample') or person.get('sample')})
            if record.get('phase') and not options['started']:
                self.write(path.with_name('voice-training-before-minimax.json'), record)
            record = {
                'provider': 'minimax', 'phase': 'workbench_queued',
                'speaker_id': record['speaker_id'] if options['started'] else 'montage-' + uuid.uuid4().hex,
                'previousBinding': voice_binding(person), 'minimaxSample': record.get('minimaxSample'),
            }
        now = time.time()
        record['workbench'] = {'state': 'queued', 'nextAt': now, 'until': now + 600,
                               'message': '正在绑定 MiniMax 音色' if phase == 'confirmed' else '等待提交 MiniMax 录音'}
        self.write(path, record)
        return self.public(person)

    def start_aliyun(self, cfg, person, path, record, requested_model):
        options = self.public(person)['aliyunClone']
        if not options['canStart']:
            raise ValueError('请先完成正在处理的复刻。')
        conn = cfg['connections'].get('aliyun', {})
        if not self.pc.connection_keys(conn)[0]:
            raise ValueError('请先在设置保存阿里百炼 API Key。')
        if options['resuming']:
            if not matches_binding(person, record.get('previousBinding', {})):
                raise ValueError('人物音色已变化，请检查当前绑定。')
            if requested_model and requested_model != record['model']:
                raise ValueError('请先用上次所选模型继续复刻。')
        else:
            model = aliyun_voice.model_id(requested_model or person.get('aliyun_model') or
                                          cfg.get('settings', {}).get('aliyun_model', aliyun_voice.DEFAULT_MODEL))
            workspace = aliyun_voice.workspace_id(conn.get('workspace'))
            self.sample({'sample': record.get('aliyunSample') or person.get('sample')})
            new = {'provider': 'aliyun', 'model': model, 'workspace': workspace,
                   'phase': 'workbench_queued', 'prefix': 'wb' + uuid.uuid4().hex[:8],
                   'aliyunSample': record.get('aliyunSample')}
            if person.get('speaker'):
                new['previousBinding'] = voice_binding(person)
            record = new
        now = time.time()
        record['workbench'] = {'state': 'queued', 'nextAt': now, 'until': now + 600,
                               'message': '等待阿里百炼复刻' if record['phase'] in UNSENT else '正在恢复阿里百炼复刻'}
        self.write(path, record)
        return self.public(person)

    def start(self, ident, slots=None, speaker=None, overwrite=False, requested=None, model=None):
        with self.app.transaction():
            cfg, person = self.load_person(ident)
            if self.active(ident):
                return self.public(person)
            path = self.path(ident)
            record = self.read(path, {})
            configured = self.requested_provider(cfg, person, requested)
            ali_pending = self.public(person)['aliyunClone']['resuming']
            if configured == 'aliyun' or ali_pending:
                if configured != 'aliyun':
                    raise ValueError('还有阿里百炼复刻待确认，请在人物中选择阿里百炼继续。')
                return self.start_aliyun(cfg, person, path, record, model)
            if configured == 'minimax' and can_replace_with_minimax(person) and slots is None:
                return self.start_minimax_switch(cfg, person, path, record)
            if person.get('speaker') and not self.public(person)['canQuery']:
                return self.public(person)
            connection = cfg['connections'].get(person.get('connection'), {})
            phase = record.get('phase')
            retryable = not person.get('speaker') and retryable_registration(record)
            replace_postpaid = configured == 'minimax' and self.public(person)['replacePostpaid']
            initial = not phase or phase in UNSENT or retryable or replace_postpaid
            provider = configured if initial or slots is not None else record.get('provider', 'volcengine')
            voice_connection = cfg['connections'].get('minimax', {}) if provider == 'minimax' else connection
            if not self.pc.connection_keys(voice_connection)[0]:
                service = 'MiniMax' if provider == 'minimax' else '火山'
                raise ValueError(f'请先在语音合成与复刻设置保存{service} API Key。')
            resume = phase in ('submission_unknown', 'submitted') and record.get('workbench', {}).get('state') == 'paused'
            if initial or slots is not None:
                self.sample(person)
                if slots is not None:
                    if not self.public(person)['needsSlot'] or provider != 'volcengine':
                        raise ValueError('已经有复刻任务在处理，请等结果返回。')
                    identity = self.select_slot(cfg, ident, slots, speaker, overwrite)
                    if record.get('speaker_id') == 'custom_speaker_id':
                        self.write(path.with_name('voice-training-previous-postpaid.json'), record)
                    record = {**identity, 'phase': 'workbench_queued', 'connection': person.get('connection')}
                    resume = False
                elif provider == 'minimax':
                    if replace_postpaid:
                        self.write(path.with_name('voice-training-previous-postpaid.json'), record)
                    same = record.get('provider') == 'minimax'
                    record = {'provider': 'minimax', 'speaker_id': record['speaker_id'] if same else 'montage-' + uuid.uuid4().hex,
                              'phase': 'workbench_queued', 'connection': person.get('connection')}
                elif phase != 'prepared':
                    identity = self.reserve(cfg, person, connection)
                    if (retryable and identity['speaker_id'] == record.get('speaker_id') == 'custom_speaker_id'
                            and record.get('connection', person.get('connection')) == person.get('connection')
                            and record.get('custom_speaker_id')):
                        identity['custom_speaker_id'] = record['custom_speaker_id']
                    record = {**identity, 'phase': 'workbench_queued', 'connection': person.get('connection')}
            now = time.time()
            record['workbench'] = {'state': 'queued', 'nextAt': now, 'until': now + 600, 'resume': resume,
                                   'message': '等待提交录音' if record['phase'] in UNSENT else '正在恢复上一次复刻'}
            self.write(path, record)
            return self.public(person)

    def retrain(self, ident):
        with self.app.transaction():
            cfg, person = self.load_person(ident)
            status = self.public(person)
            if self.active(ident):
                return status
            if not status['canRetrain']:
                raise ValueError('这个音色当前不能重练。已正式合成的后付费音色会锁定。')
            if not status['hasReplacement']:
                raise ValueError('请先上传一段新录音。')
            if not self.pc.connection_keys(cfg['connections'].get(person.get('connection'), {}))[0]:
                raise ValueError('请先在制作服务中保存语音 API Key。')
            path = self.path(ident)
            record = self.read(path)
            self.sample({'sample': record['replacementSample']})
            now = time.time()
            record.update(phase='workbench_queued', retraining=True,
                          workbench={'state': 'queued', 'nextAt': now, 'until': now + 600,
                                     'message': '等待检查这个音色能否重练'})
            self.write(path, record)
            return self.public(person)

    def tick(self):
        if self.worker and self.worker.is_alive():
            return
        now = time.time()
        with self.app.transaction():
            due = []
            for ident in self.pc.load()['people']:
                path = self.path(ident)
                record = self.read(path, {})
                state = record.get('workbench', {})
                if state.get('state') in ACTIVE and state.get('nextAt', 0) <= now:
                    due.append((state.get('nextAt', 0), ident, path, record))
            if not due:
                return
            _, ident, path, record = min(due)
            ticket = uuid.uuid4().hex
            record['workbench'].update(ticket=ticket, nextAt=now + 75)
            self.write(path, record)
        self.worker = threading.Thread(target=self.step, args=(ident, path, record, ticket), daemon=True)
        self.worker.start()

    def step(self, ident, path, record, ticket):
        state = record['workbench']
        submitting = False
        try:
            cfg, person = self.load_person(ident)
            is_minimax = record.get('provider') == 'minimax'
            is_aliyun = record.get('provider') == 'aliyun'
            phase = record['phase']
            identity = {k: record[k] for k in ('speaker_id', 'custom_speaker_id') if record.get(k)}
            if (is_minimax or is_aliyun) and phase == 'confirmed':
                self.bind(ident, path, record, ticket, identity)
                return
            if record.get('previousBinding') and not matches_binding(person, record['previousBinding']):
                self.finish(path, record, ticket, 'failed', '人物音色已变化，请检查当前绑定。')
                return
            if person.get('speaker') and not record.get('previousBinding') and not (record.get('retraining') and managed_postpaid(person, record)):
                if record.get('retraining'):
                    self.end_retrain(path, record, ticket, '人物的音色 ID 已变化，请检查当前绑定。')
                else:
                    self.finish(path, record, ticket, 'ready', '音色已绑定')
                return
            connection = record['provider'] if is_minimax or is_aliyun else person.get('connection')
            key = self.pc.connection_keys(cfg['connections'].get(connection, {}))[0]
            if not key:
                raise ValueError('语音 API Key 已失效，请补齐后继续。')
            if is_aliyun:
                if phase in UNSENT:
                    audio = aliyun_voice.sample_audio(self.sample({'sample': record.get('aliyunSample') or person.get('sample')}))
                    record['phase'] = 'submission_unknown'
                    if not self.finish(path, record, ticket, 'submitting', '正在提交阿里百炼复刻', delay=75):
                        return
                    submitting = True
                    record['speaker_id'] = aliyun_voice.create(record['workspace'], key, record['model'], record['prefix'], audio)
                    record['phase'] = 'submitted'
                    self.finish(path, record, ticket, 'training', '等待阿里百炼确认音色', delay=2)
                else:
                    self.query_aliyun(ident, path, record, ticket, key)
            elif is_minimax:
                if phase not in UNSENT:
                    self.finish(path, record, ticket, 'paused', 'MiniMax 未确认上次提交，请先在 MiniMax 控制台核对音色。')
                    return
                if not record.get('fileId'):
                    sample = record.get('minimaxSample') or person.get('sample')
                    record['fileId'] = minimax_voice.upload(self.sample({'sample': sample}), key)
                record['phase'] = 'submission_unknown'
                if not self.finish(path, record, ticket, 'submitting', '正在提交 MiniMax 复刻', delay=120):
                    return
                submitting = True
                minimax_voice.clone(record['fileId'], identity['speaker_id'], key)
                record['phase'] = 'confirmed'
                if self.finish(path, record, ticket, 'queued', '复刻成功，正在绑定音色', delay=75):
                    self.bind(ident, path, record, ticket, identity)
            elif phase == 'ready':
                self.bind(ident, path, record, ticket, identity)
            elif phase in UNSENT:
                if identity['speaker_id'] != 'custom_speaker_id' and time.time() - record.get('slotCheckedAt', 0) > 60:
                    self.check_slot(path, record, ticket, identity, key)
                    return
                sample = person
                if record.get('retraining'):
                    reply = voice_clone.call('get_voice', identity, key)
                    status = reply.get('status')
                    record['status'] = status
                    if status == 4:
                        self.end_retrain(path, record, ticket, '这个音色已经正式合成过，不能再用原 ID 重练。')
                        return
                    if status not in (0, 2, 3):
                        raise ValueError('还不能确认这个音色可重练，请稍后再试。')
                    record['previousVoice'] = {k: reply.get(k) for k in ('status', 'create_time', 'version')}
                    sample = {'sample': record['replacementSample']}
                payload = {**identity, 'audio': {'data': base64.b64encode(self.sample(sample).read_bytes()).decode(), 'format': 'wav'}}
                record['phase'] = 'submission_unknown'
                if not self.finish(path, record, ticket, 'submitting', '正在提交录音', delay=75):
                    return
                submitting = True
                voice_clone.call('voice_clone', payload, key)
                record['phase'] = 'submitted'
                self.finish(path, record, ticket, 'training', '复刻请求已提交，等待服务返回状态', delay=1)
            else:
                self.query(ident, path, record, ticket, identity, key)
        except Exception as error:
            reason = self.app.redact(str(error))[:350]
            http_status = voice_http_status(reason)
            state['httpStatus'] = http_status
            if submitting and record['phase'] == 'submission_unknown' and (
                    http_status in (400, 401, 403, 422) or isinstance(error, (minimax_voice.Rejected, aliyun_voice.Rejected))):
                self.fail_registration(path, record, ticket, '复刻请求被拒绝。' + reason)
            elif record.get('phase') in UNSENT:
                if record.get('retraining'):
                    self.end_retrain(path, record, ticket, reason)
                else:
                    self.finish(path, record, ticket, 'failed', reason)
            elif submitting and record.get('phase') == 'submission_unknown':
                self.finish(path, record, ticket, 'training', '等待核对提交结果。' + reason, delay=5)
            elif http_status in (401, 403):
                self.finish(path, record, ticket, 'paused', reason)
            else:
                failures = state.get('queryErrors', 0) + 1
                state['queryErrors'] = failures
                polling = failures < 3 and time.time() < state['until']
                message = '复刻进度暂未更新。' if polling else '复刻暂时中断，再点一次「复刻」即可恢复。'
                self.finish(path, record, ticket, 'training' if polling else 'paused', message + reason, delay=6 if polling else 0)

    def query_aliyun(self, ident, path, record, ticket, key):
        workspace = record['workspace']
        if not record.get('speaker_id'):
            voice = aliyun_voice.find_voice(workspace, key, record['model'], record['prefix'])
            if not voice:
                if time.time() >= record['workbench']['until']:
                    self.finish(path, record, ticket, 'paused', '上次提交未确认。点「复刻」继续核对阿里百炼。')
                else:
                    self.finish(path, record, ticket, 'training', '正在核对阿里百炼上次提交', delay=8)
                return
            record.update(speaker_id=voice, phase='submitted')
            self.finish(path, record, ticket, 'training', '找到阿里百炼音色，等待确认')
            return
        status = aliyun_voice.enrollment(workspace, key, 'query_voice', voice_id=record['speaker_id'])
        if status.get('target_model') != record['model']:
            self.fail_registration(path, record, ticket, '阿里百炼返回的音色模型与选择不符。')
        elif status.get('status') == 'OK':
            record['phase'] = 'confirmed'
            if self.finish(path, record, ticket, 'queued', '复刻成功，正在绑定音色', delay=75):
                self.bind(ident, path, record, ticket, {'speaker_id': record['speaker_id']})
        elif status.get('status') == 'UNDEPLOYED':
            self.fail_registration(path, record, ticket, '阿里百炼未通过录音审核，换一段录音可重试。')
        elif time.time() >= record['workbench']['until']:
            self.finish(path, record, ticket, 'paused', '阿里百炼仍在处理，点「复刻」可继续确认。')
        else:
            self.finish(path, record, ticket, 'training', '阿里百炼正在审核音色', delay=4)

    def check_slot(self, path, record, ticket, identity, key):
        if not self.finish(path, record, ticket, 'checking', '正在检查预付费槽位', delay=75):
            return
        reply = voice_clone.call('get_voice', identity, key)
        allowed = (0, 2, 3) if record.get('overwrite') else (0, 3)
        if reply.get('status') not in allowed or reply.get('available_training_times') == 0:
            self.fail_registration(path, record, ticket, '这个槽位的状态已经变化，请重新选择。')
            return
        if record.get('overwrite'):
            record['previousVoice'] = {k: reply.get(k) for k in ('status', 'create_time', 'version', 'available_training_times')}
        record['slotCheckedAt'] = time.time()
        self.finish(path, record, ticket, 'queued', '槽位可用，等待提交录音')

    def query(self, ident, path, record, ticket, identity, key):
        reply = voice_clone.call('get_voice', identity, key)
        status = reply.get('status')
        record['status'] = status
        state = record['workbench']
        state.pop('queryErrors', None)
        state.pop('httpStatus', None)
        if state.get('resume') and status == 0 and record['phase'] == 'submission_unknown':
            state.pop('resume')
            record['phase'] = 'workbench_queued'
            record.pop('slotCheckedAt', None)
            self.finish(path, record, ticket, 'queued', '准备提交本地录音')
            return
        if status == 1 and record['phase'] == 'submission_unknown':
            record['phase'] = 'submitted'
        if (record.get('retraining') or record.get('overwrite')) and record['phase'] == 'submission_unknown':
            if status == 4:
                self.fail_registration(path, record, ticket, '火山显示音色已锁定，无法确认本次覆盖。')
                return
            # A lost response can leave the old successful version visible under the same ID.
            previous = record.get('previousVoice', {})
            changed = any(previous.get(k) is not None and reply.get(k) is not None and reply[k] != previous[k]
                          for k in ('create_time', 'version', 'available_training_times'))
            if status == 1 or (status in (2, 3) and (status != previous.get('status') or changed)):
                record['phase'] = 'submitted'
            else:
                polling = time.time() < record['workbench']['until']
                self.finish(path, record, ticket, 'training' if polling else 'paused',
                            '暂未确认这次重练是否提交成功。' if polling else '这次重练暂时中断，再点「复刻」可恢复。', delay=5 if polling else 0)
                return
        if status in (2, 4):
            self.save_demo(record, reply)
            self.bind(ident, path, record, ticket, identity)
        elif status == 3:
            self.fail_registration(path, record, ticket, '火山报告复刻失败，可以更换录音后再试。')
        elif time.time() >= record['workbench']['until']:
            self.finish(path, record, ticket, 'paused', '复刻暂时中断，再点一次「复刻」即可恢复。')
        else:
            progress = '语音服务正在复刻' if status == 1 else '等待语音服务确认任务'
            self.finish(path, record, ticket, 'training', progress, delay=5)

    def fail_registration(self, path, record, ticket, message):
        if record.get('retraining'):
            self.end_retrain(path, record, ticket, message)
        else:
            record['phase'] = 'retryable'
            self.finish(path, record, ticket, 'failed', message)

    def end_retrain(self, path, record, ticket, message):
        record.pop('retraining', None)
        record.pop('previousVoice', None)
        record['phase'] = 'ready'
        self.finish(path, record, ticket, 'failed', message)

    def save_demo(self, record, reply):
        record.pop('demo', None)
        for voice in reply.get('speaker_status') or []:
            url = voice.get('demo_audio')
            if isinstance(url, str) and url.startswith('https://') and urlsplit(url).hostname:
                record['demo'] = {'url': url, 'expiresAt': time.time() + 3600}
                return

    def bind(self, ident, path, record, ticket, identity):
        with self.app.transaction():
            if self.read(path, {}).get('workbench', {}).get('ticket') != ticket:
                return
            cfg, person = self.load_person(ident)
            if record.get('retraining'):
                person['sample'] = record['replacementSample']
                self.pc.write(self.pc.directory() / 'project.json', cfg)
                for field in ('retraining', 'replacementSample', 'previousVoice'):
                    record.pop(field, None)
                message = '重练完成，继续使用原来的音色 ID。'
            elif record.get('provider') == 'aliyun':
                voice_id = identity['speaker_id']
                if person.get('voice_provider') != 'aliyun' or person.get('speaker') != voice_id:
                    prior = record.get('previousBinding')
                    if (prior and not matches_binding(person, prior)) or (not prior and person.get('speaker')):
                        self.finish(path, record, ticket, 'failed', '人物音色已变化，请检查当前绑定。')
                        return
                    person.update(speaker=voice_id, custom_speaker_id=None, status='ready', voice_provider='aliyun',
                                  aliyun_model=record['model'], aliyun_workspace=record['workspace'])
                    from voice_dialects import supported_dialects
                    if person.get('voice_dialect') and person['voice_dialect'] not in supported_dialects('aliyun', record['model']):
                        person.pop('voice_dialect', None)
                    if record.get('aliyunSample'):
                        person['sample'] = record['aliyunSample']
                    cfg['default_person'] = cfg.get('default_person') or ident
                    self.pc.write(self.pc.directory() / 'project.json', cfg)
                record.pop('previousBinding', None)
                record.pop('aliyunSample', None)
                message = '阿里百炼复刻完成，已绑定音色'
            elif record.get('previousBinding'):
                voice_id = identity['speaker_id']
                if person.get('voice_provider') != 'minimax' or person.get('speaker') != voice_id:
                    if not matches_binding(person, record['previousBinding']):
                        self.finish(path, record, ticket, 'failed', '人物音色已变化，请检查当前绑定。')
                        return
                    person.update(speaker=voice_id, custom_speaker_id=None, status='ready', voice_provider='minimax')
                    person.pop('aliyun_model', None)
                    person.pop('aliyun_workspace', None)
                    from voice_dialects import supported_dialects
                    mm_model = cfg.get('settings', {}).get('minimax_model', 'speech-2.8-hd')
                    if person.get('voice_dialect') and person['voice_dialect'] not in supported_dialects('minimax', mm_model):
                        person.pop('voice_dialect', None)
                    if record.get('minimaxSample'):
                        person['sample'] = record['minimaxSample']
                    self.pc.write(self.pc.directory() / 'project.json', cfg)
                record.pop('previousBinding', None)
                record.pop('minimaxSample', None)
                message = 'MiniMax 复刻完成，已切换为新音色'
            else:
                if not person.get('speaker') and (record.get('slotOwner') or record.get('provider') == 'minimax'):
                    owner = cfg['people'].get(record.get('slotOwner'))
                    if owner and owner['id'] != ident and owner.get('speaker') == identity['speaker_id']:
                        owner.update(speaker=None, custom_speaker_id=None, status='needs_voice')
                        owner.pop('voice_provider', None)
                        old_path = self.path(owner['id'])
                        self.write(old_path, {'phase': 'retryable', 'workbench': {'state': 'failed',
                           'message': '原槽位已转给其他人物，上传录音后可以重新复刻。'}})
                    bound_provider = record.get('provider', 'volcengine')
                    person.update(speaker=identity['speaker_id'], custom_speaker_id=None, status='ready',
                                  voice_provider=bound_provider)
                    from voice_dialects import supported_dialects
                    bound_model = cfg.get('settings', {}).get('minimax_model', 'speech-2.8-hd') if bound_provider == 'minimax' else None
                    if person.get('voice_dialect') and person['voice_dialect'] not in supported_dialects(bound_provider, bound_model):
                        person.pop('voice_dialect', None)
                    cfg['default_person'] = cfg.get('default_person') or ident
                    self.pc.write(self.pc.directory() / 'project.json', cfg)
                elif not person.get('speaker'):
                    self.app.people.bind(ident, identity['speaker_id'], identity.get('custom_speaker_id'))
                message = '复刻完成，音色 ID 已自动填写'
            record['phase'] = 'ready'
            self.finish(path, record, ticket, 'ready', message)

    def finish(self, path, record, ticket, state, message, delay=0):
        with self.app.transaction():
            if self.read(path, {}).get('workbench', {}).get('ticket') != ticket:
                return False
            record['workbench'].update(state=state, message=message, nextAt=time.time() + delay)
            self.write(path, record)
            return True
