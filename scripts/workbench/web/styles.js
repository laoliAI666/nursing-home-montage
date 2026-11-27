'use strict';
// Style Library (风格库) — 30+ cohesive Cover & Video styles + Digital Human visual bindings.
paths.palette = '<path d="M12 3a9 9 0 1 0 0 18h1.5a2.5 2.5 0 0 0 0-5H12a2 2 0 0 1-2-2c0-1.1.9-2 2-2h4a5 5 0 0 0 5-5 4 4 0 0 0-9-4Z"/><circle cx="7.5" cy="10.5" r="1"/><circle cx="10.5" cy="7.5" r="1"/><circle cx="15" cy="7.5" r="1"/>';
labels.styles = '风格库';

let styleTab = 'videos';
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

function videoStyleSuites() {
  const s = getStylesData();
  return (s.covers || []).map(cover => {
    const video = findVideoStyle(cover.pairId);
    const boundMap = new Map();
    for (const p of [...(cover.boundPeople || []), ...(video?.boundPeople || [])]) {
      if (p?.id && !boundMap.has(p.id)) boundMap.set(p.id, p);
    }
    const isDefault = cover.id === s.defaultCoverId && video?.id === s.defaultVideoStyleId;
    return {
      id: cover.id,
      num: cover.num || String(cover.order || '').padStart(2, '0'),
      order: cover.order || 0,
      name: cover.name,
      category: cover.category,
      layoutTag: cover.layoutTag || video?.layoutTag || '',
      description: cover.description || video?.description || '',
      swatches: cover.swatches || video?.swatches || [],
      tags: [...new Set([...(cover.tags || []), ...(video?.tags || [])])],
      cover,
      video,
      isDefault,
      boundPeople: [...boundMap.values()],
    };
  });
}

function suiteCardHtml(suite) {
  const bound = suite.boundPeople || [];
  const boundText = bound.length ? `已绑：${bound.map(p => p.name).join('、')}` : '暂未单独绑定';
  const num = E(suite.num);
  return `<article class="style-card style-suite-card ${suite.isDefault ? 'is-default' : ''}" data-open-style="${E(suite.cover.id)}" data-style-kind="suite" tabindex="0" role="button" aria-label="查看视频风格 ${E(suite.name)}">
    <div class="style-suite-pair-stage">
      ${(suite.isDefault || bound.length) ? `<div class="style-preview-badges">
        <span></span>
        <div class="style-status-stack">
          ${suite.isDefault ? '<span class="style-status-pill is-default">当前默认</span>' : ''}
          ${bound.length ? `<span class="style-status-pill is-bound">已绑 ${bound.length} 人</span>` : ''}
        </div>
      </div>` : ''}
      <div class="style-preview-box">
        <div class="style-cover-slot" style="width:100%;height:100%" data-cover-preview data-cover-id="${E(suite.cover.id)}"><div class="style-preview-placeholder">渲染预览中…</div></div>
      </div>
      <div class="style-preview-box">
        ${suite.video ? videoPreviewHtml(suite.video, `suite_${suite.video.id}`) : '<div class="style-preview-placeholder">暂无视频预览</div>'}
      </div>
    </div>
    <div class="style-suite-pair-labels"><span>封面样式</span><span>视频样式</span></div>
    <div class="style-card-body">
      <div class="style-card-head">
        <h3><span class="style-num-pill" style="margin-right:6px;vertical-align:1px">${num}</span>${E(suite.name)}</h3>
        <span class="style-cat-tag">${E(suite.category)}</span>
      </div>
      <p class="style-layout-meta">${E(suite.layoutTag || suite.description || '')}</p>
      <div class="style-swatches">${(suite.swatches || []).map(c => `<i class="style-swatch" style="background:${E(c)}"></i>`).join('')}</div>
      <div class="style-bound-row">
        <span class="style-bound-names" title="${E(boundText)}">${E(boundText)}</span>
        <span class="text-button">详情 / 绑定 ${icon('chevron-right')}</span>
      </div>
    </div>
  </article>`;
}

