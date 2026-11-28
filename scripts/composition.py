"""Native 1080x1920 media scaffold shared by the one production composition."""
from pathlib import Path
import html,json
from workflow import read


def scaffold(job,font_styles=""):
    esc=lambda value:html.escape(str(value),quote=True)
    timeline=read(job/'natural-timeline.json');total=timeline['duration']
    media=[]
    for i,shot in enumerate(read(job/'shot-list.json')):
        common=f'id="shot-{i:02d}" class="clip footage" src="{esc(shot["asset"])}" data-start="{shot["start"]}" data-duration="{shot["duration"]}" data-track-index="0" style="z-index:1;object-position:50% {shot["positionY"]}%"'
        if Path(shot['asset']).suffix.lower() in ('.mp4','.mov','.m4v','.mkv','.avi','.webm'):
            media.append(f'<video {common} muted playsinline data-media-start="{shot["sourceStart"]}"></video>')
        else:media.append(f'<img {common} alt="{esc(shot["description"])}">')
    animations=[]
    for a in read(job/'avatars.json'):
        ident=a['id']
        media.append(f'<div id="presenter-{ident}-wrap" data-layout-allow-overlap style="position:absolute;left:{a["x"]}px;top:{a["y"]}px;width:{a["width"]}px;height:{a["height"]}px;z-index:1100;opacity:0"><video id="presenter-{ident}" class="clip" src="{esc(a["video"])}" data-start="{a["start"]}" data-duration="{a["duration"]}" data-track-index="5" muted playsinline style="width:100%;height:100%;object-fit:contain"></video></div>')
        animations.append(f'tl.fromTo("#presenter-{ident}-wrap",{{opacity:0}},{{opacity:1,duration:{a["fadeIn"]},immediateRender:false}},{a["start"]});')
        if a.get('fadeOut'):animations.append(f'tl.to("#presenter-{ident}-wrap",{{opacity:0,duration:{a["fadeOut"]}}},{a["start"]+a["duration"]-a["fadeOut"]});')
    for ident,track in [('orange-ui',1),('orange-captions',2)]:
        media.append(f'<div id="{ident}" class="clip host" data-composition-id="{ident}" data-composition-src="compositions/{ident}.html" data-start="0" data-duration="{total}" data-width="1080" data-height="1920" data-track-index="{track}" style="z-index:{1000 if track==1 else 1200}"></div>')
    media.append(f'<audio id="female-narration" class="clip" src="{esc(timeline["audio"])}" data-start="{timeline["voiceStart"]}" data-duration="{timeline["voiceDuration"]}" data-volume="1" data-track-index="3"></audio>')
    bgm=read(job/'bgm-timeline.json')
    if bgm.get('enabled'):
        attrs={'id':'music-bed','class':'clip','src':bgm['audio'],'data-start':0,'data-duration':total,'data-volume':1,'data-track-index':4}
        for key in ('data-fx-chain','data-fx-carve','data-automation'):
            if key in bgm:attrs[key]=json.dumps(bgm[key],ensure_ascii=False,separators=(',',':'))
        media.append('<audio '+' '.join(f'{key}="{esc(value)}"' for key,value in attrs.items())+'></audio>')
    result='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><script src="assets/gsap.min.js"></script><style>*{box-sizing:border-box}html,body{margin:0;width:1080px;height:1920px;overflow:hidden;background:#000}#root{position:relative;width:100%;height:100%;overflow:hidden}.footage{position:absolute;left:0;top:0px;width:1080px;height:1920px;object-fit:cover}.host{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}</style><body>'''
    result+='<style>'+font_styles+'</style>'
    result+=f'<div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-fps="30" data-width="1080" data-height="1920">'+''.join(media)+'</div><script>const tl=gsap.timeline({paused:true});'+''.join(animations)+'window.__timelines["main"]=tl;</script></body></html>'
    (job/'index.html').write_text(result,encoding='utf-8')
