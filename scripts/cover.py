"""Local cover generation and a self-contained editor, sharing one Canvas layout."""
from pathlib import Path
import base64,html,json,mimetypes,re,shutil,os
from workflow import read,write,sha,digest,run,settings,probe,hyperframes_cli,SKILL

def title_text(plan):
    value=plan.get('cover',{}).get('main_title','')
    if isinstance(value,dict):
        text=value.get('text','');lines=value.get('lines')
        if lines:
            if ''.join(lines)!=text.replace('\n',''):raise ValueError('主标题分行必须保留完整原文')
            text='\n'.join(lines)
        return text
    return value

def validate_content(plan,cfg,inventory):
    options=cfg.get('cover',{})
    if not options.get('enabled'):return
    if options.get('template','teal-adaptive')!='teal-adaptive':raise ValueError('未知封面模板')
    person=cfg.get('person',{})
    if not person.get('socialMediaName'):raise ValueError('缺少与数字人绑定的社交媒体名，请用户指定名字，无需平台或 ID。')
    if not person.get('id'):raise ValueError('封面需要明确数字人身份')
    if not options.get('header_text'):raise ValueError('请先确认项目固定封面头标')
    if not title_text(plan).strip():raise ValueError('缺少根据本条视频编写的封面主标题')
    if not plan.get('institution',{}).get('name'):raise ValueError('封面必须使用机构完整名称')
    value=plan['cover']['main_title'];highlights=value.get('highlights',[]) if isinstance(value,dict) else plan['cover'].get('highlights',[])
    if not isinstance(highlights,list) or any(not w or w not in title_text(plan).replace('\n','') for w in highlights):raise ValueError('高亮词必须来自主标题原文')
    labels=plan['cover'].get('labels',[])
    if len(labels)!=3:raise ValueError('封面需要三枚标签')
    files={r['path']:r for r in inventory.get('files',[])}
    for label in labels:
        if not isinstance(label,dict) or not label.get('text','').strip():raise ValueError('标签需要 text 和同机构素材来源')
        units=sum(.55 if ord(c)<128 else 1.0 for c in label['text'].strip())
        if units>5.8:raise ValueError('封面标签过长（每枚最多 5 个汉字，48px 下放不下），请改短：'+label['text'].strip())
        row=files.get(label.get('source'))
        if not row or row['kind'] not in ('image','video'):raise ValueError('封面图片必须来自当前机构素材：'+str(label.get('source')))
        if label.get('time',0)<0:raise ValueError('封面提帧时间不能为负')
        crop=label.get('crop',[0,0,1,1]);focus=label.get('focus',[.5,.5])
        if len(crop)!=4 or not (0<=crop[0]<crop[2]<=1 and 0<=crop[1]<crop[3]<=1) or len(focus)!=2 or not all(0<=x<=1 for x in focus):raise ValueError('图片 crop/focus 超出归一化边界')
    portrait=person.get('coverPortrait') or cfg.get('avatar',{}).get('portrait')
    if not portrait or not Path(portrait).is_file():raise ValueError('缺少所选数字人的人像')
    if options.get('image') and not Path(options['image']).is_file():raise ValueError('指定封面不存在')

def data_uri(path):
    path=Path(path);mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return 'data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()

def browser_package(cfg,job):
    entry=cfg.get('render',{}).get('cli_path')
    candidates=[]
    if entry:candidates.extend(p/'package.json' for p in Path(entry).resolve().parents)
    candidates.extend(Path(job).glob('node_modules/hyperframes/package.json'))
    candidates.extend((Path.home()/'.npm/_npx').glob('*/node_modules/hyperframes/package.json'))
    for p in candidates:
        if p.is_file():
            d=read(p)
            if d.get('name')=='hyperframes' and d.get('version')==cfg['render']['cli_version']:return p
    run([*hyperframes_cli(job),'--version'],cwd=job)
    for p in (Path.home()/'.npm/_npx').glob('*/node_modules/hyperframes/package.json'):
        if read(p).get('version')==cfg['render']['cli_version']:return p
    raise RuntimeError('请先准备锁定的 HyperFrames 渲染环境')

