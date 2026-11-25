// Unified 1080x1920 Canvas Cover Engine for all builtin & extensible cover styles.
// Baseline 'teal-adaptive' delegates directly to ../teal-adaptive/layout.mjs for zero drift.
import { STICKERS as BUILTIN_STICKERS } from '../../styles/stickers.mjs';
import { mountCover as mountTealAdaptive } from '../teal-adaptive/layout.mjs';

export const BOUNDS = {
  title: { initial: 168, min: 112, max: 184 },
  tags: { initial: 60, min: 48, max: 64 },
  identity: { initial: 72, min: 54, max: 78 },
  brand: { initial: 104, min: 64, max: 112 },
};

const LOADED_FONTS = new Set();
const STICKER_IMG_CACHE = new Map();

async function ensureFont(fontUrl) {
  if (!fontUrl) return;
  if (LOADED_FONTS.has(fontUrl)) return;
  const font = new FontFace('CoverBlack', `url(${fontUrl})`, { weight: '900' });
  await font.load();
  document.fonts.add(font);
  LOADED_FONTS.add(fontUrl);
}

function loadImage(url, cache) {
  if (cache && cache.has(url)) return cache.get(url);
  const promise = new Promise((resolve, reject) => {
    const im = new Image();
    im.onload = () => resolve(im);
    im.onerror = () => reject(new Error('图片无法加载'));
    im.src = url;
  });
  if (cache) cache.set(url, promise);
  return promise;
}

function loadStickerImage(key, customStickers) {
  const raw = (customStickers && customStickers[key]) || BUILTIN_STICKERS[key];
  if (!raw) return Promise.resolve(null);
  const cacheKey = raw.startsWith('<svg') ? `svg:${key}:${raw.length}` : `url:${raw}`;
  if (STICKER_IMG_CACHE.has(cacheKey)) return STICKER_IMG_CACHE.get(cacheKey);
  const src = raw.startsWith('<svg')
    ? 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(raw)
    : raw;
  const p = loadImage(src).catch(() => null);
  STICKER_IMG_CACHE.set(cacheKey, p);
  return p;
}

