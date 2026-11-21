import os
from pathlib import Path
import hashlib,json,math,re
from workflow import *
from alignment import character_map,safe_boundary
from caption_layout import Typography,auto_caption_lines

CARE_TERMS=('老年公寓','书画室','阅读室','医务室','生活照护','失智照护','康复护理','健康监测','就医通道','忆路同行')

def find_font(cfg):
    override=cfg['typography'].get('body_font')
    if override:
        if not Path(override).is_file():raise ValueError('Configured body font missing')
        return Path(override)
    return SKILL/'assets/LXGWWenKai-Regular.ttf'
def caption_parts(s, typography, protected, characters, cursor):
    if s.get('captions'):return s['captions']
    result=[]
    parts=re.split(r'(（[^（）]*）|\([^()]*\))',s['text'])
    clauses=['']
    for i,part in enumerate(parts):
        if i%2:
            clauses[-1]+=part
        else:
            first,*rest=re.split(r'[，,；;：:。！？!?]',part)
            clauses[-1]+=first
            clauses.extend(rest)
    for clause in clauses:
        clause=clause.strip();count=len(clean(clause))
        if not count:continue
        for lines in auto_caption_lines(clause,typography,protected,clean,characters,cursor):
            result.append({'text':''.join(lines),'lines':lines,'focus':''})
        cursor+=count
    return result


def fit_shot_windows(shots, files, count, fps):
    """Allocate measured narration frames within the reviewed source footage."""
    if count<len(shots):raise ValueError('Too many shots for this sentence')
    weights=[shot.get('weight',1) for shot in shots]
    total=sum(weights)
    starts=[];limits=[];lengths=[];previous=0
    for i,shot in enumerate(shots):
        end=round(count*sum(weights[:i+1])/total) if i+1<len(shots) else count
        end=min(count-(len(shots)-i-1),max(previous+1,end))
        row=files[shot['path']];start=shot.get('sourceStart',0);cap=count
        if row['kind']=='video':
            if (row['duration']+.001)*fps<1:raise ValueError('Source video has no complete frame: '+shot['path'])
            start=min(start,max(0,row['duration']+.001-1/fps))
            cap=math.floor((row['duration']-start+.001)*fps+1e-9)
        starts.append(start);limits.append(cap);lengths.append(min(end-previous,cap));previous=end
    remaining=count-sum(lengths)
    # Keep the chosen start points whenever other shots can fill the gap.
    for i in sorted(range(len(shots)),key=lambda i:limits[i]-lengths[i],reverse=True):
        extra=min(remaining,limits[i]-lengths[i]);lengths[i]+=extra;remaining-=extra
    for i,shot in enumerate(shots):
        if not remaining:break
        row=files[shot['path']]
        if row['kind']!='video':continue
        extra=min(remaining,math.floor((row['duration']+.001)*fps+1e-9)-lengths[i])
        if extra:
            lengths[i]+=extra;remaining-=extra
            starts[i]=min(starts[i],max(0,row['duration']-lengths[i]/fps))
    fitted=list(zip(shots,starts,lengths))
    if remaining:
        # Replay a reviewed moving shot from this sentence if its footage is exhausted.
        index=max(range(len(shots)),key=lengths.__getitem__)
        if lengths[index]<fps:raise ValueError('分镜缺少足够的实拍时长')
        while remaining:
            extra=min(remaining,lengths[index]);remaining-=extra
            fitted.append((shots[index],0,extra))
    return fitted


