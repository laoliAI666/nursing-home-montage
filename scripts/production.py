from pathlib import Path
from html.parser import HTMLParser
import json,sys,os
from tempfile import TemporaryDirectory
from workflow import *
from build_job import compose


def cache_avatar(job,key,record):
    from shared_cache import SharedCache
    files={'video':record['video']}
    files.update({'frame-'+str(i):p for i,p in enumerate(record['visualReviewFrames'])})
    metadata={k:record[k] for k in ('taskId','driverPcmSha256','videoSha256')}
    shared=SharedCache(project_root())
    alignment=record['alignment']
    if isinstance(alignment,dict):
        # Jobs from older engines saved the timing object inline.
        with TemporaryDirectory(prefix='avatar-alignment-') as temporary:
            path=Path(temporary)/'alignment.json'
            write(path,alignment)
            shared.store_files('avatar',key,{**files,'alignment':path},metadata,owner=job)
    else:
        shared.store_files('avatar',key,{**files,'alignment':alignment},metadata,owner=job)

def restore_avatar(job,key,folder):
    from shared_cache import SharedCache
    shared=SharedCache(project_root());entry=shared.read('avatar',key)
    if entry is None:return None
    output=folder/'shared-source'
    destinations={label:output/('source.mp4' if label=='video' else 'alignment.json' if label=='alignment' else label+'.png') for label in entry['files']}
    metadata=shared.restore_files('avatar',key,destinations,owner=job)
    if metadata is None:return None
    return {**metadata,'cacheKey':key,'video':str(destinations['video']),'alignment':str(destinations['alignment']),
            'run':str(output),'visualReviewFrames':[str(destinations[k]) for k in sorted(destinations) if k.startswith('frame-')],
            'visualReviewPending':True,'reusedVerifiedGeneration':True}

def avatars(job,wait_seconds=45):
    setup(job);cfg=settings(job);pending=[];completed=[];cache_warnings=[]
    from preflight import require_preflight
    require_preflight(job)
    for a in read(job/'avatars.json'):
        audio=job/a['audio'];e=read(job/'avatar-engine.json');portrait=Path(e['image'])
        key=digest({'pcm':pcm_sha(audio),'portrait':sha(portrait),'avatar':e['avatar'],'resource':e['avatar']['req_key'],'connection':cfg.get('person',{}).get('connection'),'project':str(project_root())})
        cache=project_root()/'作品/.montage-cache/avatar'/f'{key}.json'
        run_dir=job/'runs'/('avatar-'+a['id']);record_path=job/'assets'/('avatar-'+a['id'])/'generation-source.json'
        if record_path.exists():
            record=read(record_path)
            if record['cacheKey']!=key:raise RuntimeError('Avatar driver/config changed; use a new job version')
            if Path(record['video']).exists() and sha(record['video'])==record['videoSha256']:
                completed.append(a['id']);continue
        if not run_dir.exists():
            restored=restore_avatar(job,key,record_path.parent)
            if restored:
                write(record_path,restored);completed.append(a['id']);continue
        if cache.exists() and not run_dir.exists():
            c=read(cache)
            if Path(c['video']).exists() and sha(c['video'])==c['videoSha256']:
                cache_avatar(job,key,c)
                c=restore_avatar(job,key,record_path.parent)
                write(record_path,c);completed.append(a['id']);continue
        if run_dir.exists():
            s=read(run_dir/'run.json')
            if s['text']!=a['text']:raise RuntimeError('Avatar text changed; use a new version')
            r=engine(job,['resume',run_dir,'--wait-seconds',str(wait_seconds)],service='avatar')
        else:
            r=engine(job,['generate','--text',a['text'],'--reuse-audio',audio,'--out',run_dir,'--wait-seconds',str(wait_seconds)],service='avatar')
        if r.returncode==3:
            s=read(run_dir/'run.json')
            align_audio(Path(s['tts']['file']),a['text'],run_dir,cfg,retry_prompt=institution_asr_hint(read(job/'plan.json')))
            r=engine(job,['review',run_dir,'--note','Independent medium model phoneme check passed; no human listening claimed.','--evidence',run_dir/'alignment-check.json'],service='avatar')
            if r.returncode:raise RuntimeError(r.stdout)
            r=engine(job,['resume',run_dir,'--wait-seconds',str(wait_seconds)],service='avatar')
        if r.returncode==2:
            s=read(run_dir/'run.json');pending.append({'id':a['id'],'taskId':s.get('task_id'),'run':str(run_dir)})
            # One bounded cloud wait per command. Resume never creates another task for this id.
            break
        if r.returncode:raise RuntimeError(r.stdout[-2200:]+r.stderr[-1200:])
        s=read(run_dir/'run.json');v=s['verification']
        alignment=v['alignment']
        if isinstance(alignment,dict):
            path=run_dir/'alignment.json'
            if not path.is_file():
                # Some adapters return the verified timing only in run.json.
                write(path,alignment)
            alignment=str(path)
        record={'cacheKey':key,'video':v['output'],'videoSha256':v['sha256'],'taskId':s['task_id'],'run':str(run_dir),'alignment':str(alignment),'driverPcmSha256':pcm_sha(audio),'visualReviewFrames':v['visual_review_frames'],'visualReviewPending':True}
        write(record_path,record)
        try:
            cache_avatar(job,key,record)
            write(cache,record)
        except OSError:
            cache_warnings.append(a['id'])
        completed.append(a['id'])
    state(job,phase='avatars_pending' if pending else 'avatars_ready',pending=pending,next='avatars' if pending else 'Inspect provider frames, then cutouts')
    return {'completed':completed,'pending':pending,**({'cacheWarnings':cache_warnings} if cache_warnings else {})}

