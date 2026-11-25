"""Full-frame video compositor supporting the locked fullscreen-orange baseline and all extensible video styles."""
import base64,html,json,math,re,sys
from pathlib import Path
from fontTools import subset
from fontTools.ttLib import TTFont
from caption_layout import Typography,reflow
from platform_ui import select_profiles,lowest_box,audit
from workflow import read,write,settings,clean,phonemes,sha,SKILL
from alignment import character_map
from composition import scaffold
import project_config as pc
import style_registry as sr


def character_times(job):
    path=job/'caption-characters.json';raw=job/'assets/voice/narration.mp3'
    if path.exists():
        stored=read(path)
        if not raw.is_file() or stored.get('sourceAudioSha256')!=sha(raw):raise ValueError('Stored character timing does not match narration audio')
        return stored['characters']
    voice=read(job/'voice.json') if (job/'voice.json').exists() else {};alignment=Path(voice.get('alignment',''))
    if not alignment.is_file():return None
    data=read(alignment)
    if not data.get('passed') or not raw.is_file() or data.get('sha256')!=sha(raw):raise ValueError('Stale character timing; realign unchanged source audio')
    offset=read(job/'natural-timeline.json')['voiceStart']
    chars=[{**c,'start':c['start']+offset,'end':c['end']+offset} for c in character_map(data,(job/'narration.txt').read_text(),clean,phonemes)]
    write(path,{'sourceAudioSha256':data['sha256'],'characters':chars});return chars


def font_subset(job,name,path,index,text,weight=None):
    font=TTFont(path,fontNumber=index) if index is not None else TTFont(path)
    font.recalcTimestamp=False
    missing=sorted({c for c in text if not c.isspace() and ord(c) not in font.getBestCmap()})
    if missing:raise ValueError('字体缺少实际用字：'+''.join(missing[:30]))
    options=subset.Options();options.flavor='woff2';options.recalc_timestamp=False;ss=subset.Subsetter(options=options);ss.populate(text=text);ss.subset(font);font.flavor='woff2'
    out=job/f'assets/fonts/{name}.woff2';out.parent.mkdir(parents=True,exist_ok=True);font.save(out)
    fw=weight if weight is not None else (700 if name=='OrangeRounded' else 900)
    encoded=base64.b64encode(out.read_bytes()).decode("ascii")
    # Embed the small subset once at document scope; compiled subtemplates drop font faces.
    return f"@font-face{{font-family:{name};src:url('data:font/woff2;base64,{encoded}') format('woff2');font-weight:{fw};font-display:block}}"


def _measure_text(metrics_typo, text, size, tracking=0):
    total = 0.0
    for idx, c in enumerate(text):
        glyph = metrics_typo.cm.get(ord(c))
        if glyph is None:
            total += size * 0.6
        else:
            total += metrics_typo.font['hmtx'][glyph][0] * size / metrics_typo.units
        if idx + 1 < len(text):
            total += tracking
    return total


def _fit_text_size(metrics_typo, text, max_size, min_size, max_width):
    size = float(max_size)
    while size > min_size and _measure_text(metrics_typo, text, size) > max_width:
        size -= 2.0
    natural = _measure_text(metrics_typo, text, size)
    return size, min(natural, max_width)


def _embed_sticker(stickers, key, x, y, w, h, uid='s1', rotate=0):
    raw = stickers.get(key)
    if not raw:
        return ''
    m = re.search(r'viewBox="([^"]+)"', raw)
    vb = m.group(1) if m else '0 0 180 180'
    inner = re.sub(r'^<svg[^>]*>|</svg>\s*$', '', raw.strip())
    # Namespace gradient/filter IDs to prevent collisions inside the shared SVG document.
    ids = set(re.findall(r'\bid="([^"]+)"', inner))
    for old in ids:
        new = f'stk_{uid}_{old}'
        inner = inner.replace(f'id="{old}"', f'id="{new}"').replace(f'url(#{old})', f'url(#{new})')
    svg = f'<svg x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" viewBox="{vb}" overflow="visible">{inner}</svg>'
    if rotate:
        cx, cy = x + w / 2, y + h / 2
        return f'<g transform="rotate({rotate} {cx:.1f} {cy:.1f})">{svg}</g>'
    return svg


def _svg_scrims(vspec):
    scrim = vspec.get('scrim') or {}
    parts = []
    top_c = (scrim.get('top') or '').strip()
    bot_c = (scrim.get('bottom') or '').strip()
    if top_c or bot_c:
        defs = []
        rects = []
        if top_c:
            defs.append(f'<linearGradient id="scrim-top" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{html.escape(top_c, quote=True)}"/><stop offset="100%" stop-color="rgba(0,0,0,0)"/></linearGradient>')
            rects.append('<rect x="0" y="0" width="1080" height="470" fill="url(#scrim-top)"/>')
        if bot_c:
            defs.append(f'<linearGradient id="scrim-bot" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(0,0,0,0)"/><stop offset="100%" stop-color="{html.escape(bot_c, quote=True)}"/></linearGradient>')
            rects.append('<rect x="0" y="1320" width="1080" height="600" fill="url(#scrim-bot)"/>')
        parts.append('<defs>' + ''.join(defs) + '</defs>' + ''.join(rects))
    return ''.join(parts)


