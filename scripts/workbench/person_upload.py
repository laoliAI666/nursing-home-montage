"""Receive the two original assets for a new workbench person."""
from email import policy
from email.parser import BytesParser
import hashlib
from pathlib import Path
import shutil
import tempfile
import uuid

from voice_training import AUDIO_FORMATS, MAX_AUDIO_BYTES


IMAGE_FORMATS = {'.jpg': 'jpeg_pipe', '.jpeg': 'jpeg_pipe', '.png': 'png_pipe', '.webp': 'webp_pipe'}
MAX_IMAGE_BYTES = 20_000_000
MAX_UPLOAD_BYTES = MAX_IMAGE_BYTES + MAX_AUDIO_BYTES + 65_536
TEXT_FIELDS = {'name', 'social_media_name', 'speaker', 'custom_speaker_id', 'person_voice_provider', 'speech_rate', 'default'}


def read_form(content_type, body):
    message = BytesParser(policy=policy.default).parsebytes(
        b'Content-Type: ' + content_type.encode('ascii') + b'\r\n\r\n' + body)
    if message.get_content_type() != 'multipart/form-data' or not message.is_multipart() or message.defects:
        raise ValueError('无法读取上传表单，请重新选择文件。')
    parts = {}
    for part in message.iter_parts():
        field = part.get_param('name', header='content-disposition')
        if (field not in TEXT_FIELDS | {'photo', 'recording'} or field in parts or part.is_multipart()
                or part.defects or part.get_content_disposition() != 'form-data' or part.get('Content-Transfer-Encoding')):
            raise ValueError('人物资料上传格式不正确。')
        data = part.get_payload(decode=True) or b''
        if field in TEXT_FIELDS:
            if part.get_filename() or len(data) > 1024:
                raise ValueError('人物资料字段格式不正确。')
            parts[field] = data.decode('utf-8')
        else:
            parts[field] = (Path(part.get_filename() or '').suffix.lower(), data)
    photo_suffix, photo = parts.pop('photo', ('', b''))
    audio_suffix, recording = parts.pop('recording', ('', b''))
    if not photo or not recording:
        raise ValueError('请同时上传一张人物图片和一段录音。')
    if photo_suffix not in IMAGE_FORMATS or len(photo) > MAX_IMAGE_BYTES:
        raise ValueError('请选择不超过 20 MB 的 JPG、PNG 或 WebP 图片。')
    if audio_suffix not in AUDIO_FORMATS or len(recording) > MAX_AUDIO_BYTES:
        raise ValueError('请选择不超过 10 MB 的 WAV、MP3、M4A、AAC、FLAC 或 OGG 录音。')
    parts['default'] = parts.get('default') == 'on'
    return parts, (photo_suffix, photo), (audio_suffix, recording)


def create_person(app, values, photo, recording, run):
    if not values.get('social_media_name', '').strip():
        raise ValueError('请填写社媒署名。')
    if not values.get('name', '').strip():
        raise ValueError('请填写人物名称。')
    ident = 'p-' + uuid.uuid4().hex[:12]
    folder = app.voice_training.folder(ident)
    folder.parent.mkdir(parents=True, exist_ok=True)
    photo_suffix, photo_data = photo
    audio_suffix, audio_data = recording
    fingerprint = hashlib.sha256(hashlib.sha256(photo_data).digest() + hashlib.sha256(audio_data).digest()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='.person-upload-', dir=folder.parent) as directory:
        staging = Path(directory)
        image = staging / ('source-image' + photo_suffix)
        audio = staging / ('source-audio' + audio_suffix)
        image.write_bytes(photo_data)
        audio.write_bytes(audio_data)
        portrait, sample = staging / 'portrait.png', staging / 'sample.wav'
        result = run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                      '-f', IMAGE_FORMATS[photo_suffix], '-max_pixels', '40000000', '-i', image,
                      '-vf', r'scale=min(1600\,iw):min(1600\,ih):force_original_aspect_ratio=decrease',
                      '-frames:v', '1', portrait], capture_output=True, timeout=30)
        if result.returncode or not portrait.is_file():
            raise ValueError('无法读取这张人物图片，请重新选择。')
        if portrait.stat().st_size >= 4_700_000:
            raise ValueError('处理后的人物图片过大，请换一张背景更简单的图片。')
        app.voice_training.normalize_sample(audio, sample)
        root = app.pc.directory().resolve()
        files = {key: str((folder / path.name).relative_to(root)) for key, path in (
            ('portrait', portrait), ('sample', sample), ('portrait_source', image), ('sample_source', audio))}
        with app.transaction():
            cfg = app.pc.load()
            retry = next((p for p in cfg['people'].values() if p.get('source_hash') == fingerprint
                          and p.get('name') == values['name'].strip()[:60]
                          and p.get('social_media_name') == values['social_media_name'].strip()), None)
            if retry:
                return retry['id']
            cfg['connections'].setdefault('main', {'provider': 'volcengine'})
            person = {'id': ident, 'connection': 'main', 'speaker': None, 'status': 'needs_voice',
                      'source_hash': fingerprint, **files}
            app.apply_person_fields(cfg, person, values)
            cfg['people'][ident] = person
            staging.rename(folder)
            try:
                app.pc.write(root / 'project.json', cfg)
            except Exception:
                shutil.rmtree(folder)
                raise
    return ident