function filteredSuites() {
  const list = videoStyleSuites();
  const q = styleQuery.trim().toLowerCase();
  return list.filter(item => {
    if (styleOnlyBound && !(item.boundPeople || []).length) return false;
    if (styleCategory !== '全部' && item.category !== styleCategory) return false;
    if (!q) return true;
    const hay = [item.num, item.id, item.name, item.category, item.layoutTag, item.description, item.video?.id, item.video?.name, ...(item.tags || [])].join(' ').toLowerCase();
    return hay.includes(q);
  });
}

function styleCategories(kind) {
  const list = kind === 'video' ? getStylesData().videos : getStylesData().covers;
  return ['全部', ...new Set(list.map(x => x.category))];
}

function paintStylesGrid() {
  const host = $('#styles-grid-host');
  if (!host) return;
  if (styleTab === 'text') {
    window.paintTextStylesTab?.(host);
    return;
  }
  const items = filteredSuites();
  if (!items.length) {
    host.innerHTML = emptyState('没有找到匹配的视频风格', '试试清空关键词，或切换其他分类查看全部视频风格。', '<button type="button" class="secondary" data-action="reset-style-filters">重置筛选</button>');
    return;
  }
  host.innerHTML = `<div class="style-grid style-suite-grid">${items.map(item => suiteCardHtml(item)).join('')}</div>`;
  observeCoverContainers(host, 1 / 3);
}

