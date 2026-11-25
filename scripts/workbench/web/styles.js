'use strict';
// Style Library (风格库) — 30+ cohesive Cover & Video styles + Digital Human visual bindings.
paths.palette = '<path d="M12 3a9 9 0 1 0 0 18h1.5a2.5 2.5 0 0 0 0-5H12a2 2 0 0 1-2-2c0-1.1.9-2 2-2h4a5 5 0 0 0 5-5 4 4 0 0 0-9-4Z"/><circle cx="7.5" cy="10.5" r="1"/><circle cx="10.5" cy="7.5" r="1"/><circle cx="15" cy="7.5" r="1"/>';
labels.styles = '风格库';

let styleTab = 'covers';
let styleCategory = '全部';
let styleQuery = '';
let styleOnlyBound = false;

let coverEnginePromise = null;
let stickersPromise = null;
const styleDemoImageCache = new Map();
const coverThumbCache = new Map();
let coverRenderQueue = Promise.resolve();
let styleLazyObserver = null;

function loadCoverEngine() {
  if (!coverEnginePromise) coverEnginePromise = import('/assets/covers/engine/cover_engine.mjs');
  return coverEnginePromise;
}
function loadStyleStickers() {
  if (!stickersPromise) stickersPromise = import('/assets/styles/stickers.mjs').then(m => m.STICKERS || {});
  return stickersPromise;
}

function getStylesData() {
  return state?.styles || { defaultCoverId: 'teal-adaptive', defaultVideoStyleId: 'fullscreen-orange', covers: [], videos: [], demoAssets: {} };
}
function findCoverStyle(id) {
  const s = getStylesData();
  return s.covers.find(c => c.id === id) || s.covers.find(c => c.id === s.defaultCoverId) || s.covers[0];
}
function findVideoStyle(id) {
  const s = getStylesData();
  return s.videos.find(v => v.id === id) || s.videos.find(v => v.id === s.defaultVideoStyleId) || s.videos[0];
}

function buildDemoCoverInput(entry, scale = 1 / 3) {
  const demo = getStylesData().demoAssets || {};
  return {
    template: entry.id,
    styleName: entry.name,
    styleSpec: entry.coverSpec,
    renderScale: scale,
    _imageCache: styleDemoImageCache,
    header_text: '林暖带你看养老',
    title: '湖畔花园养老院\n真有这么舒服吗',
    highlights: ['湖畔花园'],
    labels: ['阳光单间', '湖畔花园', '营养膳食'],
    institution_name: '青松里（南山湖畔）颐养社区',
    identity: { person_id: 'demo', person_name: '林小暖', social_media_name: '林暖带你看' },
    avatar: demo.host || '/media/style-demo/virtual-host.png',
    avatar_box: [365, 750, 680, 955],
    font: demo.font || '/media/style-demo/font.otf',
    photos: [
      { data: demo.room || '/media/style-demo/scene-room.jpg' },
      { data: demo.garden || '/media/style-demo/scene-garden.jpg' },
      { data: demo.lounge || '/media/style-demo/scene-lounge.jpg' },
      { data: demo.dining || '/media/style-demo/scene-dining.jpg' },
    ],
  };
}

function renderCoverThumbDataUrl(entry, scale = 1 / 3) {
  const key = `${entry.id}@${scale.toFixed(3)}`;
  if (coverThumbCache.has(key)) return Promise.resolve(coverThumbCache.get(key));
  const job = coverRenderQueue.then(async () => {
    if (coverThumbCache.has(key)) return coverThumbCache.get(key);
    const { mountCover } = await loadCoverEngine();
    const canvas = document.createElement('canvas');
    await mountCover(canvas, buildDemoCoverInput(entry, scale));
    const url = canvas.toDataURL('image/jpeg', scale < 0.45 ? 0.86 : 0.92);
    coverThumbCache.set(key, url);
    return url;
  });
  coverRenderQueue = job.catch(() => {});
  return job;
}

function hydrateCoverPreview(container, entry, scale = 1 / 3) {
  if (!container || !entry) return;
  const key = `${entry.id}@${scale.toFixed(3)}`;
  if (coverThumbCache.has(key)) {
    container.innerHTML = `<img class="style-cover-img" src="${coverThumbCache.get(key)}" alt="${E(entry.name)} 封面预览">`;
    return;
  }
  renderCoverThumbDataUrl(entry, scale)
    .then(url => {
      if (container.isConnected && container.dataset.coverId === entry.id) {
        container.innerHTML = `<img class="style-cover-img" src="${url}" alt="${E(entry.name)} 封面预览">`;
      }
    })
    .catch(() => {
      if (container.isConnected) {
        container.innerHTML = '<div class="style-preview-placeholder">预览暂未就绪</div>';
      }
    });
}

function observeCoverContainers(root, scale = 1 / 3) {
  if (styleLazyObserver) styleLazyObserver.disconnect();
  const targets = [...root.querySelectorAll('[data-cover-preview]')];
  if (!targets.length) return;
  styleLazyObserver = new IntersectionObserver(entries => {
    for (const item of entries) {
      if (!item.isIntersecting) continue;
      styleLazyObserver.unobserve(item.target);
      const entry = findCoverStyle(item.target.dataset.coverId);
      if (entry) hydrateCoverPreview(item.target, entry, scale);
    }
  }, { rootMargin: '240px 0px' });
  targets.forEach(el => {
    const entry = findCoverStyle(el.dataset.coverId);
    const key = `${el.dataset.coverId}@${scale.toFixed(3)}`;
    if (entry && coverThumbCache.has(key)) {
      hydrateCoverPreview(el, entry, scale);
    } else {
      styleLazyObserver.observe(el);
    }
  });
}

// Live declarative 9:16 Video Style miniature SVG (mirrors compose_fullscreen.py 1:1).
let cachedStickerStrings = {};
loadStyleStickers().then(s => { cachedStickerStrings = s; }).catch(() => {});