def build(job):
    plan,files=validate_plan(job);setup(job);cfg=settings(job)
    v=read(job/'voice.json');source=Path(v['audio']);text=(job/'narration.txt').read_text(encoding='utf-8').strip()
    if sha(source)!=v['sha256'] or digest(text)!=v['textSha256']:raise RuntimeError('Stale voice; regenerate/realign before building')
    aligned=read(v['alignment'])
    if not aligned['passed'] or aligned['sha256']!=sha(source) or aligned['textSha256']!=digest(text):raise RuntimeError('Stale or failed word alignment')
    chars=character_map(aligned,text,clean,phonemes)
    canonical=clean(text)
    if phonemes(''.join(c['c'] for c in chars))!=phonemes(canonical) or len(chars)!=len(canonical):raise RuntimeError('Cannot map source words one-to-one; resolve the alignment discrepancy first')
    offset=cfg['voice']['start'];fps=cfg['video']['fps'];duration=v['duration'];total=math.ceil((duration+offset)*fps)/fps
    ranges=[];cursor=0
    for s in plan['sentences']:
        n=len(clean(s['text']));ranges.append((cursor,cursor+n));cursor+=n
    assert cursor==len(chars)
    boundaries=[0]+[round(((chars[a-1]['end']+chars[a]['start'])/2+offset)*fps)/fps for a,b in ranges[1:]]+[total]
    voice_dir=job/'assets/voice';voice_dir.mkdir(exist_ok=True)
    raw=copy_asset(source,voice_dir/'narration.mp3')
    levels=run(['ffmpeg','-hide_banner','-i',raw,'-af','aformat=channel_layouts=stereo,loudnorm=I=-17:TP=-1.5:LRA=11:print_format=json','-f','null','-']).stderr
    levels=json.loads(levels[levels.rfind('{'):levels.rfind('}')+1]);gain=cfg['voice']['target_lufs']-float(levels['input_i'])
    run(['ffmpeg','-y','-v','error','-i',raw,'-af',f'volume={gain:.4f}dB','-ar','48000','-ac','2','-c:a','pcm_s16le',voice_dir/'narration-master.wav'])
    import project_config as pc
    font=find_font(cfg)
    cap=pc.merge(read(SKILL/'assets/templates/fullscreen-orange.json'),cfg['template'].get('overrides',{}))['caption']
    typography=Typography(font,cfg['typography']['collection_index'],cap)
    inst=plan['institution']
    protected={*CARE_TERMS,*(part for field in ('displayName','name') for part in re.split(r'[·•（）()\s]+',inst[field]) if part)}
    cues=[];cursor=0
    for s in plan['sentences']:
        for c in caption_parts(s,typography,protected,chars,cursor):
            n=len(clean(c['text']))
            if clean(c['text'])!=canonical[cursor:cursor+n]:raise ValueError('Caption draft dropped/changed words; edit the plan')
            if not safe_boundary(chars,cursor) or not safe_boundary(chars,cursor+n):raise ValueError('字幕不能从数字内部切开；合并该数字所在字幕后重新 build，复用原配音。')
            cues.append({**c,'focus':c.get('focus',''),'start':round(chars[cursor]['start']+offset,4),'speechEnd':round(chars[cursor+n-1]['end']+offset,4),'canonicalRange':[cursor,cursor+n]});cursor+=n
    for i,c in enumerate(cues):
        next_start=cues[i+1]['start'] if i+1<len(cues) else total
        c['end']=round(min(c['speechEnd']+.2,next_start-.025),6) if i+1<len(cues) else total
        c['duration']=round(c['end']-c['start'],6)
        if c['duration']<=.2:raise ValueError('Caption has too little reading time; merge its wording with a neighbour')
    write(job/'captions.json',cues)
    write(job/'caption-source.json',cues)
    write(job/'caption-characters.json',{'sourceAudioSha256':v['sha256'],'characters':[{**c,'start':c['start']+offset,'end':c['end']+offset} for c in chars]})
    def stamp(t):
        ms=round(t*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000);return f'{h:02}:{m:02}:{s:02},{ms:03}'
    (job/'subtitles.srt').write_text('\n\n'.join(str(i+1)+'\n'+stamp(c['start'])+' --> '+stamp(c['end'])+'\n'+'\n'.join(c['lines']) for i,c in enumerate(cues))+'\n', encoding='utf-8')
    source_root=Path(read(job/'inventory.json')['source']);shots=[];ledger=[]
    for si,sentence in enumerate(plan['sentences']):
        selected=sentence['shots'];start_frame=round(boundaries[si]*fps);last_frame=round(boundaries[si+1]*fps);count=last_frame-start_frame
        running=start_frame
        for s,src_start,length in fit_shot_windows(selected,files,count,fps):
            st=running/fps;running+=length;end=running/fps
            row=files[s['path']];orig=(source_root/s['path']).resolve()
            if sha(orig)!=row['sha256']:raise RuntimeError('Input changed since inventory: '+s['path'])
            ext=orig.suffix.lower();dest=job/'assets/footage'/(row['sha256'][:12]+ext)
            if not dest.is_file() and os.environ.get('MONTAGE_WORKBENCH_DELIVERY'):
                tasks = project_root()/'.montage/workbench/tasks'
                if orig.is_relative_to(tasks) and source_root.is_relative_to(tasks):
                    from publish_assets import link_or_copy
                    link_or_copy(orig,dest)
                else:
                    from shared_cache import SharedCache
                    SharedCache(project_root()).share_immutable(orig,dest,job,namespace='assets')
            else:
                copy_asset(orig,dest)
            shot={'id':f'shot-{len(shots):02}','asset':str(dest.relative_to(job)),'start':st,'end':end,'duration':end-st,'sourceStart':src_start,'positionY':s.get('positionY',50),'description':s['description'],'sentenceIndex':si}
            shots.append(shot);ledger.append({'file':shot['asset'],'source':str(orig),'sha256':row['sha256'],'kind':row['kind'],'modified':False})
    write(job/'shot-list.json',shots);write(job/'asset-sources.json',ledger)
    from content_review import shot_audit
    shot_audit(job)
    (job/'source-description.txt').write_text(json.dumps(plan['institution'],ensure_ascii=False)+'\n'+(job/'source-text.txt').read_text(encoding='utf-8'), encoding='utf-8')
    inst=plan['institution'];reference=cfg['typography'].get('reference_identity')==inst and bool(cfg['typography'].get('reference_asset'))
    write(job/'institution.json',{**inst,'headerMode':'reference' if reference else 'dynamic'})
    match=[{'family':'Project body font' if cfg['typography'].get('body_font') else 'LXGW WenKai','file':str(font),'index':cfg['typography']['collection_index'],'stroke':1,'angle':0,'tracking':4}]
    write(job/'orange-font-match.json',match)
    # Fresh subsets must include the new institution and narration, not the previous project's glyph set.
    for f in ('OrangeRounded.woff2','OrangeLabel.woff2'):(job/'assets/fonts'/f).unlink(missing_ok=True)
    timeline={'duration':total,'voiceStart':offset,'voiceDuration':duration,'audio':'assets/voice/narration-master.wav','audioSha256':sha(voice_dir/'narration-master.wav'),'rawAudio':'assets/voice/narration.mp3','rawAudioSha256':sha(raw),'constantGainDB':gain,'speaker':cfg['voice']['speaker'],'speechRate':cfg['voice']['speech_rate'],'outroStart':total,'outroDuration':0,'holdLastCaption':True,'chapters':[[boundaries[i],boundaries[i+1]-boundaries[i],s['label']] for i,s in enumerate(plan['sentences'])]}
    write(job/'natural-timeline.json',timeline)
    if cfg['music'].get('enabled') and cfg['music'].get('resource'):
        music_path=cfg['music']['resource'];music_path=Path(music_path) if Path(music_path).is_absolute() else project_root()/music_path
        if not music_path.exists():raise RuntimeError('Configured music resource missing; select a local licensed track in settings.music.resource')
        music_duration=float(probe(music_path)['format']['duration'])
        loop=[]
        if music_duration<total:
            if music_duration<=0 or not os.environ.get('MONTAGE_WORKBENCH_DELIVERY'):
                raise RuntimeError('Music is shorter than narration; choose a longer local track')
            loop=['-stream_loop','-1']
        (job/'assets/music').mkdir(exist_ok=True)
        run(['ffmpeg','-y','-v','error',*loop,'-i',music_path,'-t',total,'-af',f"volume={cfg['music']['static_gain_db']}dB",'-ar','48000','-ac','2','-c:a','pcm_s16le',job/'assets/music/bed.wav'])
        bgm={'enabled':True,'audio':'assets/music/bed.wav','duration':total,'data-fx-chain':{'version':1,'nodes':[{'type':'gain','id':'outro-lift','params':{'gain':0}}]},'data-fx-carve':{'enabled':True,'sources':['female-narration'],'strength':cfg['music']['carve_strength']},'data-automation':{'version':1,'lanes':[{'target':'volume','points':[{'t':0,'v':0},{'t':cfg['music']['fade_in'],'v':1},{'t':max(cfg['music']['fade_in'],total-cfg['music']['fade_out']),'v':1},{'t':total,'v':0}]},{'target':'fx.outro-lift.gain','points':[{'t':0,'v':cfg['music']['speech_gain_db']},{'t':total,'v':cfg['music']['speech_gain_db']}]}]}}
    else:bgm={'enabled':False}
    write(job/'bgm-timeline.json',bgm)
    mode=cfg['avatar']['mode'];indices=[]
    if mode not in ('off','opening','closing','both'):raise ValueError('avatar.mode must be off/opening/closing/both')
    if mode in ('opening','both'):indices.append(0)
    if mode in ('closing','both') and len(ranges)-1 not in indices:indices.append(len(ranges)-1)
    avatars=[]
    for si in indices:
        first,last=ranges[si];prev_end=chars[first-1]['end'] if first else 0
        start=0 if si==0 else max(prev_end+.02,chars[first]['start']-cfg['avatar']['sentence_padding'])
        end=duration if si==len(ranges)-1 else min(math.ceil((chars[last-1]['end']+.16)*fps)/fps,chars[last]['start']-.02)
        start=math.floor(start*fps)/fps;ident='opening' if si==0 else 'closing';out=job/'assets'/('avatar-'+ident);out.mkdir(exist_ok=True)
        run(['ffmpeg','-y','-v','error','-ss',start,'-i',raw,'-t',end-start,'-vn','-ar','24000','-ac','1','-c:a','pcm_s16le',out/'driver.wav'])
        is_last=si==len(ranges)-1;display_end=total if is_last else offset+end
        avatars.append({'id':ident,'video':str((out/'avatar-outlined.webm').relative_to(job)),'audio':str((out/'driver.wav').relative_to(job)),'start':offset+start,'duration':display_end-offset-start,'x':cfg['avatar']['x'],'y':cfg['avatar']['bottom']-cfg['avatar']['height'],'width':cfg['avatar']['width'],'height':cfg['avatar']['height'],'fadeIn':cfg['avatar']['fade_in'],'fadeOut':0 if is_last else min(cfg['avatar']['fade_out'],max(.04,end-chars[last-1]['end'])),'text':plan['sentences'][si]['text'],'sourceNarrationStart':start})
    write(job/'avatars.json',avatars)
    write(job/'build-inputs.json',{'planSha256':sha(job/'plan.json'),'voiceSha256':v['sha256'],'sourceHashes':{p:r['sha256'] for p,r in files.items()},'noPostTtsTimeStretch':True})
    state(job,phase='timed',next='avatars',duration=total)
    compose(job)
    return {'duration':total,'shots':len(shots),'captions':len(read(job/'captions.json')),'avatarWindows':[[a['start'],a['start']+a['duration']] for a in avatars]}

def compose(job):
    settings(job)
    from compose_fullscreen import compose as compose_fullscreen
    compose_fullscreen(job)
    from cover import attach
    attach(job)