def cutouts(job):
    results=[]
    for a in read(job/'avatars.json'):
        target=job/a['video'];folder=target.parent;record=read(folder/'generation-source.json')
        if sha(record['video'])!=record['videoSha256']:raise RuntimeError('Generated video changed after verification')
        key=digest({'video':record['videoSha256'],'outlineScript':sha(SKILL/'scripts/outline_presenter.py'),'matte':'u2net_human_seg','size':[320,440]})
        cache=project_root()/'作品/.montage-cache/cutout'/f'{key}.json'
        previous=read(folder/'cutout-source.json') if (folder/'cutout-source.json').exists() else {}
        if target.exists() and previous.get('cacheKey')==key and previous.get('sha256')==sha(target):
            results.append(str(target));continue
        from shared_cache import SharedCache
        shared=SharedCache(project_root())
        restored=shared.restore_files('cutout',key,{'video':target},owner=job)
        if restored is not None:
            c={'cacheKey':key,'file':str(target),'sha256':sha(target)};write(folder/'cutout-source.json',c);results.append(str(target));continue
        if cache.exists():
            c=read(cache)
            if Path(c['file']).exists() and sha(c['file'])==c['sha256']:
                copy_asset(c['file'],target);c={**c,'file':str(target)}
                shared.store_files('cutout',key,{'video':target},owner=job);write(folder/'cutout-source.json',c);results.append(str(target));continue
        # Frame rate conversion only. Audio, speech speed and pitch are untouched.
        normalized=folder/'presenter-cfr.mkv'
        run(['ffmpeg','-y','-v','error','-i',record['video'],'-t',a['duration'],'-vf','fps=30','-an','-c:v','ffv1',normalized])
        hyperframes(job,['remove-background',normalized,'-o',folder/'avatar-matte.mov','--device','auto','--json'])
        run(['uv','run','--python','3.11',SKILL/'scripts/outline_presenter.py','--project',job,'--asset-dir',str(folder.relative_to(job)),'--min-seconds',str(max(.1,a['duration']-1/30))],cwd=job)
        c={'cacheKey':key,'file':str(target),'sha256':sha(target)};write(folder/'cutout-source.json',c);write(cache,c);shared.store_files('cutout',key,{'video':target},owner=job);results.append(str(target))
        # Both can be recreated from the retained, verified provider video.
        # The alpha WebM and its verification record are the actual render input.
        normalized.unlink(missing_ok=True)
        (folder/'avatar-matte.mov').unlink(missing_ok=True)
    compose(job);state(job,phase='composed',next='carve, check and inspect snapshots')
    return {'cutouts':results}

def review_provider(job,frames,note):
    if not frames or not note.strip():raise ValueError('请提供实际看过的接口原片帧及具体观察。')
    for a in read(job/'avatars.json'):
        path=job/'assets'/('avatar-'+a['id'])/'generation-source.json';record=read(path)
        known={str(Path(p).resolve()) for p in record['visualReviewFrames']}
        selected=[str(Path(p).resolve()) for p in frames if str(Path(p).resolve()) in known]
        if not known.issubset(set(selected)):raise ValueError('每个数字人的原片首、中、尾检查帧都需查看并记录：'+a['id'])
        if sha(record['video'])!=record['videoSha256']:raise ValueError('原片已变化，需重新核验。')
        record.update(visualReviewPending=False,visualReview={'frames':selected,'note':note,'sha256':record['videoSha256']})
        write(path,record)
    return {'providerReviewRecorded':True}