async function renderStylesPage() {
  const parts = location.hash.slice(1).split('/');
  if (parts[1] === 'text') styleTab = 'text';
  else if (parts[1] === 'videos' || parts[1] === 'covers') styleTab = 'videos';
  await loadStyleStickers().catch(() => {});
  const s = getStylesData();
  const textList = s.textStyles || [];
  const isText = styleTab === 'text';
  const cats = isText
    ? ['全部', ...new Set(textList.map(x => x.category || '自定义模板'))]
    : styleCategories('cover');
  if (!cats.includes(styleCategory)) styleCategory = '全部';
  const canManageText = typeof window.hasPerm === 'function' ? window.hasPerm('styles.text_manage') : true;

  const headAction = isText
    ? `${canManageText ? `<button type="button" class="primary" data-action="new-text-style">${icon('plus')} 新建口播写法模板</button>` : ''}`
    : `<a class="secondary" href="#people">${icon('person')} 前往数字人管理</a>`;

  main.innerHTML = `${heading('风格库', '左边管视频的「画面长相」，右边管口播的「说话方式」。选好一套设为全局默认，以后新建视频会自动套用。', headAction)}
    <nav class="content-tabs" aria-label="风格库分类">
      <a href="#styles/videos" ${!isText ? 'aria-current="page"' : ''}>视频风格<span>${(s.covers || []).length}</span></a>
      <a href="#styles/text" ${isText ? 'aria-current="page"' : ''}>文本风格<span>${textList.length}</span></a>
    </nav>
    ${isText ? (window.renderTextStylesBanner ? window.renderTextStylesBanner() : '') : ''}
    <div class="styles-toolbar">
      <div class="filter-tabs" role="group" aria-label="按风格分类筛选">
        ${cats.map(cat => `<button type="button" data-style-cat="${E(cat)}" class="${styleCategory === cat && !styleOnlyBound ? 'active' : ''}">${E(cat)}</button>`).join('')}
        ${!isText ? `<button type="button" data-style-bound-filter class="${styleOnlyBound ? 'active' : ''}">已绑定数字人</button>` : ''}
      </div>
      <div class="styles-toolbar-right">
        <label class="search-field">${icon('search')}<input id="style-search" type="search" placeholder="${isText ? '搜索文本风格名、口吻或规则…' : '搜索视频风格名、配色、构图…'}" value="${E(styleQuery)}"></label>
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
  const isCover = kind === 'cover' || kind === 'suite';
  const entry = isCover ? findCoverStyle(id) : findVideoStyle(id);
  if (!entry) return;
  const pairEntry = isCover ? findVideoStyle(entry.pairId) : findCoverStyle(entry.pairId);
  const coverEntry = isCover ? entry : pairEntry;
  const videoEntry = isCover ? pairEntry : entry;
  const s = getStylesData();
  const isSuiteDefault = coverEntry.id === s.defaultCoverId && videoEntry.id === s.defaultVideoStyleId;
  const people = state.config.people || [];
  const canSetDefault = typeof window.hasPerm === 'function' ? window.hasPerm('styles.set_default') : true;
  const canBind = typeof window.hasPerm === 'function' ? window.hasPerm('styles.bind') : true;

  const dialog = document.createElement('dialog');
  dialog.className = 'style-dialog';
  dialog.innerHTML = `
    <div class="style-dialog-head">
      <h2><span class="style-num-pill">${E(coverEntry.num || '')}</span> ${E(coverEntry.name)} <span class="style-cat-tag">${E(coverEntry.category)}</span></h2>
      <button type="button" data-close aria-label="关闭">${icon('close')}</button>
    </div>
    <div class="style-dialog-body">
      <div class="style-detail-grid">
        <div class="style-detail-pair">
          <div class="style-detail-frame">
            <div class="style-preview-box" data-modal-cover data-cover-id="${E(coverEntry.id)}"><div class="style-preview-placeholder">加载高清预览…</div></div>
            <span>封面样式 · ${E(coverEntry.name)}</span>
          </div>
          <div class="style-detail-frame">
            <div class="style-preview-box">${videoPreviewHtml(videoEntry, 'modal_' + videoEntry.id)}</div>
            <span>视频样式 · ${E(videoEntry.name)}</span>
          </div>
        </div>
        <div class="style-detail-info">
          <p class="style-detail-desc">${E(coverEntry.description || coverEntry.layoutTag || '')}</p>
          <div class="style-swatches">${(coverEntry.swatches || []).map(c => `<i class="style-swatch" style="background:${E(c)};width:18px;height:18px"></i>`).join('')}<span class="subtle" style="margin-left:6px">${E(coverEntry.layoutTag || '')}</span></div>
          <div class="style-detail-tags">${(coverEntry.tags || []).map(t => `<span>#${E(t)}</span>`).join('')}</div>

          <div class="style-bind-box">
            <h3>工作台默认视频风格</h3>
            <p class="form-help" style="margin:0 0 12px">未单独绑定视频风格的数字人，新建制作时会自动成套使用当前默认封面与视频样式。</p>
            <div class="style-dialog-actions">
              ${canSetDefault
                ? `<button type="button" class="secondary" id="modal-set-default-pair" ${isSuiteDefault ? 'disabled' : ''}>${isSuiteDefault ? '已是当前全局默认' : '设为全局默认视频风格'}</button>`
                : `<span class="subtle">${isSuiteDefault ? '当前为工作台全局默认视频风格' : '仅管理员/超管可修改全局默认风格'}</span>`}
            </div>
          </div>

          <div class="style-bind-box">
            <h3>绑定到数字人</h3>
            ${people.length
              ? (canBind
                ? `<p class="form-help" style="margin:0">勾选要使用这套视频风格（封面 + 视频）的数字人；取消已勾选的会恢复跟随默认。</p>
                  <div class="style-bind-people">${people.map(p => {
                    const checked = p.cover_template_id === coverEntry.id && (!p.video_style_id || p.video_style_id === videoEntry.id);
                    const curCover = p.cover_template_id ? findCoverStyle(p.cover_template_id)?.name : '默认';
                    const curVideo = p.video_style_id ? findVideoStyle(p.video_style_id)?.name : '默认';
                    return `<label class="style-bind-person"><input type="checkbox" value="${E(p.id)}" ${checked ? 'checked' : ''}>${p.hasPortrait ? `<img src="/media/person/${E(p.id)}" alt="">` : icon('person')}<span><strong>${E(p.name)}</strong><small>当前：${curCover === curVideo ? E(curCover) : `封面 ${E(curCover)} · 视频 ${E(curVideo)}`}</small></span></label>`;
                  }).join('')}</div>
                  <button type="button" class="primary wide" id="modal-save-bind">${icon('check')} 保存数字人绑定</button>`
                : `<p class="form-help" style="margin:0">已绑定数字人：${(coverEntry.boundPeople || []).length ? E(coverEntry.boundPeople.map(p => p.name).join('、')) : '暂无'}（无绑定权限）。</p>`)
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

  dialog.querySelector('#modal-set-default-pair')?.addEventListener('click', async e => {
    $('#modal-style-error', dialog).textContent = '';
    await busy(e.currentTarget, async () => {
      try {
        const res = await api('/api/styles/default', { kind: 'cover', id: coverEntry.id, includePair: true });
        state.styles = res.styles;
        state.config = res.config;
        notify(`已将视频风格「${coverEntry.name}」设为工作台全局默认`);
        close();
        if (route === 'styles') paintStylesGrid();
      } catch (err) {
        $('#modal-style-error', dialog).textContent = err.message;
      }
    });
  });

  dialog.querySelector('#modal-save-bind')?.addEventListener('click', async e => {
    $('#modal-style-error', dialog).textContent = '';
    const peopleIds = [...dialog.querySelectorAll('.style-bind-people input:checked')].map(x => x.value);
    await busy(e.currentTarget, async () => {
      try {
        const res = await api('/api/styles/bind', { kind: 'cover', id: coverEntry.id, peopleIds, bindPair: true });
        state.styles = res.styles;
        state.config = res.config;
        notify('数字人视频风格绑定已保存');
        close();
        if (route === 'styles') paintStylesGrid();
      } catch (err) {
        $('#modal-style-error', dialog).textContent = err.message;
      }
    });
  });
}

// Unified Video Style Suite Picker Modal (Cover + Video bundled) for the Digital Human Editor.
function openPersonStylePicker(currentCoverId, currentVideoId, onSelect) {
  const s = getStylesData();
  const suites = s.covers.map(cover => ({
    cover,
    video: findVideoStyle(cover.pairId),
  }));
  const isFollowingDefault = !currentCoverId && !currentVideoId;
  const effectiveCoverId = currentCoverId || s.defaultCoverId;
  const effectiveVideoId = currentVideoId || s.defaultVideoStyleId;
  const matchedCover = currentCoverId ? findCoverStyle(currentCoverId) : null;
  const isLegacyMixed = Boolean(
    (currentCoverId && currentVideoId && matchedCover && matchedCover.pairId !== currentVideoId) ||
    (!currentCoverId && currentVideoId && findVideoStyle(currentVideoId)?.pairId !== s.defaultCoverId)
  );
  let pickCat = '全部';
  let pickQuery = '';

  const dialog = document.createElement('dialog');
  dialog.className = 'style-dialog style-suite-picker-dialog';
  dialog.innerHTML = `
    <div class="style-dialog-head">
      <div>
        <h2>选择视频风格（共 ${suites.length} 套）</h2>
        <p class="style-dialog-sub">每套视频风格已捆绑对应封面样式与视频样式，点选一套即可同时完成封面与视频绑定。</p>
      </div>
      <button type="button" data-close aria-label="关闭">${icon('close')}</button>
    </div>
    <div class="style-dialog-body">
      <div class="styles-toolbar" style="margin-bottom:10px">
        <div class="filter-tabs" data-picker-cats>
          ${styleCategories('cover').map(cat => `<button type="button" data-pick-cat="${E(cat)}" class="${cat === '全部' ? 'active' : ''}">${E(cat)}</button>`).join('')}
        </div>
        <label class="search-field">${icon('search')}<input type="search" data-pick-search placeholder="搜索风格名称、编号或色系…"></label>
      </div>
      ${isLegacyMixed ? '<p class="style-picker-notice">当前数字人为旧版分开绑定的混搭配置，点选下方任意一套组合即可统一为成套视频风格。</p>' : ''}
      <div class="style-picker-grid style-picker-suite-grid" data-picker-grid></div>
    </div>`;
  document.body.append(dialog);
  dialog.showModal();

  const close = () => { dialog.close(); dialog.remove(); };
  dialog.querySelector('[data-close]').onclick = close;
  dialog.addEventListener('click', e => { if (e.target === dialog) close(); });

  const paintGrid = () => {
    const q = pickQuery.trim().toLowerCase();
    const filtered = suites.filter(({ cover, video }) => {
      if (pickCat !== '全部' && cover.category !== pickCat) return false;
      if (!q) return true;
      return [
        cover.num, cover.id, cover.name, cover.category, cover.layoutTag, ...(cover.tags || []),
        video?.id, video?.name, video?.layoutTag, ...(video?.tags || []),
      ].join(' ').toLowerCase().includes(q);
    });
    const grid = dialog.querySelector('[data-picker-grid]');
    if (!filtered.length) {
      grid.innerHTML = '<p class="style-picker-empty">没有找到匹配的视频风格，请尝试清空搜索词或切换分类。</p>';
      return;
    }
    grid.innerHTML = filtered.map(({ cover, video }) => {
      const isWorkspaceDefault = cover.id === s.defaultCoverId && video.id === s.defaultVideoStyleId;
      const isSelected = !isLegacyMixed && (
        isFollowingDefault
          ? cover.id === s.defaultCoverId
          : (cover.id === effectiveCoverId && video.id === effectiveVideoId)
      );
      return `
        <button type="button" class="style-picker-item style-picker-suite ${isSelected ? 'is-selected' : ''}" data-pick-cover="${E(cover.id)}" data-pick-video="${E(video.id)}" aria-pressed="${isSelected ? 'true' : 'false'}">
          <div class="style-picker-pair">
            ${isSelected ? `<div class="style-preview-badges">
              <span></span>
              <span class="style-status-pill is-selected-pill">${icon('check')} 当前使用</span>
            </div>` : ''}
            <div class="style-preview-box">
              <div style="width:100%;height:100%" data-cover-preview data-cover-id="${E(cover.id)}"><div class="style-preview-placeholder">加载中…</div></div>
            </div>
            <div class="style-preview-box">
              ${videoPreviewHtml(video, 'pk_' + video.id)}
            </div>
          </div>
          <div class="style-picker-pair-labels"><span>封面样式</span><span>视频样式</span></div>
          <div class="style-picker-item-meta">
            <div class="style-picker-meta-top">
              <strong>${E((cover.num ? cover.num + ' · ' : '') + cover.name)}</strong>
              <span class="style-picker-tags">
                ${isWorkspaceDefault ? '<span class="style-default-tag">默认</span>' : ''}
                <span class="style-cat-tag">${E(cover.category)}</span>
              </span>
            </div>
            <small>${E(cover.layoutTag || cover.description || '')}</small>
          </div>
        </button>`;
    }).join('');
    observeCoverContainers(grid, 1 / 3);
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
    const item = e.target.closest('[data-pick-cover]');
    if (!item) return;
    onSelect({ coverId: item.dataset.pickCover, videoId: item.dataset.pickVideo });
    close();
  });
}

