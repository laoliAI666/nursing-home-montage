"""Project-scoped orchestration. All media and timings are job data, not skill constants."""
from pathlib import Path
import copy,hashlib,json,math,os,re,shutil,subprocess,sys,zipfile
from narration_timing import MINUTE_TARGET, frame_duration, minute_tempo, within_target

SKILL=Path(__file__).resolve().parent.parent
ENGINE=SKILL/'modules/digital-human'
import project_config as pc
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def write(p,value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n', encoding='utf-8');tmp.replace(p)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def project_root():return pc.root()
def run(args,cwd=None,check=True,binary=False,env=None):
    result=subprocess.run((['rtk','proxy'] if shutil.which('rtk') else [])+list(map(str,args)),cwd=cwd,capture_output=True,text=not binary,encoding=None if binary else "utf-8",env=env)
    if check and result.returncode:raise RuntimeError(str(result.stderr)[-3500:]+'\n'+str(result.stdout)[-1500:])
    return result
def probe(path):return json.loads(run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',path]).stdout)
def decoded_duration(path):
    return len(run(['ffmpeg','-v','error','-i',path,'-vn','-ac','1','-ar','24000','-f','s16le','-'],binary=True).stdout)/48000
def pcm_sha(path):return hashlib.sha256(run(['ffmpeg','-v','error','-i',path,'-vn','-ac','1','-ar','24000','-f','s16le','-'],binary=True).stdout).hexdigest()
def settings(job):
    cfg=pc.resolved(job)
    if cfg['video']!={'width':1080,'height':1920,'fps':30}:raise ValueError('This approved template is 1080x1920 at 30fps; another format requires adapting its composition first')
    ident=cfg.get('template',{}).get('id','fullscreen-orange')
    import style_registry as sr
    if not sr.video_exists(ident) and not cfg.get('template',{}).get('_frozenEntry'):raise ValueError(f'未知视频样式：{ident}；请在风格库中选择内置或本地扩展的视频样式。')
    for role,font in cfg.get('template',{}).get('overrides',{}).get('fonts',{}).items():
        if font.get('file') and not Path(font['file']).is_file():raise ValueError('Template font is missing; fix the project font reference before production: '+role)
    a=cfg['avatar']
    min_y,max_y=0,1920
    if a['width']<=0 or a['height']<=0 or a['x']<0 or a['x']+a['width']>1080 or a['bottom']>max_y or a['bottom']-a['height']<min_y:raise ValueError('Presenter must stay inside the real-footage region above captions')
    return cfg
def hyperframes_cli(job):
    cfg=settings(job);entry=cfg.get('render',{}).get('cli_path')
    if not entry:return ['npx','--yes','hyperframes@'+cfg['render']['cli_version']]
    entry=Path(entry).resolve()
    if not entry.is_file():raise ValueError('Configured HyperFrames CLI entry is missing')
    package=next((p/'package.json' for p in entry.parents if (p/'package.json').is_file() and read(p/'package.json').get('name')=='hyperframes'),None)
    if not package or read(package)['version']!=cfg['render']['cli_version']:raise ValueError('Configured HyperFrames CLI does not match the pinned version')
    return ['node',str(entry)]
def hyperframes(job,args,cwd=None,check=True):
    env = pc.render_env(job)
    if os.environ.get('MONTAGE_WORKBENCH_DELIVERY') and args[0] in ('check','snapshot','render'):
        cache = pc.directory()/'cache/proxy'/settings(job)['render']['cli_version']
        cache.mkdir(exist_ok=True,parents=True)
        local = job/'.transcode-cache'
        if not local.exists() and not local.is_symlink():
            try:
                local.symlink_to(cache,target_is_directory=True)
            except OSError:
                pass  # Windows permissions and volumes without symlinks use a video-local cache.
        env.setdefault('HYPERFRAMES_PROXY_CACHE_MAX_BYTES', str(512 * 1024**2))
    return run([*hyperframes_cli(job),*args],cwd=cwd or job,check=check,env=env)
def state(job,**fields):
    old=read(job/'state.json') if (job/'state.json').exists() else {}
    old.update(fields);write(job/'state.json',old);return old
def copy_asset(src,dst):
    src=Path(src).resolve();dst=Path(dst);dst.parent.mkdir(parents=True,exist_ok=True)
    if dst.exists():
        if sha(src)!=sha(dst):raise RuntimeError('Asset changed; use a new output directory: '+str(dst))
    elif src!=dst:shutil.copy2(src,dst)
    return dst
def extract_text(path):
    path=Path(path);ext=path.suffix.lower()
    if ext in ('.txt','.md'):
        for enc in ('utf-8-sig','gb18030'):
            try:return path.read_text(encoding=enc)
            except UnicodeDecodeError:pass
        raise ValueError('Cannot decode '+str(path))
    if ext=='.docx':
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(path) as z:tree=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        return '\n'.join(''.join(p.itertext()) for p in tree.findall('.//w:p',ns))
    if ext=='.pdf':
        if not shutil.which('pdftotext'):raise RuntimeError('PDF text extraction needs pdftotext; use the PDF skill to extract it.')
        text=run(['pdftotext','-layout',path,'-']).stdout
        if not text.strip():raise RuntimeError('Scanned PDF needs OCR before it can be a fact source: '+str(path))
        return text
    raise ValueError('Unsupported fact document: '+str(path))
def inventory(source,job):
    source=Path(source).resolve();job.mkdir(parents=True,exist_ok=True)
    if not source.is_dir():raise ValueError('Input must be one institution directory')
    if job==source or source in job.parents:raise ValueError('Output must be outside the input material directory')
    if (job/'inventory.json').exists() and Path(read(job/'inventory.json')['source'])!=source:raise ValueError('This job belongs to another input')
    from shared_cache import SharedCache
    cache=SharedCache(project_root());rows=[];blocked=[];hits=0
    def analyze(p):
        ext=p.suffix.lower()
        if ext in ('.txt','.md','.docx','.pdf'):return {'kind':'text','text':extract_text(p)}
        m=probe(p);v=next((x for x in m['streams'] if x['codec_type']=='video'),None)
        if not v:raise RuntimeError('No video stream: '+str(p))
        return {'kind':'image' if ext in ('.jpg','.jpeg','.png','.webp','.heic') else 'video','width':v['width'],'height':v['height'],'duration':float(m['format'].get('duration',0))}
    analyzer_version='inventory-v2-'+sha(Path(__file__))
    for p in sorted(source.rglob('*')):
        if not p.is_file() or any(x.startswith('.') for x in p.relative_to(source).parts):continue
        if re.search(r'access.?key|secret|credential|凭据|密钥',p.name,re.I):continue
        if p.suffix.lower() not in ('.txt','.md','.docx','.pdf','.mp4','.mov','.m4v','.mkv','.avi','.webm','.jpg','.jpeg','.png','.webp','.heic'):continue
        try:details,hit,content_hash=cache.material(p,analyze,analyzer_version)
        except RuntimeError as e:blocked.append(str(e));continue
        rows.append({'path':str(p.relative_to(source)),'sha256':content_hash,'bytes':p.stat().st_size,**details});hits+=int(hit)
    write(job/'inventory.json',{'source':str(source),'files':rows,'blockedDocuments':blocked})
    (job/'source-text.txt').write_text('\n\n'.join('【'+r['path']+'】\n'+r['text'] for r in rows if r['kind']=='text'), encoding='utf-8')
    state(job,phase='inventoried',input=str(source),next='Read source-text and inspect media; author plan.json using references/plan-schema.md')
    return {'files':len(rows),'textDocuments':sum(r['kind']=='text' for r in rows),'blockedDocuments':blocked,'cacheHits':hits}

def integer_zh(n):
    chars='零一二三四五六七八九';n=int(n)
    if n==0:return chars[0]
    if n>=100000:return ''.join(chars[int(c)] for c in str(n))
    out='';zero=False
    for unit,name in ((10000,'万'),(1000,'千'),(100,'百'),(10,'十'),(1,'')):
        digit,n=divmod(n,unit)
        if digit:
            if zero:out+='零';zero=False
            out+=chars[digit]+name
        elif out and n:zero=True
    return out[1:] if out.startswith('一十') else out
_PAREN_RE=re.compile(r'（[^（）]*）|\([^()]*\)')
def strip_parenthetical(text):
    prev=None;out=str(text or '')
    while prev!=out:
        prev=out;out=_PAREN_RE.sub('',out)
    return out.translate(str.maketrans('','','（）()'))

def clean(text):
    from opencc import OpenCC
    return spoken_characters(OpenCC('t2s').convert(text))

def spoken_characters(text):
    text=strip_parenthetical(text).replace('〇','零')
    digits='零一二三四五六七八九'
    text=re.sub(r'\d{4}(?=年)',lambda m:''.join(digits[int(c)] for c in m[0]),text)
    text=re.sub(r'\d+\.\d+',lambda m:integer_zh(m[0].split('.')[0])+'点'+''.join(digits[int(c)] for c in m[0].split('.')[1]),text)
    text=re.sub(r'\d+',lambda m:integer_zh(m[0]),text)
    return ''.join(c.lower() for c in text if '\u3400'<=c<='\u9fff' or c.isascii() and c.isalpha())
def phonemes(text):
    from pypinyin import lazy_pinyin
    return lazy_pinyin(clean(text))
def word_letters(word,next_word=''):
    if re.search(r'\d{4}\s*$',word) and next_word.lstrip(' ,，、').startswith('年'):
        return clean(word+'年')[:-1]
    return clean(word)
def validate_plan(job):
    plan=read(job/'plan.json');inv=read(job/'inventory.json');files={r['path']:r for r in inv['files']};facts={}
    inst=plan['institution']
    if not all(inst.get(k) for k in ('name','displayName','headline')):raise ValueError('Institution name, displayName and headline are required')
    if len(inst['headline'])>12 or len(inst['displayName'])>12:raise ValueError('Shorten the truthful display headline/name to at most 12 characters; do not shrink locked captions')
    for fact in plan['facts']:
        source=files.get(fact['source'])
        if not source or source['kind']!='text' or not fact['quote'].strip() or fact['quote'] not in source['text']:raise ValueError('Fact quotation is not in the input document: '+fact['id'])
        if fact['id'] in facts:raise ValueError('Duplicate fact id')
        facts[fact['id']]=fact
    if not plan['sentences']:raise ValueError('Narration cannot be empty')
    closing=settings(job).get('closing_hook',{})
    if closing.get('enabled') and plan['sentences'][-1].get('kind')!='cta':
        raise ValueError('结尾需要独立 kind=cta 的引导句及真实视频镜头；本次明确不需要时设置 closing_hook.enabled=false')
    if closing.get('enabled'):
        import people
        selection=people.closing_hook(plan,settings(job));last=plan['sentences'][-1]
        previous=plan.get('closingHookSelection',{})
        if previous.get('text')==selection['text'] and previous.get('person_id')==selection['person_id'] and not plan.get('settings',{}).get('closing_hook',{}).get('hook_id'):
            selection=previous
        if last.get('text')!=selection['text']:
            last['text']=selection['text'];last.pop('captions',None)
        plan['closingHookSelection']=selection
    cfg=settings(job)
    if cfg.get('cover',{}).get('enabled'):
        from cover import validate_content
        validate_content(plan,cfg,inv)
    if cfg.get('delivery',{}).get('enabled'):
        from delivery import validate_publication
        validate_publication(plan.get('publication',{}))
    for i,s in enumerate(plan['sentences']):
        if not clean(s['text']):raise ValueError('Empty sentence')
        kind=s.get('kind','fact')
        if kind not in ('fact','hook','cta'):raise ValueError('Sentence kind must be fact, hook or cta')
        if kind=='cta' and i!=len(plan['sentences'])-1:raise ValueError('CTA must be the final sentence')
        if kind=='fact' and not s.get('factIds'):raise ValueError('Every factual sentence needs source references')
        if any(x not in facts for x in s.get('factIds',[])):raise ValueError('Unknown fact reference')
        if not s.get('shots'):raise ValueError('Every sentence needs shots')
        for shot in s['shots']:
            row=files.get(shot['path'])
            if not row or row['kind'] not in ('image','video'):raise ValueError('Shot is not an input visual: '+shot['path'])
            if i==0 and row['kind']!='video':raise ValueError('Opening shots must be real video')
            if kind=='cta' and row['kind']!='video':raise ValueError('Closing hook must continue real moving footage')
            if shot.get('sourceStart',0)<0 or shot.get('weight',1)<=0:raise ValueError('Invalid shot start or weight')
        if s.get('captions') and ''.join(clean(c['text']) for c in s['captions'])!=clean(s['text']):raise ValueError('Captions must cover the complete original sentence')
        for c in s.get('captions',[]):
            if clean(''.join(c['lines']))!=clean(c['text']) or not 1<=len(c['lines'])<=2:raise ValueError('Caption lines must preserve wording and use 1–2 lines')
    # Quotations prove provenance, not the truth of an arbitrary paraphrase; SKILL.md requires semantic source review.
    (job/'narration.txt').write_text('\n'.join(strip_parenthetical(s['text']) for s in plan['sentences'])+'\n', encoding='utf-8')
    write(job/'plan.json',plan)
    from content_review import audit
    audit(job)
    return plan,files

def engine_env(job):return pc.engine_env(job)
def engine(job,args,service='speech'):
    runtime=read(job/'runtime.json') if (job/'runtime.json').exists() else {}
    script=runtime.get('adapters',{}).get(service,str(ENGINE/'scripts/generate.py'))
    return run(['uv','run','--python','3.11',script,*args],env=engine_env(job),check=False)
def setup(job,install=False):
    job.mkdir(parents=True,exist_ok=True);settings(job);pc.freeze(job)
    cfg=settings(job);template=SKILL/'assets/render-project'
    if (job/'orange-font-match.json').is_file():
        matches=read(job/'orange-font-match.json');matches[0].update(file=cfg['typography'].get('body_font') or str(SKILL/'assets/LXGWWenKai-Regular.ttf'),index=cfg['typography'].get('collection_index'))
        write(job/'orange-font-match.json',matches)
    for name in ('package.json','package-lock.json','hyperframes.json'):copy_asset(template/name,job/name)
    (job/'assets/fonts').mkdir(parents=True,exist_ok=True);(job/'assets/reference').mkdir(exist_ok=True)
    copy_asset(SKILL/'assets/gsap.min.js',job/'assets/gsap.min.js')
    if os.environ.get('MONTAGE_WORKBENCH_DELIVERY'):
        from shared_cache import SharedCache
        cache=SharedCache(project_root())
        cache.share_immutable(SKILL/'assets/NotoSansSC-Black.otf',job/'assets/fonts/NotoSansSC-Black.otf',job,namespace='assets')
        if cfg['typography'].get('reference_asset'):
            cache.share_immutable(cfg['typography']['reference_asset'],job/'assets/reference/orange-ui-target.png',job,namespace='assets')
    else:
        copy_asset(SKILL/'assets/NotoSansSC-Black.otf',job/'assets/fonts/NotoSansSC-Black.otf')
        if cfg['typography'].get('reference_asset'):copy_asset(cfg['typography']['reference_asset'],job/'assets/reference/orange-ui-target.png')
    e=read(ENGINE/'config.json');e['tts'].update({k:cfg['voice'][k] for k in ('speaker','resource_id','model','speech_rate')})
    if cfg['avatar'].get('portrait'):e['image']=str(Path(cfg['avatar']['portrait']).resolve())
    else:e['image']=''
    if cfg['voice'].get('custom_speaker_id'):e['tts']['custom_speaker_id']=cfg['voice']['custom_speaker_id']
    connection=cfg.get('service',{})
    e['storage']=pc.storage_settings(connection.get('storage',{}))
    e['tts'].update({k:v for k,v in connection.get('tts',{}).items() if k in ('endpoint','resource_id','model','sample_rate','bit_rate')})
    if cfg['voice'].get('provider')=='minimax':
        e['tts'].update(provider='minimax', model=cfg['voice']['minimax_model'], sample_rate=32000)
    if cfg['voice'].get('provider')=='aliyun':
        e['tts'].update(provider='aliyun', model=cfg['voice']['aliyun_model'],
                        workspace=cfg['voice']['aliyun_workspace'], sample_rate=24000)
    e['avatar'].update(connection.get('avatar',{}))
    if cfg['avatar'].get('prompt'):e['avatar']['prompt']=cfg['avatar']['prompt']
    if cfg['voice'].get('pronunciation_dict'):
        rules={r.split('/',1)[0]:r for r in e['tts']['pronunciation_dict']['tone']+cfg['voice']['pronunciation_dict']['tone']}
        e['tts']['pronunciation_dict']={'tone':list(rules.values())}
    write(job/'avatar-engine.json',e)
    write(job/'meta.json',{'id':job.name,'name':job.name})
    if install:
        from shared_cache import install_dependencies
        install_dependencies(project_root(),job,template,run)
    return {'project':str(job),'dependenciesInstalled':install}

def institution_asr_hint(plan):
    name=plan.get('institution',{}).get('name','')
    return '机构专名：'+name.translate(str.maketrans('','','（）()')) if name else ''


class SpeechAlignmentError(RuntimeError):
    pass


def align_audio(audio,text,out,cfg,prompt='',*,retry_prompt=''):
    from faster_whisper import WhisperModel
    import difflib
    from opencc import OpenCC
    cached=out/'alignment-check.json'
    if cached.exists():
        d=read(cached)
        if d.get('sha256')==sha(audio) and d.get('textSha256')==digest(text) and d.get('passed'):return d
    cache=Path.home()/cfg['alignment']['local_cache'];model_path=str(cache) if cache.exists() else cfg['alignment']['model']
    model=WhisperModel(model_path,device='cpu',compute_type='int8',cpu_threads=cfg['alignment']['threads'])
    cc=OpenCC('t2s');expected=phonemes(text)
    def recognize(hint, hotwords=None):
        opts={'hotwords': hotwords} if hotwords else {}
        parts,_=model.transcribe(str(audio),language='zh',beam_size=5,word_timestamps=True,vad_filter=False,initial_prompt=hint or None,**opts)
        rows=[{'start':s.start,'end':s.end,'text':cc.convert(s.text),'words':[{'word':cc.convert(w.word),'start':w.start,'end':w.end,'probability':w.probability} for w in s.words or []]} for s in parts]
        recognized=''.join(r['text'] for r in rows);actual=phonemes(recognized)
        differences=[{'op':tag,'expected':expected[x:y],'recognized':actual[u:v],'expectedRange':[x,y]} for tag,x,y,u,v in difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_opcodes() if tag!='equal']
        return rows,recognized,differences
    rows,recognized,diff=recognize(prompt)
    first_diff=None
    if diff and retry_prompt and retry_prompt!=prompt:
        first_diff=diff
        rows,recognized,diff=recognize(retry_prompt)
    keyword_retry=None
    normalized=clean(text)
    if (diff and len(diff)<=4 and len(normalized)==len(expected)
            and all(d['op']=='replace' and 1<=len(d['expected'])==len(d['recognized'])<=2 for d in diff)):
        # Recheck neighboring words on the same audio. Require an exact full
        # transcript and measured numeric timings after this bounded retry.
        hints=[]
        numerals=re.compile(r'[零一二两三四五六七八九十百千万亿点]')
        for d in diff:
            start,end=d['expectedRange']
            disputed=normalized[start:end]
            if not numerals.search(disputed):
                left=numerals.split(normalized[max(0,start-2):start])[-1]
                right=numerals.split(normalized[end:end+2])[0]
                hints.append(left+disputed+right)
        if hints:
            hotwords='，'.join(dict.fromkeys(hints))
            keyword_retry={'priorDifferences':diff,'hotwords':hotwords}
            rows,recognized,diff=recognize('',hotwords)
    result={'sha256':sha(audio),'textSha256':digest(text),'expected':text,'recognized':recognized,'differences':diff,'passed':bool(expected) and not diff,'method':'local faster-whisper medium word alignment with simplified Chinese and numeral normalization','humanListeningPerformed':False,'segments':rows}
    if first_diff is not None:result['properNameRetry']={'firstDifferences':first_diff,'prompt':retry_prompt}
    if keyword_retry is not None:result['keywordRetry']=keyword_retry
    if not diff:
        from alignment import character_map
        try:
            character_map(result,text,clean,phonemes);result['numericMapping']={'passed':True,'method':'joined numeric tokens with measured group intervals'}
        except ValueError as error:
            result['numericMapping']={'passed':False,'error':str(error)};result['passed']=False
    write(cached,result)
    if not result['passed']:raise SpeechAlignmentError('Speech content or numeric timing needs review; see '+str(cached))
    return result
def fit_voice_duration(audio, out, cfg, target):
    if target is None:
        return audio, {}
    lead, fps = cfg['voice']['start'], cfg['video']['fps']
    original = decoded_duration(audio)
    duration = frame_duration(original, lead, fps)
    if within_target(duration, target):
        return audio, {}
    rate = minute_tempo(original, lead, fps) if target == MINUTE_TARGET else None
    if rate is None or rate <= 1:
        return audio, {'durationAdjustmentRequired':duration}

    sped = out/'narration-tempo.mp3'
    temp = out/'narration-tempo.tmp.mp3'
    run(['ffmpeg','-y','-v','error','-i',audio,'-vn','-af',f'atempo={rate:.10f}',
         '-c:a','libmp3lame','-q:a','2',temp])
    fitted = frame_duration(decoded_duration(temp), lead, fps)
    if not within_target(fitted, target):
        temp.unlink()
        return audio, {'durationAdjustmentRequired':duration}
    temp.replace(sped)
    return sped, {'tempoAdjustment':{'rate':rate,'beforeSeconds':duration,'afterSeconds':fitted,
                                     'sourceSha256':sha(audio)}}


def voice(job,reuse=None):
    plan,_=validate_plan(job);setup(job);cfg=settings(job);
    from preflight import require_preflight
    from content_review import require_review
    require_preflight(job);require_review(job)
    if not cfg['voice'].get('speaker'):raise ValueError('请先选择已就绪的数字人，或为该人物完成音色复刻。')
    text=(job/'narration.txt').read_text(encoding='utf-8').strip();out=job/'runs/voice'
    target=os.environ.get('MONTAGE_TARGET_DURATION_SECONDS')
    target=int(target) if target and not reuse else None
    cache_inputs={'text':text,'tts':read(job/'avatar-engine.json')['tts'],'connection':cfg.get('person',{}).get('connection'),'project':str(project_root())}
    if target == MINUTE_TARGET:
        cache_inputs['durationPolicy']={'target':target,'minimum':55,'lead':cfg['voice']['start'],'fps':cfg['video']['fps']}
    key=digest(cache_inputs)
    cache=project_root()/'作品/.montage-cache/voice'/f'{key}.json'
    if (job/'voice.json').exists():
        existing=read(job/'voice.json')
        if existing.get('cacheKey')!=key:raise RuntimeError('Voice text/settings changed; use a new job version')
        if Path(existing['audio']).exists() and sha(existing['audio'])==existing['sha256']:
            check=read(existing['alignment'])
            if check.get('passed') and check.get('sha256')==existing['sha256'] and check.get('textSha256')==digest(text):return existing
    from shared_cache import SharedCache
    shared=SharedCache(project_root())
    if not reuse and not out.exists():
        # Restored files belong to this job; later acceptance can safely delete them.
        restored=shared.restore_files('voice',key,{'audio':out/'narration.mp3','alignment':out/'alignment-check.json'},owner=job)
        if restored is not None:
            audio=out/'narration.mp3';checked=read(out/'alignment-check.json')
            if checked.get('passed') and checked.get('sha256')==sha(audio) and checked.get('textSha256')==digest(text):
                result={'audio':str(audio),'sha256':sha(audio),'duration':decoded_duration(audio),'alignment':str(out/'alignment-check.json'),'textSha256':digest(text),'cacheKey':key,'reusedVerifiedVoice':True}
                if restored.get('tempoAdjustment'):result['tempoAdjustment']=restored['tempoAdjustment']
                write(job/'voice.json',result);state(job,phase='voice_ready',next='build');return result
            raise RuntimeError('Cached voice alignment does not match its audio/text; refuse regeneration until reviewed')
    cached=read(cache) if cache.exists() else None
    if not reuse and not out.exists() and cached and Path(cached['audio']).is_file() and sha(cached['audio'])==cached['sha256'] and Path(cached.get('alignment','')).is_file():
        checked=read(cached['alignment'])
        if checked.get('passed') and checked.get('sha256')==cached['sha256'] and checked.get('textSha256')==digest(text):
            out.mkdir(parents=True);audio=copy_asset(cached['audio'],out/'narration.mp3');copy_asset(cached['alignment'],out/'alignment-check.json')
            result={'audio':str(audio),'sha256':sha(audio),'duration':decoded_duration(audio),'alignment':str(out/'alignment-check.json'),'textSha256':digest(text),'cacheKey':key,'reusedVerifiedVoice':True}
            if cached.get('tempoAdjustment'):result['tempoAdjustment']=cached['tempoAdjustment']
            write(job/'voice.json',result);state(job,phase='voice_ready',next='build');return result
    if not out.exists():
        args=['generate','--text-file',job/'narration.txt','--out',out,'--audio-only']
        if reuse:args+=['--reuse-audio',Path(reuse).resolve()]
        r=engine(job,args)
    else:
        s=read(out/'run.json')
        if s['text']!=text or s['config']['tts']!=read(job/'avatar-engine.json')['tts']:raise RuntimeError('Narration/config changed; use a new job version')
        r=engine(job,['resume',out,'--audio-only'])
    if r.returncode not in (0,3):raise RuntimeError(r.stdout[-2200:]+r.stderr[-1000:])
    s=read(out/'run.json');raw=Path(s['tts']['file'])
    audio,timing=fit_voice_duration(raw,out,cfg,target)
    if 'durationAdjustmentRequired' in timing:return timing
    try:
        alignment=align_audio(audio,text,out,cfg,plan.get('asrPrompt',''),retry_prompt=institution_asr_hint(plan))
    except SpeechAlignmentError:
        # Re-synthesize once only for a confirmed transcript mismatch. Preserve
        # the original audio and QA; keep the approved wording and all gates.
        failed_check = read(out/'alignment-check.json')
        if (not os.environ.get('MONTAGE_WORKBENCH_DELIVERY') or reuse or s.get('task_id')
                or s.get('revisions', 0) or s.get('tts', {}).get('reused_audio')
                or not failed_check['differences']):
            raise
        write(out/'alignment-check-before-retry.json', failed_check)
        r=engine(job,['revise',out])
        if r.returncode not in (0,3):raise RuntimeError(r.stdout[-2200:]+r.stderr[-1000:])
        s=read(out/'run.json');raw=Path(s['tts']['file'])
        audio,timing=fit_voice_duration(raw,out,cfg,target)
        if 'durationAdjustmentRequired' in timing:return timing
        alignment=align_audio(audio,text,out,cfg,plan.get('asrPrompt',''),retry_prompt=institution_asr_hint(plan))
    if s.get('qa_status') not in ('passed','reviewed'):
        evidence=out/'alignment-check.json'
        if audio != raw:
            # The speech provider's original receipt and review keep their own audio hash.
            original_check=out/'original-check'
            original_check.mkdir(exist_ok=True)
            align_audio(raw,text,original_check,cfg,plan.get('asrPrompt',''),retry_prompt=institution_asr_hint(plan))
            evidence=original_check/'alignment-check.json'
        r=engine(job,['review',out,'--note','Independent medium model phoneme and word check passed; no human listening claimed.','--evidence',evidence])
        if r.returncode:raise RuntimeError(r.stdout)
        r=engine(job,['resume',out,'--audio-only'])
        if r.returncode:raise RuntimeError(r.stdout)
    write(job/'voice.json',{'audio':str(audio),'sha256':sha(audio),'duration':decoded_duration(audio),'alignment':str(out/'alignment-check.json'),'textSha256':digest(text),'cacheKey':key,**timing})
    if not reuse:
        write(cache,{'audio':str(audio),'sha256':sha(audio),'alignment':str(out/'alignment-check.json'),**timing})
        shared.store_files('voice',key,{'audio':audio,'alignment':out/'alignment-check.json'},{'textSha256':digest(text),**timing},owner=job)
    state(job,phase='voice_ready',next='build')
    return read(job/'voice.json')