def carve(job):
    if not read(job/'bgm-timeline.json').get('enabled'):return {'skipped':'no music configured'}
    run(['node',SKILL/'scripts/carve.mjs','--comp',job/'index.html','--bed','music-bed','--voice','female-narration','--strength',str(settings(job)['music']['carve_strength']),'--core',job])
    bgm=read(job/'bgm-timeline.json')
    class Parser(HTMLParser):
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='audio' and attrs.get('id')=='music-bed':
                for key in ('data-fx-chain','data-automation','data-fx-carve'):
                    if key in attrs:bgm[key]=json.loads(attrs[key])
    Parser().feed((job/'index.html').read_text(encoding='utf-8'));write(job/'bgm-timeline.json',bgm)
    return {'carveSaved':True}
def input_fingerprint(job):
    # Bind checks to the actual HTML and every locally referenced media/font; no mtime approvals.
    files=list((job/'compositions').glob('*.html'))+[job/'index.html']
    import re,html
    content=''.join(p.read_text(encoding='utf-8') for p in files)
    files.extend(p for p in (job/'cover.png',job/'cover.json',job/'cover-editor.html') if p.is_file())
    for path in set(re.findall(r'(?:src|href)=[\"\x27](assets/[^\"\x27]+)',content)+re.findall(r'url\([\"\x27]?(assets/[^\"\x27\)]+)',content)):
        p=job/html.unescape(path)
        if p.is_file():files.append(p)
    return digest({str(p.relative_to(job)):sha(p) for p in files})
def require_cutouts(job):
    for a in read(job/'avatars.json'):
        if not (job/a['video']).is_file():raise RuntimeError('Missing requested presenter: '+a['id'])
def check(job):
    from cover import require_current
    require_current(job)
    require_cutouts(job);r=hyperframes(job,['check','--json'],check=False)
    (job/'check.log').write_text(r.stdout+'\n'+r.stderr, encoding='utf-8')
    try:d=json.JSONDecoder().raw_decode(r.stdout[r.stdout.index('{'):])[0]
    except (ValueError,json.JSONDecodeError):raise RuntimeError('Check did not return JSON; see check.log')
    d['inputFingerprint']=input_fingerprint(job);write(job/'check.json',d)
    if r.returncode or not d.get('ok'):
        section=next((k for k in ('runtime','layout','lint','contrast','audio') if d.get(k,{}).get('errorCount',0)), 'runtime')
        findings=d.get(section,{}).get('findings',[]);message=next((f.get('message','') for f in findings if f.get('severity')=='error'), '检查未完成')
        raise RuntimeError(('浏览器运行环境不可用' if d.get('browserSkipped') else section+' 检查失败')+'：'+message+'；查看 check.json/check.log。')
    return {'ok':True,'warnings':{k:v.get('warningCount') for k,v in d.items() if isinstance(v,dict) and v.get('warningCount')}}
def snapshot(job,times=None):
    total=read(job/'natural-timeline.json')['duration'];avatars=read(job/'avatars.json')
    if not times:
        points=review_times(job,total)
        times=','.join(str(t) for t in sorted(set(max(0,min(total-1/30,x)) for x in points)))
    r=hyperframes(job,['snapshot','--at',times,'--no-end','--output','qc-layout'])
    return {'output':str(job/'qc-layout'),'result':r.stdout[-2200:]}
def review_times(job,total):
    points=[0,1/30,.6,total/2,total-1/30]
    for name in ('shot-list.json','captions.json'):
        if (job/name).is_file():
            for row in read(job/name):points.append((row['start']+row.get('end',row['start']+row.get('duration',0)))/2)
    for a in read(job/'avatars.json'):
        points.extend([a['start']+min(.6,a['duration']/2),a['start']+a['duration']/2,a['start']+a['duration']-1/30])
    return sorted(set(round(max(0,min(total-1/30,t))*30)/30 for t in points))
def contrast_review(job,frames,note):
    c=read(job/'check.json');fingerprint=input_fingerprint(job)
    if c.get('inputFingerprint')!=fingerprint:raise ValueError('Layout changed; run the complete check again')
    if not note.strip() or not frames:raise ValueError('Actual layout frames and a concrete visual contrast finding are required')
    errors=c.get('contrast',{}).get('errorCount',0)
    if not errors or any(v.get('errorCount',0) for k,v in c.items() if isinstance(v,dict) and k not in ('contrast','visualContrastReview')):raise ValueError('Only reference-style contrast failures can receive this visual review; fix all other errors')
    findings=[f for f in c['contrast'].get('findings',[]) if f.get('severity')=='error']
    if not findings or any(f.get('code')!='contrast_aa_failure' for f in findings):raise ValueError('Unexpected contrast error; inspect it before continuing')
    evidence=[]
    for path in frames:
        p=Path(path).resolve()
        if not p.is_relative_to((job/'qc-layout').resolve()) or not p.is_file():raise ValueError('Use actual snapshots from this job/qc-layout')
        evidence.append({'file':str(p),'sha256':sha(p)})
    c['visualContrastReview']={'inputFingerprint':fingerprint,'frames':evidence,'findings':findings,'note':note,'fullCheckPassed':False,'referenceStyleRetained':True}
    write(job/'check.json',c)
    return {'renderAcceptedWithVisualContrastReview':True,'fullCheckPassed':False,'contrastErrorsRetained':errors}