def _svg_header(vspec, headline, brand, metrics_typo, stickers):
    h = vspec.get('header') or {}
    variant = h.get('variant', 'card-border')
    esc = lambda s: html.escape(str(s), quote=True)
    title_c = h.get('titleColor', '#FFFFFF')
    brand_c = h.get('brandColor', '#FFFFFF')
    bg = h.get('bg', 'rgba(20,22,26,0.88)')
    border = h.get('border', '')
    border_w = int(h.get('borderW', 4))
    radius = int(h.get('radius', 22))
    sticker_key = h.get('sticker', '')

    def text_el(family, text, x, y, size, tlen, fill, anchor='middle', stroke=None, stroke_w=0, extra=''):
        st = f' stroke="{esc(stroke)}" stroke-width="{stroke_w}" paint-order="stroke fill" stroke-linejoin="round"' if stroke and stroke_w else ''
        return (
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="{family}" '
            f'font-size="{size:.2f}" font-weight="900" textLength="{tlen:.2f}" '
            f'lengthAdjust="spacingAndGlyphs" fill="{esc(fill)}"{st}{extra}>{esc(text)}</text>'
        )

    if variant == 'double-pill':
        ts, tw = _fit_text_size(metrics_typo, headline, 56, 36, 740)
        bs, bw = _fit_text_size(metrics_typo, brand, 48, 32, 740)
        pw1 = max(420, min(920, tw + 110))
        pw2 = max(380, min(900, bw + 100))
        parts = [
            f'<rect x="{540-pw1/2:.1f}" y="128" width="{pw1:.1f}" height="96" rx="48" fill="{esc(h.get("topBg","#FFF7EA"))}" stroke="{esc(h.get("topBorder","#F48C2E"))}" stroke-width="5"/>',
            text_el('TitleFace', headline, 540, 195, ts, tw, h.get('topColor', '#5A341C')),
            f'<rect x="{540-pw2/2:.1f}" y="238" width="{pw2:.1f}" height="86" rx="43" fill="{esc(h.get("botBg","#F48C2E"))}" stroke="{esc(h.get("botBorder","#FFF7EA"))}" stroke-width="4"/>',
            text_el('BrandFace', brand, 540, 298, bs, bw, h.get('botColor', '#FFFFFF')),
        ]
        if sticker_key:
            parts.append(_embed_sticker(stickers, sticker_key, 540 + pw1 / 2 - 48, 92, 110, 110, 'hdr'))
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'minimal-corner':
        ts, tw = _fit_text_size(metrics_typo, headline, 42, 30, 520)
        bs, bw = _fit_text_size(metrics_typo, brand, 38, 28, 520)
        card_w = max(380, min(680, max(tw, bw) + (150 if sticker_key else 84)))
        tx = 166 if sticker_key else 90
        parts = [
            f'<rect x="56" y="130" width="{card_w:.1f}" height="156" rx="20" fill="{esc(bg)}" style="filter:drop-shadow(0 8px 18px rgba(0,0,0,0.18))"/>',
            _embed_sticker(stickers, sticker_key, 74, 166, 78, 78, 'hdr') if sticker_key else '',
            text_el('TitleFace', headline, tx, 198, ts, tw, title_c, anchor='start'),
            text_el('BrandFace', brand, tx, 254, bs, bw, brand_c, anchor='start'),
        ]
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'split-badge':
        badge_text = h.get('badgeText', '实探')
        ts, tw = _fit_text_size(metrics_typo, headline, 50, 34, 640)
        bs, bw = _fit_text_size(metrics_typo, brand, 46, 32, 640)
        card_w = 944
        bx = 68
        parts = [
            f'<rect x="{bx}" y="132" width="{card_w}" height="182" rx="22" fill="{esc(bg)}" stroke="{esc(border or "#FFFDF8")}" stroke-width="4"/>',
            f'<rect x="{bx+14}" y="146" width="166" height="154" rx="16" fill="{esc(h.get("badgeBg","#F9A03F"))}"/>',
            f'<text x="{bx+97}" y="242" text-anchor="middle" font-family="OrangeLabel" font-size="50" font-weight="900" fill="#181818">{esc(badge_text)}</text>',
            text_el('TitleFace', headline, bx + 206, 206, ts, tw, title_c, anchor='start'),
            text_el('BrandFace', brand, bx + 206, 276, bs, bw, brand_c, anchor='start'),
        ]
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'editorial-lines':
        ts, tw = _fit_text_size(metrics_typo, headline, 58, 38, 860)
        bs, bw = _fit_text_size(metrics_typo, brand, 50, 34, 860)
        lc = h.get('lineColor', '#FFFFFF')
        st = h.get('stroke', '#000000')
        parts = [
            f'<rect x="76" y="130" width="928" height="4" fill="{esc(lc)}"/>',
            text_el('TitleFace', headline, 540, 212, ts, tw, title_c, stroke=st, stroke_w=8),
            text_el('BrandFace', brand, 540, 286, bs, bw, brand_c, stroke=st, stroke_w=8),
            f'<rect x="76" y="314" width="928" height="4" fill="{esc(lc)}"/>',
        ]
        if h.get('rightTag'):
            parts.append(f'<text x="996" y="116" text-anchor="end" font-family="OrangeLabel" font-size="28" font-weight="900" fill="{esc(lc)}">{esc(h["rightTag"])}</text>')
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'vlog-underline':
        ts, tw = _fit_text_size(metrics_typo, headline, 58, 38, 820)
        bs, bw = _fit_text_size(metrics_typo, brand, 50, 34, 820)
        st = h.get('stroke', '#1E3A2F')
        lc = h.get('lineColor', '#A7D796')
        parts = [
            text_el('TitleFace', headline, 540, 206, ts, tw, title_c, stroke=st, stroke_w=10),
            f'<rect x="{540-max(tw,bw)/2:.1f}" y="226" width="{max(tw,bw):.1f}" height="8" rx="4" fill="{esc(lc)}"/>',
            text_el('BrandFace', brand, 540, 294, bs, bw, brand_c, stroke=st, stroke_w=10),
        ]
        if sticker_key:
            parts.append(_embed_sticker(stickers, sticker_key, 860, 114, 130, 118, 'hdr'))
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'glow-band':
        ts, tw = _fit_text_size(metrics_typo, headline, 58, 38, 880)
        bs, bw = _fit_text_size(metrics_typo, brand, 50, 34, 880)
        bc = border or '#FF6B4A'
        parts = [
            f'<rect x="44" y="134" width="992" height="184" rx="22" fill="{esc(bg)}"/>',
            f'<rect x="44" y="134" width="992" height="6" rx="3" fill="{esc(bc)}"/>',
            f'<rect x="44" y="312" width="992" height="6" rx="3" fill="{esc(bc)}"/>',
            text_el('TitleFace', headline, 540, 212, ts, tw, title_c),
            text_el('BrandFace', brand, 540, 284, bs, bw, brand_c),
        ]
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'glass-left-bar':
        ts, tw = _fit_text_size(metrics_typo, headline, 54, 36, 760)
        bs, bw = _fit_text_size(metrics_typo, brand, 48, 32, 760)
        parts = [
            f'<rect x="64" y="132" width="952" height="182" rx="20" fill="{esc(bg)}" stroke="{esc(border or "#DCEABC88")}" stroke-width="3"/>',
            f'<rect x="64" y="132" width="16" height="182" rx="8" fill="{esc(h.get("bar","#DCEABC"))}"/>',
            text_el('TitleFace', headline, 114, 208, ts, tw, title_c, anchor='start'),
            text_el('BrandFace', brand, 114, 278, bs, bw, brand_c, anchor='start'),
        ]
        if sticker_key:
            parts.append(_embed_sticker(stickers, sticker_key, 868, 134, 136, 118, 'hdr'))
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'icon-card':
        ts, tw = _fit_text_size(metrics_typo, headline, 52, 34, 700)
        bs, bw = _fit_text_size(metrics_typo, brand, 46, 32, 700)
        icon_key = h.get('icon', '')
        parts = [
            f'<rect x="64" y="132" width="952" height="182" rx="{radius}" fill="{esc(bg)}" stroke="{esc(border or "#C89B54")}" stroke-width="{border_w}"/>',
            _embed_sticker(stickers, icon_key, 94, 166, 114, 114, 'hdr') if icon_key else '',
            text_el('TitleFace', headline, 236, 208, ts, tw, title_c, anchor='start'),
            text_el('BrandFace', brand, 236, 278, bs, bw, brand_c, anchor='start'),
        ]
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'taped-card':
        ts, tw = _fit_text_size(metrics_typo, headline, 54, 36, 800)
        bs, bw = _fit_text_size(metrics_typo, brand, 48, 34, 800)
        sh = h.get('shadow', '#D96B43')
        parts = [
            f'<rect x="82" y="146" width="932" height="176" rx="16" fill="{esc(sh)}"/>',
            f'<rect x="74" y="138" width="932" height="176" rx="16" fill="{esc(bg)}" stroke="{esc(border or "#4A3428")}" stroke-width="5"/>',
            text_el('TitleFace', headline, 540, 212, ts, tw, title_c),
            text_el('BrandFace', brand, 540, 282, bs, bw, brand_c),
        ]
        if h.get('tapeLeft'):
            parts.append(_embed_sticker(stickers, h['tapeLeft'], 52, 108, 174, 48, 'tl', rotate=-10))
        if h.get('tapeRight'):
            parts.append(_embed_sticker(stickers, h['tapeRight'], 854, 108, 174, 48, 'tr', rotate=10))
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant in ('comic-flag', 'comic-card'):
        ts, tw = _fit_text_size(metrics_typo, headline, 56, 36, 800)
        bs, bw = _fit_text_size(metrics_typo, brand, 48, 34, 800)
        sh = h.get('shadow', '#181818')
        rot = -1.5 if variant == 'comic-flag' else 0
        body = [
            f'<rect x="82" y="144" width="932" height="178" rx="18" fill="{esc(sh)}"/>',
            f'<rect x="72" y="134" width="932" height="178" rx="18" fill="{esc(bg)}" stroke="{esc(border or "#181818")}" stroke-width="6"/>',
            text_el('TitleFace', headline, 540, 208, ts, tw, title_c),
            text_el('BrandFace', brand, 540, 280, bs, bw, brand_c),
        ]
        if sticker_key:
            body.append(_embed_sticker(stickers, sticker_key, 906, 102, 114, 114, 'hdr'))
        tr = f' transform="rotate({rot} 540 223)"' if rot else ''
        return f'<g id="styled-header"{tr}>' + ''.join(body) + '</g>'

    if variant == 'hazard-card':
        ts, tw = _fit_text_size(metrics_typo, headline, 54, 36, 800)
        bs, bw = _fit_text_size(metrics_typo, brand, 48, 34, 800)
        ac = border or '#FFD426'
        stripes = ''.join(
            f'<polygon points="{x},132 {x+24},132 {x+10},152 {x-14},152" fill="#181A1E"/>'
            for x in range(68, 1030, 48)
        )
        parts = [
            f'<defs><clipPath id="hz-clip"><rect x="64" y="132" width="952" height="20" rx="6"/></clipPath></defs>',
            f'<rect x="64" y="132" width="952" height="186" rx="18" fill="{esc(bg)}" stroke="{esc(ac)}" stroke-width="4"/>',
            f'<g clip-path="url(#hz-clip)"><rect x="64" y="132" width="952" height="20" fill="{esc(ac)}"/>{stripes}</g>',
            text_el('TitleFace', headline, 540, 222, ts, tw, title_c),
            text_el('BrandFace', brand, 540, 290, bs, bw, brand_c),
        ]
        if sticker_key:
            parts.append(_embed_sticker(stickers, sticker_key, 896, 152, 106, 106, 'hdr'))
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'viewfinder':
        ts, tw = _fit_text_size(metrics_typo, headline, 52, 34, 820)
        bs, bw = _fit_text_size(metrics_typo, brand, 46, 32, 820)
        cc = h.get('cornerColor', '#FFFFFF')
        dc = h.get('dotColor', '#FF3B30')
        parts = [
            f'<rect x="64" y="130" width="952" height="188" rx="14" fill="{esc(bg)}"/>',
            f'<path d="M64 170 V130 H108 M972 130 H1016 V170 M64 278 V318 H108 M972 318 H1016 V278" stroke="{esc(cc)}" stroke-width="5" fill="none"/>',
            f'<circle cx="106" cy="162" r="9" fill="{esc(dc)}"/>',
            f'<text x="128" y="172" font-family="OrangeLabel" font-size="26" font-weight="900" fill="{esc(cc)}">REC</text>',
            text_el('TitleFace', headline, 540, 226, ts, tw, title_c),
            text_el('BrandFace', brand, 540, 290, bs, bw, brand_c),
        ]
        if h.get('rightTag'):
            parts.append(f'<text x="980" y="172" text-anchor="end" font-family="OrangeLabel" font-size="24" font-weight="900" fill="{esc(cc)}" opacity="0.85">{esc(h["rightTag"])}</text>')
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'live-pill':
        ts, tw = _fit_text_size(metrics_typo, headline, 52, 34, 780)
        bs, bw = _fit_text_size(metrics_typo, brand, 46, 32, 780)
        dc = h.get('dotColor', '#4ADE80')
        parts = [
            f'<rect x="64" y="132" width="952" height="182" rx="91" fill="{esc(bg)}" stroke="{esc(border or dc)}" stroke-width="4"/>',
            f'<circle cx="128" cy="223" r="14" fill="{esc(dc)}"/>',
            text_el('TitleFace', headline, 550, 208, ts, tw, title_c),
            text_el('BrandFace', brand, 550, 278, bs, bw, brand_c),
        ]
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'top-bottom-bar':
        ts, tw = _fit_text_size(metrics_typo, headline, 56, 36, 840)
        bs, bw = _fit_text_size(metrics_typo, brand, 48, 34, 840)
        lc = h.get('lineColor') or border or '#E5C07B'
        parts = [
            f'<rect x="64" y="132" width="952" height="184" rx="14" fill="{esc(bg)}"/>',
            f'<rect x="64" y="132" width="952" height="7" fill="{esc(lc)}"/>',
            f'<rect x="64" y="309" width="952" height="7" fill="{esc(lc)}"/>',
            text_el('TitleFace', headline, 540, 212, ts, tw, title_c),
            text_el('BrandFace', brand, 540, 282, bs, bw, brand_c),
        ]
        if sticker_key:
            parts.append(_embed_sticker(stickers, sticker_key, 892, 118, 114, 114, 'hdr'))
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    if variant == 'bottom-bar-card':
        ts, tw = _fit_text_size(metrics_typo, headline, 54, 36, 820)
        bs, bw = _fit_text_size(metrics_typo, brand, 48, 34, 820)
        bar = h.get('bar', '#5CE1E6')
        parts = [
            f'<rect x="64" y="132" width="952" height="184" rx="18" fill="{esc(bg)}"/>',
            f'<rect x="64" y="304" width="952" height="12" rx="6" fill="{esc(bar)}"/>',
            text_el('TitleFace', headline, 540, 210, ts, tw, title_c),
            text_el('BrandFace', brand, 540, 278, bs, bw, brand_c),
        ]
        if sticker_key:
            parts.append(_embed_sticker(stickers, sticker_key, 894, 128, 110, 110, 'hdr'))
        return '<g id="styled-header">' + ''.join(parts) + '</g>'

    # Default 'card-border'
    ts, tw = _fit_text_size(metrics_typo, headline, 54, 36, 820)
    bs, bw = _fit_text_size(metrics_typo, brand, 48, 34, 820)
    stroke_attr = f' stroke="{esc(border)}" stroke-width="{border_w}"' if border else ''
    parts = [
        f'<rect x="64" y="132" width="952" height="184" rx="{radius}" fill="{esc(bg)}"{stroke_attr}/>',
        text_el('TitleFace', headline, 540, 210, ts, tw, title_c),
        text_el('BrandFace', brand, 540, 282, bs, bw, brand_c),
    ]
    if sticker_key:
        parts.append(_embed_sticker(stickers, sticker_key, 892, 116, 116, 116, 'hdr'))
    return '<g id="styled-header">' + ''.join(parts) + '</g>'