function embedStickerSvg(key, x, y, w, h, uid, rotate = 0) {
  const raw = cachedStickerStrings[key];
  if (!raw) return '';
  const m = raw.match(/viewBox="([^"]+)"/);
  const vb = m ? m[1] : '0 0 180 180';
  let inner = raw.trim().replace(/^<svg[^>]*>|<\/svg>\s*$/g, '');
  const ids = [...new Set([...inner.matchAll(/\bid="([^"]+)"/g)].map(x => x[1]))];
  for (const old of ids) {
    const nxt = `p_${uid}_${old}`;
    inner = inner.replaceAll(`id="${old}"`, `id="${nxt}"`).replaceAll(`url(#${old})`, `url(#${nxt})`);
  }
  const svg = `<svg x="${x}" y="${y}" width="${w}" height="${h}" viewBox="${vb}" overflow="visible">${inner}</svg>`;
  return rotate ? `<g transform="rotate(${rotate} ${x + w / 2} ${y + h / 2})">${svg}</g>` : svg;
}

function videoStyleSvg(entry, uid = 'v') {
  const vspec = entry.videoSpec || {};
  const headline = '实地探访青松里';
  const brand = '青松里 · 南山湖畔';
  const chapterText = '居室环境';

  if (entry.id === 'fullscreen-orange' || vspec.engine === 'fullscreen-orange') {
    return `<svg class="video-stage-svg" viewBox="0 0 916 1717" preserveAspectRatio="none">
      <g transform="translate(46 107) scale(0.9)">
        <text x="458" y="220" text-anchor="middle" font-size="82" font-weight="900" font-style="italic" fill="#FFFFFF" stroke="#14100C" stroke-width="10" paint-order="stroke fill" stroke-linejoin="round">${headline}</text>
        <path fill="#FF941F" d="M58 265L788 251L856 265L829 278L877 288L853 304L887 316L871 330L883 344L858 354L871 370L827 383L92 401L38 383L51 369L24 351L45 337L26 321L55 306L42 291L76 281Z"/>
        <text x="458" y="353" text-anchor="middle" font-size="74" font-weight="900" font-style="italic" fill="#FFFFFF" stroke="#111" stroke-width="10" paint-order="stroke fill" stroke-linejoin="round">${brand}</text>
      </g>
      <rect x="60" y="1025" width="245" height="76" rx="7" fill="#FF982D"/>
      <text x="182" y="1079" text-anchor="middle" font-size="44" font-weight="900" fill="#111111">${chapterText}</text>
      <g transform="matrix(0.866 0 -0.045 0.911 458 1390)">
        <text text-anchor="middle" font-size="86" font-weight="900" fill="#FFFFFF" stroke="#15100B" stroke-width="10" paint-order="stroke fill" stroke-linejoin="round">房间采光很好</text>
      </g>
      <g transform="matrix(0.796 0 -0.042 0.806 458 1496)">
        <text text-anchor="middle" font-size="86" font-weight="900" fill="#FA98C3" stroke="#15100B" stroke-width="10" paint-order="stroke fill" stroke-linejoin="round">配备<tspan fill="#FDD722" font-size="104">24小时监测</tspan></text>
      </g>
    </svg>`;
  }

  const h = vspec.header || {};
  const ch = vspec.chapter || {};
  const cap = vspec.caption || {};
  const scrim = vspec.scrim || {};
  const side = vspec.avatarSide || 'left';
  const variant = h.variant || 'card-border';

  let defs = '';
  let scrimSvg = '';
  if (scrim.top) {
    defs += `<linearGradient id="${uid}_st" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${E(scrim.top)}"/><stop offset="100%" stop-color="rgba(0,0,0,0)"/></linearGradient>`;
    scrimSvg += `<rect x="0" y="0" width="1080" height="470" fill="url(#${uid}_st)"/>`;
  }
  if (scrim.bottom) {
    defs += `<linearGradient id="${uid}_sb" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(0,0,0,0)"/><stop offset="100%" stop-color="${E(scrim.bottom)}"/></linearGradient>`;
    scrimSvg += `<rect x="0" y="1320" width="1080" height="600" fill="url(#${uid}_sb)"/>`;
  }

  let headerSvg = '';
  const tc = E(h.titleColor || '#FFFFFF');
  const bc = E(h.brandColor || '#FFFFFF');
  const bg = E(h.bg || 'rgba(20,22,26,0.88)');
  const bd = E(h.border || '');
  const rad = h.radius ?? 22;

  if (variant === 'double-pill') {
    headerSvg = `<rect x="130" y="128" width="820" height="96" rx="48" fill="${E(h.topBg || '#FFF7EA')}" stroke="${E(h.topBorder || '#F48C2E')}" stroke-width="5"/>
      <text x="540" y="195" text-anchor="middle" font-size="54" font-weight="900" fill="${E(h.topColor || '#5A341C')}">${headline}</text>
      <rect x="160" y="238" width="760" height="86" rx="43" fill="${E(h.botBg || '#F48C2E')}" stroke="${E(h.botBorder || '#FFF7EA')}" stroke-width="4"/>
      <text x="540" y="298" text-anchor="middle" font-size="46" font-weight="900" fill="${E(h.botColor || '#FFFFFF')}">${brand}</text>
      ${h.sticker ? embedStickerSvg(h.sticker, 875, 92, 110, 110, uid + 'h') : ''}`;
  } else if (variant === 'minimal-corner') {
    headerSvg = `<rect x="56" y="130" width="600" height="156" rx="20" fill="${bg}"/>
      ${h.sticker ? embedStickerSvg(h.sticker, 74, 166, 78, 78, uid + 'h') : ''}
      <text x="${h.sticker ? 166 : 90}" y="198" font-size="42" font-weight="900" fill="${tc}">${headline}</text>
      <text x="${h.sticker ? 166 : 90}" y="254" font-size="38" font-weight="900" fill="${bc}">${brand}</text>`;
  } else if (variant === 'split-badge') {
    headerSvg = `<rect x="68" y="132" width="944" height="182" rx="22" fill="${bg}" stroke="${bd || '#FFFDF8'}" stroke-width="4"/>
      <rect x="82" y="146" width="166" height="154" rx="16" fill="${E(h.badgeBg || '#F9A03F')}"/>
      <text x="165" y="242" text-anchor="middle" font-size="50" font-weight="900" fill="#181818">${E(h.badgeText || '实探')}</text>
      <text x="274" y="206" font-size="50" font-weight="900" fill="${tc}">${headline}</text>
      <text x="274" y="276" font-size="46" font-weight="900" fill="${bc}">${brand}</text>`;
  } else if (variant === 'editorial-lines') {
    const lc = E(h.lineColor || '#FFFFFF');
    const st = E(h.stroke || '#000000');
    headerSvg = `<rect x="76" y="130" width="928" height="4" fill="${lc}"/>
      <text x="540" y="212" text-anchor="middle" font-size="56" font-weight="900" fill="${tc}" stroke="${st}" stroke-width="8" paint-order="stroke fill">${headline}</text>
      <text x="540" y="286" text-anchor="middle" font-size="48" font-weight="900" fill="${bc}" stroke="${st}" stroke-width="8" paint-order="stroke fill">${brand}</text>
      <rect x="76" y="314" width="928" height="4" fill="${lc}"/>`;
  } else if (variant === 'vlog-underline') {
    const st = E(h.stroke || '#1E3A2F');
    headerSvg = `<text x="540" y="206" text-anchor="middle" font-size="56" font-weight="900" fill="${tc}" stroke="${st}" stroke-width="10" paint-order="stroke fill">${headline}</text>
      <rect x="200" y="226" width="680" height="8" rx="4" fill="${E(h.lineColor || '#A7D796')}"/>
      <text x="540" y="294" text-anchor="middle" font-size="48" font-weight="900" fill="${bc}" stroke="${st}" stroke-width="10" paint-order="stroke fill">${brand}</text>
      ${h.sticker ? embedStickerSvg(h.sticker, 860, 114, 130, 118, uid + 'h') : ''}`;
  } else if (variant === 'glass-left-bar') {
    headerSvg = `<rect x="64" y="132" width="952" height="182" rx="20" fill="${bg}" stroke="${bd || '#DCEABC88'}" stroke-width="3"/>
      <rect x="64" y="132" width="16" height="182" rx="8" fill="${E(h.bar || '#DCEABC')}"/>
      <text x="114" y="208" font-size="52" font-weight="900" fill="${tc}">${headline}</text>
      <text x="114" y="278" font-size="46" font-weight="900" fill="${bc}">${brand}</text>
      ${h.sticker ? embedStickerSvg(h.sticker, 868, 134, 136, 118, uid + 'h') : ''}`;
  } else if (variant === 'icon-card') {
    headerSvg = `<rect x="64" y="132" width="952" height="182" rx="${rad}" fill="${bg}" stroke="${bd || '#C89B54'}" stroke-width="${h.borderW || 4}"/>
      ${h.icon ? embedStickerSvg(h.icon, 94, 166, 114, 114, uid + 'h') : ''}
      <text x="236" y="208" font-size="50" font-weight="900" fill="${tc}">${headline}</text>
      <text x="236" y="278" font-size="46" font-weight="900" fill="${bc}">${brand}</text>`;
  } else if (variant === 'taped-card') {
    headerSvg = `<rect x="82" y="146" width="932" height="176" rx="16" fill="${E(h.shadow || '#D96B43')}"/>
      <rect x="74" y="138" width="932" height="176" rx="16" fill="${bg}" stroke="${bd || '#4A3428'}" stroke-width="5"/>
      <text x="540" y="212" text-anchor="middle" font-size="52" font-weight="900" fill="${tc}">${headline}</text>
      <text x="540" y="282" text-anchor="middle" font-size="46" font-weight="900" fill="${bc}">${brand}</text>
      ${h.tapeLeft ? embedStickerSvg(h.tapeLeft, 52, 108, 174, 48, uid + 'tl', -10) : ''}
      ${h.tapeRight ? embedStickerSvg(h.tapeRight, 854, 108, 174, 48, uid + 'tr', 10) : ''}`;
  } else if (variant === 'comic-flag' || variant === 'comic-card') {
    headerSvg = `<g ${variant === 'comic-flag' ? 'transform="rotate(-1.5 540 223)"' : ''}>
      <rect x="82" y="144" width="932" height="178" rx="18" fill="${E(h.shadow || '#181818')}"/>
      <rect x="72" y="134" width="932" height="178" rx="18" fill="${bg}" stroke="${bd || '#181818'}" stroke-width="6"/>
      <text x="540" y="208" text-anchor="middle" font-size="54" font-weight="900" fill="${tc}">${headline}</text>
      <text x="540" y="280" text-anchor="middle" font-size="46" font-weight="900" fill="${bc}">${brand}</text>
      ${h.sticker ? embedStickerSvg(h.sticker, 906, 102, 114, 114, uid + 'h') : ''}
    </g>`;
  } else if (variant === 'hazard-card') {
    const ac = E(bd || '#FFD426');
    headerSvg = `<rect x="64" y="132" width="952" height="186" rx="18" fill="${bg}" stroke="${ac}" stroke-width="4"/>
      <rect x="64" y="132" width="952" height="20" rx="6" fill="${ac}"/>
      <text x="540" y="222" text-anchor="middle" font-size="52" font-weight="900" fill="${tc}">${headline}</text>
      <text x="540" y="290" text-anchor="middle" font-size="46" font-weight="900" fill="${bc}">${brand}</text>
      ${h.sticker ? embedStickerSvg(h.sticker, 896, 152, 106, 106, uid + 'h') : ''}`;
  } else if (variant === 'viewfinder') {
    const cc = E(h.cornerColor || '#FFFFFF');
    headerSvg = `<rect x="64" y="130" width="952" height="188" rx="14" fill="${bg}"/>
      <path d="M64 170 V130 H108 M972 130 H1016 V170 M64 278 V318 H108 M972 318 H1016 V278" stroke="${cc}" stroke-width="5" fill="none"/>
      <circle cx="106" cy="162" r="9" fill="${E(h.dotColor || '#FF3B30')}"/>
      <text x="128" y="172" font-size="26" font-weight="900" fill="${cc}">REC</text>
      <text x="540" y="226" text-anchor="middle" font-size="50" font-weight="900" fill="${tc}">${headline}</text>
      <text x="540" y="290" text-anchor="middle" font-size="44" font-weight="900" fill="${bc}">${brand}</text>`;
  } else if (variant === 'live-pill') {
    const dc = E(h.dotColor || '#4ADE80');
    headerSvg = `<rect x="64" y="132" width="952" height="182" rx="91" fill="${bg}" stroke="${bd || dc}" stroke-width="4"/>
      <circle cx="128" cy="223" r="14" fill="${dc}"/>
      <text x="550" y="208" text-anchor="middle" font-size="50" font-weight="900" fill="${tc}">${headline}</text>
      <text x="550" y="278" text-anchor="middle" font-size="46" font-weight="900" fill="${bc}">${brand}</text>`;
  } else if (variant === 'glow-band' || variant === 'top-bottom-bar') {
    const lc = E(h.lineColor || bd || '#E5C07B');
    headerSvg = `<rect x="54" y="132" width="972" height="184" rx="16" fill="${bg}"/>
      <rect x="54" y="132" width="972" height="7" fill="${lc}"/>
      <rect x="54" y="309" width="972" height="7" fill="${lc}"/>
      <text x="540" y="212" text-anchor="middle" font-size="54" font-weight="900" fill="${tc}">${headline}</text>
      <text x="540" y="282" text-anchor="middle" font-size="46" font-weight="900" fill="${bc}">${brand}</text>
      ${h.sticker ? embedStickerSvg(h.sticker, 892, 118, 114, 114, uid + 'h') : ''}`;
  } else if (variant === 'bottom-bar-card') {
    headerSvg = `<rect x="64" y="132" width="952" height="184" rx="18" fill="${bg}"/>
      <rect x="64" y="304" width="952" height="12" rx="6" fill="${E(h.bar || '#5CE1E6')}"/>
      <text x="540" y="210" text-anchor="middle" font-size="52" font-weight="900" fill="${tc}">${headline}</text>
      <text x="540" y="278" text-anchor="middle" font-size="46" font-weight="900" fill="${bc}">${brand}</text>
      ${h.sticker ? embedStickerSvg(h.sticker, 894, 128, 110, 110, uid + 'h') : ''}`;
  } else {
    headerSvg = `<rect x="64" y="132" width="952" height="184" rx="${rad}" fill="${bg}" ${bd ? `stroke="${bd}" stroke-width="${h.borderW || 4}"` : ''}/>
      <text x="540" y="210" text-anchor="middle" font-size="52" font-weight="900" fill="${tc}">${headline}</text>
      <text x="540" y="282" text-anchor="middle" font-size="46" font-weight="900" fill="${bc}">${brand}</text>
      ${h.sticker ? embedStickerSvg(h.sticker, 892, 116, 116, 116, uid + 'h') : ''}`;
  }

  // Chapter pill positioned on avatarSide right below avatar.
  const chW = 280;
  const chX = side === 'right' ? (1080 - 56 - chW) : 56;
  const chY = 1148;
  const chR = Math.min(ch.radius ?? 14, 37);
  const chLabel = `${ch.prefix || ''}${chapterText}${ch.suffix || ''}`;
  const chSvg = `${ch.variant === 'comic-shadow' ? `<rect x="${chX + 6}" y="${chY + 6}" width="${chW}" height="74" rx="${chR}" fill="${E(ch.shadow || '#181818')}"/>` : ''}
    <rect x="${chX}" y="${chY}" width="${chW}" height="74" rx="${chR}" fill="${E(ch.bg || '#FF982D')}" ${ch.border ? `stroke="${E(ch.border)}" stroke-width="4"` : ''}/>
    ${ch.variant === 'dot-pill' ? `<circle cx="${chX + 30}" cy="${chY + 37}" r="9" fill="${E(ch.dot || ch.color || '#FFF')}"/>` : ''}
    ${['left-bar', 'left-bar-border'].includes(ch.variant) ? `<rect x="${chX + 4}" y="${chY + 8}" width="10" height="58" rx="5" fill="${E(ch.bar || ch.color || '#FFF')}"/>` : ''}
    <text x="${chX + chW / 2 + (ch.variant === 'dot-pill' ? 12 : 0)}" y="${chY + 51}" text-anchor="middle" font-size="40" font-weight="900" fill="${E(ch.color || '#111')}">${E(chLabel)}</text>`;

  // Captions matching caption color, stroke, backdrop, focusMode.
  const capColor = E(cap.color || '#FFFFFF');
  const capStroke = E(cap.stroke || '#14161A');
  const capSW = cap.strokeW ?? 12;
  const focusColor = E(cap.focusColor || '#FFE135');
  const isPill = cap.focusMode === 'pill';
  const backdrop = cap.backdrop
    ? `<rect x="190" y="1396" width="700" height="94" rx="${cap.backdropRadius || 14}" fill="${E(cap.backdrop)}"/><rect x="145" y="1504" width="790" height="98" rx="${cap.backdropRadius || 14}" fill="${E(cap.backdrop)}"/>`
    : '';
  const pillRect = isPill
    ? `<rect x="408" y="1504" width="440" height="94" rx="16" fill="${E(cap.focusBg || '#F48C2E')}" ${cap.focusBorder ? `stroke="${E(cap.focusBorder)}" stroke-width="4"` : ''}/>`
    : '';
  const focusTspan = isPill
    ? `<tspan fill="${focusColor}" stroke="none" font-size="78">24小时监测</tspan>`
    : `<tspan fill="${focusColor}" font-size="84">24小时监测</tspan>`;
  const capSvg = `${backdrop}${pillRect}
    <text x="540" y="1466" text-anchor="middle" font-size="74" font-weight="900" fill="${capColor}" stroke="${capStroke}" stroke-width="${capSW}" paint-order="stroke fill" stroke-linejoin="round">房间采光很通透</text>
    <text x="540" y="1576" text-anchor="middle" font-size="74" font-weight="900" fill="${capColor}" stroke="${capStroke}" stroke-width="${capSW}" paint-order="stroke fill" stroke-linejoin="round">配备 ${focusTspan}</text>`;

  return `<svg class="video-stage-svg" viewBox="0 0 1080 1920" preserveAspectRatio="none">
    ${defs ? `<defs>${defs}</defs>` : ''}${scrimSvg}${headerSvg}${chSvg}${capSvg}
  </svg>`;
}

function videoPreviewHtml(entry, uid = 'v') {
  const demo = getStylesData().demoAssets || {};
  const bgs = [demo.vRoom || '/media/style-demo/video-room.jpg', demo.vGarden || '/media/style-demo/video-garden.jpg', demo.vLobby || '/media/style-demo/video-lobby.jpg'];
  const bgUrl = bgs[((entry.order || 1) - 1) % bgs.length];
  const side = entry.videoSpec?.avatarSide === 'right' ? 'side-right' : 'side-left';
  return `<div class="video-stage-wrap">
    <div class="video-stage-inner">
      <img class="video-stage-bg" src="${E(bgUrl)}" alt="">
      <img class="video-stage-host ${side}" src="${E(demo.host || '/media/style-demo/virtual-host.png')}" alt="">
      ${videoStyleSvg(entry, uid)}
    </div>
  </div>`;
}

function styleCardHtml(entry, kind) {
  const isCover = kind === 'cover';
  const bound = entry.boundPeople || [];
  const boundText = bound.length ? `已绑：${bound.map(p => p.name).join('、')}` : '暂未单独绑定';
  const num = E(entry.num || String(entry.order).padStart(2, '0'));
  return `<article class="style-card ${entry.isDefault ? 'is-default' : ''}" data-open-style="${E(entry.id)}" data-style-kind="${kind}" tabindex="0" role="button" aria-label="查看${E(entry.name)}">
    <div class="style-preview-box">
      ${(entry.isDefault || bound.length) ? `<div class="style-preview-badges">
        <span></span>
        <div class="style-status-stack">
          ${entry.isDefault ? '<span class="style-status-pill is-default">当前默认</span>' : ''}
          ${bound.length ? `<span class="style-status-pill is-bound">已绑 ${bound.length} 人</span>` : ''}
        </div>
      </div>` : ''}
      ${isCover
        ? `<div class="style-cover-slot" style="width:100%;height:100%" data-cover-preview data-cover-id="${E(entry.id)}"><div class="style-preview-placeholder">渲染预览中…</div></div>`
        : videoPreviewHtml(entry, `card_${entry.id}`)}
    </div>
    <div class="style-card-body">
      <div class="style-card-head">
        <h3><span class="style-num-pill" style="margin-right:6px;vertical-align:1px">${num}</span>${E(entry.name)}</h3>
        <span class="style-cat-tag">${E(entry.category)}</span>
      </div>
      <p class="style-layout-meta">${E(entry.layoutTag || entry.description || '')}</p>
      <div class="style-swatches">${(entry.swatches || []).map(c => `<i class="style-swatch" style="background:${E(c)}"></i>`).join('')}</div>
      <div class="style-bound-row">
        <span class="style-bound-names" title="${E(boundText)}">${E(boundText)}</span>
        <span class="text-button">详情 / 绑定 ${icon('chevron-right')}</span>
      </div>
    </div>
  </article>`;
}

function filteredStyles(kind) {
  const s = getStylesData();
  const list = kind === 'cover' ? s.covers : s.videos;
  const q = styleQuery.trim().toLowerCase();
  return list.filter(item => {
    if (styleOnlyBound && !(item.boundPeople || []).length) return false;
    if (styleCategory !== '全部' && item.category !== styleCategory) return false;
    if (!q) return true;
    const hay = [item.num, item.id, item.name, item.category, item.layoutTag, item.description, ...(item.tags || [])].join(' ').toLowerCase();
    return hay.includes(q);
  });
}

function styleCategories(kind) {
  const list = kind === 'cover' ? getStylesData().covers : getStylesData().videos;
  return ['全部', ...new Set(list.map(x => x.category))];
}

function paintStylesGrid() {
  const kind = styleTab === 'videos' ? 'video' : 'cover';
  const items = filteredStyles(kind);
  const host = $('#styles-grid-host');
  if (!host) return;
  if (!items.length) {
    host.innerHTML = emptyState('没有找到匹配的样式', '试试清空关键词，或切换其他分类查看全部样式。', '<button type="button" class="secondary" data-action="reset-style-filters">重置筛选</button>');
    return;
  }
  host.innerHTML = `<div class="style-grid">${items.map(item => styleCardHtml(item, kind)).join('')}</div>`;
  if (kind === 'cover') observeCoverContainers(host, 1 / 3);
}

async function renderStylesPage() {
  const parts = location.hash.slice(1).split('/');
  if (parts[1] === 'videos') styleTab = 'videos';
  else if (parts[1] === 'covers') styleTab = 'covers';
  await loadStyleStickers().catch(() => {});
  const s = getStylesData();
  const kind = styleTab === 'videos' ? 'video' : 'cover';
  const cats = styleCategories(kind);
  if (!cats.includes(styleCategory)) styleCategory = '全部';

  main.innerHTML = `${heading('风格库', '每套样式含统一视觉的封面样式与视频样式，示例均采用虚拟写实主播与虚拟院区；数字人可按需单独绑定或成套绑定。', `<a class="secondary" href="#people">${icon('person')} 前往数字人管理</a>`)}
    <nav class="content-tabs" aria-label="风格库分类">
      <a href="#styles/covers" ${styleTab === 'covers' ? 'aria-current="page"' : ''}>封面样式<span>${s.covers.length}</span></a>
      <a href="#styles/videos" ${styleTab === 'videos' ? 'aria-current="page"' : ''}>视频样式<span>${s.videos.length}</span></a>
    </nav>
    <div class="styles-toolbar">
      <div class="filter-tabs" role="group" aria-label="按风格赛道筛选">
        ${cats.map(cat => `<button type="button" data-style-cat="${E(cat)}" class="${styleCategory === cat && !styleOnlyBound ? 'active' : ''}">${E(cat)}</button>`).join('')}
        <button type="button" data-style-bound-filter class="${styleOnlyBound ? 'active' : ''}">已绑定数字人</button>
      </div>
      <div class="styles-toolbar-right">
        <label class="search-field">${icon('search')}<input id="style-search" type="search" placeholder="搜索风格名、配色、构图…" value="${E(styleQuery)}"></label>
      </div>
    </div>
    <div id="styles-grid-host"></div>`;

  paintStylesGrid();
  $('#style-search')?.addEventListener('input', e => {
    styleQuery = e.target.value;
    paintStylesGrid();
  });
}

function openStyleDetailModal(kind, id) {
  const isCover = kind === 'cover';
  const entry = isCover ? findCoverStyle(id) : findVideoStyle(id);
  if (!entry) return;
  const pairEntry = isCover ? findVideoStyle(entry.pairId) : findCoverStyle(entry.pairId);
  const coverEntry = isCover ? entry : pairEntry;
  const videoEntry = isCover ? pairEntry : entry;
  const people = state.config.people || [];
  const fieldKey = isCover ? 'cover_template_id' : 'video_style_id';

  const dialog = document.createElement('dialog');
  dialog.className = 'style-dialog';
  dialog.innerHTML = `
    <div class="style-dialog-head">
      <h2><span class="style-num-pill">${E(entry.num || '')}</span> ${E(entry.name)} <span class="style-cat-tag">${E(entry.category)}</span></h2>
      <button type="button" data-close aria-label="关闭">${icon('close')}</button>
    </div>
    <div class="style-dialog-body">
      <div class="style-detail-grid">
        <div class="style-detail-pair">
          <div class="style-detail-frame">
            <div class="style-preview-box" data-modal-cover data-cover-id="${E(coverEntry.id)}"><div class="style-preview-placeholder">加载高清预览…</div></div>
            <span>${isCover ? '当前封面样式' : '配套封面样式'} · ${E(coverEntry.name)}</span>
          </div>
          <div class="style-detail-frame">
            <div class="style-preview-box">${videoPreviewHtml(videoEntry, 'modal_' + videoEntry.id)}</div>
            <span>${!isCover ? '当前视频样式' : '配套视频样式'} · ${E(videoEntry.name)}</span>
          </div>
        </div>
        <div class="style-detail-info">
          <p class="style-detail-desc">${E(entry.description || entry.layoutTag || '')}</p>
          <div class="style-swatches">${(entry.swatches || []).map(c => `<i class="style-swatch" style="background:${E(c)};width:18px;height:18px"></i>`).join('')}<span class="subtle" style="margin-left:6px">${E(entry.layoutTag || '')}</span></div>
          <div class="style-detail-tags">${(entry.tags || []).map(t => `<span>#${E(t)}</span>`).join('')}</div>

          <div class="style-bind-box">
            <h3>工作台默认样式</h3>
            <p class="form-help" style="margin:0 0 12px">未单独绑定样式的数字人，新建制作时会自动使用工作台默认样式。</p>
            <div class="style-dialog-actions">
              <button type="button" class="secondary" id="modal-set-default" ${entry.isDefault ? 'disabled' : ''}>${entry.isDefault ? '已是当前默认' : `设为默认${isCover ? '封面' : '视频'}样式`}</button>
              <button type="button" class="secondary" id="modal-set-default-pair">成套设为默认（封面 + 视频）</button>
            </div>
          </div>

          <div class="style-bind-box">
            <h3>绑定到数字人</h3>
            ${people.length
              ? `<p class="form-help" style="margin:0">勾选要使用此样式的数字人；取消已勾选的会恢复跟随默认（不影响绑了其他样式的人物）。</p>
                <div class="style-bind-people">${people.map(p => {
                  const checked = p[fieldKey] === entry.id;
                  const curCover = p.cover_template_id ? findCoverStyle(p.cover_template_id)?.name : '默认';
                  const curVideo = p.video_style_id ? findVideoStyle(p.video_style_id)?.name : '默认';
                  return `<label class="style-bind-person"><input type="checkbox" value="${E(p.id)}" ${checked ? 'checked' : ''}>${p.hasPortrait ? `<img src="/media/person/${E(p.id)}" alt="">` : icon('person')}<span><strong>${E(p.name)}</strong><small>当前：封面 ${E(curCover)} · 视频 ${E(curVideo)}</small></span></label>`;
                }).join('')}</div>
                <label class="style-pair-check"><input type="checkbox" id="modal-bind-pair" checked> 同时成套绑定配套的${isCover ? '视频' : '封面'}样式（${E(pairEntry.name)}）</label>
                <button type="button" class="primary wide" id="modal-save-bind">${icon('check')} 保存数字人绑定</button>`
              : '<p class="form-help">还没有添加数字人。先到「数字人」页面创建人物后再绑定。</p>'}
          </div>
          <div id="modal-style-error" class="inline-error" role="alert"></div>
        </div>
      </div>
    </div>`;

  document.body.append(dialog);
  dialog.showModal();
  hydrateCoverPreview(dialog.querySelector('[data-modal-cover]'), coverEntry, 0.5);

  const close = () => { dialog.close(); dialog.remove(); };
  dialog.querySelector('[data-close]').onclick = close;
  dialog.addEventListener('click', e => { if (e.target === dialog) close(); });

  const setDef = async (includePair, btn) => {
    $('#modal-style-error', dialog).textContent = '';
    await busy(btn, async () => {
      try {
        const res = await api('/api/styles/default', { kind, id: entry.id, includePair });
        state.styles = res.styles;
        state.config = res.config;
        notify(includePair ? `已将「${entry.name}」成套设为工作台默认` : `已将「${entry.name}」设为默认${isCover ? '封面' : '视频'}样式`);
        close();
        if (route === 'styles') paintStylesGrid();
      } catch (err) {
        $('#modal-style-error', dialog).textContent = err.message;
      }
    });
  };
  dialog.querySelector('#modal-set-default')?.addEventListener('click', e => setDef(false, e.currentTarget));
  dialog.querySelector('#modal-set-default-pair')?.addEventListener('click', e => setDef(true, e.currentTarget));

  dialog.querySelector('#modal-save-bind')?.addEventListener('click', async e => {
    $('#modal-style-error', dialog).textContent = '';
    const peopleIds = [...dialog.querySelectorAll('.style-bind-people input:checked')].map(x => x.value);
    const bindPair = Boolean(dialog.querySelector('#modal-bind-pair')?.checked);
    await busy(e.currentTarget, async () => {
      try {
        const res = await api('/api/styles/bind', { kind, id: entry.id, peopleIds, bindPair });
        state.styles = res.styles;
        state.config = res.config;
        notify('数字人视觉风格绑定已保存');
        close();
        if (route === 'styles') paintStylesGrid();
      } catch (err) {
        $('#modal-style-error', dialog).textContent = err.message;
      }
    });
  });
}

// Visual Style Picker Modal for the Digital Human Editor page.
function openPersonStylePicker(kind, currentId, onSelect) {
  const isCover = kind === 'cover';
  const s = getStylesData();
  const list = isCover ? s.covers : s.videos;
  const defEntry = isCover ? findCoverStyle(s.defaultCoverId) : findVideoStyle(s.defaultVideoStyleId);
  let pickCat = '全部';
  let pickQuery = '';

  const dialog = document.createElement('dialog');
  dialog.className = 'style-dialog';
  dialog.innerHTML = `
    <div class="style-dialog-head">
      <h2>选择专属${isCover ? '封面' : '视频'}样式（共 ${list.length} 套）</h2>
      <button type="button" data-close aria-label="关闭">${icon('close')}</button>
    </div>
    <div class="style-dialog-body">
      <div class="styles-toolbar" style="margin-bottom:10px">
        <div class="filter-tabs" data-picker-cats>
          ${styleCategories(kind).map(cat => `<button type="button" data-pick-cat="${E(cat)}" class="${cat === '全部' ? 'active' : ''}">${E(cat)}</button>`).join('')}
        </div>
        <label class="search-field">${icon('search')}<input type="search" data-pick-search placeholder="搜索风格名称或色系…"></label>
      </div>
      <div class="style-picker-grid" data-picker-grid></div>
    </div>`;
  document.body.append(dialog);
  dialog.showModal();

  const close = () => { dialog.close(); dialog.remove(); };
  dialog.querySelector('[data-close]').onclick = close;
  dialog.addEventListener('click', e => { if (e.target === dialog) close(); });

  const paintGrid = () => {
    const q = pickQuery.trim().toLowerCase();
    const filtered = list.filter(item => {
      if (pickCat !== '全部' && item.category !== pickCat) return false;
      if (!q) return true;
      return [item.num, item.name, item.category, item.layoutTag, ...(item.tags || [])].join(' ').toLowerCase().includes(q);
    });
    const grid = dialog.querySelector('[data-picker-grid]');
    const defaultCard = pickCat === '全部' && !q
      ? `<button type="button" class="style-picker-item ${!currentId ? 'is-selected' : ''}" data-pick-id="">
          <div class="style-preview-box">${isCover
            ? `<div style="width:100%;height:100%" data-cover-preview data-cover-id="${E(defEntry.id)}"></div>`
            : videoPreviewHtml(defEntry, 'pk_def_' + defEntry.id)}</div>
          <div class="style-picker-item-meta"><strong>跟随工作台默认</strong><small>当前默认：${E(defEntry.name)}</small></div>
        </button>`
      : '';
    grid.innerHTML = defaultCard + filtered.map(item => `
      <button type="button" class="style-picker-item ${currentId === item.id ? 'is-selected' : ''}" data-pick-id="${E(item.id)}">
        <div class="style-preview-box">
          ${isCover
            ? `<div style="width:100%;height:100%" data-cover-preview data-cover-id="${E(item.id)}"></div>`
            : videoPreviewHtml(item, 'pk_' + item.id)}
        </div>
        <div class="style-picker-item-meta"><strong>${E((item.num ? item.num + ' · ' : '') + item.name)}</strong><small>${E(item.category)}</small></div>
      </button>`).join('');
    if (isCover) observeCoverContainers(grid, 1 / 3);
  };

  paintGrid();
  dialog.querySelector('[data-picker-cats]').addEventListener('click', e => {
    const b = e.target.closest('[data-pick-cat]');
    if (!b) return;
    pickCat = b.dataset.pickCat;
    dialog.querySelectorAll('[data-pick-cat]').forEach(x => x.classList.toggle('active', x === b));
    paintGrid();
  });
  dialog.querySelector('[data-pick-search]').addEventListener('input', e => {
    pickQuery = e.target.value;
    paintGrid();
  });
  dialog.querySelector('[data-picker-grid]').addEventListener('click', e => {
    const item = e.target.closest('[data-pick-id]');
    if (!item) return;
    onSelect(item.dataset.pickId);
    close();
  });
}

// Mount visual style pickers inside the Digital Human Editor (#person-module-visual).
window.mountPersonStyleSelectors = async function (form) {
  const host = form?.querySelector('#person-style-bindings');
  if (!host) return;
  await loadStyleStickers().catch(() => {});
  const coverInput = form.querySelector('input[name="cover_template_id"]');
  const videoInput = form.querySelector('input[name="video_style_id"]');
  if (!coverInput || !videoInput) return;

  const paint = () => {
    const s = getStylesData();
    const cid = coverInput.value || '';
    const vid = videoInput.value || '';
    const coverEntry = findCoverStyle(cid || s.defaultCoverId);
    const videoEntry = findVideoStyle(vid || s.defaultVideoStyleId);
    const pairVideo = coverEntry?.pairId ? findVideoStyle(coverEntry.pairId) : null;
    const showPairSync = Boolean(cid && pairVideo && (vid || s.defaultVideoStyleId) !== pairVideo.id);

    host.innerHTML = `
      <div class="person-style-header">
        <label class="field-label" style="margin:0">专属封面与视频样式<span class="optional">选填</span></label>
        <a class="text-button" href="#styles">浏览全部 30 套风格库 ${icon('arrow-up-right')}</a>
      </div>
      <div class="person-style-grid">
        <div class="person-style-slot">
          <div class="person-style-thumb" data-person-cover-thumb data-cover-id="${E(coverEntry.id)}"></div>
          <div class="person-style-info">
            <span class="person-style-kind">封面样式</span>
            <strong class="person-style-name">${cid ? E(coverEntry.name) : '跟随工作台默认'}</strong>
            <span class="person-style-sub">${cid ? E(coverEntry.category + ' · ' + coverEntry.layoutTag) : '当前默认：' + E(coverEntry.name)}</span>
            <div class="person-style-actions">
              <button type="button" class="secondary" data-pick-person-style="cover">从封面库挑选</button>
              ${cid ? '<button type="button" class="text-button" data-clear-person-style="cover">恢复默认</button>' : ''}
            </div>
          </div>
        </div>
        <div class="person-style-slot">
          <div class="person-style-thumb">${videoPreviewHtml(videoEntry, 'ps_' + videoEntry.id)}</div>
          <div class="person-style-info">
            <span class="person-style-kind">视频样式</span>
            <strong class="person-style-name">${vid ? E(videoEntry.name) : '跟随工作台默认'}</strong>
            <span class="person-style-sub">${vid ? E(videoEntry.category + ' · ' + videoEntry.layoutTag) : '当前默认：' + E(videoEntry.name)}</span>
            <div class="person-style-actions">
              <button type="button" class="secondary" data-pick-person-style="video">从视频库挑选</button>
              ${vid ? '<button type="button" class="text-button" data-clear-person-style="video">恢复默认</button>' : ''}
            </div>
          </div>
        </div>
      </div>
      ${showPairSync
        ? `<div class="person-style-sync"><span>所选封面「${E(coverEntry.name)}」有同色系配套视频样式「${E(pairVideo.name)}」</span><button type="button" class="secondary" data-sync-pair-video="${E(pairVideo.id)}">一键套用配套视频样式</button></div>`
        : ''}
      <p class="form-help">保存后，新建制作选中该数字人时会自动套用其绑定的封面样式与视频样式；也可自由混搭不同封面与视频风格。</p>`;

    hydrateCoverPreview(host.querySelector('[data-person-cover-thumb]'), coverEntry, 1 / 4);
    host.querySelector('[data-pick-person-style="cover"]')?.addEventListener('click', () => {
      openPersonStylePicker('cover', coverInput.value, picked => {
        coverInput.value = picked;
        paint();
      });
    });
    host.querySelector('[data-pick-person-style="video"]')?.addEventListener('click', () => {
      openPersonStylePicker('video', videoInput.value, picked => {
        videoInput.value = picked;
        paint();
      });
    });
    host.querySelector('[data-clear-person-style="cover"]')?.addEventListener('click', () => {
      coverInput.value = '';
      paint();
    });
    host.querySelector('[data-clear-person-style="video"]')?.addEventListener('click', () => {
      videoInput.value = '';
      paint();
    });
    host.querySelector('[data-sync-pair-video]')?.addEventListener('click', e => {
      videoInput.value = e.currentTarget.dataset.syncPairVideo;
      paint();
      notify('已同步配套视频样式，点击下方「保存人物资料」生效');
    });
  };
  paint();
};

// Hook render() to handle #styles routes cleanly.
const renderBeforeStyles = render;
render = function () {
  const hashHead = (location.hash.slice(1) || '').split('/')[0];
  if (hashHead !== 'styles') {
    if (styleLazyObserver) styleLazyObserver.disconnect();
    return renderBeforeStyles();
  }
  route = 'styles';
  updateChrome();
  renderStylesPage();
};

// Show bound visual styles in task detail brief when present.
const taskBeforeStyles = renderTask;
renderTask = function () {
  taskBeforeStyles();
  const t = state.tasks.find(x => x.id === selectedTask);
  const vs = t?.personVisualStyles;
  if (!vs || !Object.keys(vs).length) return;
  const brief = main.querySelector('.task-brief');
  if (!brief) return;
  const rows = Object.entries(vs).map(([pid, row]) => {
    const p = state.config.people.find(x => x.id === pid);
    const pname = p ? p.name : pid;
    return `<p>${E(pname)}：封面「${E(row.coverName || row.coverTemplateId)}」 · 视频「${E(row.videoStyleName || row.videoStyleId)}」</p>`;
  }).join('');
  brief.insertAdjacentHTML('beforeend', `<h3>本次固定视觉风格</h3>${rows}`);
};

// Delegated interactions on the Style Library page.
document.addEventListener('click', e => {
  const catBtn = e.target.closest('[data-style-cat]');
  if (catBtn) {
    styleCategory = catBtn.dataset.styleCat;
    styleOnlyBound = false;
    document.querySelectorAll('[data-style-cat]').forEach(b => b.classList.toggle('active', b === catBtn));
    document.querySelector('[data-style-bound-filter]')?.classList.remove('active');
    paintStylesGrid();
    return;
  }
  const boundBtn = e.target.closest('[data-style-bound-filter]');
  if (boundBtn) {
    styleOnlyBound = !styleOnlyBound;
    boundBtn.classList.toggle('active', styleOnlyBound);
    if (styleOnlyBound) {
      document.querySelectorAll('[data-style-cat]').forEach(b => b.classList.remove('active'));
    } else {
      document.querySelectorAll('[data-style-cat]').forEach(b => b.classList.toggle('active', b.dataset.styleCat === styleCategory));
    }
    paintStylesGrid();
    return;
  }
  if (e.target.closest('[data-action="reset-style-filters"]')) {
    styleCategory = '全部';
    styleQuery = '';
    styleOnlyBound = false;
    renderStylesPage();
    return;
  }
  const card = e.target.closest('[data-open-style]');
  if (card) {
    openStyleDetailModal(card.dataset.styleKind, card.dataset.openStyle);
  }
});

document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const card = e.target.closest?.('[data-open-style]');
  if (card) {
    e.preventDefault();
    openStyleDetailModal(card.dataset.styleKind, card.dataset.openStyle);
  }
});