export async function mountCover(canvas, input, onchange = () => {}) {
  const spec = input.styleSpec || null;
  const isBaseline =
    !spec ||
    spec.engine === 'teal-adaptive' ||
    input.template === 'teal-adaptive';
  if (isBaseline) {
    const scale = Number(input.renderScale || 1);
    if (scale === 1) {
      return mountTealAdaptive(canvas, input, onchange);
    }
    // Scaled thumbnail preview for baseline teal-adaptive in Style Library.
    const off = document.createElement('canvas');
    off.width = 1080;
    off.height = 1920;
    const app = await mountTealAdaptive(off, input, onchange);
    canvas.width = Math.round(1080 * scale);
    canvas.height = Math.round(1920 * scale);
    const tctx = canvas.getContext('2d');
    tctx.clearRect(0, 0, canvas.width, canvas.height);
    tctx.drawImage(off, 0, 0, canvas.width, canvas.height);
    canvas.dataset.ready = off.dataset.ready || 'true';
    return app;
  }

  const SEMANTIC_WORDS = [
    '养老院', '老年公寓', '养老生活', '生活照护', '照护服务',
    '服务中心', '居住环境', '活动空间', '院区环境', '颐养社区',
  ];
  const state = {
    title: String(input.title || ''),
    highlights: Array.isArray(input.highlights)
      ? input.highlights.join('、')
      : String(input.highlights || ''),
    tags: [...(input.labels || ['', '', ''])],
    tagPreferred: 44,
    brand: String(input.institution_name || ''),
    boundName: String(input.identity?.social_media_name || ''),
  };

  const renderScale = Number(input.renderScale || 1);
  canvas.width = Math.round(1080 * renderScale);
  canvas.height = Math.round(1920 * renderScale);
  const ctx = canvas.getContext('2d');
  const probe = document.createElement('canvas').getContext('2d');

  await ensureFont(input.font);
  const imgCache = input._imageCache || null;
  const photos = await Promise.all(
    (input.photos || []).map(p => loadImage(p.data, imgCache))
  );
  const avatar = await loadImage(input.avatar, imgCache);

  // Preload all stickers referenced by spec.stickers or spec.header.sealKey.
  const stickerKeys = new Set();
  for (const s of spec.stickers || []) if (s.key) stickerKeys.add(s.key);
  if (spec.header?.sealKey) stickerKeys.add(spec.header.sealKey);
  const stickerMap = new Map();
  await Promise.all(
    [...stickerKeys].map(async k => {
      const im = await loadStickerImage(k, input.stickers);
      if (im) stickerMap.set(k, im);
    })
  );

  // Pre-build static background buffer at native 1080x1920 for crisp reuse.
  const bgCanvas = document.createElement('canvas');
  bgCanvas.width = 1080;
  bgCanvas.height = 1920;
  drawBackground(bgCanvas.getContext('2d'), spec.bg || { type: 'solid', color: '#14161A' });

  let lastLayout;

  function metrics(text, em) {
    probe.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
    const m = probe.measureText(text || ' ');
    const left = Math.max(0, m.actualBoundingBoxLeft || 0);
    const right = Math.max(m.width, m.actualBoundingBoxRight || m.width);
    const up = m.actualBoundingBoxAscent || em * 0.84;
    const down = m.actualBoundingBoxDescent || em * 0.14;
    return {
      w: Math.max(1, Math.ceil(left + right)),
      h: Math.max(1, Math.ceil(up + down)),
      adv: Math.max(1, m.width),
      left,
      up,
      down,
    };
  }

  function sizedFont(lines, min, max, width, height, gap = 0) {
    const fits = em => {
      const ms = lines.map(t => metrics(t, em));
      return (
        ms.every(m => m.w <= width) &&
        ms.reduce((a, m) => a + m.h, 0) + gap * (ms.length - 1) <= height
      );
    };
    if (!fits(min)) return { em: min, fit: false };
    let lo = min;
    let hi = max;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (fits(mid)) lo = mid;
      else hi = mid - 1;
    }
    return { em: lo, fit: true };
  }

  function words() {
    return state.highlights
      .split(/[,，、]/)
      .map(s => s.trim())
      .filter(Boolean);
  }

  function splitCandidates(text, protect = []) {
    const chars = Array.from(text);
    const list = [];
    const protectedWords = [...protect, ...SEMANTIC_WORDS];
    for (let i = 1; i < chars.length; i++) {
      const a = chars.slice(0, i).join('');
      const b = chars.slice(i).join('');
      if (/^[，。！？、；：）】》,.!?:;]/.test(b)) continue;
      let crossed = false;
      for (const word of protectedWords) {
        let start = text.indexOf(word);
        while (start >= 0) {
          if (a.length > start && a.length < start + word.length) crossed = true;
          start = text.indexOf(word, start + 1);
        }
      }
      if (!crossed) list.push([a, b]);
    }
    return list;
  }

  function chooseTwo(text, { min, max, width, height, gap = 14, protect = [], manual = false }) {
    let candidates;
    if (manual && text.includes('\n')) {
      const lines = text.split('\n').map(s => s.trim());
      if (lines.length !== 2 || lines.some(s => !s)) {
        return { lines, em: min, fit: false, reason: '请只保留一次换行，并填写两行内容。' };
      }
      const joined = lines.join('');
      const at = lines[0].length;
      for (const w of protect) {
        const i = joined.indexOf(w);
        if (i >= 0 && at > i && at < i + w.length) {
          return { lines, em: min, fit: false, reason: '手动换行拆开了高亮词，请调整断行位置。' };
        }
      }
      candidates = [lines];
    } else {
      candidates = splitCandidates(text.replace(/\n/g, ''), protect);
    }
    if (!candidates.length) {
      return { lines: [text], em: min, fit: false, reason: '请填写足够分成两行的标题，并保留高亮词完整。' };
    }
    let best;
    for (const lines of candidates) {
      const r = sizedFont(lines, min, max, width, height, gap);
      const widths = lines.map(t => metrics(t, r.em).w);
      const score = r.em * 100 - (Math.abs(widths[0] - widths[1]) / Math.max(1, width)) * 12;
      const item = { ...r, lines, score };
      if (!best || (item.fit && !best.fit) || (item.fit === best.fit && score > best.score)) {
        best = item;
      }
    }
    return best;
  }

  function titleLayout() {
    const t = spec.title || { x: 48, y: 200, w: 984, h: 320, align: 'center' };
    const text = state.title.trim();
    const missing = words().filter(w => !text.replace(/\n/g, '').includes(w));
    const isBand = t.mode === 'band';
    const boxW = isBand ? Math.max(360, (t.w || 980) - 72) : Math.max(340, (t.w || 960) - 20);
    const boxH = isBand ? (t.textH || Math.max(96, (t.h || 200) - 70)) : Math.max(120, (t.h || 300) - 8);
    const maxEm = isBand
      ? Math.min(112, Math.round(boxH * 0.82))
      : Math.min(156, Math.round(boxH * 0.48));
    const minEm = isBand ? 44 : 52;
    const gap = Math.max(10, Math.round(boxH * 0.05));

    let r;
    const cleanText = text.replace(/\n/g, '');
    if (t.singleLine && !text.includes('\n')) {
      const single = sizedFont([cleanText], Math.max(50, minEm), maxEm, boxW, boxH, 0);
      if (single.fit) {
        r = { ...single, lines: [cleanText] };
      } else {
        r = chooseTwo(text, { min: minEm, max: Math.round(maxEm * 0.72), width: boxW, height: boxH, gap: 10, protect: words(), manual: false });
      }
    } else {
      r = chooseTwo(text, { min: minEm, max: maxEm, width: boxW, height: boxH, gap, protect: words(), manual: true });
      if (!r.fit && !text.includes('\n')) {
        const single = sizedFont([cleanText], minEm, maxEm, boxW, boxH, 0);
        if (single.fit) r = { ...single, lines: [cleanText] };
      }
    }

    return {
      ...r,
      boxW,
      boxH,
      gap,
      error: !text
        ? '请填写主标题。'
        : missing.length
          ? `高亮词“${missing.join('、')}”不在标题中，请修改高亮词或清空。`
          : !r.fit
            ? r.reason || '主标题在最小字号下仍放不下，请改短文字。'
            : null,
    };
  }

  function tagSlotMaxWidths() {
    const widths = [320, 320, 320];
    const ts = spec.tagStyle || {};
    const padX = ts.padX ?? 20;
    for (const p of spec.photos || []) {
      if (p.labelIdx >= 0 && p.labelIdx < 3) {
        if (p.checklist) {
          widths[p.labelIdx] = Math.max(180, p.w - (p.thumbW || 240) - (p.pad || 14) * 2 - 44);
        } else if (p.polaroid) {
          widths[p.labelIdx] = Math.max(180, p.w - 36);
        } else {
          widths[p.labelIdx] = Math.max(180, p.w - 32 - padX * 2);
        }
      }
    }
    for (const et of spec.extraTags || []) {
      if (et.labelIdx >= 0 && et.labelIdx < 3) {
        widths[et.labelIdx] = Math.max(160, (et.w || 340) - padX * 2 - (ts.accentBar ? 18 : 0));
      }
    }
    return widths;
  }

  function tagDisplayText(idx) {
    let prefix = '';
    for (const p of spec.photos || []) {
      if (p.labelIdx === idx && p.labelPrefix) prefix = p.labelPrefix;
    }
    for (const et of spec.extraTags || []) {
      if (et.labelIdx === idx && et.prefix) prefix = et.prefix;
    }
    return prefix + String(state.tags[idx] || '').trim();
  }

  function tagsLayout() {
    const maxWs = tagSlotMaxWidths();
    const minEm = 28;
    const maxEm = 44;
    let em = Math.min(maxEm, Math.max(minEm, state.tagPreferred));
    const displayTexts = [0, 1, 2].map(i => tagDisplayText(i));
    const fitsAt = sz =>
      [0, 1, 2].every(i => state.tags[i]?.trim() && metrics(displayTexts[i], sz).w <= maxWs[i]);
    while (em > minEm && !fitsAt(em)) em--;
    const ok = fitsAt(em);
    const ts = spec.tagStyle || {};
    const padX = ts.padX ?? 20;
    const padY = ts.padY ?? 12;
    const cards = displayTexts.map((t, i) => {
      const m = metrics(t, em);
      return { w: Math.min(maxWs[i] + padX * 2, m.w + padX * 2), h: m.h + padY * 2 };
    });
    return {
      em,
      cards,
      displayTexts,
      fit: ok,
      error: ok ? null : '标签文字过长或为空，请改短；三枚标签保持统一字号。',
    };
  }

  function brandLayout() {
    const b = spec.brand || { x: 42, y: 1712, w: 996, h: 134 };
    const text = state.brand.trim();
    const maxW = Math.max(360, b.w - 60);
    const maxH = Math.max(68, b.h - 24);
    const single = sizedFont([text], 34, Math.min(72, Math.round(maxH * 0.72)), maxW, maxH);
    let r = { ...single, lines: [text] };
    if (!single.fit) {
      r = chooseTwo(text, { min: 30, max: 52, width: maxW, height: maxH, gap: 6 });
    }
    return {
      ...r,
      x: b.x,
      y: b.y,
      w: b.w,
      h: b.h,
      error: !text
        ? '请填写机构完整名称。'
        : !r.fit
          ? '完整院名过长，完整名称不会被截断。'
          : null,
    };
  }

  function identityLayout() {
    const idSpec = spec.identity || { x: 80, y: 1520 };
    const text = state.boundName.trim();
    const maxW = Math.max(240, idSpec.w || 460);
    const maxH = 66;
    const single = sizedFont([text], 28, 46, maxW - 44, maxH);
    let r = { ...single, lines: [text] };
    if (!single.fit) {
      r = chooseTwo(text, { min: 26, max: 38, width: maxW - 44, height: 84, gap: 6 });
    }
    const ms = r.lines.map(t => metrics(t, r.em));
    const textW = Math.max(...ms.map(m => m.w));
    const textH = ms.reduce((a, m) => a + m.h, 0) + (ms.length - 1) * 6;
    const w = Math.max(idSpec.w ? Math.min(idSpec.w, textW + 64) : textW + 64, 180);
    const h = Math.max(70, textH + 30);
    const x = idSpec.align === 'center' ? Math.round(idSpec.x - w / 2) : idSpec.x;
    return {
      ...r,
      x,
      y: idSpec.y,
      w,
      h,
      error: !text
        ? '缺少与数字人绑定的社交媒体名，请先指定并确认。'
        : !r.fit
          ? '绑定署名过长，不会截断真实署名。'
          : null,
    };
  }

  function layout() {
    const title = titleLayout();
    const tags = tagsLayout();
    const brand = brandLayout();
    const identity = identityLayout();
    const headerText = String(input.header_text || '').trim();
    const headerErr = !headerText ? '缺少项目固定封面头标。' : null;
    return {
      title,
      tags,
      brand,
      identity,
      errors: [title.error, tags.error, identity.error, brand.error, headerErr].filter(Boolean),
    };
  }

  function drawCoverPhoto(im, dx, dy, dw, dh, crop = [0, 0, 1, 1], focus = [0.5, 0.5]) {
    const cw = (crop[2] - crop[0]) * im.width;
    const ch = (crop[3] - crop[1]) * im.height;
    const scale = Math.max(dw / cw, dh / ch);
    const sw = dw / scale;
    const sh = dh / scale;
    const sx = crop[0] * im.width + (cw - sw) * focus[0];
    const sy = crop[1] * im.height + (ch - sh) * focus[1];
    ctx.drawImage(im, sx, sy, sw, sh, dx, dy, dw, dh);
  }

  function drawStickerItem(s) {
    const im = stickerMap.get(s.key);
    if (!im) return;
    ctx.save();
    const cx = s.x + s.w / 2;
    const cy = s.y + s.h / 2;
    ctx.translate(cx, cy);
    if (s.rotate) ctx.rotate((s.rotate * Math.PI) / 180);
    if (s.flipX) ctx.scale(-1, 1);
    ctx.drawImage(im, -s.w / 2, -s.h / 2, s.w, s.h);
    ctx.restore();
  }

  function drawPillTag(text, em, x, y, style, overrides = {}) {
    const bg = overrides.bg || style.bg || 'rgba(0,0,0,0.78)';
    const color = overrides.color || style.color || '#FFFFFF';
    const padX = style.padX ?? 20;
    const padY = style.padY ?? 11;
    const r = style.radius ?? 12;
    const m = metrics(text, em);
    const w = m.w + padX * 2 + (style.accentBar ? 12 : 0);
    const h = m.h + padY * 2;
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, Math.min(r, h / 2, w / 2));
    ctx.fillStyle = bg;
    ctx.fill();
    if (style.border) {
      ctx.strokeStyle = style.border;
      ctx.lineWidth = style.borderW || 3;
      ctx.stroke();
    }
    if (style.accentBar) {
      ctx.save();
      ctx.clip();
      ctx.fillStyle = style.accentBar;
      ctx.fillRect(x, y, 10, h);
      ctx.restore();
    }
    ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
    ctx.fillStyle = color;
    ctx.textBaseline = 'alphabetic';
    const tx = x + padX + (style.accentBar ? 8 : 0) + m.left;
    const ty = y + (h - m.h) / 2 + m.up;
    ctx.fillText(text, tx, ty);
    ctx.restore();
    return { w, h };
  }

  function drawPhotos(l) {
    const ts = spec.tagStyle || {};
    for (const p of spec.photos || []) {
      const photoIdx = p.idx < photos.length ? p.idx : p.idx % Math.max(1, photos.length);
      const im = photos[photoIdx];
      const pSpec = input.photos?.[photoIdx] || {};
      const crop = pSpec.crop || [0, 0, 1, 1];
      const focus =
        p.idx >= photos.length
          ? [0.72, 0.45]
          : pSpec.focus || [0.5, 0.5];

      ctx.save();
      if (p.rotate) {
        ctx.translate(p.x + p.w / 2, p.y + p.h / 2);
        ctx.rotate((p.rotate * Math.PI) / 180);
        ctx.translate(-(p.x + p.w / 2), -(p.y + p.h / 2));
      }

      if (p.checklist) {
        const r = p.r ?? 20;
        const pad = p.pad ?? 14;
        const thumbW = p.thumbW ?? 232;
        if (p.hardShadow) {
          ctx.beginPath();
          ctx.roundRect(p.x + 8, p.y + 8, p.w, p.h, r);
          ctx.fillStyle = p.hardShadow;
          ctx.fill();
        }
        ctx.beginPath();
        ctx.roundRect(p.x, p.y, p.w, p.h, r);
        ctx.fillStyle = p.cardBg || '#1E232B';
        ctx.fill();
        if (p.border) {
          ctx.strokeStyle = p.borderColor || '#FFD426';
          ctx.lineWidth = p.border;
          ctx.stroke();
        }
        if (im) {
          ctx.save();
          ctx.beginPath();
          ctx.roundRect(p.x + pad, p.y + pad, thumbW, p.h - pad * 2, Math.max(8, r - 6));
          ctx.clip();
          drawCoverPhoto(im, p.x + pad, p.y + pad, thumbW, p.h - pad * 2, crop, focus);
          ctx.restore();
        }
        const contentX = p.x + pad + thumbW + 24;
        const contentW = p.w - pad * 2 - thumbW - 36;
        let textY = p.y + p.h / 2;
        if (p.badge) {
          const bm = metrics(p.badge, 30);
          const bw = bm.w + 26;
          const bh = 44;
          ctx.beginPath();
          ctx.roundRect(contentX, p.y + 24, bw, bh, 10);
          ctx.fillStyle = ts.bg || '#FFD426';
          ctx.fill();
          ctx.font = `900 30px CoverBlack, "PingFang SC", sans-serif`;
          ctx.fillStyle = ts.color || '#181A1E';
          ctx.textBaseline = 'alphabetic';
          ctx.fillText(p.badge, contentX + 13 + bm.left, p.y + 24 + (bh - bm.h) / 2 + bm.up);
          textY = p.y + 84 + (p.h - 104) / 2;
        }
        if (p.labelIdx >= 0 && p.labelIdx < 3) {
          const lbl = l.tags.displayTexts[p.labelIdx];
          const fit = sizedFont([lbl], 28, Math.max(l.tags.em, 44), contentW, 64);
          const lm = metrics(lbl, fit.em);
          ctx.font = `900 ${fit.em}px CoverBlack, "PingFang SC", sans-serif`;
          ctx.fillStyle = ts.textColor || '#FFFFFF';
          ctx.textBaseline = 'alphabetic';
          ctx.fillText(lbl, contentX + lm.left, textY - lm.h / 2 + lm.up);
        }
        ctx.restore();
        continue;
      }

      if (p.polaroid) {
        const pad = p.pad ?? 18;
        const footH = p.footH ?? 74;
        const totalH = p.h + footH;
        const r = p.r ?? 8;
        ctx.save();
        ctx.shadowColor = 'rgba(42,28,18,0.22)';
        ctx.shadowBlur = 24;
        ctx.shadowOffsetY = 10;
        ctx.beginPath();
        ctx.roundRect(p.x, p.y, p.w, totalH, r);
        ctx.fillStyle = p.cardBg || '#FFFDF8';
        ctx.fill();
        ctx.restore();
        if (p.border) {
          ctx.beginPath();
          ctx.roundRect(p.x, p.y, p.w, totalH, r);
          ctx.strokeStyle = p.borderColor || '#E0D5C5';
          ctx.lineWidth = p.border;
          ctx.stroke();
        }
        if (im) {
          ctx.save();
          ctx.beginPath();
          ctx.roundRect(p.x + pad, p.y + pad, p.w - pad * 2, p.h - pad, 4);
          ctx.clip();
          drawCoverPhoto(im, p.x + pad, p.y + pad, p.w - pad * 2, p.h - pad, crop, focus);
          ctx.restore();
        }
        if (p.labelIdx >= 0 && p.labelIdx < 3) {
          const lbl = l.tags.displayTexts[p.labelIdx];
          const lm = metrics(lbl, l.tags.em);
          ctx.font = `900 ${l.tags.em}px CoverBlack, "PingFang SC", sans-serif`;
          ctx.fillStyle = ts.color || '#4A3428';
          ctx.textBaseline = 'alphabetic';
          ctx.fillText(lbl, p.x + (p.w - lm.w) / 2 + lm.left, p.y + p.h + (footH - lm.h) / 2 + lm.up);
        }
        ctx.restore();
        continue;
      }

      if (p.circle) {
        const cx = p.x + p.w / 2;
        const cy = p.y + p.h / 2;
        const rad = Math.min(p.w, p.h) / 2;
        if (p.outerBorder) {
          ctx.beginPath();
          ctx.arc(cx, cy, rad + 12, 0, Math.PI * 2);
          ctx.strokeStyle = p.outerBorder;
          ctx.lineWidth = 3;
          ctx.stroke();
        }
        ctx.save();
        ctx.beginPath();
        ctx.arc(cx, cy, rad, 0, Math.PI * 2);
        ctx.clip();
        if (im) drawCoverPhoto(im, p.x, p.y, p.w, p.h, crop, focus);
        ctx.restore();
        if (p.border) {
          ctx.beginPath();
          ctx.arc(cx, cy, rad, 0, Math.PI * 2);
          ctx.strokeStyle = p.borderColor || '#FFFFFF';
          ctx.lineWidth = p.border;
          ctx.stroke();
        }
      } else {
        const r = p.r ?? 18;
        const radii = p.archTop
          ? [Math.min(p.archTop, p.w / 2, p.h / 2), Math.min(p.archTop, p.w / 2, p.h / 2), r, r]
          : r;
        if (p.hardShadow) {
          ctx.beginPath();
          ctx.roundRect(p.x + 10, p.y + 10, p.w, p.h, radii);
          ctx.fillStyle = p.hardShadow;
          ctx.fill();
        } else {
          ctx.save();
          ctx.shadowColor = 'rgba(0,0,0,0.22)';
          ctx.shadowBlur = 22;
          ctx.shadowOffsetY = 8;
          ctx.beginPath();
          ctx.roundRect(p.x, p.y, p.w, p.h, radii);
          ctx.fillStyle = '#000000';
          ctx.fill();
          ctx.restore();
        }
        ctx.save();
        ctx.beginPath();
        ctx.roundRect(p.x, p.y, p.w, p.h, radii);
        ctx.clip();
        if (im) drawCoverPhoto(im, p.x, p.y, p.w, p.h, crop, focus);
        ctx.restore();
        if (p.border) {
          ctx.beginPath();
          ctx.roundRect(p.x, p.y, p.w, p.h, radii);
          ctx.strokeStyle = p.borderColor || '#FFFFFF';
          ctx.lineWidth = p.border;
          ctx.stroke();
        }
        if (p.outerBorder) {
          ctx.beginPath();
          ctx.roundRect(p.x - 10, p.y - 10, p.w + 20, p.h + 20, Array.isArray(radii) ? radii.map(v => v + 6) : r + 6);
          ctx.strokeStyle = p.outerBorder;
          ctx.lineWidth = 2.5;
          ctx.stroke();
        }
      }

      if (p.liveBadge) {
        const bm = metrics(p.liveBadge, 28);
        const bw = bm.w + 46;
        const bh = 46;
        const bx = p.x + p.w - bw - 18;
        const by = p.y + 18;
        ctx.beginPath();
        ctx.roundRect(bx, by, bw, bh, 99);
        ctx.fillStyle = 'rgba(20,22,26,0.82)';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(bx + 20, by + bh / 2, 7, 0, Math.PI * 2);
        ctx.fillStyle = '#FF3B30';
        ctx.fill();
        ctx.font = `900 28px CoverBlack, "PingFang SC", sans-serif`;
        ctx.fillStyle = '#FFFFFF';
        ctx.textBaseline = 'alphabetic';
        ctx.fillText(p.liveBadge, bx + 34 + bm.left, by + (bh - bm.h) / 2 + bm.up);
      }

      if (p.labelIdx >= 0 && p.labelIdx < 3) {
        const lbl = l.tags.displayTexts[p.labelIdx];
        const em = l.tags.em;
        const padX = ts.padX ?? 20;
        const padY = ts.padY ?? 11;
        const m = metrics(lbl, em);
        const tw = m.w + padX * 2 + (ts.accentBar ? 12 : 0);
        const th = m.h + padY * 2;
        const inset = 18;
        let lx = p.x + inset;
        let ly = p.y + inset;
        const pos = p.labelPos || 'tl';
        if (pos === 'tr') lx = p.x + p.w - tw - inset;
        else if (pos === 'bl') ly = p.y + p.h - th - inset;
        else if (pos === 'br') {
          lx = p.x + p.w - tw - inset;
          ly = p.y + p.h - th - inset;
        } else if (pos === 'tc') {
          lx = p.x + (p.w - tw) / 2;
        }
        if (p.labelOffsetY) ly += p.labelOffsetY;
        drawPillTag(lbl, em, lx, ly, ts, { bg: p.tagBg, color: p.tagColor });
      }
      ctx.restore();
    }

    // Render extraTags for 1-photo / 2-photo styles so all 3 labels are always visible.
    for (const et of spec.extraTags || []) {
      if (et.labelIdx < 0 || et.labelIdx >= 3) continue;
      const lbl = l.tags.displayTexts[et.labelIdx];
      drawPillTag(lbl, l.tags.em, et.x, et.y, ts);
    }
  }

  function drawHeader() {
    const h = spec.header;
    if (!h) return;
    const raw = (h.prefix || '') + String(input.header_text || '').trim() + (h.suffix || '');
    const maxW = h.w || 900;
    const pref = h.size || 46;
    const fit = sizedFont([raw], 26, pref, maxW - 44, 72);
    const em = fit.em;
    const m = metrics(raw, em);
    const style = h.style || 'pill';

    ctx.save();
    if (style === 'hard-shadow') {
      const pw = Math.min(maxW, m.w + 68);
      const ph = Math.max(76, m.h + 30);
      const cx = h.align === 'center' ? h.x : h.x + pw / 2;
      const cy = h.y + ph / 2;
      ctx.translate(cx, cy);
      if (h.angle) ctx.rotate((h.angle * Math.PI) / 180);
      ctx.beginPath();
      ctx.roundRect(-pw / 2 + 8, -ph / 2 + 8, pw, ph, h.radius ?? 14);
      ctx.fillStyle = h.shadow || '#181818';
      ctx.fill();
      ctx.beginPath();
      ctx.roundRect(-pw / 2, -ph / 2, pw, ph, h.radius ?? 14);
      ctx.fillStyle = h.bg || '#FFE033';
      ctx.fill();
      if (h.border) {
        ctx.strokeStyle = h.border;
        ctx.lineWidth = h.borderW || 6;
        ctx.stroke();
      }
      ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#181818';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(raw, -m.w / 2 + m.left, -m.h / 2 + m.up);
    } else if (style === 'masthead') {
      const w = h.w || 960;
      const x = h.x ?? 60;
      const y = h.y ?? 70;
      ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#111111';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(raw, x + m.left, y + m.up);
      if (h.rightText) {
        const rm = metrics(h.rightText, Math.round(em * 0.72));
        ctx.font = `900 ${Math.round(em * 0.72)}px CoverBlack, "PingFang SC", sans-serif`;
        ctx.fillStyle = h.rightColor || h.color || '#666666';
        ctx.fillText(h.rightText, x + w - rm.w + rm.left, y + m.up - 2);
      }
      ctx.fillStyle = h.lineColor || h.color || '#111111';
      ctx.fillRect(x, y + m.h + 16, w, 4);
    } else if (style === 'accent-line') {
      const x = h.x ?? 60;
      const y = h.y ?? 76;
      ctx.fillStyle = h.barColor || '#DCEABC';
      ctx.beginPath();
      ctx.roundRect(x, y, 10, m.h + 12, 5);
      ctx.fill();
      ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#FFFFFF';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(raw, x + 26 + m.left, y + 6 + m.up);
    } else if (style === 'minimal-line') {
      const x = h.x ?? 60;
      const y = h.y ?? 76;
      const w = h.w || Math.max(m.w + 40, 420);
      ctx.fillStyle = h.lineColor || '#3D6B58';
      ctx.fillRect(x, y, 54, 5);
      ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#1E2022';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(raw, x + 70 + m.left, y - m.h / 2 + 4 + m.up);
      if (h.rightText) {
        const rm = metrics(h.rightText, Math.round(em * 0.7));
        ctx.font = `900 ${Math.round(em * 0.7)}px CoverBlack, "PingFang SC", sans-serif`;
        ctx.fillStyle = h.rightColor || h.lineColor || '#666666';
        ctx.fillText(h.rightText, x + w - rm.w, y - m.h / 2 + 4 + m.up);
      }
    } else if (style === 'underline') {
      const x = h.align === 'center' ? Math.round((h.x ?? 540) - m.w / 2) : (h.x ?? 60);
      const y = h.y ?? 74;
      ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#FFFFFF';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(raw, x + m.left, y + m.up);
      ctx.fillStyle = h.lineColor || '#A7D796';
      ctx.beginPath();
      ctx.roundRect(x, y + m.h + 12, m.w, 8, 4);
      ctx.fill();
    } else if (style === 'seal-underline') {
      const x = h.x ?? 64;
      const y = h.y ?? 72;
      const w = h.w || 952;
      const sealIm = h.sealKey ? stickerMap.get(h.sealKey) : null;
      const offsetX = sealIm ? 76 : 0;
      if (sealIm) ctx.drawImage(sealIm, x, y - 6, 62, 62);
      ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#1E433E';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(raw, x + offsetX + m.left, y + m.up);
      ctx.fillStyle = h.lineColor || '#C89B54';
      ctx.fillRect(x, y + m.h + 18, w, 3);
    } else if (style === 'rec-bar') {
      const x = h.x ?? 48;
      const y = h.y ?? 64;
      const w = h.w || 984;
      const bh = 68;
      ctx.beginPath();
      ctx.roundRect(x, y, w, bh, h.radius ?? 14);
      ctx.fillStyle = h.bg || 'rgba(12,16,20,0.82)';
      ctx.fill();
      if (h.border) {
        ctx.strokeStyle = h.border;
        ctx.lineWidth = h.borderW || 2;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(x + 30, y + bh / 2, 10, 0, Math.PI * 2);
      ctx.fillStyle = h.dotColor || '#FF3B30';
      ctx.fill();
      const label = (h.badge ? h.badge + ' · ' : '') + raw;
      const lm = metrics(label, Math.min(em, 36));
      ctx.font = `900 ${Math.min(em, 36)}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#FFFFFF';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(label, x + 52 + lm.left, y + (bh - lm.h) / 2 + lm.up);
      if (h.rightText) {
        const rm = metrics(h.rightText, 28);
        ctx.font = `900 28px CoverBlack, "PingFang SC", sans-serif`;
        ctx.fillStyle = h.rightColor || '#A0B4C2';
        ctx.fillText(h.rightText, x + w - rm.w - 24, y + (bh - rm.h) / 2 + rm.up);
      }
    } else if (style === 'live-pill') {
      const badge = h.badge ? h.badge + '  ' : '';
      const full = badge + raw;
      const lm = metrics(full, em);
      const pw = Math.min(maxW, lm.w + 96);
      const ph = Math.max(72, lm.h + 28);
      const px = h.align === 'center' ? Math.round((h.x ?? 540) - pw / 2) : (h.x ?? 60);
      const py = h.y ?? 70;
      ctx.beginPath();
      ctx.roundRect(px, py, pw, ph, 99);
      ctx.fillStyle = h.bg || 'rgba(18,24,34,0.86)';
      ctx.fill();
      if (h.border) {
        ctx.strokeStyle = h.border;
        ctx.lineWidth = h.borderW || 3;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(px + 34, py + ph / 2, 10, 0, Math.PI * 2);
      ctx.fillStyle = h.dotColor || '#4ADE80';
      ctx.fill();
      ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#FFFFFF';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(full, px + 56 + lm.left, py + (ph - lm.h) / 2 + lm.up);
    } else if (style === 'plain-center' || style === 'plain-left') {
      const x = style === 'plain-center' ? Math.round((h.x ?? 540) - m.w / 2) : (h.x ?? 60);
      const y = h.y ?? 72;
      ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#FFFFFF';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(raw, x + m.left, y + m.up);
    } else {
      // 'pill', 'rect', 'rect-border'
      const isPill = style === 'pill';
      const pw = Math.min(maxW, m.w + (isPill ? 76 : 60));
      const ph = Math.max(66, m.h + 26);
      const px = h.align === 'center' ? Math.round((h.x ?? 540) - pw / 2) : (h.x ?? 60);
      const py = h.y ?? 70;
      const rad = isPill ? 99 : (h.radius ?? 12);
      ctx.beginPath();
      ctx.roundRect(px, py, pw, ph, Math.min(rad, ph / 2));
      if (h.bg) {
        ctx.fillStyle = h.bg;
        ctx.fill();
      }
      if (h.border || style === 'rect-border') {
        ctx.strokeStyle = h.border || h.color || '#FFFFFF';
        ctx.lineWidth = h.borderW || 4;
        ctx.stroke();
      }
      if (h.barColor) {
        ctx.save();
        ctx.clip();
        ctx.fillStyle = h.barColor;
        ctx.fillRect(px, py, 10, ph);
        ctx.restore();
      }
      ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = h.color || '#FFFFFF';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(raw, px + (pw - m.w) / 2 + m.left, py + (ph - m.h) / 2 + m.up);
    }
    ctx.restore();
  }

  function drawRichTitleLine(text, em, baseX, baseY, align, tSpec, lineIdx) {
    const ws = words().sort((a, b) => b.length - a.length);
    const baseColor = lineIdx === 1 && tSpec.line2Color ? tSpec.line2Color : (tSpec.color || '#FFFFFF');
    const hlColor = tSpec.highlightColor || baseColor;
    const hlMode = tSpec.highlightMode || 'color';

    const runs = [];
    let i = 0;
    while (i < text.length) {
      const hit = ws.find(w => text.startsWith(w, i));
      if (hit) {
        runs.push({ text: hit, hl: true });
        i += hit.length;
      } else {
        const ch = Array.from(text.slice(i))[0];
        if (runs.length && !runs[runs.length - 1].hl) runs[runs.length - 1].text += ch;
        else runs.push({ text: ch, hl: false });
        i += ch.length;
      }
    }

    probe.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
    const padPill = hlMode === 'pill' || hlMode === 'outline-pill' ? 14 : 0;
    const runWidths = runs.map(r => probe.measureText(r.text).width + (r.hl ? padPill * 2 : 0));
    const totalW = runWidths.reduce((a, b) => a + b, 0);
    const fullM = metrics(text, em);
    let cursorX = align === 'center' ? baseX - totalW / 2 : baseX;
    const baselineY = baseY + fullM.up;

    ctx.save();
    ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
    ctx.textBaseline = 'alphabetic';
    ctx.lineJoin = 'round';
    ctx.miterLimit = 2;

    // Pass 1: highlight backdrops/underlines.
    let rx = cursorX;
    for (let idx = 0; idx < runs.length; idx++) {
      const r = runs[idx];
      const rw = runWidths[idx];
      if (r.hl && (hlMode === 'pill' || hlMode === 'outline-pill')) {
        const py = baseY - 8;
        const ph = fullM.h + 18;
        ctx.beginPath();
        ctx.roundRect(rx + 2, py, rw - 4, ph, 16);
        if (tSpec.highlightBg) {
          ctx.fillStyle = tSpec.highlightBg;
          ctx.fill();
        }
        if (hlMode === 'outline-pill' || tSpec.highlightBorder) {
          ctx.strokeStyle = tSpec.highlightBorder || hlColor;
          ctx.lineWidth = 5;
          ctx.stroke();
        }
      } else if (r.hl && hlMode === 'underline') {
        const uh = Math.max(14, Math.round(em * 0.22));
        ctx.fillStyle = tSpec.underlineColor || hlColor;
        ctx.beginPath();
        ctx.roundRect(rx, baselineY - uh + 10, rw, uh, 6);
        ctx.fill();
      }
      rx += rw;
    }

    // Pass 2: glyphs with optional hardShadow + stroke + fill.
    rx = cursorX;
    for (let idx = 0; idx < runs.length; idx++) {
      const r = runs[idx];
      const rw = runWidths[idx];
      const gx = rx + (r.hl ? padPill : 0);
      const fill = r.hl ? (hlMode === 'underline' ? baseColor : hlColor) : baseColor;
      if (tSpec.hardShadow) {
        ctx.fillStyle = tSpec.hardShadow;
        ctx.fillText(r.text, gx + 7, baselineY + 7);
      }
      if (tSpec.stroke && tSpec.strokeW) {
        ctx.strokeStyle = tSpec.stroke;
        ctx.lineWidth = tSpec.strokeW;
        ctx.strokeText(r.text, gx, baselineY);
      }
      ctx.fillStyle = fill;
      ctx.fillText(r.text, gx, baselineY);
      rx += rw;
    }
    ctx.restore();
  }

  function drawTitle(l) {
    const t = spec.title || { x: 48, y: 200, w: 984, h: 320, align: 'center' };
    const r = l.title;
    ctx.save();
    if (t.mode === 'band') {
      if (t.angle) {
        ctx.translate(t.x + t.w / 2, t.y + t.h / 2);
        ctx.rotate((t.angle * Math.PI) / 180);
        ctx.translate(-(t.x + t.w / 2), -(t.y + t.h / 2));
      }
      ctx.beginPath();
      ctx.roundRect(t.x, t.y, t.w, t.h, t.radius ?? 0);
      ctx.fillStyle = t.bg || '#14161A';
      ctx.fill();
      if (t.border && t.borderW) {
        ctx.strokeStyle = t.border;
        ctx.lineWidth = t.borderW;
        ctx.stroke();
      }
      if (t.borderTop) {
        ctx.fillStyle = t.borderTop;
        ctx.fillRect(t.x, t.y, t.w, t.borderW || 6);
      }
      if (t.borderBottom) {
        ctx.fillStyle = t.borderBottom;
        ctx.fillRect(t.x, t.y + t.h - (t.borderW || 6), t.w, t.borderW || 6);
      }
    }

    if (r.error) {
      ctx.font = `900 56px CoverBlack, "PingFang SC", sans-serif`;
      ctx.fillStyle = '#FF5A4F';
      ctx.textAlign = 'center';
      ctx.fillText('主标题待调整', t.x + t.w / 2, t.y + t.h / 2);
      ctx.restore();
      return;
    }

    const heights = r.lines.map(line => metrics(line, r.em).h);
    const totalH = heights.reduce((a, b) => a + b, 0) + r.gap * (r.lines.length - 1);
    const startBoxY = t.mode === 'band' && t.textY ? t.textY : t.y;
    const availH = t.mode === 'band' && t.textH ? t.textH : t.h;
    let y = startBoxY + (availH - totalH) / 2;
    const align = t.align || 'center';
    const anchorX = align === 'center' ? t.x + t.w / 2 : t.x;
    r.lines.forEach((line, idx) => {
      drawRichTitleLine(line, r.em, anchorX, y, align, t, idx);
      y += heights[idx] + r.gap;
    });
    ctx.restore();
  }

  function drawHost(l) {
    const h = spec.host || { pos: 'right', top: 820, maxW: 650, maxH: 900, outline: '#FFFFFF' };
    const scale = Math.min(h.maxW / avatar.width, h.maxH / avatar.height);
    const dw = Math.round(avatar.width * scale);
    const dh = Math.round(avatar.height * scale);
    let dx;
    if (h.pos === 'left') dx = -12;
    else if (h.pos === 'center') dx = Math.round((1080 - dw) / 2);
    else dx = 1080 - dw + 12;
    // Anchor bottom flush behind the brand card so no floating gap or poke-through occurs.
    const brandY = l.brand?.y ?? 1712;
    const anchorBottom = Math.max(h.top + h.maxH, brandY + 28);
    const dy = anchorBottom - dh;

    ctx.save();
    // Clip just below brand top edge so waist never protrudes under the brand card.
    ctx.beginPath();
    ctx.rect(0, 0, 1080, brandY + 34);
    ctx.clip();

    if (h.outline) {
      const mask = document.createElement('canvas');
      mask.width = avatar.width;
      mask.height = avatar.height;
      const mc = mask.getContext('2d');
      mc.drawImage(avatar, 0, 0);
      mc.globalCompositeOperation = 'source-in';
      mc.fillStyle = h.outline;
      mc.fillRect(0, 0, mask.width, mask.height);
      const rad = 5;
      for (let a = 0; a < Math.PI * 2; a += Math.PI / 8) {
        ctx.drawImage(mask, dx + Math.cos(a) * rad, dy + Math.sin(a) * rad, dw, dh);
      }
    }
    ctx.shadowColor = 'rgba(0,0,0,0.26)';
    ctx.shadowBlur = 26;
    ctx.shadowOffsetY = 10;
    ctx.drawImage(avatar, dx, dy, dw, dh);
    ctx.restore();
  }

  function drawIdentity(l) {
    const idSpec = spec.identity || {};
    const r = l.identity;
    ctx.save();
    ctx.translate(r.x + r.w / 2, r.y + r.h / 2);
    if (idSpec.angle) ctx.rotate((idSpec.angle * Math.PI) / 180);
    const rad = Math.min(idSpec.radius ?? 18, r.h / 2, r.w / 2);
    if (idSpec.hardShadow) {
      ctx.beginPath();
      ctx.roundRect(-r.w / 2 + 7, -r.h / 2 + 7, r.w, r.h, rad);
      ctx.fillStyle = idSpec.hardShadow;
      ctx.fill();
    } else {
      ctx.shadowColor = 'rgba(0,0,0,0.22)';
      ctx.shadowBlur = 18;
      ctx.shadowOffsetY = 6;
    }
    ctx.beginPath();
    ctx.roundRect(-r.w / 2, -r.h / 2, r.w, r.h, rad);
    ctx.fillStyle = idSpec.bg || '#14161A';
    ctx.fill();
    ctx.shadowColor = 'transparent';
    if (idSpec.border) {
      ctx.strokeStyle = idSpec.border;
      ctx.lineWidth = idSpec.borderW || 4;
      ctx.stroke();
    }
    if (idSpec.accentBar) {
      ctx.save();
      ctx.clip();
      ctx.fillStyle = idSpec.accentBar;
      ctx.fillRect(-r.w / 2, -r.h / 2, 10, r.h);
      ctx.restore();
    }
    const lines = r.error ? ['待指定社交媒体名'] : r.lines;
    const em = r.error ? 32 : r.em;
    const hs = lines.map(t => metrics(t, em));
    const totalH = hs.reduce((a, m) => a + m.h, 0) + (lines.length - 1) * 6;
    let y = -totalH / 2;
    ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
    ctx.fillStyle = idSpec.color || '#FFFFFF';
    ctx.textBaseline = 'alphabetic';
    lines.forEach((line, idx) => {
      const m = hs[idx];
      ctx.fillText(line, -m.w / 2 + m.left, y + m.up);
      y += m.h + 6;
    });
    ctx.restore();
  }

  function drawBrand(l) {
    const b = spec.brand || { x: 42, y: 1712, w: 996, h: 134 };
    const r = l.brand;
    const rad = Math.min(b.radius ?? 20, b.h / 2);
    ctx.save();
    if (b.hardShadow) {
      ctx.beginPath();
      ctx.roundRect(b.x + 8, b.y + 8, b.w, b.h, rad);
      ctx.fillStyle = b.hardShadow;
      ctx.fill();
    } else if (b.shadow) {
      ctx.shadowColor = b.shadow;
      ctx.shadowBlur = 24;
      ctx.shadowOffsetY = 8;
    }
    ctx.beginPath();
    ctx.roundRect(b.x, b.y, b.w, b.h, rad);
    ctx.fillStyle = b.bg || '#FFFFFF';
    ctx.fill();
    ctx.shadowColor = 'transparent';
    if (b.border) {
      ctx.strokeStyle = b.border;
      ctx.lineWidth = b.borderW || 4;
      ctx.stroke();
    }
    if (b.bottomBar) {
      ctx.save();
      ctx.clip();
      ctx.fillStyle = b.bottomBar;
      ctx.fillRect(b.x, b.y + b.h - 10, b.w, 10);
      ctx.restore();
    }
    if (b.topBottomLine) {
      ctx.fillStyle = b.topBottomLine;
      ctx.fillRect(b.x, b.y, b.w, 3);
      ctx.fillRect(b.x, b.y + b.h - 3, b.w, 3);
    }

    const lines = r.error ? ['完整院名待调整'] : r.lines;
    const em = r.error ? 44 : r.em;
    const hs = lines.map(t => metrics(t, em));
    const totalH = hs.reduce((a, m) => a + m.h, 0) + (lines.length - 1) * 6;
    let y = b.y + (b.h - totalH) / 2;
    ctx.font = `900 ${em}px CoverBlack, "PingFang SC", sans-serif`;
    ctx.fillStyle = b.color || '#14161A';
    ctx.textBaseline = 'alphabetic';
    lines.forEach((line, idx) => {
      const m = hs[idx];
      ctx.fillText(line, b.x + (b.w - m.w) / 2 + m.left, y + m.up);
      y += m.h + 6;
    });
    ctx.restore();
  }

  function render() {
    const l = layout();
    lastLayout = l;
    ctx.save();
    ctx.setTransform(renderScale, 0, 0, renderScale, 0, 0);
    ctx.drawImage(bgCanvas, 0, 0);
    for (const s of spec.stickers || []) if (s.behind) drawStickerItem(s);
    drawPhotos(l);
    drawHost(l);
    // For band mode titles (#02/#09), header pill sits cleanly above the title band.
    if (spec.title?.mode === 'band') {
      drawTitle(l);
      drawHeader();
    } else {
      drawHeader();
      drawTitle(l);
    }
    for (const s of spec.stickers || []) if (!s.behind) drawStickerItem(s);
    drawIdentity(l);
    drawBrand(l);
    ctx.restore();

    canvas.dataset.ready = String(!l.errors.length);
    canvas.dataset.labelFontSizes = [l.tags.em, l.tags.em, l.tags.em].join(',');
    onchange(l);
    return l;
  }

  // Automatic fallback for workbench batch delivery (preserves full institution name).
  const originalTitle = state.title;
  let titleAdjustment;
  if (input.automaticTitleFallback && titleLayout().error) {
    state.title = originalTitle.replace(/\n/g, '');
    if (titleLayout().error) state.highlights = '';
    if (titleLayout().error) state.title = input.automaticTitleFallback;
    if (titleLayout().error) state.title = '养老机构实地探访';
    if (!titleLayout().error) titleAdjustment = { original: originalTitle, resolved: state.title };
  }
  const originalTags = [...state.tags];
  let tagAdjustment;
  if (input.automaticTitleFallback && tagsLayout().error) {
    const maxWs = tagSlotMaxWidths();
    const fitTag = (raw, idx) => {
      let t = String(raw || '').trim();
      if (!t) return '院内实景';
      if (metrics(t, 28).w <= maxWs[idx]) return t;
      t = t.replace(/^(?:院内|院区|机构|专属|花园式|无障碍|适老化|多功能)/, '') || t;
      while (t.length > 2 && metrics(t, 28).w > maxWs[idx]) t = t.slice(0, -1);
      return t;
    };
    state.tags = originalTags.map(fitTag);
    if (!tagsLayout().error) tagAdjustment = { original: originalTags, resolved: [...state.tags] };
  }

  render();

  return {
    ready: true,
    report: () => ({
      ...lastLayout,
      template: input.template || 'custom',
      identity: { ...input.identity },
      institution_name: state.brand,
      title_text: state.title,
      title_highlights: words(),
      titleAdjustment,
      tagAdjustment,
      label_texts: [...state.tags],
      imageGenerationUsed: false,
    }),
    png: () => {
      if (lastLayout.errors.length) throw new Error(lastLayout.errors.join('; '));
      return canvas.toDataURL('image/png');
    },
    set: patch => {
      Object.assign(state, patch);
      return render();
    },
    replacePhoto: async (i, url) => {
      photos[i] = await loadImage(url);
      if (input.photos && input.photos[i]) input.photos[i] = { data: url };
      return render();
    },
  };
}

function drawBackground(bc, bg) {
  if (bg.type === 'linear' && Array.isArray(bg.colors) && bg.colors.length) {
    const g = bc.createLinearGradient(0, 0, 0, 1920);
    bg.colors.forEach((c, i) => g.addColorStop(i / Math.max(1, bg.colors.length - 1), c));
    bc.fillStyle = g;
    bc.fillRect(0, 0, 1080, 1920);
  } else if (bg.type === 'radial' && Array.isArray(bg.colors) && bg.colors.length) {
    const g = bc.createRadialGradient(540, 560, 60, 540, 700, 1300);
    bg.colors.forEach((c, i) => g.addColorStop(i / Math.max(1, bg.colors.length - 1), c));
    bc.fillStyle = g;
    bc.fillRect(0, 0, 1080, 1920);
  } else if (bg.type === 'split') {
    const splitY = bg.splitY ?? 620;
    if (bg.topEnd) {
      const g = bc.createLinearGradient(0, 0, 0, splitY);
      g.addColorStop(0, bg.topColor || '#163D37');
      g.addColorStop(1, bg.topEnd);
      bc.fillStyle = g;
    } else {
      bc.fillStyle = bg.topColor || '#163D37';
    }
    bc.fillRect(0, 0, 1080, splitY);
    bc.fillStyle = bg.bottomColor || '#F5F7F1';
    bc.fillRect(0, splitY, 1080, 1920 - splitY);
  } else {
    bc.fillStyle = bg.color || '#14161A';
    bc.fillRect(0, 0, 1080, 1920);
  }

  if (bg.pattern === 'dots') {
    bc.fillStyle = bg.dotColor || 'rgba(255,255,255,0.12)';
    for (let y = 20; y < 1920; y += 36) {
      for (let x = 20; x < 1080; x += 36) {
        bc.beginPath();
        bc.arc(x, y, 3, 0, Math.PI * 2);
        bc.fill();
      }
    }
  } else if (bg.pattern === 'grid') {
    bc.strokeStyle = bg.gridColor || 'rgba(255,255,255,0.12)';
    bc.lineWidth = 1.5;
    bc.beginPath();
    for (let x = 0; x <= 1080; x += 48) {
      bc.moveTo(x, 0);
      bc.lineTo(x, 1920);
    }
    for (let y = 0; y <= 1920; y += 48) {
      bc.moveTo(0, y);
      bc.lineTo(1080, y);
    }
    bc.stroke();
  }

  if (Array.isArray(bg.hazardTop) && bg.hazardTop.length >= 2) {
    bc.save();
    bc.beginPath();
    bc.rect(0, 0, 1080, 40);
    bc.clip();
    bc.fillStyle = bg.hazardTop[0];
    bc.fillRect(0, 0, 1080, 40);
    bc.fillStyle = bg.hazardTop[1];
    for (let x = -40; x < 1140; x += 56) {
      bc.beginPath();
      bc.moveTo(x, 0);
      bc.lineTo(x + 28, 0);
      bc.lineTo(x - 12, 40);
      bc.lineTo(x - 40, 40);
      bc.closePath();
      bc.fill();
    }
    bc.restore();
  }

  if (bg.sprockets) {
    bc.fillStyle = bg.sprockets;
    for (let y = 32; y < 1900; y += 76) {
      bc.beginPath();
      bc.roundRect(14, y, 24, 40, 7);
      bc.roundRect(1042, y, 24, 40, 7);
      bc.fill();
    }
  }

  if (bg.frameBorder) {
    const ins = bg.frameInset ?? 24;
    bc.beginPath();
    bc.roundRect(ins, ins, 1080 - ins * 2, 1920 - ins * 2, bg.frameRadius ?? 0);
    bc.strokeStyle = bg.frameBorder;
    bc.lineWidth = bg.frameW ?? 4;
    bc.stroke();
  }

  if (Array.isArray(bg.doubleFrame) && bg.doubleFrame.length >= 2) {
    bc.strokeStyle = bg.doubleFrame[0];
    bc.lineWidth = 4;
    bc.strokeRect(22, 22, 1036, 1876);
    bc.strokeStyle = bg.doubleFrame[1];
    bc.lineWidth = 2;
    bc.strokeRect(36, 36, 1008, 1848);
  }
}