def _safe_ui_text(text, cmap):
    return ''.join(c for c in str(text or '') if c.isspace() or ord(c) in cmap)


def _svg_chapter(vspec, idx, st, du, label, metrics_typo, side, y_top):
    ch = vspec.get('chapter') or {}
    variant = ch.get('variant', 'solid-pill')
    esc = lambda s: html.escape(str(s), quote=True)
    prefix = _safe_ui_text(ch.get('prefix', ''), metrics_typo.cm)
    suffix = _safe_ui_text(ch.get('suffix', ''), metrics_typo.cm)
    text = f"{prefix}{label}{suffix}"
    size = 40
    tw = _measure_text(metrics_typo, text, size)
    extra_l = 32 if variant == 'dot-pill' else (20 if variant in ('left-bar', 'left-bar-border') else 0)
    w = max(210, tw + 56 + extra_l)
    if w > 880:
        raise ValueError('章节标签过长，请改短，不压扁文字。')
    h = 74
    x = (1080 - 56 - w) if side == 'right' else 56
    r = min(int(ch.get('radius', 14)), h // 2)
    bg = ch.get('bg', '#FF982D')
    color = ch.get('color', '#111111')
    border = ch.get('border', '')

    parts = []
    if variant == 'comic-shadow':
        sh = ch.get('shadow', '#181818')
        parts.append(f'<rect x="{x+6:.1f}" y="{y_top+6:.1f}" width="{w:.1f}" height="{h}" rx="{r}" fill="{esc(sh)}"/>')
    stroke_attr = f' stroke="{esc(border)}" stroke-width="4"' if border else ''
    parts.append(f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{w:.1f}" height="{h}" rx="{r}" fill="{esc(bg)}"{stroke_attr}/>')
    if variant == 'dot-pill':
        parts.append(f'<circle cx="{x+30:.1f}" cy="{y_top+h/2:.1f}" r="9" fill="{esc(ch.get("dot", color))}"/>')
        tx = x + 30 + (w - 30) / 2
    elif variant in ('left-bar', 'left-bar-border'):
        parts.append(f'<rect x="{x+4:.1f}" y="{y_top+8:.1f}" width="10" height="{h-16}" rx="5" fill="{esc(ch.get("bar", color))}"/>')
        tx = x + 16 + (w - 16) / 2
    else:
        tx = x + w / 2
    parts.append(f'<text x="{tx:.1f}" y="{y_top+51:.1f}" text-anchor="middle" font-family="OrangeLabel" font-size="{size}" font-weight="900" fill="{esc(color)}">{esc(text)}</text>')
    return f'<g id="chapter-{idx}" class="clip" data-start="{st}" data-duration="{du}" data-track-index="1">' + ''.join(parts) + '</g>'


def _compose_styled(job, cfg, template_id):
    video_entry = sr.get_video(template_id) if sr.video_exists(template_id) else cfg.get('template', {}).get('_frozenEntry')
    if not video_entry:
        raise ValueError(f'未知视频样式：{template_id}')
    vspec = video_entry.get('videoSpec') or {}
    vcap = vspec.get('caption') or {}
    cap = sr.style_caption_spec(video_entry)
    font_path = str(SKILL / 'assets/NotoSansSC-Black.otf')
    roles = cfg['template'].get('overrides', {}).get('fonts', {})
    if roles.get('body', {}).get('file'):
        font_path = roles['body']['file']
    typography = Typography(font_path, None, cap)

    source = job / 'caption-source.json'
    if not source.exists():
        write(source, read(job / 'captions.json'))
    cues, changes = reflow(read(job / 'captions.json'), typography, clean, character_times(job))
    write(job / 'captions.json', cues)

    profiles = select_profiles(cfg.get('platform_ui', {}))
    rows = []
    max_height = 0
    for cue in cues:
        rs = [typography.bounds(line, cue.get('focus', '') if cue.get('focus', '') in line else '', j, style_row=0)[2] for j, line in enumerate(cue['lines'])]
        max_height = max(max_height, max(r[3] for r in rs) - min(r[1] for r in rs))
        rows.append(rs)

    limits = list(cap['limits'])
    if profiles:
        limits[3] = cap.get('measured_bottom_limit', 1920)
    placement = lowest_box(cap['max_width'], max_height, limits, profiles)
    center = (placement[0] + placement[2]) / 2
    bottom = placement[3]
    painted = []
    fits = []
    for cue, rs in zip(cues, rows):
        dx = center - 540
        dy = bottom - max(r[3] for r in rs)
        actual = [[r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy] for r in rs]
        painted.extend(actual)
        fits.append({'start': cue['start'], 'end': cue['end'], 'lines': cue['lines'], 'paintedBoundsNative': actual, 'translationNative': [dx, dy]})
    checks = audit(painted, profiles)
    if any(c['overlappingOrClippedLines'] for c in checks):
        raise ValueError('Subtitle intersects measured platform UI')
    write(job / 'platform-ui-audit.json', {'mode': cfg.get('platform_ui', {}).get('mode', 'common'), 'basis': 'project-owned measured screenshot profiles; no universal app dimensions assumed', 'placementNative': placement, 'profiles': profiles, 'platforms': checks})
    write(job / 'caption-fit.json', {'fontSizeChanged': False, 'styleBasis': template_id, 'maxAllowedWidthNative': cap['max_width'], 'changes': changes, 'cues': fits})

    avatar_side = vspec.get('avatarSide', 'left')
    av = cfg['avatar']
    chapter_top = 1146.0
    av_x = (1080 - av['width'] - av['x']) if avatar_side == 'right' else av['x']
    avatars = read(job / 'avatars.json') if (job / 'avatars.json').exists() else []
    for a in avatars:
        a.update(x=av_x, y=min(av['bottom'], chapter_top - 24, placement[1] - 24) - av['height'], width=av['width'], height=av['height'])
        if a['y'] < 420:
            raise ValueError('Full-frame avatar has no space between header and captions; configure its dimensions')
    write(job / 'avatars.json', avatars)

    inst = read(job / 'institution.json')
    timeline = read(job / 'natural-timeline.json')
    extra_ui_chars = _safe_ui_text(
        str(vspec.get('header', {}).get('badgeText', ''))
        + str(vspec.get('header', {}).get('rightTag', ''))
        + str(vspec.get('chapter', {}).get('prefix', ''))
        + str(vspec.get('chapter', {}).get('suffix', ''))
        + 'REC0123456789· ',
        typography.cm,
    )
    all_text = (
        ''.join(''.join(c['lines']) for c in cues)
        + (job / 'narration.txt').read_text(encoding='utf-8')
        + json.dumps(inst, ensure_ascii=False)
        + ''.join(str(c[2]) for c in timeline['chapters'])
        + extra_ui_chars
    )
    # Single unified font across all 4 font families for consistent visual identity.
    font_styles = ''
    for family in ('OrangeRounded', 'OrangeLabel', 'TitleFace', 'BrandFace'):
        font_styles += font_subset(job, family, font_path, None, all_text, weight=900)

    esc = lambda s: html.escape(str(s), quote=True)
    styles = '#root{position:absolute;inset:0;pointer-events:none}.overlay{position:absolute;inset:0;width:100%;height:100%}'
    def document(ident, body):
        return f'<template><style>{styles}</style><div id="root" data-composition-id="{ident}" data-width="1080" data-height="1920"><svg class="overlay" width="1080" height="1920" viewBox="0 0 1080 1920" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">{body}</svg></div><script>const tl=gsap.timeline({{paused:true}});window.__timelines["{ident}"]=tl;</script></template>'

    stickers = sr.load_stickers()
    brand = re.sub(r'\s*·\s*', ' · ', inst['displayName']).strip()
    ui = _svg_scrims(vspec) + _svg_header(vspec, inst['headline'], brand, typography, stickers)
    for i, (st, du, label) in enumerate(timeline['chapters']):
        ui += _svg_chapter(vspec, i, st, du, label, typography, avatar_side, chapter_top)
    (job / 'compositions').mkdir(exist_ok=True)
    (job / 'compositions/orange-ui.html').write_text(document('orange-ui', ui), encoding='utf-8')

    cap_color = vcap.get('color', '#FFFFFF')
    cap_stroke = vcap.get('stroke', '#14161A')
    cap_stroke_w = int(vcap.get('strokeW', cap['stroke']))
    focus_color = vcap.get('focusColor', '#FFE135')
    focus_mode = vcap.get('focusMode', 'color')
    focus_bg = vcap.get('focusBg', '#F48C2E')
    focus_border = vcap.get('focusBorder', '')
    backdrop = vcap.get('backdrop', '')
    backdrop_r = int(vcap.get('backdropRadius', 14))
    k = math.tan(math.radians(cap['angle']))

    groups = []
    for i, (cue, fit) in enumerate(zip(cues, fits)):
        parts = []
        for row, line in enumerate(cue['lines']):
            focus = cue.get('focus', '')
            focus = focus if focus in line else ''
            b, tx, _ = typography.bounds(line, focus, row, style_row=0)
            by = cap['baselines'][row]
            tr = f'matrix(1 0 {-k:.6f} 1 {tx:.2f} {by:.2f})' if cap['angle'] else f'translate({tx:.2f} {by:.2f})'
            line_svg = []
            if backdrop:
                bw = (b[2] - b[0]) + 44
                bh = (b[3] - b[1]) + 24
                line_svg.append(f'<rect x="{b[0]-22:.1f}" y="{b[1]-12:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="{backdrop_r}" fill="{esc(backdrop)}"/>')
            if focus and focus_mode == 'pill':
                before, after = line.split(focus, 1)
                pre_w = _measure_text(typography, before, cap['font_size'], cap['tracking']) + (cap['tracking'] if before else 0)
                f_size = cap['font_size'] * cap['focus_ratio']
                foc_w = _measure_text(typography, focus, f_size, cap['tracking'])
                pill_x = pre_w - 10
                pill_y = -f_size * 0.90
                pill_w = foc_w + 20
                pill_h = f_size * 1.14
                pb = f' stroke="{esc(focus_border)}" stroke-width="4"' if focus_border else ''
                line_svg.append(f'<rect x="{pill_x:.1f}" y="{pill_y:.1f}" width="{pill_w:.1f}" height="{pill_h:.1f}" rx="14" fill="{esc(focus_bg)}"{pb}/>')
                content = (
                    esc(before)
                    + f'<tspan fill="{esc(focus_color)}" stroke="none" font-size="{f_size:.2f}">{esc(focus)}</tspan>'
                    + esc(after)
                )
            elif focus:
                before, after = line.split(focus, 1)
                f_size = cap['font_size'] * cap['focus_ratio']
                content = (
                    esc(before)
                    + f'<tspan fill="{esc(focus_color)}" font-size="{f_size:.2f}">{esc(focus)}</tspan>'
                    + esc(after)
                )
            else:
                content = esc(line)
            st_attr = f' stroke="{esc(cap_stroke)}" stroke-width="{cap_stroke_w}" stroke-linejoin="round" paint-order="stroke fill"' if cap_stroke and cap_stroke_w else ''
            line_svg.append(
                f'<text font-family="OrangeRounded" font-weight="900" font-size="{cap["font_size"]}" '
                f'letter-spacing="{cap["tracking"]}" fill="{esc(cap_color)}"{st_attr} '
                f'style="text-shadow:0 4px 6px rgba(0,0,0,0.45)">{content}</text>'
            )
            parts.append(f'<g transform="{tr}">' + ''.join(line_svg) + '</g>')
        dx, dy = fit['translationNative']
        groups.append(f'<g id="caption-{i}" class="clip" data-start="{cue["start"]}" data-duration="{cue["duration"]}" data-track-index="0"><g transform="translate({dx:.2f} {dy:.2f})">' + ''.join(parts) + '</g></g>')
    (job / 'compositions/orange-captions.html').write_text(document('orange-captions', ''.join(groups)), encoding='utf-8')
    scaffold(job, font_styles)
    write(job / 'captions-orange.json', cues)
    write(job / 'caption-fit.json', {'fontSizeChanged': False, 'styleBasis': template_id, 'maxAllowedWidthNative': cap['max_width'], 'changes': changes, 'cues': fits})

    def stamp(t):
        ms = round(t * 1000)
        return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

    (job / 'subtitles.srt').write_text('\n\n'.join(f'{i+1}\n{stamp(c["start"])} --> {stamp(c["end"])}\n' + '\n'.join(c['lines']) for i, c in enumerate(cues)) + '\n', encoding='utf-8')
    write(job / 'template-layout.json', {'template': template_id, 'pictureOutput': [0, 0, 1080, 1920], 'captionBoxNative': placement, 'avatarOutput': avatars, 'socialUIBurnedIn': False, 'measuredUIProfiles': list(profiles)})
    return {'template': template_id, 'cueCount': len(cues), 'reflowedGroups': len(changes), 'placement': placement, 'platformChecks': checks}


def compose(job):
    cfg=settings(job)
    template_id=cfg.get('template',{}).get('id',sr.DEFAULT_VIDEO_ID)
    if template_id!=sr.DEFAULT_VIDEO_ID:
        return _compose_styled(job,cfg,template_id)

    # Exact locked baseline path for 'fullscreen-orange' (body-v6 zero-drift guarantee).
    spec=pc.merge(read(SKILL/'assets/templates/fullscreen-orange.json'),cfg['template'].get('overrides',{}))
    cap=spec['caption'];match=read(job/'orange-font-match.json')[0]
    typography=Typography(match['file'],match.get('index'),cap)
    source=job/'caption-source.json'
    if not source.exists():write(source,read(job/'captions.json'))
    cues,changes=reflow(read(job/'captions.json'),typography,clean,character_times(job))
    write(job/'captions.json',cues)
    profiles=select_profiles(cfg.get('platform_ui',{}))
    rows=[];max_height=0
    for cue in cues:
        accent_row2=cue.get('accentSecondLine',True)
        rs=[typography.bounds(line,cue.get('focus','') if cue.get('focus','') in line else '',j,style_row=j if accent_row2 else 0)[2] for j,line in enumerate(cue['lines'])]
        max_height=max(max_height,max(r[3] for r in rs)-min(r[1] for r in rs));rows.append(rs)
    # Select one stable zone per export; common profiles intersect all selected app layouts.
    limits=list(cap['limits'])
    if profiles:limits[3]=cap.get('measured_bottom_limit',1920)
    placement=lowest_box(cap['max_width'],max_height,limits,profiles)
    center=(placement[0]+placement[2])/2;bottom=placement[3]
    painted=[];fits=[]
    for cue,rs in zip(cues,rows):
        dx=center-540;dy=bottom-max(r[3] for r in rs)
        actual=[[r[0]+dx,r[1]+dy,r[2]+dx,r[3]+dy] for r in rs]
        painted.extend(actual);fits.append({'start':cue['start'],'end':cue['end'],'lines':cue['lines'],'paintedBoundsNative':actual,'translationNative':[dx,dy]})
    checks=audit(painted,profiles)
    if any(c['overlappingOrClippedLines'] for c in checks):raise ValueError('Subtitle intersects measured platform UI')
    write(job/'platform-ui-audit.json',{'mode':cfg.get('platform_ui',{}).get('mode','common'),'basis':'project-owned measured screenshot profiles; no universal app dimensions assumed','placementNative':placement,'profiles':profiles,'platforms':checks})
    write(job/'caption-fit.json',{'fontSizeChanged':False,'styleBasis':'approved-reference','maxAllowedWidthNative':cap['max_width'],'changes':changes,'cues':fits})

    # Generate the unchanged shot/audio scaffold and fresh project-owned font subsets.
    avatars=read(job/'avatars.json') if (job/'avatars.json').exists() else []
    av=spec['avatar'];chapter_top=spec['chapter']['rect'][1]*1920/1717
    for a in avatars:
        a.update(x=av['x'],y=min(av['bottom'],chapter_top-24,placement[1]-24)-av['height'],width=av['width'],height=av['height'])
        if a['y']<520:raise ValueError('Full-frame avatar has no space between header and captions; configure its dimensions')
    write(job/'avatars.json',avatars)
    # Use the approved reference geometry while sharing the one current media scaffold.
    inst=read(job/'institution.json');timeline=read(job/'natural-timeline.json')
    all_text=''.join(''.join(c['lines']) for c in cues)+(job/'narration.txt').read_text()+json.dumps(inst,ensure_ascii=False)+''.join(str(c[2]) for c in timeline['chapters'])
    font_styles=font_subset(job,'OrangeRounded',match['file'],match.get('index'),all_text)
    label_metrics=None
    roles=cfg['template'].get('overrides',{}).get('fonts',{})
    for role,family in [('body','OrangeLabel'),('title','TitleFace'),('subtitle','BrandFace')]:
        definition=roles.get(role,{})
        path=definition.get('file') or str(SKILL/'assets/NotoSansSC-Black.otf');index=definition.get('index')
        font_styles+=font_subset(job,family,path,index,all_text)
        if role=='body':label_metrics=Typography(path,index,cap)
    esc=lambda s:html.escape(str(s),quote=True)
    styles='#root{position:absolute;inset:0;pointer-events:none}.overlay{position:absolute;inset:0;width:100%;height:100%}'
    def document(ident,body):
        return f'<template><style>{styles}</style><div id="root" data-composition-id="{ident}" data-width="1080" data-height="1920"><svg class="overlay" width="1080" height="1920" viewBox="0 0 916 1717" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">{body}</svg></div><script>const tl=gsap.timeline({{paused:true}});window.__timelines["{ident}"]=tl;</script></template>'
    inst=read(job/'institution.json');h=spec['header'];hx,hy=h['translation']
    brand=re.sub(r'\s*·\s*',' · ',inst['displayName']).strip()
    ui=f'''<g transform="translate({hx*916/1080} {hy*1717/1920}) scale({h['scale']})"><text x="458" y="220" text-anchor="middle" font-family="TitleFace" font-size="86" font-weight="900" font-style="italic" textLength="{h['title_width']}" lengthAdjust="spacingAndGlyphs" fill="white" stroke="#14100C" stroke-width="10" paint-order="stroke fill" stroke-linejoin="round" style="text-shadow:0 4px 6px #0008">{esc(inst['headline'])}</text><path fill="#FF941F" d="M58 265L788 251L856 265L829 278L877 288L853 304L887 316L871 330L883 344L858 354L871 370L827 383L92 401L38 383L51 369L24 351L45 337L26 321L55 306L42 291L76 281Z"/><text x="458" y="353" text-anchor="middle" font-family="BrandFace" font-weight="900" font-style="italic" font-size="82" textLength="{h['brand_width']}" lengthAdjust="spacingAndGlyphs" fill="white" stroke="#111" stroke-width="10" paint-order="stroke fill" stroke-linejoin="round">{esc(brand)}</text></g>'''
    timeline=read(job/'natural-timeline.json');x,y,w,height=spec['chapter']['rect']
    for i,(st,du,label) in enumerate(timeline['chapters']):
        size=spec['chapter']['font_size']
        label_width=sum(label_metrics.font['hmtx'][label_metrics.cm[ord(c)]][0]*size/label_metrics.units for c in label)
        width=max(w,label_width+40)
        if x+width>840:raise ValueError('章节标签过长，请改短，不压扁文字。')
        ui+=f'<g id="chapter-{i}" class="clip" data-start="{st}" data-duration="{du}" data-track-index="1"><rect x="{x}" y="{y}" width="{width}" height="{height}" rx="7" fill="#FF982D"/><text x="{x+width/2}" y="{y+54}" text-anchor="middle" font-family="OrangeLabel" font-size="{spec["chapter"]["font_size"]}" font-weight="900" fill="#111">{esc(label)}</text></g>'
    (job/'compositions').mkdir(exist_ok=True)
    (job/'compositions/orange-ui.html').write_text(document('orange-ui',ui),encoding='utf-8')
    groups=[]
    for i,(cue,fit) in enumerate(zip(cues,fits)):
        parts=[]
        accent_row2=cue.get('accentSecondLine',True)
        for row,line in enumerate(cue['lines']):
            focus=cue.get('focus','');focus=focus if focus in line else ''
            style_row=row if accent_row2 else 0
            b,tx,r=typography.bounds(line,focus,row,style_row=style_row);sx,sy=cap['scales'][style_row]
            content=esc(line)
            if focus:
                before,after=line.split(focus,1);content=esc(before)+f'<tspan fill="#FDD722" font-size="{cap["font_size"]*cap["focus_ratio"]}">{esc(focus)}</tspan>'+esc(after)
            k=math.tan(math.radians(cap['angle']));color='#FA98C3' if (row and accent_row2) else '#FFFFFF'
            parts.append(f'<g transform="matrix({sx} 0 {-sx*k} {sy} {tx} {cap["baselines"][row]})"><text font-family="OrangeRounded" font-weight="700" font-size="{cap["font_size"]}" letter-spacing="{cap["tracking"]}" fill="{color}" stroke="#15100B" stroke-width="{cap["stroke"]}" stroke-linejoin="round" paint-order="stroke fill" style="text-shadow:0 4px 5px #0008">{content}</text></g>')
        dx,dy=fit['translationNative']
        groups.append(f'<g id="caption-{i}" class="clip" data-start="{cue["start"]}" data-duration="{cue["duration"]}" data-track-index="0"><g transform="translate({dx*916/1080} {dy*1717/1920})">'+''.join(parts)+'</g></g>')
    (job/'compositions/orange-captions.html').write_text(document('orange-captions',''.join(groups)),encoding='utf-8')
    scaffold(job,font_styles)
    write(job/'captions-orange.json',cues)
    write(job/'caption-fit.json',{'fontSizeChanged':False,'styleBasis':'approved-reference','maxAllowedWidthNative':cap['max_width'],'changes':changes,'cues':fits})
    def stamp(t):
        ms=round(t*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    (job/'subtitles.srt').write_text('\n\n'.join(f'{i+1}\n{stamp(c["start"])} --> {stamp(c["end"])}\n'+ '\n'.join(c['lines']) for i,c in enumerate(cues))+'\n',encoding='utf-8')
    write(job/'template-layout.json',{'template':'fullscreen-orange','pictureOutput':[0,0,1080,1920],'captionBoxNative':placement,'avatarOutput':avatars,'socialUIBurnedIn':False,'measuredUIProfiles':list(profiles)})
    return {'template':'fullscreen-orange','cueCount':len(cues),'reflowedGroups':len(changes),'placement':placement,'platformChecks':checks}


if __name__=='__main__':print(json.dumps(compose(Path(sys.argv[1]).resolve()),ensure_ascii=False,indent=2))