// Mount unified Video Style selector inside the Digital Human Editor (#person-module-visual).
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
    const isFollowingDefault = !cid && !vid;
    const coverEntry = findCoverStyle(cid || s.defaultCoverId);
    const videoEntry = findVideoStyle(vid || (cid ? coverEntry?.pairId : s.defaultVideoStyleId));
    const isLegacyMixed = Boolean(
      (cid && vid && coverEntry?.pairId !== vid) ||
      (!cid && vid && videoEntry?.pairId !== s.defaultCoverId)
    );
    const titleText = isLegacyMixed
      ? `封面：${coverEntry.name} / 视频：${videoEntry.name}`
      : `${coverEntry.num ? coverEntry.num + ' · ' : ''}${coverEntry.name}`;
    const statusBadge = isLegacyMixed
      ? '<span class="person-style-badge is-warn">旧版混搭</span>'
      : (isFollowingDefault
        ? '<span class="person-style-badge is-default">跟随工作台默认</span>'
        : '<span class="person-style-badge is-bound">已绑定专属风格</span>');

    host.innerHTML = `
      <div class="person-style-header">
        <label class="field-label" style="margin:0">视频风格<span class="optional">选填</span></label>
        <a class="text-button" href="#styles">浏览全部 ${s.covers.length} 套风格库 ${icon('arrow-up-right')}</a>
      </div>
      <div class="person-style-suite">
        <div class="person-style-pair-thumbs">
          <div class="person-style-thumb-wrap">
            <div class="person-style-thumb" data-person-cover-thumb data-cover-id="${E(coverEntry.id)}"></div>
            <span>封面样式</span>
          </div>
          <div class="person-style-thumb-wrap">
            <div class="person-style-thumb">${videoPreviewHtml(videoEntry, 'ps_' + videoEntry.id)}</div>
            <span>视频样式</span>
          </div>
        </div>
        <div class="person-style-info">
          <div class="person-style-title-row">
            <strong class="person-style-name">${E(titleText)}</strong>
            ${statusBadge}
          </div>
          <span class="person-style-sub">${isLegacyMixed
            ? '当前数字人仍为旧版分开绑定配置，点击下方「选择视频风格」即可统一为成套组合。'
            : E(`${coverEntry.category} · ${coverEntry.layoutTag || coverEntry.description || ''}`)}</span>
          ${!isLegacyMixed && (coverEntry.swatches || []).length
            ? `<div class="style-swatches">${coverEntry.swatches.map(c => `<i class="style-swatch" style="background:${E(c)}"></i>`).join('')}</div>`
            : ''}
          <div class="person-style-actions">
            <button type="button" class="secondary" data-pick-person-style="suite">选择视频风格</button>
            ${!isFollowingDefault ? '<button type="button" class="text-button" data-clear-person-style="suite">恢复工作台默认</button>' : ''}
          </div>
        </div>
      </div>
      <p class="form-help">封面样式与视频样式成套捆绑；选定一套视频风格并保存人物资料后，制作时将自动同步套用对应封面与视频样式。</p>`;

    hydrateCoverPreview(host.querySelector('[data-person-cover-thumb]'), coverEntry, 1 / 4);
    host.querySelector('[data-pick-person-style="suite"]')?.addEventListener('click', () => {
      openPersonStylePicker(coverInput.value, videoInput.value, ({ coverId, videoId }) => {
        if (!coverInput.value && !videoInput.value && coverId === s.defaultCoverId && videoId === s.defaultVideoStyleId) {
          return;
        }
        coverInput.value = coverId;
        videoInput.value = videoId;
        paint();
      });
    });
    host.querySelector('[data-clear-person-style="suite"]')?.addEventListener('click', () => {
      coverInput.value = '';
      videoInput.value = '';
      paint();
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
    const cLabel = row.coverName || row.coverTemplateId;
    const vLabel = row.videoStyleName || row.videoStyleId;
    return cLabel === vLabel
      ? `<p>${E(pname)}：视频风格「${E(cLabel)}」</p>`
      : `<p>${E(pname)}：封面「${E(cLabel)}」 · 视频「${E(vLabel)}」</p>`;
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
