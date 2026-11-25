import os
from pathlib import Path
import hashlib,json,math,re
from workflow import *
from alignment import character_map,safe_boundary
from caption_layout import Typography,auto_caption_lines

CARE_TERMS=(
    '老年公寓','书画室','阅读室','棋牌室','医务室','生活照护','失智照护','康复护理',
    '健康监测','就医通道','绿色通道','三甲医院','忆路同行','乐享365','自理生活区',
    '生活照护区','失智照护楼层','低盐低脂','营养均衡','24小时','实地','了解','家属',
    '长辈','养老院','参观','预约','留言','评论区','餐食','定制','专属','服务','照护',
    '设施','环境','空间','团队','安排','活动','休闲','广场','高龄','失能','失智','分区','方案',
)
FOCUS_TERMS=(
    '失智照护楼层','自理生活区','生活照护区','绿色就医通道','忆路同行','乐享365',
    '失智照护','生活照护','低盐低脂','营养均衡','三甲医院','绿色通道','医务室',
    '24小时','9号线','10号线','棋牌室','书画室','阅读室','多功能活动室',
    '户外休闲广场','空中花园','日间照料','短期托养','康养旅居','定制膳食',
    '专属管家','健康管理','预约参观','评论区','留言',
)

def _pick_focus(lines, typography, institution_terms=()):
    joined=''.join(lines)
    candidates=[*FOCUS_TERMS,*institution_terms]
    for term in sorted(dict.fromkeys(t for t in candidates if t and len(t)>=2),key=len,reverse=True):
        if term in joined:
            for row,line in enumerate(lines):
                if term in line and typography.fits(line,term,row,style_row=0):
                    return term
    return ''

def _opening_avatar_window(text, first, last, chars, duration, fps, max_seconds=10.0):
    """End opening digital human after the 1st complete sentence, capped at max_seconds."""
    text = strip_parenthetical(text)
    cut = last
    avatar_text = text
    sent = re.search(r'[。！？!?]', text)
    if sent:
        n = len(clean(text[:sent.end()]))
        if 0 < n < (last - first):
            cut = first + n
            avatar_text = text[:sent.end()].strip()
    def end_for(idx):
        if idx >= len(chars):
            return duration
        return min(math.ceil((chars[idx - 1]['end'] + .16) * fps) / fps, chars[idx]['start'] - .02)
    end = end_for(cut)
    if max_seconds and end > max_seconds:
        # Prefer the latest clause boundary within max_seconds (at least 3s).
        best_cut, best_end, best_text = None, None, None
        for m in re.finditer(r'[，,；;。！？!?]', text):
            n = len(clean(text[:m.end()]))
            if not 0 < n <= (last - first):
                continue
            cand_end = end_for(first + n)
            if 3.0 <= cand_end <= max_seconds:
                best_cut, best_end, best_text = first + n, cand_end, text[:m.end()].strip()
        if best_cut is not None:
            cut, end, avatar_text = best_cut, best_end, best_text
        else:
            end = math.floor(max_seconds * fps) / fps
    return cut, end, avatar_text

def find_font(cfg):
    override=cfg['typography'].get('body_font')
    if override:
        if not Path(override).is_file():raise ValueError('Configured body font missing')
        return Path(override)
    return SKILL/'assets/LXGWWenKai-Regular.ttf'
def caption_parts(s, typography, protected, characters, cursor, *, focus_terms=()):
    if s.get('captions'):return s['captions']
    result=[]
    display_text=strip_parenthetical(s['text'])
    clauses=re.split(r'[，,；;：:。！？!?]',display_text)
    merged=[]
    for clause in clauses:
        clause=clause.strip()
        if not len(clean(clause)):continue
        if merged and (len(clean(clause))<=4 or len(clean(merged[-1]))<=4):
            merged[-1]=merged[-1]+'，'+clause
        else:
            merged.append(clause)
    for clause in merged:
        count=len(clean(clause))
        for lines in auto_caption_lines(clause,typography,protected,clean,characters,cursor):
            result.append({'text':''.join(lines),'lines':lines,'focus':_pick_focus(lines,typography,focus_terms),'accentSecondLine':False})
        cursor+=count
    return result


