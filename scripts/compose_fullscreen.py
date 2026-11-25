"""The approved full-frame reference layout, with measured caption and chapter fit."""
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


def font_subset(job,name,path,index,text):
    font=TTFont(path,fontNumber=index) if index is not None else TTFont(path)
    font.recalcTimestamp=False
    missing=sorted({c for c in text if not c.isspace() and ord(c) not in font.getBestCmap()})
    if missing:raise ValueError('字体缺少实际用字：'+''.join(missing[:30]))
    options=subset.Options();options.flavor='woff2';options.recalc_timestamp=False;ss=subset.Subsetter(options=options);ss.populate(text=text);ss.subset(font);font.flavor='woff2'
    out=job/f'assets/fonts/{name}.woff2';out.parent.mkdir(parents=True,exist_ok=True);font.save(out)
    weight=700 if name=='OrangeRounded' else 900
    encoded=base64.b64encode(out.read_bytes()).decode("ascii")
    # Embed the small subset once at document scope; compiled subtemplates drop font faces.
    return f"@font-face{{font-family:{name};src:url('data:font/woff2;base64,{encoded}') format('woff2');font-weight:{weight};font-display:block}}"


def compose(job):
    cfg=settings(job);spec=pc.merge(read(SKILL/'assets/templates/fullscreen-orange.json'),cfg['template'].get('overrides',{}))
    cap=spec['caption'];match=read(job/'orange-font-match.json')[0]
    typography=Typography(match['file'],match.get('index'),cap)
    source=job/'caption-source.json'
    if not source.exists():write(source,read(job/'captions.json'))
    cues,changes=reflow(read(job/'captions.json'),typography,clean,character_times(job))
    write(job/'captions.json',cues)
    profiles=select_profiles(cfg.get('platform_ui',{}))
    rows=[];max_height=0
    for cue in cues:
        rs=[typography.bounds(line,cue.get('focus','') if cue.get('focus','') in line else '',j)[2] for j,line in enumerate(cue['lines'])]
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
        for row,line in enumerate(cue['lines']):
            focus=cue.get('focus','');focus=focus if focus in line else ''
            b,tx,r=typography.bounds(line,focus,row);sx,sy=cap['scales'][row]
            content=esc(line)
            if focus:
                before,after=line.split(focus,1);content=esc(before)+f'<tspan fill="#FDD722" font-size="{cap["font_size"]*cap["focus_ratio"]}">{esc(focus)}</tspan>'+esc(after)
            k=math.tan(math.radians(cap['angle']));color='#FA98C3' if row else '#FFFFFF'
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
