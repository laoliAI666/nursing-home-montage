"""Resolve workspace paths and keep already recorded task layouts readable."""
from pathlib import Path
import json
import re
import tempfile


def workspace_path(project, value):
    if not isinstance(value, str) or len(value) > 4096 or '\x00' in value:
        raise ValueError('请输入有效的工作区目录')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = project / path
    path = path.resolve()
    if any(part in ('.montage', '.agents', '.git') or re.fullmatch(r'批次_[a-f0-9]{12}', part)
           for part in path.parts):
        raise ValueError('请选择独立的工作区目录，不能放在任务工程或内部配置中')
    try:
        path.mkdir(exist_ok=True, parents=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as exc:
        raise ValueError(f'工作区不可写：{path}（{exc.strerror}）') from exc
    return path


def video_job(task, index):
    root = Path(task['job'])
    if task.get('workspace'):
        return root / f'视频_{index+1:02d}' / '工程'
    return root if task['count'] == 1 else root / f'variant-{index+1:02d}'


def delivery_folder(task, index, version='original'):
    if not task.get('workspace'):
        return None
    folder = video_job(task, index).parent / '交付产物'
    return folder if version == 'original' else folder / ('封面版本_' + version)


def owned_batch(app, task, *, allow_missing=False):
    ident = task['id']
    if task.get('workspace'):
        expected = Path(task['workspace']) / ('批次_' + ident)
    else:
        expected = app.project / '作品' / ('工作台_' + ident)
    if Path(task['job']) != expected or expected.is_symlink():
        raise ValueError('只能清理这个任务自己的批次工程')
    if expected.exists():
        if expected.resolve() != expected.absolute():
            raise ValueError('只能清理这个任务自己的批次工程')
    elif expected.parent.exists() and expected.parent.resolve() != expected.parent.absolute():
        raise ValueError('只能清理这个任务自己的批次工程')
    if task.get('workspace'):
        if not expected.exists():
            if allow_missing:
                return expected
            raise ValueError('工作区工程目录已不存在（可能已被手动移动或删除），无法验收；可直接删除这条任务记录')
        marker = expected / '.workbench-batch.json'
        expected_marker = {'id': ident, 'project': str(app.project)}
        if marker.is_symlink() or not marker.is_file():
            raise ValueError('工作区批次归属无法验证，不能清理')
        try:
            actual_marker = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError('工作区批次归属无法验证，不能清理') from exc
        if actual_marker != expected_marker:
            raise ValueError('工作区批次归属无法验证，不能清理')
    return expected