def fit_shot_windows(shots, files, count, fps, *, fallback_pool=None):
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
    fitted=[[shot,start,length] for shot,start,length in zip(shots,starts,lengths)]
    if remaining and fallback_pool:
        # Fill shortfall from distinct alternate footage before ever replaying a clip.
        for alt in fallback_pool:
            if not remaining:break
            if fitted and alt['path']==fitted[-1][0]['path']:continue
            row=files[alt['path']]
            cap=remaining if row['kind']!='video' else math.floor((row['duration']+.001)*fps+1e-9)
            if cap<fps and cap<remaining:continue
            extra=min(remaining,cap)
            if extra>0:
                fitted.append([alt,0,extra]);remaining-=extra
    if remaining:
        # Replay a reviewed moving shot from this sentence only when no alternate footage exists.
        index=max(range(len(shots)),key=lengths.__getitem__)
        if lengths[index]<fps:raise ValueError('分镜缺少足够的实拍时长')
        while remaining:
            extra=min(remaining,lengths[index]);remaining-=extra
            fitted.append([shots[index],0,extra])
    # Prevent jarring sub-second flash cuts on appended fallback/replay tails.
    if len(fitted)>len(shots) and fitted[-1][2]<fps:
        need=fps-fitted[-1][2]
        donor=max(range(len(fitted)-1),key=lambda idx:fitted[idx][2])
        can_give=max(0,fitted[donor][2]-fps)
        transfer=min(need,can_give)
        if transfer>0:
            fitted[donor][2]-=transfer
            fitted[-1][2]+=transfer
            row=files[fitted[-1][0]['path']]
            if row['kind']=='video':
                max_len=math.floor((row['duration']+.001)*fps+1e-9)
                if fitted[-1][2]>max_len:
                    excess=fitted[-1][2]-max_len
                    fitted[-1][2]=max_len
                    fitted[donor][2]+=excess
    return [(s,st,ln) for s,st,ln in fitted]


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
    run(['ffmpeg','-y','-v','error','-i',raw,'-af',f'volume={gain:.4f}dB,alimiter=limit=0.7943:attack=3:release=40:level=false','-ar','48000','-ac','2','-c:a','pcm_s16le',voice_dir/'narration-master.wav'])
    import project_config as pc
    import style_registry as sr
    template_id=cfg.get('template',{}).get('id',sr.DEFAULT_VIDEO_ID)
    if template_id==sr.DEFAULT_VIDEO_ID:
        font=find_font(cfg)
        cap=pc.merge(read(SKILL/'assets/templates/fullscreen-orange.json'),cfg['template'].get('overrides',{}))['caption']
        typography=Typography(font,cfg['typography']['collection_index'],cap)
        avatar_x=cfg['avatar']['x']
    else:
        video_entry=sr.get_video(template_id)
        font=SKILL/'assets/NotoSansSC-Black.otf'
        cap=sr.style_caption_spec(video_entry)
        typography=Typography(font,None,cap)
        avatar_side=(video_entry.get('videoSpec') or {}).get('avatarSide','left')
        avatar_x=(1080-cfg['avatar']['width']-cfg['avatar']['x']) if avatar_side=='right' else cfg['avatar']['x']
    inst=plan['institution']
    inst_parts=tuple(part for field in ('displayName','name') for part in re.split(r'[·•（）()\s]+',inst[field]) if part)
    protected={*CARE_TERMS,*inst_parts}
    brand_focus=tuple(p for p in inst_parts if len(p)>=2 and p not in ('老年公寓','养老院','养老机构'))
    cues=[];cursor=0
    for s in plan['sentences']:
        for c in caption_parts(s,typography,protected,chars,cursor,focus_terms=brand_focus):
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
    used_paths={}
    for s in plan['sentences']:
        for sh in s['shots']:
            used_paths[sh['path']]=used_paths.get(sh['path'],0)+1
    visual_files=[r for r in files.values() if r['kind'] in ('video','image')]
    for si,sentence in enumerate(plan['sentences']):
        selected=sentence['shots'];start_frame=round(boundaries[si]*fps);last_frame=round(boundaries[si+1]*fps);count=last_frame-start_frame
        running=start_frame
        moving_only=(si==0 or sentence.get('kind')=='cta')
        next_first=(plan['sentences'][si+1]['shots'][0]['path']
                    if si+1<len(plan['sentences']) and plan['sentences'][si+1].get('shots') else None)
        avoid={sh['path'] for sh in selected}|({next_first} if next_first else set())
        pool_rows=[r for r in visual_files if (not moving_only or r['kind']=='video') and r['path'] not in avoid]
        pool_rows.sort(key=lambda r:(used_paths.get(r['path'],0),0 if r['kind']=='video' else 1,-float(r.get('duration',0))))
        fallback_pool=[{'path':r['path'],'sourceStart':0,'weight':1,'description':'同机构实拍补镜'} for r in pool_rows]
        for s,src_start,length in fit_shot_windows(selected,files,count,fps,fallback_pool=fallback_pool):
            used_paths[s['path']]=used_paths.get(s['path'],0)+1
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
    chapters=[]
    for i,s in enumerate(plan['sentences']):
        st,du,lb=boundaries[i],boundaries[i+1]-boundaries[i],s['label']
        if chapters and chapters[-1][2]==lb:chapters[-1][1]+=du
        else:chapters.append([st,du,lb])
    timeline={'duration':total,'voiceStart':offset,'voiceDuration':duration,'audio':'assets/voice/narration-master.wav','audioSha256':sha(voice_dir/'narration-master.wav'),'rawAudio':'assets/voice/narration.mp3','rawAudioSha256':sha(raw),'constantGainDB':gain,'speaker':cfg['voice']['speaker'],'speechRate':cfg['voice']['speech_rate'],'outroStart':total,'outroDuration':0,'holdLastCaption':True,'chapters':chapters}
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
    max_opening=cfg['avatar'].get('max_opening_seconds',10.0)
    for si in indices:
        first,last=ranges[si];prev_end=chars[first-1]['end'] if first else 0
        start=0 if si==0 else max(prev_end+.02,chars[first]['start']-cfg['avatar']['sentence_padding'])
        if si==0:
            cut,end,avatar_text=_opening_avatar_window(plan['sentences'][si]['text'],first,last,chars,duration,fps,max_opening)
            last_char_end=chars[min(cut,len(chars))-1]['end']
        else:
            end=duration if si==len(ranges)-1 else min(math.ceil((chars[last-1]['end']+.16)*fps)/fps,chars[last]['start']-.02)
            avatar_text=plan['sentences'][si]['text']
            last_char_end=chars[last-1]['end']
        start=math.floor(start*fps)/fps;ident='opening' if si==0 else 'closing';out=job/'assets'/('avatar-'+ident);out.mkdir(exist_ok=True)
        run(['ffmpeg','-y','-v','error','-ss',start,'-i',raw,'-t',end-start,'-vn','-ar','24000','-ac','1','-c:a','pcm_s16le',out/'driver.wav'])
        is_last=si==len(ranges)-1 and si!=0;display_end=total if is_last else offset+end
        avatars.append({'id':ident,'video':str((out/'avatar-outlined.webm').relative_to(job)),'audio':str((out/'driver.wav').relative_to(job)),'start':offset+start,'duration':display_end-offset-start,'x':avatar_x,'y':cfg['avatar']['bottom']-cfg['avatar']['height'],'width':cfg['avatar']['width'],'height':cfg['avatar']['height'],'fadeIn':cfg['avatar']['fade_in'],'fadeOut':0 if is_last else min(cfg['avatar']['fade_out'],max(.04,end-last_char_end)),'text':avatar_text,'sourceNarrationStart':start})
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
