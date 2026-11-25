"""Persistent user data lives outside the replaceable skill installation."""
import copy,hashlib,json,os,re,shutil,subprocess,sys,uuid
from pathlib import Path
from file_transfer import storage_settings

SKILL=Path(__file__).resolve().parent.parent
SCHEMA=1
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value,private=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    # Open private files with restrictive permissions from the first byte.
    tmp=path.with_name(path.name+'.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600 if private else 0o644)
    with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
    if private:os.chmod(tmp,0o600)
    tmp.replace(path)
def root(start=None):
    if os.environ.get('MONTAGE_PROJECT_ROOT'):return Path(os.environ['MONTAGE_PROJECT_ROOT']).resolve()
    current=Path(start or Path.cwd()).resolve()
    for p in (current,*current.parents):
        if (p/'.montage/project.json').is_file():return p
    for p in SKILL.parents:
        if p.name=='.agents':return p.parent
    return current
def directory():return root()/'.montage'
def init():
    d=directory();p=d/'project.json'
    if not p.exists():write(p,{'schema_version':SCHEMA,'project_id':uuid.uuid4().hex,'default_person':None,'connections':{},'settings':{},'people':{},'output_dir':'作品'})
    config=load()
    (d/'.gitignore').write_text('*\n!.gitignore\n', encoding='utf-8')
    return {'project':str(root()),'config':str(p),'initialized':True,'people':len(config['people'])}
def load():
    p=directory()/'project.json'
    if not p.exists():raise ValueError('项目尚未初始化：先运行 project.py init，再只补齐 doctor 列出的缺项。')
    cfg=read(p)
    if cfg.get('schema_version',0)>SCHEMA:raise ValueError('此项目配置来自更新版本，请先更新skill；不会重置配置。')
    if cfg.get('schema_version')!=SCHEMA:
        raise ValueError('未知配置格式，保留原文件；请提供版本迁移器，不能用默认值覆盖。')
    return cfg
def merge(base,override):
    out=copy.deepcopy(base)
    for k,v in override.items():out[k]=merge(out[k],v) if isinstance(v,dict) and isinstance(out.get(k),dict) else copy.deepcopy(v)
    return out
def save_patch(patch):
    if set(patch)-{'settings','connections','default_person','output_dir'}:raise ValueError('只能修改项目设置、连接或默认人物；人物使用 people 命令。')
    cfg=merge(load(),patch);write(directory()/'project.json',cfg);return {'saved':True}
def asset_path(value):
    if not value:return None
    if str(value).startswith('skill:'):
        p=(SKILL/str(value)[len('skill:'):]).resolve()
        if not p.is_relative_to(SKILL.resolve()):raise ValueError('skill: 资源必须位于 skill 目录内。')
        return p
    p=Path(value).expanduser();return p if p.is_absolute() else directory()/p
def secret(ref):
    if not ref:return ''
    if isinstance(ref,str):return os.environ.get(ref,'')
    value=os.environ.get(ref.get('env',''),'')
    if value:return value
    if ref.get('local'):
        p=directory()/'secrets.json'
        return read(p).get(ref['local'],'') if p.exists() else ''
    if ref.get('keychain') and sys.platform=='darwin':
        result=subprocess.run(['/usr/bin/security','find-generic-password','-a','VOLCANO_TTS_API_KEY','-s','com.openai.codex.volcano-tts','-w'],capture_output=True,text=True)
        if result.returncode==0:return result.stdout.strip()
    return ''
def connection_keys(connection):
    api=secret(connection.get('speech_key'));ak=secret(connection.get('access_key'));sk=secret(connection.get('secret_key'))
    legacy=connection.get('access_keys',{}).get('file')
    if not (ak and sk) and legacy:
        p=asset_path(legacy)
        if p.is_file():
            values={}
            for line in p.read_text(encoding='utf-8-sig').splitlines():
                m=re.match(r'^\s*[-*]?\s*(AccessKeyID|SecretAccessKey)\s*[:：=]\s*(.*?)\s*$',line,re.I)
                if m:values[m[1].lower()]=m[2].strip().strip('`"\'')
            ak=values.get('accesskeyid','');sk=values.get('secretaccesskey','')
    return api,ak,sk
def store_connection(name,values):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',name):raise ValueError('连接名称使用字母、数字、横线或下划线。')
    cfg=load();conn=cfg['connections'].get(name,{'provider':'volcengine'})
    secrets=read(directory()/'secrets.json') if (directory()/'secrets.json').exists() else {}
    for key,field in (('api_key','speech_key'),('ak','access_key'),('sk','secret_key')):
        if values.get(key):
            slot=name+'.'+key;secrets[slot]=values[key].strip();conn[field]={'local':slot}
    storage=copy.deepcopy(conn.get('storage',{}))
    if values.get('storage_provider'):storage['provider']=values['storage_provider'].strip()
    for k in ('bucket','region','endpoint'):
        if values.get(k):storage[k]=values[k].strip()
    conn['storage']=storage_settings(storage)
    write(directory()/'secrets.json',secrets,private=True)
    cfg['connections'][name]=conn;write(directory()/'project.json',cfg)
    return {'saved':True,'connection':name}
def person(identifier=None,ready=False):
    cfg=load();identifier=identifier or cfg.get('default_person')
    matches=[p for p in cfg['people'].values() if identifier in (p['id'],p['name'])]
    if len(matches)!=1:raise ValueError('请指定唯一的数字人名称或ID；可先查看人物清单。')
    p=copy.deepcopy(matches[0])
    if ready and not (p.get('speaker') and asset_path(p.get('portrait')).is_file()):raise ValueError('数字人尚未就绪：'+p['name'])
    return p
def resolved(job):
    frozen=job/'runtime.json'
    if frozen.exists():return read(frozen)['settings']
    cfg=load();plan=read(job/'plan.json') if (job/'plan.json').exists() else {}
    out=merge(read(SKILL/'preset.json'),cfg.get('settings',{}))
    # Old project defaults migrate for new jobs; frozen runtimes return above unchanged.
    if out.get('template',{}).get('id') in ('legacy-orange','orange','green'):out['template']={'id':'fullscreen-orange','overrides':{}}
    identifier=plan.get('person') or cfg.get('default_person')
    p=person(identifier) if identifier else None
    if p:
        out['voice']['speaker']=p.get('speaker');out['voice']['speech_rate']=p.get('speech_rate',out['voice']['speech_rate'])
        out['voice']['custom_speaker_id']=p.get('custom_speaker_id')
        if p.get('voice_provider')=='minimax':
            out['voice']['provider']='minimax'
            out['voice']['minimax_model']=cfg.get('settings',{}).get('minimax_model','speech-2.8-hd')
        if p.get('voice_provider')=='aliyun':
            from aliyun_voice import model_id, workspace_id
            out['voice'].update(provider='aliyun', aliyun_model=model_id(p.get('aliyun_model')),
                                aliyun_workspace=workspace_id(p.get('aliyun_workspace') or
                                    cfg['connections'].get('aliyun',{}).get('workspace')))
        out['avatar']['portrait']=str(asset_path(p.get('portrait')))
        if p.get('music_resource'):
            out.setdefault('music',{})['resource']=p['music_resource']
        if p.get('cover_template_id'):
            out.setdefault('cover',{})['template']=p['cover_template_id']
        if p.get('video_style_id'):
            out.setdefault('template',{})['id']=p['video_style_id']
        out['person']={'id':p['id'],'name':p['name'],'spokenName':p.get('spoken_name',p['name']),'connection':p['connection'],
            'socialMediaName':p.get('social_media_name'),'coverPortrait':str(asset_path(p['cover_portrait'])) if p.get('cover_portrait') else None,
            'coverTemplateId':p.get('cover_template_id'),'videoStyleId':p.get('video_style_id'),
            'closingHooks':copy.deepcopy(p.get('closing_hooks',{}))}
    out=merge(out,plan.get('settings',{}))
    for role,font in out.get('template',{}).get('overrides',{}).get('fonts',{}).items():
        if font.get('file'):font['file']=str(asset_path(font['file']))
    conn=cfg['connections'].get(out.get('person',{}).get('connection'),{})
    out['service']={k:copy.deepcopy(conn[k]) for k in ('provider','storage','tts','avatar') if k in conn}
    out['service']['storage']=storage_settings(conn.get('storage'))
    for section,key in (('avatar','portrait'),('music','resource'),('typography','body_font'),('typography','reference_asset'),('cover','image'),('cover','font')):
        if section not in out:continue
        if out[section].get(key):out[section][key]=str(asset_path(out[section][key]))
    return out
def runtime_files(base=SKILL, include_workbench=True):
    """Only production resources belong in a resumable task engine."""
    roots=['SKILL.md','README.md','preset.json','VERSION','release.json','LICENSE','THIRD_PARTY_NOTICES.md','agents','assets','modules','scripts','references']
    result=[]
    for name in roots:
        source=base/name
        for p in ([source] if source.is_file() else source.rglob('*') if source.is_dir() else []):
            rel=p.relative_to(base)
            if not p.is_file() or any(x in ('.git','__pycache__','.DS_Store') for x in rel.parts) or p.suffix=='.pyc':continue
            if not include_workbench and (rel.parts[:2]==('scripts','workbench') or rel.as_posix()=='scripts/workbench.py'):continue
            if rel.parts[0]=='scripts' and (p.name.startswith('test_') or p.name in ('install.py','regression.py')):continue
            result.append(p)
    return sorted(result)

def freeze(job):
    job=Path(job);bound=job/'runtime.json'
    if bound.exists():return read(bound)
    cfg=resolved(job);person_asset=cfg['avatar'].get('portrait')
    workbench = bool(os.environ.get('MONTAGE_WORKBENCH_DELIVERY'))
    if workbench:
        from shared_cache import SharedCache
        cache = SharedCache(root())
        def freeze_asset(source, target):
            cache.share_immutable(source,target,job,namespace='assets')
    else:
        freeze_asset = shutil.copy2
    if cfg.get('music',{}).get('enabled'):
        resource=cfg['music'].get('resource')
        if not resource or not Path(resource).is_file():raise ValueError('默认或指定配乐不存在，请恢复 skill 音乐资源或明确关闭配乐。')
        source=Path(resource);dest=job/'assets/music'/('source'+source.suffix.lower())
        dest.parent.mkdir(parents=True,exist_ok=True)
        if source.resolve()!=dest.resolve():freeze_asset(source,dest)
        cfg['music']['resource']=str(dest.resolve())
    if person_asset:
        dest=job/'assets/person'/Path(person_asset).name;dest.parent.mkdir(parents=True,exist_ok=True)
        if Path(person_asset).resolve()!=dest.resolve():freeze_asset(person_asset,dest)
        cfg['avatar']['portrait']=str(dest.resolve())
    cover_asset=cfg.get('person',{}).get('coverPortrait')
    if cover_asset:
        dest=job/'assets/person/cover-portrait.png';dest.parent.mkdir(parents=True,exist_ok=True)
        if Path(cover_asset).resolve()!=dest.resolve():freeze_asset(cover_asset,dest)
        cfg['person']['coverPortrait']=str(dest.resolve())
    if cfg.get('cover',{}).get('image'):
        source=Path(cfg['cover']['image']);dest=job/'assets/cover-input'/source.name;dest.parent.mkdir(parents=True,exist_ok=True)
        if source.resolve()!=dest.resolve():freeze_asset(source,dest)
        cfg['cover']['image']=str(dest.resolve())
    font_refs=[(cfg.get('typography',{}),'body_font','caption'),(cfg.get('cover',{}),'font','cover')]
    font_refs.extend((value,'file','template-'+role) for role,value in cfg.get('template',{}).get('overrides',{}).get('fonts',{}).items())
    for container,key,role in font_refs:
        if not container.get(key):continue
        source=Path(container[key]);dest=job/'assets/fonts'/('source-'+role+source.suffix)
        if not source.is_file():raise ValueError('配置字体缺失：'+str(source))
        dest.parent.mkdir(parents=True,exist_ok=True)
        if source.resolve()!=dest.resolve():
            from shared_cache import SharedCache
            SharedCache(root()).share_immutable(source,dest,owner=job)
        container[key]=str(dest.resolve())
    plan=read(job/'plan.json') if (job/'plan.json').exists() else {}
    if plan.get('closingHookSelection'):cfg['closingHookSelection']=copy.deepcopy(plan['closingHookSelection'])
    import style_registry as sr
    for sec, key, getter, kind in (('cover','template',sr.get_cover,'covers'),('template','id',sr.get_video,'videos')):
        sid=cfg.get(sec,{}).get(key)
        if sid:
            entry=getter(sid)
            if not entry.get('builtin'):
                src=sr.style_source_file(entry)
                if src and src.is_file():
                    dest=job/'assets/styles'/kind/src.name;dest.parent.mkdir(parents=True,exist_ok=True)
                    if src.resolve()!=dest.resolve():shutil.copy2(src,dest)
                cfg.setdefault(sec,{})['_frozenEntry']=sr.public_entry(entry)
    files=runtime_files(include_workbench=False)
    manifest={str(p.relative_to(SKILL)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    ident=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:20]
    engine=directory()/'engines'/ident
    if not engine.exists():
        staging=engine.with_name(ident+'.tmp')
        if staging.exists():shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for p in files:
            relative = p.relative_to(SKILL)
            dest=staging/relative
            if workbench and relative.parts[0]=='assets':
                cache.share_immutable(p,dest,engine,namespace='assets')
            else:
                dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(p,dest)
        staging.replace(engine)
    # A pluggable engine implements generate/resume/review and the bundled run.json contract.
    connection=load()['connections'].get(cfg.get('person',{}).get('connection'),{})
    adapters={}
    for kind,path in connection.get('adapters',{}).items():
        if kind not in ('speech','avatar'):raise ValueError('未知服务适配器类型')
        source=asset_path(path);target=job/'assets/adapters'/kind
        if not (source/'generate.py').is_file():raise ValueError('适配器缺少generate.py')
        shutil.copytree(source,target);adapters[kind]=str((target/'generate.py').resolve())
    data={'schema_version':1,'version':(SKILL/'VERSION').read_text(encoding='utf-8').strip(),'engine':str(engine),'project':str(root()),'settings':cfg,'adapters':adapters,'manifest':manifest}
    write(bound,data);return data
def engine_env(job):
    cfg=resolved(job);name=cfg.get('person',{}).get('connection');conn=load()['connections'].get(name,{})
    api,ak,sk=connection_keys(conn);env=os.environ.copy()
    if cfg.get('voice',{}).get('provider')=='minimax':
        env['MINIMAX_TTS_API_KEY']=secret(load()['connections'].get('minimax',{}).get('speech_key'))
    if cfg.get('voice',{}).get('provider')=='aliyun':
        env['DASHSCOPE_API_KEY']=connection_keys(load()['connections'].get('aliyun',{}))[0]
    # Explicit values prevent inherited keys from accidentally selecting another account.
    env.update(MONTAGE_PROJECT_ROOT=str(root()),MONTAGE_AVATAR_CONFIG=str(job/'avatar-engine.json'),VOLCANO_TTS_API_KEY=api,VOLC_ACCESSKEY=ak,VOLC_SECRETKEY=sk,MONTAGE_MANAGED_CREDENTIALS='1')
    return env
def render_env(job):
    env=os.environ.copy();mode=resolved(job).get('render',{}).get('browser_sandbox','inherit')
    if mode not in ('inherit','required','disabled'):raise ValueError('browser_sandbox 只接受 inherit/required/disabled。')
    if mode!='inherit':env['HYPERFRAMES_CHROME_SANDBOX']='1' if mode=='required' else '0'
    return env
def doctor():
    cfg=load();tools={k:bool(shutil.which(k)) for k in ('ffmpeg','ffprobe','node','npm','uv')};connections={}
    for name,c in cfg['connections'].items():
        api,ak,sk=connection_keys(c)
        try:
            s=storage_settings(c.get('storage'));storage_ok=s['provider']=='tmpfile' or all(s.get(k) for k in ('bucket','region','endpoint'))
        except ValueError:storage_ok=False
        connections[name]={'speech_key':bool(api),'access_keys':bool(ak and sk),'storage':storage_ok}
    people=[]
    for p in cfg['people'].values():
        people.append({'id':p['id'],'name':p['name'],'portrait':bool(p.get('portrait') and asset_path(p['portrait']).is_file()),'voice':bool(p.get('speaker')),'connection':p['connection'],'status':p.get('status'),
            'socialMediaName':bool(p.get('social_media_name')),'personalHooks':len(p.get('closing_hooks',{}).get('library',{}))})
    need_cover=merge(read(SKILL/'preset.json'),cfg.get('settings',{})).get('cover',{}).get('enabled',False)
    usable=[p for p in people if p['portrait'] and p['voice'] and (not need_cover or p['socialMediaName']) and p['connection'] in connections and all(connections[p['connection']].values())]
    return {'project':str(root()),'tools':tools,'connections':connections,'people':people,'defaultPerson':cfg.get('default_person'),'ready':all(tools.values()) and bool(usable),'remoteAuthVerified':False}