def prepare(job):
    cfg=settings(job);plan=read(job/'plan.json');inv=read(job/'inventory.json');validate_content(plan,cfg,inv)
    folder=job/'assets/cover';folder.mkdir(parents=True,exist_ok=True)
    sources=[];photos=[];root=Path(inv['source']).resolve();files={r['path']:r for r in inv['files']}
    for i,label in enumerate(plan['cover']['labels']):
        source=(root/label['source']).resolve()
        if not source.is_relative_to(root):raise ValueError('封面素材不能越出机构目录')
        if sha(source)!=files[label['source']]['sha256']:raise ValueError('封面素材已变化，请重新编排')
        target=folder/f'photo-{i}.png';time=float(label.get('time',0))
        args=['ffmpeg','-y','-v','error']
        if files[label['source']]['kind']=='video':args+=['-ss',str(time)]
        run([*args,'-i',source,'-frames:v','1','-vf','scale=min(1920\\,iw):-1',target])
        if not target.is_file():raise ValueError('提帧失败，请检查素材时间点')
        photos.append({'data':data_uri(target),'crop':label.get('crop',[0,0,1,1]),'focus':label.get('focus',[.5,.5])})
        sources.append({'source':label['source'],'time':time,'sha256':sha(source),'label':label['text'],'match':label.get('match','best-effort')})
    person=cfg['person'];avatar=person.get('coverPortrait')
    if not avatar:
        portrait=Path(cfg['avatar']['portrait']);cache=Path(__import__('project_config').directory())/'people'/person['id']/'cover-cache'/sha(portrait)
        cache.mkdir(parents=True,exist_ok=True);avatar=cache/'portrait.png'
        if not avatar.is_file():
            from workflow import hyperframes
            hyperframes(job,['remove-background',portrait,'-o',avatar,'--device','auto','--json'])
    avatar=Path(avatar);stream=next(s for s in probe(avatar)['streams'] if s['codec_type']=='video')
    # Normalize framing for newly derived people; explicit assets may preserve an approved box.
    avbox=person.get('coverBox')
    if not avbox:
        scale=min(710/stream['width'],986/stream['height']);w=round(stream['width']*scale);h=round(stream['height']*scale);avbox=[1054-w,1703-h,w,h]
    # Cover typography is independent of the body's handwritten caption font.
    font=Path(cfg.get('cover',{}).get('font') or SKILL/'assets/NotoSansSC-Black.otf')
    # WOFF2 retains every glyph so offline future edits are not limited to today's text.
    webfont=folder/'cover-font.woff2';font_record=folder/'font.json'
    font_key=digest({'font':sha(font)})
    if not webfont.is_file() or not font_record.is_file() or read(font_record).get('key')!=font_key:
        import project_config as pc
        cached=pc.directory()/'cache/cover-fonts'/(font_key+'.woff2');cached.parent.mkdir(parents=True,exist_ok=True)
        if not cached.is_file():
            from fontTools.ttLib import TTFont
            temporary=cached.with_name(cached.name+f'.{os.getpid()}.tmp')
            face=TTFont(font,fontNumber=0,recalcTimestamp=False);face.flavor='woff2';face.save(temporary);face.close();temporary.replace(cached)
        from publish_assets import link_or_copy
        link_or_copy(cached,webfont)
        write(font_record,{'key':font_key})
    value=plan['cover']['main_title'];highlights=value.get('highlights',[]) if isinstance(value,dict) else plan['cover'].get('highlights',[])
    payload={'header_text':cfg['cover']['header_text'],'title':title_text(plan),'highlights':highlights,'labels':[x['text'] for x in plan['cover']['labels']],
        'institution_name':plan['institution']['name'],'identity':{'person_id':person['id'],'person_name':person['name'],'social_media_name':person['socialMediaName']},
        'avatar':data_uri(avatar),'avatar_box':avbox,'font':data_uri(webfont),'photos':photos}
    if cfg['cover'].get('image'):
        payload['provided_cover']=data_uri(cfg['cover']['image'])
    elif os.environ.get('MONTAGE_WORKBENCH_DELIVERY'):
        # Keep the entire main title by reflowing first; if it is too long,
        # use the short, fact-checked video headline already in the plan.
        payload['automaticTitleFallback'] = plan['institution']['headline']
    record={'template':'teal-adaptive','content':{k:v for k,v in payload.items() if k not in ('avatar','font','photos','provided_cover')},'sources':sources,'avatarSha256':sha(avatar),'fontSha256':sha(font),'imageGenerationUsed':False}
    record['inputFingerprint']=digest({'plan':plan.get('cover'),'identity':payload['identity'],'automaticTitleFallback':payload.get('automaticTitleFallback'),'avatarBox':avbox,'institution':plan['institution']['name'],'header':payload['header_text'],'sources':sources,'avatar':record['avatarSha256'],'font':record['fontSha256'],
        'provided':sha(cfg['cover']['image']) if cfg['cover'].get('image') else None,'layout':sha(SKILL/'assets/covers/teal-adaptive/layout.mjs'),'editor':sha(SKILL/'assets/covers/teal-adaptive/cover.html'),'renderer':sha(SKILL/'scripts/cover_renderer.mjs'),'browserRuntime':sha(SKILL/'scripts/browser_runtime.mjs')})
    return cfg,payload,record