def accepted_check(job,c):
    fingerprint=input_fingerprint(job)
    if c.get('inputFingerprint')!=fingerprint:return False
    if c.get('ok'):return True
    review=c.get('visualContrastReview',{})
    return review.get('inputFingerprint')==fingerprint and bool(review.get('frames')) and not any(v.get('errorCount',0) for k,v in c.items() if isinstance(v,dict) and k not in ('contrast','visualContrastReview'))
def render(job):
    from cover import require_current
    require_current(job)
    require_cutouts(job);c=read(job/'check.json') if (job/'check.json').exists() else {}
    if not accepted_check(job,c):check(job)
    cfg=settings(job);out=job/'renders/final.mp4';out.parent.mkdir(exist_ok=True)
    # A published final may share this inode. A later render must get a new file.
    if out.is_file() and out.stat().st_nlink>1:out.unlink()
    r=hyperframes(job,['render','--quality','delivery','--crf',str(cfg['render']['crf']),'--fps','30','--workers',str(cfg['render']['workers']),'--output',out],check=False)
    (job/'render.log').write_text(r.stdout+'\n'+r.stderr, encoding='utf-8')
    if r.returncode:raise RuntimeError('Render failed; see render.log')
    state(job,phase='rendered',video=str(out),next='verify, inspect actual rendered frames, review')
    return {'video':str(out)}
