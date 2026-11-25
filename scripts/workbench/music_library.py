"""Local music catalog and immutable selections for workbench requests."""
from pathlib import Path
import hashlib
import json
import math
import shutil
import time
import uuid
import re

AUDIO = {'.mp3','.wav','.m4a','.aac','.flac','.ogg','.opus'}
MAX_BYTES = 256 * 1024 * 1024


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            value.update(block)
    return value.hexdigest()


class MusicLibrary:
    def __init__(self,app,read,write,run):
        self.app,self.read,self.write,self.run=app,read,write,run
        self.root=app.data/'music'
        self.index=self.root/'library.json'
        if not self.index.is_file():
            with app.transaction():
                if not self.index.is_file():
                    cfg=app.pc.load().get('settings',{}).get('music',{})
                    preset=read(app.skill/'preset.json',{}).get('music',{})
                    path=app.pc.asset_path(cfg.get('resource') or preset.get('resource'))
                    if not path or not path.is_file():
                        raise ValueError('当前默认配乐不存在，请恢复项目的配乐资源后再打开工作台')
                    row=self.store(path,path.name,builtin=True)
                    self.write(self.index,{'defaultId':row['id'],'tracks':[row]})

    def checked(self,args,timeout=120):
        result=self.run(args,capture_output=True,timeout=timeout)
        if result.returncode:
            raise ValueError('音频文件无法完整读取，请换一个可播放的文件后重试')
        return result.stdout

    def metadata(self,path,decode=False):
        raw=self.checked(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],30)
        info=json.loads(raw)
        audio=next((x for x in info.get('streams',[]) if x.get('codec_type')=='audio'),None)
        if not audio or any(x.get('codec_type')=='video' and not x.get('disposition',{}).get('attached_pic') for x in info.get('streams',[])):
            raise ValueError('请上传音乐音频文件，视频请放在机构资料库')
        duration=float(info.get('format',{}).get('duration') or audio.get('duration') or 0)
        if not math.isfinite(duration) or duration<=0:
            raise ValueError('无法读取音乐时长，请换一个音频文件')
        if decode:
            self.checked(['ffmpeg','-v','error','-xerror','-err_detect','explode','-i',str(path),'-map','0:a:0','-f','null','-'])
        return {'duration':duration,'sampleRate':int(audio.get('sample_rate',0)),'channels':audio.get('channels',0)}

    def store(self,path,filename,builtin=False):
        meta=self.metadata(path,decode=not builtin)
        ident=uuid.uuid4().hex[:12]
        dest=self.root/ident/('source'+path.suffix.lower());dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,dest)
        name='Tenderness · 轻柔配乐' if 'Tenderness_Benjamin_Tissot' in filename else Path(filename).stem
        return {'id':ident,'name':name[:100],'filename':filename,'file':str(dest.relative_to(self.root)),'bytes':dest.stat().st_size,'sha256':sha(dest),'created':time.time(),'archived':False,'builtin':builtin,**meta}

    def data(self):
        return self.read(self.index)

    def track(self,ident,active=False):
        if not re.fullmatch(r'[a-f0-9]{12}',str(ident)):
            raise ValueError('这首配乐不存在，请重新选择')
        row=next((x for x in self.data()['tracks'] if x['id']==ident),None)
        if not row or (active and row.get('archived')):
            raise ValueError('这首配乐已收起或不可用，请重新选择')
        return row,self.root/row['file']

    def public(self):
        data=self.data()
        return {'defaultId':data['defaultId'],'tracks':[{k:v for k,v in row.items() if k not in ('file','sha256')}|{'isDefault':row['id']==data['defaultId'],'url':f"/media/music/{row['id']}"} for row in data['tracks']]}

    def add(self,path,filename):
        self.metadata(path,decode=True)
        fingerprint=sha(path)
        with self.app.transaction():
            data=self.data();old=next((t for t in data['tracks'] if t['sha256']==fingerprint),None)
            if old:
                old['archived']=False
                self.write(self.index,data)
                return {'trackId':old['id'],'duplicate':True,'library':self.public()}
            # File is already decoded; store probes it again but avoids a second full decode.
            row=self.store(path,filename,builtin=True);row['builtin']=False
            data['tracks'].append(row);self.write(self.index,data)
            return {'trackId':row['id'],'duplicate':False,'library':self.public()}

    def edit(self,ident,values):
        with self.app.transaction():
            self.track(ident)
            data=self.data();row=next(t for t in data['tracks'] if t['id']==ident)
            if values.get('archived') and (ident==data['defaultId'] or values.get('default')):
                raise ValueError('默认配乐不能收起，请先把另一首设为默认')
            if 'name' in values:
                name=str(values['name']).strip()
                if not name or len(name)>100:raise ValueError('请填写 1–100 个字的配乐名称')
                row['name']=name
            if values.get('default'):
                if row.get('archived'):raise ValueError('请先恢复这首配乐，再设为默认')
                path=self.root/row['file']
                if not path.is_file() or sha(path)!=row['sha256']:raise ValueError('配乐文件不完整，请重新上传')
                self.app.pc.save_patch({'settings':{'music':{'resource':str(path.relative_to(self.app.pc.directory()))}}})
                data['defaultId']=ident
            if 'archived' in values:
                if values['archived'] and ident==data['defaultId']:raise ValueError('默认配乐不能收起，请先把另一首设为默认')
                row['archived']=bool(values['archived'])
            self.write(self.index,data)
            return self.public()

    def snapshot(self,ident,task_id,original=None,slot=None):
        if original:
            path=Path(original['resource']);row=original
            if not path.is_file() or sha(path)!=row['sha256']:raise ValueError('原任务配乐副本不完整，请先恢复该文件')
        else:
            row,path=self.track(ident or self.data()['defaultId'],active=True)
            if not path.is_file() or sha(path)!=row['sha256']:raise ValueError('配乐文件已变化，请重新上传后选择')
        base=self.app.task_path(task_id).parent/'music'
        target=(base/str(slot)/('source'+path.suffix.lower())) if slot else (base/('source'+path.suffix.lower()))
        from publish_assets import link_or_copy
        link_or_copy(path,target)
        if sha(target)!=row['sha256']:raise ValueError('配乐复制未完成，请重试')
        return {k:row[k] for k in ('id','name','filename','sha256','duration')}|{'resource':str(target)}

    def original(self,task,job,person_id=None):
        if not task.get('music',True):return None
        pid=person_id or task.get('person')
        per_person=(task.get('videoMusicTracks') or {}).get(pid) if pid else None
        if per_person:return per_person
        if task.get('musicTrack'):return task['musicTrack']
        frozen=self.read(job/'runtime.json',{}).get('settings',{}).get('music',{})
        plan=self.read(job/'plan.json',{}).get('settings',{}).get('music',{})
        path=Path(frozen.get('resource') or plan.get('resource') or '')
        if not path.is_file():
            path=next(iter((job/'assets/music').glob('source.*')),None)
        if not path or not path.is_file():
            raise ValueError('原视频的配乐文件不可用，请恢复原工程的音乐文件后再重做；也可以新建批次重新选曲。')
        return {'id':'legacy','name':'原视频配乐','filename':path.name,'sha256':sha(path),'resource':str(path),'duration':self.metadata(path)['duration']}