def editor_html(payload):
    layout=(SKILL/'assets/covers/teal-adaptive/layout.mjs').read_text(encoding='utf-8').replace('export const ','const ').replace('export async function ','async function ')
    template=(SKILL/'assets/covers/teal-adaptive/cover.html').read_text(encoding='utf-8')
    return template.replace('__LAYOUT__',layout).replace('__DATA__',json.dumps(payload,ensure_ascii=False).replace('<','\\u003c'))

def generate(job):
    cfg=settings(job)
    if not cfg.get('cover',{}).get('enabled'):return {'skipped':'cover disabled'}
    cfg,payload,record=prepare(job);out=job/'cover.png';previous=read(job/'cover.json') if (job/'cover.json').exists() else {}
    if previous.get('inputFingerprint')==record['inputFingerprint'] and out.is_file() and sha(out)==previous.get('pngSha256') and (job/'cover-editor.html').is_file() and sha(job/'cover-editor.html')==previous.get('editorSha256'):
        return {'cover':str(out),'reused':True}
    (job/'cover-editor.html').write_text(editor_html(payload),encoding='utf-8')
    temp=job/'assets/cover/layout.json'
    import project_config as pc
    run(['node',SKILL/'scripts/cover_renderer.mjs',job/'cover-editor.html',out,temp,browser_package(cfg,job)],env=pc.render_env(job))
    record['layout']=read(temp)
    title_adjustment = record['layout'].get('titleAdjustment')
    if title_adjustment:
        record['content']['title'] = record['layout']['title_text']
        record['content']['highlights'] = record['layout']['title_highlights']
    if record['layout'].get('tagAdjustment'):
        record['content']['labels'] = record['layout']['label_texts']
    if cfg['cover'].get('image'):
        image=Path(cfg['cover']['image']);meta=next(s for s in probe(image)['streams'] if s['codec_type']=='video')
        if (meta['width'],meta['height'])!=(1080,1920):raise ValueError('指定封面须为 1080×1920，避免擅自裁切')
        if image.suffix.lower()=='.png':shutil.copy2(image,out)
        else:run(['ffmpeg','-y','-v','error','-i',image,'-frames:v','1',out])
    record.update(pngSha256=sha(out),editorSha256=sha(job/'cover-editor.html'),provided=bool(cfg['cover'].get('image')));write(job/'cover.json',record)
    return {'cover':str(out),'editor':str(job/'cover-editor.html'),'layoutPassed':not record['layout']['errors']}

def attach(job):
    """Idempotent seek-safe one-frame overlay, common to all body composers."""
    cfg=settings(job);path=job/'index.html'
    if not cfg.get('cover',{}).get('enabled') or not path.is_file():return
    text=path.read_text(encoding='utf-8');text=re.sub(r'<!-- cover:start -->.*?<!-- cover:end -->','',text,flags=re.S)
    if not (job/'cover.png').is_file():return
    fps=cfg['video']['fps'];layer=f'''<!-- cover:start --><img id="default-cover" class="clip" data-start="0" data-duration="{1/fps:.12f}" data-track-index="99" src="cover.png" style="position:absolute;inset:0;width:1080px;height:1920px;object-fit:cover;z-index:9999" /><!-- cover:end -->'''
    from html.parser import HTMLParser
    class RootEnd(HTMLParser):
        depth=0;end=None;root_tag=None
        def handle_starttag(self,tag,attrs):
            a=dict(attrs)
            if self.root_tag and tag==self.root_tag:self.depth+=1
            elif not self.root_tag and 'data-composition-id' in a:self.root_tag=tag;self.depth=1
        def handle_endtag(self,tag):
            if tag==self.root_tag and self.end is None:
                self.depth-=1
                if not self.depth:
                    line,col=self.getpos();self.end=sum(len(s) for s in text.splitlines(True)[:line-1])+col
    parser=RootEnd();parser.feed(text)
    if parser.end is None:raise ValueError('找不到合成根节点，无法合入首帧')
    text=text[:parser.end]+layer+text[parser.end:];path.write_text(text,encoding='utf-8')

def require_current(job):
    if not settings(job).get('cover',{}).get('enabled'):return
    if not (job/'cover.json').is_file() or not (job/'cover.png').is_file():raise ValueError('缺少封面，请先执行 cover 阶段')
    record=read(job/'cover.json');cfg,payload,current=prepare(job)
    if record.get('inputFingerprint')!=current['inputFingerprint'] or record.get('pngSha256')!=sha(job/'cover.png') or record.get('editorSha256')!=sha(job/'cover-editor.html'):raise ValueError('封面输入或文件已变化，请重新生成并检查')
    if record.get('layout',{}).get('errors'):raise ValueError('封面布局仍有错误')
