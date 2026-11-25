"""Unified registry for Cover Styles and Video Styles (builtin + project-local extensible)."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import re

SKILL = Path(__file__).resolve().parent.parent
DEFAULT_COVER_ID = 'teal-adaptive'
DEFAULT_VIDEO_ID = 'fullscreen-orange'
ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{1,63}$')

_CACHE = {}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _project_styles_dir() -> Path | None:
    try:
        import project_config as pc
        d = pc.directory() / 'styles'
        return d if d.is_dir() else None
    except Exception:
        return None


def _validate_photo_slots(cover: dict):
    """Enforce landscape/near-square photo aspect ratio (w/h >= 0.90) on non-baseline archetypes."""
    if cover.get('baseline') or cover.get('id') == DEFAULT_COVER_ID:
        return
    for idx, slot in enumerate(cover.get('coverSpec', {}).get('photos', [])):
        w, h = float(slot.get('w', 0)), float(slot.get('h', 0))
        if w <= 0 or h <= 0:
            raise ValueError(f"封面样式 {cover.get('id')} 图片槽位 {idx} 宽高无效")
        ratio = w / h
        if ratio < 0.90:
            raise ValueError(
                f"封面样式 {cover.get('id')} 图片槽位 {idx} 宽高比 {ratio:.2f} 过于细长（须 >= 0.90 以避免裁切横版实拍图）"
            )


def _normalize_entry(entry: dict, kind: str, *, builtin: bool, source_path: Path) -> dict:
    item = copy.deepcopy(entry)
    ident = str(item.get('id') or '').strip()
    if not ID_RE.fullmatch(ident):
        raise ValueError(f'无效风格 ID：{ident!r}（来自 {source_path}）')
    if not str(item.get('name') or '').strip():
        raise ValueError(f'风格缺少名称：{ident}')
    item['id'] = ident
    item['kind'] = kind
    item['builtin'] = bool(builtin)
    item['baseline'] = bool(item.get('baseline', ident in (DEFAULT_COVER_ID, DEFAULT_VIDEO_ID)))
    item['category'] = str(item.get('category') or '其他风格').strip()
    item['order'] = int(item.get('order', 999))
    item['tags'] = [str(t).strip() for t in item.get('tags', []) if str(t).strip()]
    item['swatches'] = [str(c).strip() for c in item.get('swatches', []) if str(c).strip()]
    item['_source'] = str(source_path)
    if kind == 'cover':
        _validate_photo_slots(item)
    return item


def _load_catalog(skill_dir: Path | None = None, include_project: bool = True) -> dict:
    base = Path(skill_dir or SKILL).resolve()
    catalog_path = base / 'assets/styles/catalog.json'
    proj_dir = _project_styles_dir() if include_project else None

    sig_parts = [str(base), str(catalog_path.stat().st_mtime_ns) if catalog_path.is_file() else '0']
    extra_dirs = [
        (base / 'assets/styles/covers', 'cover', True),
        (base / 'assets/styles/videos', 'video', True),
    ]
    if proj_dir:
        extra_dirs.extend([
            (proj_dir / 'covers', 'cover', False),
            (proj_dir / 'videos', 'video', False),
        ])
    for folder, _, _ in extra_dirs:
        if folder.is_dir():
            for f in sorted(folder.glob('*.json')):
                sig_parts.append(f'{f}:{f.stat().st_mtime_ns}')
    sig = '|'.join(sig_parts)
    if sig in _CACHE:
        return _CACHE[sig]

    covers: dict[str, dict] = {}
    videos: dict[str, dict] = {}

    if catalog_path.is_file():
        raw = _read_json(catalog_path)
        for row in raw.get('covers', []):
            item = _normalize_entry(row, 'cover', builtin=True, source_path=catalog_path)
            covers[item['id']] = item
        for row in raw.get('videos', []):
            item = _normalize_entry(row, 'video', builtin=True, source_path=catalog_path)
            videos[item['id']] = item

    # Auto-discover additional drop-in JSON presets (scales cleanly from 30 -> 50 -> 100+)
    for folder, kind, is_builtin in extra_dirs:
        if not folder.is_dir():
            continue
        target = covers if kind == 'cover' else videos
        for f in sorted(folder.glob('*.json')):
            row = _read_json(f)
            if not is_builtin and row.get('id') in (DEFAULT_COVER_ID, DEFAULT_VIDEO_ID):
                continue
            item = _normalize_entry(row, kind, builtin=is_builtin, source_path=f)
            target[item['id']] = item

    result = {
        'covers': dict(sorted(covers.items(), key=lambda kv: (kv[1]['order'], kv[0]))),
        'videos': dict(sorted(videos.items(), key=lambda kv: (kv[1]['order'], kv[0]))),
    }
    _CACHE.clear()
    _CACHE[sig] = result
    return result


def list_covers(skill_dir: Path | None = None, include_project: bool = True) -> list[dict]:
    return [copy.deepcopy(v) for v in _load_catalog(skill_dir, include_project)['covers'].values()]


def list_videos(skill_dir: Path | None = None, include_project: bool = True) -> list[dict]:
    return [copy.deepcopy(v) for v in _load_catalog(skill_dir, include_project)['videos'].values()]


def get_cover(ident: str, skill_dir: Path | None = None, include_project: bool = True) -> dict:
    items = _load_catalog(skill_dir, include_project)['covers']
    if ident not in items:
        raise ValueError(f'未知封面样式：{ident}')
    return copy.deepcopy(items[ident])


def get_video(ident: str, skill_dir: Path | None = None, include_project: bool = True) -> dict:
    items = _load_catalog(skill_dir, include_project)['videos']
    if ident not in items:
        raise ValueError(f'未知视频样式：{ident}')
    return copy.deepcopy(items[ident])


def cover_exists(ident: str, skill_dir: Path | None = None, include_project: bool = True) -> bool:
    return str(ident or '') in _load_catalog(skill_dir, include_project)['covers']


def video_exists(ident: str, skill_dir: Path | None = None, include_project: bool = True) -> bool:
    return str(ident or '') in _load_catalog(skill_dir, include_project)['videos']


def public_entry(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith('_')}


def load_stickers(skill_dir: Path | None = None) -> dict[str, str]:
    """Parse SVG stickers directly from assets/styles/stickers.mjs (single source of truth)."""
    base = Path(skill_dir or SKILL).resolve()
    path = base / 'assets/styles/stickers.mjs'
    if not path.is_file():
        return {}
    text = path.read_text(encoding='utf-8')
    out: dict[str, str] = {}
    for m in re.finditer(r'^\s*([A-Za-z0-9_]+)\s*:\s*`(<svg[\s\S]*?</svg>)`', text, re.M):
        out[m.group(1)] = m.group(2)
    return out


def style_caption_spec(video_entry: dict) -> dict:
    """Return a Typography-compatible native 1080x1920 caption layout spec for non-baseline video styles."""
    vcap = (video_entry.get('videoSpec') or {}).get('caption') or {}
    is_pill = vcap.get('focusMode') == 'pill'
    return {
        'font_size': 72 if is_pill else 76,
        'focus_ratio': min(1.20, max(1.0, float(vcap.get('focusRatio', 1.15)))),
        'tracking': 2,
        'angle': int(vcap.get('angle', 0)),
        'scales': [[1.0, 1.0], [1.0, 1.0]],
        'baselines': [1474, 1580],
        'max_width': 710 if is_pill else 750,
        'limits': [100, 1250, 850, 1584],
        'bottom_align': True,
        'stroke': int(vcap.get('strokeW', 12)),
        'measurement_canvas': [1080, 1920],
    }


def style_source_file(entry: dict) -> Path | None:
    src = entry.get('_source')
    return Path(src) if src else None