def verify(job):
    video=job/'renders/final.mp4';timeline=read(job/'natural-timeline.json');total=timeline['duration'];m=probe(video)
    v=next(s for s in m['streams'] if s['codec_type']=='video')
    if (v['width'],v['height'],v['r_frame_rate'])!=(1080,1920,'30/1') or abs(float(m['format']['duration'])-total)>.015:raise RuntimeError('Final video dimensions/rate/duration differ from the composition')
    if int(v.get('nb_frames',round(total*30)))!=round(total*30):raise RuntimeError('Unexpected video frame count')
    run(['ffmpeg','-v','error','-i',video,'-f','null','-'])
    if sha(job/timeline['rawAudio'])!=timeline['rawAudioSha256'] or sha(job/timeline['audio'])!=timeline['audioSha256']:raise RuntimeError('Voice was changed after timing')
    tail=total-(timeline['voiceStart']+timeline['voiceDuration'])
    if not 0<=tail<1/30+.000001:raise RuntimeError('Unrequested tail padding or clipped narration')
    lev=run(['ffmpeg','-hide_banner','-i',video,'-af','loudnorm=I=-17:TP=-1.5:LRA=11:print_format=json','-vn','-f','null','-']).stderr
    lev=json.loads(lev[lev.rfind('{'):lev.rfind('}')+1])
    if float(lev['input_tp'])>-1:raise RuntimeError('Audio peak too high')
    layout=read(job/'template-layout.json') if (job/'template-layout.json').exists() else {}
    picture=layout.get('pictureOutput',[0,478,1080,1028])
    # Inspect the clear footage, not the template's header/footer background.
    px,py,pw,ph=picture;crop=[int(pw*.8)//2*2,int(min(ph*.8,900))//2*2,int(px+pw*.1)//2*2,int(py+(ph-min(ph*.8,900))/2)//2*2]
    black=run(['ffmpeg','-hide_banner','-i',video,'-vf',f'crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]},blackdetect=d=0.06:pix_th=0.1:pic_th=0.98','-an','-f','null','-']).stderr
    events=[s for s in black.splitlines() if 'black_start:' in s]
    if events:raise RuntimeError('Black frames in actual footage: '+str(events))
    qc=job/'qc-final';qc.mkdir(exist_ok=True);times=review_times(job,total)
    frames=[]
    for i,t in enumerate(sorted(set(max(0,min(total-1/30,x)) for x in times))):
        out=qc/f'frame-{i:02}-{t:.3f}s.png';run(['ffmpeg','-y','-v','error','-ss',t,'-i',video,'-frames:v','1',out]);frames.append(str(out))
    report={'video':str(video),'sha256':sha(video),'duration':float(m['format']['duration']),'frames':round(total*30),'width':1080,'height':1920,'fps':30,'audio':lev,'tailAfterAudio':tail,'fullDecode':True,'centralFootageBlackEvents':events,'renderedReviewFrames':frames,'visualReviewPending':True,'humanListeningPerformed':False}
    report['footageBlackCheckCrop']=crop;report['template']=settings(job).get('template',{}).get('id','fullscreen-orange')
    report['reviewCoverage']={'shots':len(read(job/'shot-list.json')),'captions':len(read(job/'captions.json')),'allSampleFramesRequired':True}
    report['reviewStatus']={'speechContent':'independent-ASR','listening':'not-recorded','visualLipSync':'not-recorded','providerAudioAlignment':'separate-from-visual-lip-sync'}
    music=read(job/'bgm-timeline.json');report['music']={'enabled':music.get('enabled',False),'audio':music.get('audio'),'sourceFrozen':bool(settings(job).get('music',{}).get('resource'))}
    report['avatarWindows']=[{'id':a['id'],'start':a['start'],'end':a['start']+a['duration']} for a in read(job/'avatars.json')]
    if settings(job).get('cover',{}).get('enabled'):
        from cover import require_current
        require_current(job)
        def pixels(source):return run(['ffmpeg','-v','error','-i',source,'-frames:v','1','-vf','scale=108:192','-pix_fmt','rgb24','-f','rawvideo','-'],binary=True).stdout
        expected=pixels(job/'cover.png');actual=pixels(frames[0]);second=pixels(frames[1])
        error=sum(abs(a-b) for a,b in zip(expected,actual))/len(expected)
        difference=sum(abs(a-b) for a,b in zip(actual,second))/len(actual)
        if error>6 or difference<2:raise RuntimeError('首帧封面未匹配或泄漏到次帧')
        audio_stream=next(s for s in m['streams'] if s['codec_type']=='audio')
        if abs(float(audio_stream.get('start_time',0)))>1/30:raise RuntimeError('音轨起点发生偏移')
        report['cover']={'frames':1,'firstFrameMeanPixelError':error,'secondFrameDifference':difference,'pngSha256':sha(job/'cover.png'),'extraSeconds':0}
    checks=read(job/'check.json');report['fullCompositionCheckPassed']=checks.get('ok',False)
    if checks.get('visualContrastReview'):report['visualContrastReview']=checks['visualContrastReview']
    write(job/'verification.json',report);state(job,phase='verified_visual_review_pending',next='View renderedReviewFrames, then review')
    return report
def review(job,frames,note,listening_note=None,motion_note=None):
    report=read(job/'verification.json');video=Path(report['video'])
    if sha(video)!=report['sha256']:raise RuntimeError('Render changed; verify and inspect it again')
    if not note.strip() or not frames:raise ValueError('Record actual inspected frames and concrete findings')
    known=set(report['renderedReviewFrames'])
    frames=[str(Path(p).resolve()) for p in frames]
    if any(str(Path(p).resolve()) not in known for p in frames):raise ValueError('Review evidence must be from this verified render')
    if report.get('reviewCoverage',{}).get('allSampleFramesRequired') and not known.issubset(set(frames)):raise ValueError('请查看并记录所有逐镜／字幕检查帧；缺失 '+str(len(known-set(frames)))+' 张。')
    if settings(job).get('cover',{}).get('enabled') and not set(report['renderedReviewFrames'][:2]).issubset(set(map(str,frames))):raise ValueError('请查看并记录封面首帧与正文次帧')
    report.update(visualReviewPending=False,visualReview={'frames':frames,'note':note,'videoSha256':report['sha256']});write(job/'verification.json',report)
    if listening_note:report.setdefault('reviewStatus',{})['listening']={'note':listening_note,'humanListeningClaimed':False}
    if motion_note:report.setdefault('reviewStatus',{})['visualLipSync']={'note':motion_note,'inferredFromAudioCorrelation':False}
    write(job/'verification.json',report)
    final=job/'final.mp4'
    if not final.exists():
        try:os.link(video,final)
        except OSError:copy_asset(video,final)
    else:copy_asset(video,final)
    result={'video':str(final),'duration':report['duration'],'sha256':report['sha256']}
    if settings(job).get('delivery',{}).get('enabled'):
        from delivery import package
        result['delivery']=package(job)
    state(job,phase='complete',video=str(final),sha256=report['sha256'],next='Deliver the publish-ready ZIP' if result.get('delivery') else 'Deliver final.mp4 to the user')
    return result
