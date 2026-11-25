"""Create new versions without changing a frozen source or reusing stale speech."""
import copy,shutil
from pathlib import Path
import project_config as pc
from workflow import read,write,setup,state,clean
from build_job import compose,build,find_font


def rebase(value,source,target):
    if isinstance(value,dict):return {k:rebase(v,source,target) for k,v in value.items()}
    if isinstance(value,list):return [rebase(v,source,target) for v in value]
    if isinstance(value,str) and (value==str(source) or value.startswith(str(source)+'/')):return str(target)+value[len(str(source)):]
    return value


def revise(source,out,plan_file=None,platform=None):
    source=Path(source).resolve();out=Path(out).resolve()
    if out.exists():raise ValueError('请使用新的输出目录；原工程保持不变。')
    if source==out or source in out.parents:raise ValueError('新版本必须在原工程目录以外。')
    original=read(source/'plan.json');plan=read(plan_file) if plan_file else copy.deepcopy(original)
    cfg=copy.deepcopy(pc.resolved(source));cfg['template']['id']='fullscreen-orange'
    if plan_file:cfg=pc.merge(cfg,plan.get('settings',{}))
    cfg['template']['id']='fullscreen-orange'
    bound=pc.resolved(source)
    if plan.get('person',original.get('person'))!=original.get('person') or cfg.get('person')!=bound.get('person') or any(cfg.get('avatar',{}).get(k)!=bound.get('avatar',{}).get(k) for k in ('portrait','prompt','sentence_padding')):
        raise ValueError('revise保留冻结的人物与生成绑定；更换人物、生成提示或驱动边界请新建任务，不能继承旧口型记录。')
    if platform:
        cfg['platform_ui']['mode']='common' if platform=='common' else 'platform';cfg['platform_ui']['platform']=platform
    old_text='\n'.join(s['text'] for s in original['sentences']);new_text='\n'.join(s['text'] for s in plan['sentences'])
    same=old_text==new_text and all(cfg.get(key)==pc.resolved(source).get(key) for key in ('voice','person'))
    out.mkdir(parents=True)
    if (source/'cover.png').is_file():shutil.copy2(source/'cover.png',out/'cover.png')
    for name in ('inventory.json','source-text.txt'):
        if (source/name).is_file():shutil.copy2(source/name,out/name)
    if same:
        required=('natural-timeline.json','shot-list.json','captions.json','avatars.json')
        if any(not (source/name).is_file() for name in required):same=False
    if same:
        for name in ('voice.json','natural-timeline.json','shot-list.json','captions.json','caption-characters.json','caption-source.json','institution.json','bgm-timeline.json','avatars.json','orange-font-match.json','source-description.txt','narration.txt','build-inputs.json','asset-sources.json'):
            p=source/name
            if not p.is_file():continue
            if p.suffix=='.json':write(out/name,rebase(read(p),source,out))
            else:shutil.copy2(p,out/name)
        for folder in ('assets','runs'):
            if (source/folder).is_dir():shutil.copytree(source/folder,out/folder,copy_function=shutil.copyfile)
        # Rebase run records only; never alter immutable external provider records or source media.
        for p in list((out/'assets').rglob('*.json'))+list((out/'runs').rglob('*.json')):
            write(p,rebase(read(p),source,out))
    else:
        for folder in ('assets/person','assets/fonts','assets/cover-input','assets/music'):
            if (source/folder).is_dir():shutil.copytree(source/folder,out/folder,copy_function=shutil.copyfile)
    cfg=rebase(cfg,source,out);plan['settings']=cfg;write(out/'plan.json',plan);setup(out)
    write(out/'revision-source.json',{'source':str(source),'narrationUnchanged':old_text==new_text,'reuseTimedAssets':same,'paidGenerationCalls':0})
    if same:
        bound=pc.resolved(out);font=find_font(bound)
        write(out/'orange-font-match.json',[{'family':'Project body font','file':str(font),'index':bound['typography'].get('collection_index'),'stroke':1,'angle':0,'tracking':4}])
        if plan_file:build(out)
        else:compose(out)
        state(out,phase='composed',next='preflight, content review, cover/carve/check/snapshot/render/verify/review')
    else:state(out,phase='draft',next='validate-plan, preflight, review-content, voice and build; old speech/avatar records were not copied')
    return {'job':str(out),'reuseTimedAssets':same,'paidGenerationCalls':0}


def variant(source,out,template='fullscreen-orange',platform=None):
    if template!='fullscreen-orange':raise ValueError('新版只提供 fullscreen-orange。')
    return revise(source,out,platform=platform)
