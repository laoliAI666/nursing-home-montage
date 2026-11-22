"""Workbench-owned editions; never mutate an engine project or approved delivery."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import threading
import time
import uuid
import zipfile


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class VideoEdits:
    def __init__(self, app, read, write, run, recover=True):
        self.app, self.read, self.write, self.run = app, read, write, run
        self.root = app.data / 'video-edits'
        # An interrupted local encode must never look like a published edition.
        for p in self.root.glob('*/*/operation.json') if recover else []:
            op = read(p, {})
            if op.get('status') == 'running' and not any(x['id']==op.get('id') for x in app.queue.data()['items']):
                op.update(status='failed', message='工作台重启中断了首帧更新。原版本保留，可以重新确认。')
                write(p, op)

    def resolve(self, ident, index):
        index = int(index)
        if ident == 'reference':
            if index != 0:
                raise ValueError('视频不存在')
            baseline = self.read(self.app.project / '项目状态.json', {})
            video = Path(baseline.get('latestApprovedDelivery', {}).get('path', ''))
            job = Path(baseline.get('activeEditableProject', ''))
            if not video.is_file() or not (job / 'plan.json').is_file():
                raise ValueError('历史样片缺少可编辑工程')
            plan = self.read(job / 'plan.json')
            task = {'id':'reference', 'title':'已认可的项目样片', 'person':plan.get('person'), 'personName':'', 'requirements':'', 'music':True, 'count':1, 'source':self.read(job / 'inventory.json', {}).get('source', ''), 'status':'completed'}
            institution = next((x for x in self.app.library.records() if x.get('reference')), {})
            task.update(institutionId=institution.get('id'), institutionName=institution.get('name', plan.get('institution', {}).get('name', '')))
        else:
            from product_archive import product_deletion
            task = self.app.task(ident)
            if product_deletion(task, index):
                raise ValueError('这条成品已删除或正在清理')
            ready=task['status']=='completed' or any(x['index']==index and x['status']=='completed' for x in self.app.queue.for_task(ident))
            if not ready or not 0 <= index < task['count']:
                raise ValueError('请在视频制作完成后编辑')
            from workspace import video_job
            job = video_job(task,index)
            video = job / 'final.mp4'
            if task.get('videoSpecs'):
                spec=task['videoSpecs'][index];task={**task,'person':spec['person'],'personName':spec['personName']}
        if not video.is_file():
            raise ValueError('原视频不存在')
        folder = self.root / ident / str(index)
        return task, job, video, folder

    def current(self, ident, index, version=None):
        task, job, video, folder = self.resolve(ident, index)
        key = version if version is not None else self.read(folder / 'current.json', {}).get('version', 'original')
        if key == 'original':
            return task, job, video, folder, {'id':'original', 'label':'原始交付', 'job':str(job), 'video':str(video), 'created':task.get('created', 0)}
        if not re.fullmatch(r'[a-f0-9]{12}', key):
            raise ValueError('版本不存在')
        record = self.read(folder / 'versions' / key / 'edition.json')
        if not record or record.get('status') != 'completed':
            raise ValueError('该版本尚未完成')
        return task, job, Path(record['video']), folder, record

    def package(self, job, video, cover, plan, target):
        """Public workbench packages contain three files; HTML stays internal."""
        from publish_assets import publish_zip
        with self.app.lock:
            return publish_zip(video,cover,plan.get('publication',{}),target)

    def workspace_media(self, ident, index, version=None):
        from workspace import delivery_folder
        task, job, _, _, edition = self.current(ident,index,version)
        root = Path(edition['job'])
        output = delivery_folder(task,index,edition['id'])
        if output is None or self.read(root/'delivery.json', {}).get('mode') != 'workspace':
            return None
        if output.resolve() != output.absolute() or not all((output/name).is_file()
                and not (output/name).is_symlink() for name in ('final.mp4','cover.png','发布文案.txt')):
            raise ValueError('工作区的交付产物缺失')
        return output, self.read(root/'plan.json', {}).get('publication', {}).get('title', task['title'])

    def media(self, ident, index, kind, version=None):
        task, job, video, folder, edition = self.current(ident, index, version)
        edited = edition['id'] != 'original'
        root = Path(edition['job']) if edited else job
        product = self.workspace_media(ident,index,edition['id'])
        if product is not None:
            folder, _ = product
            if kind == 'video':return folder/'final.mp4'
            if kind == 'cover':return folder/'cover.png'
            if kind == 'package':
                return self.package(root, folder/'final.mp4', folder/'cover.png',
                                    self.read(root/'plan.json',{}), folder/'发布包.zip')
        if kind == 'video':
            return video
        if kind == 'cover':
            cover = root / 'cover.png'
            if not cover.is_file() or (ident == 'reference' and not edited):
                cover = folder / 'original-cover.png'
                if not cover.is_file():
                    cover.parent.mkdir(parents=True, exist_ok=True)
                    self.checked(['ffmpeg','-v','error','-y','-i',str(video),'-frames:v','1',str(cover)])
            return cover
        if kind == 'package':
            target = root/'delivery'/'发布包.zip' if ident != 'reference' else root/'delivery'/'workbench'/'发布包.zip' if edited else folder/'original-package'/'发布包.zip'
            return self.package(root, video, self.media(ident,index,'cover',edition['id']), self.read(root / 'plan.json', {}), target)
        raise ValueError('文件不存在')

    def detail(self, ident, index):
        task, job, video, folder, edition = self.current(ident, index)
        plan = self.read(Path(edition['job']) / 'plan.json', {})
        original = {'id':'original', 'label':'原始交付', 'created':task.get('created',0)}
        versions = [self.read(p) for p in (folder / 'versions').glob('*/edition.json')]
        history = [original]+[{k:r[k] for k in ('id','label','created')} for r in sorted(versions,key=lambda r:r['created']) if r.get('status')=='completed']
        return {'taskId':ident, 'index':int(index), 'title':plan.get('publication', {}).get('title') or task['title'], 'institutionName':task.get('institutionName',''), 'personName':task.get('personName',''), 'currentVersion':edition['id'], 'versions':history, 'narration':'\n'.join(s.get('text','') for s in plan.get('sentences',[])), 'publication':plan.get('publication',{}), 'cover':plan.get('cover',{}), 'requirements':task.get('requirements',''), 'videoPreference':task.get('videoPreferences',[''])[int(index)] if task.get('videoPreferences') else '', 'draft':self.read(folder / 'draft.json',{}), 'operation':self.read(folder / 'operation.json',{}), 'revisionTasks':[self.app.public_task(t) for t in self.app.tasks() if t.get('revisionOf')=={'taskId':ident,'index':int(index)}]}

    def cover_payload(self, ident, index):
        task, job, video, folder, edition = self.current(ident,index)
        root = Path(edition['job'])
        payload = self.read(root / 'cover-input.json')
        if not payload:
            html = (job / 'cover-editor.html').read_text(encoding='utf-8') if (job / 'cover-editor.html').is_file() else ''
            match = re.search(r'<script\b[^>]*id=[\"\']cover-data[\"\'][^>]*>(.*?)</script>', html, re.S)
            if not match:
                raise ValueError('这个旧工程缺少封面编辑数据，请先用“单条重做”生成新版本。')
            payload = json.loads(match[1])
        payload = copy.deepcopy(payload)
        # Assets and identity remain server-owned; browsers select only same-institution photos.
        payload.pop('provided_cover',None)
        return {'payload':payload,'baseVersion':edition['id'],'draft':self.read(folder/'cover-draft.json',{})}

    def save_draft(self, ident,index, values, cover=False):
        with self.app.transaction():
            self.app.acceptance.ensure_editable(ident)
            return self._save_draft(ident,index,values,cover)

    def _save_draft(self, ident,index, values, cover=False):
        self.app.acceptance.ensure_editable(ident)
        _,_,_,folder,_ = self.current(ident,index)
        allowed = ('title','highlights','labels','photos','baseVersion') if cover else ('narration','requirements','publicationTitle','publicationDescription')
        clean = {k:values[k] for k in allowed if k in values}
        if len(json.dumps(clean,ensure_ascii=False)) > 100000:
            raise ValueError('草稿过长')
        self.write(folder/('cover-draft.json' if cover else 'draft.json'),clean)
        return {'saved':True}

    def checked(self, args, timeout=600):
        result = self.run(args,capture_output=True,timeout=timeout)
        if result.returncode:
            message = result.stderr.decode(errors='replace') if isinstance(result.stderr,bytes) else result.stderr
            raise ValueError('本地视频处理失败：'+(message or '')[-500:])
        return result.stdout

    def probe(self, video):
        return json.loads(self.checked(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)]))

    def png_bytes(self, raw):
        raw=str(raw)
        if not raw.startswith('data:image/png;base64,') or len(raw)>24_000_000:
            raise ValueError('请从封面预览导出一张有效的 PNG')
        try:
            data=base64.b64decode(raw.split(',',1)[1],validate=True)
        except ValueError:
            raise ValueError('封面数据无效')
        if len(data)<32 or data[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',data[16:24])!=(1080,1920):
            raise ValueError('封面必须是 1080 × 1920 的 PNG')
        return data

    def export(self,ident,index,values):
        with self.app.transaction():
            self.app.acceptance.ensure_editable(ident)
            return self._export(ident,index,values)

    def _export(self,ident,index,values):
        _,_,_,folder,_=self.current(ident,index)
        target=folder/'exports'/(uuid.uuid4().hex[:12]+'.png');target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(self.png_bytes(values.get('png','')))
        self.checked(['ffmpeg','-v','error','-i',str(target),'-f','null','-'],30)
        return {'url':f'/media/video/{ident}/{index}/export?file={target.stem}'}

    def apply(self,ident,index,values):
        with self.app.transaction():
            self.app.acceptance.ensure_editable(ident)
            task,job,video,folder,edition = self.current(ident,index)
            if values.get('baseVersion') != edition['id']:
                raise ValueError('这个视频已有新版本，请重新打开封面编辑后确认。')
            if self.read(folder/'operation.json',{}).get('status') in ('queued','running','stopping'):
                raise ValueError('正在更新这个视频，请等待完成。')
            data = self.png_bytes(values.get('png',''))
            payload = self.cover_payload(ident,index)['payload']
            title = str(values.get('title','')).strip()
            labels = values.get('labels',[])
            highlights = values.get('highlights',[])
            if not title or len(title)>100 or len(labels)!=3 or any(not str(x).strip() or len(str(x))>30 for x in labels):
                raise ValueError('请填写主标题和三枚简短标签')
            if not isinstance(highlights,list) or any(not str(x) or str(x) not in title.replace('\n','') for x in highlights):
                raise ValueError('高亮词必须来自主标题')
            payload.update(title=title,labels=list(map(str,labels)),highlights=highlights)
            chosen = values.get('photos',[])
            if chosen and len(chosen)!=3:
                raise ValueError('请选择三张同机构素材')
            if chosen:
                for i,aid in enumerate(chosen):
                    if not aid:
                        continue
                    if not task.get('institutionId'):
                        raise ValueError('该视频未关联机构，不能跨机构换图')
                    asset,path = self.app.library.asset(task['institutionId'],str(aid))
                    if asset['kind'] not in ('image','video'):
                        raise ValueError('请选择同机构照片或视频')
                    target = folder/'photo-cache'/(str(aid)+'.png');target.parent.mkdir(parents=True,exist_ok=True)
                    args=['ffmpeg','-v','error','-y']+(['-ss','0.3'] if asset['kind']=='video' else [])+['-i',str(path),'-frames:v','1','-vf','scale=1080:-1',str(target)]
                    self.checked(args,60)
                    payload['photos'][i]={'data':'data:image/png;base64,'+base64.b64encode(target.read_bytes()).decode()}
            key=uuid.uuid4().hex[:12];out=folder/'versions'/key;out.mkdir(parents=True)
            (out/'cover.png').write_bytes(data)
            self.write(out/'cover-input.json',payload)
            plan=copy.deepcopy(self.read(Path(edition['job'])/'plan.json',{}))
            plan.setdefault('cover',{})['main_title']={'text':title.replace('\n',''),'lines':title.split('\n') if '\n' in title else None,'highlights':highlights}
            for i,text in enumerate(labels):
                if len(plan['cover'].get('labels',[]))>i:
                    plan['cover']['labels'][i]['text']=text
            self.write(out/'plan.json',plan)
            submitted={k:values[k] for k in ('title','highlights','labels','photos','baseVersion') if k in values}
            operation={'id':key,'status':'queued','message':'已加入队尾，等待更新视频首帧','created':time.time(),'baseVersion':edition['id'],'submittedDraft':submitted}
            queued=self.app.queue.enqueue_cover(ident,int(index),operation,video,out)
            operation['sequence']=queued['sequence']
            self.write(folder/'operation.json',operation)
            if ident != 'reference': self.app.update(ident, reviewStatus='pending')
            return operation

    def _render(self,ident,index,source,out,folder,operation):
        try:
            before=self.probe(source);v=next(s for s in before['streams'] if s['codec_type']=='video')
            if (v['width'],v['height'])!=(1080,1920):
                raise ValueError('当前仅支持工作台的 1080 × 1920 竖版视频')
            final=out/'final.mp4'
            self.checked(['ffmpeg','-v','error','-y','-i',str(source),'-i',str(out/'cover.png'),'-filter_complex',"[1:v]setsar=1[c];[0:v][c]overlay=0:0:enable='eq(n,0)':eof_action=repeat[v]",'-map','[v]','-map','0:a?','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','copy','-fps_mode','passthrough','-movflags','+faststart',str(final)])
            self.write(folder/'operation.json',dict(operation,status='running',message='正在检查首帧、时长与音轨…'))
            self.checked(['ffmpeg','-v','error','-i',str(final),'-f','null','-'])
            after=self.probe(final);a=next(s for s in after['streams'] if s['codec_type']=='video')
            if v.get('nb_frames')!=a.get('nb_frames') or v['avg_frame_rate']!=a['avg_frame_rate'] or abs(float(v.get('duration',before['format']['duration']))-float(a.get('duration',after['format']['duration'])))>0.002:
                raise ValueError('帧数或时长发生变化，已保留原视频并停止发布新版本')
            def pixels(path,frame=None):
                cmd=['ffmpeg','-v','error','-i',str(path)]
                if frame is not None:cmd+=['-vf',f'select=eq(n\\,{frame}),scale=108:192']
                else:cmd+=['-vf','scale=108:192']
                return self.checked(cmd+['-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
            def error(left,right):
                if len(left)!=len(right) or not left:raise ValueError('检查帧读取失败')
                return sum(abs(x-y) for x,y in zip(left,right))/len(left)
            first=error(pixels(out/'cover.png'),pixels(final,0));second=error(pixels(source,1),pixels(final,1))
            if first>10 or second>10:raise ValueError('首帧或正文时间点检查不通过，原视频保留')
            audio=True
            if any(s['codec_type']=='audio' for s in before['streams']):
                def ah(path):return self.checked(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-c','copy','-f','hash','-hash','sha256','-'])
                audio=ah(source)==ah(final)
                if not audio:raise ValueError('音轨一致性检查失败')
            report={'fullDecode':True,'firstFrameMeanError':first,'secondFrameMeanError':second,'audioUnchanged':audio,'frameCount':a.get('nb_frames'),'duration':a.get('duration'),'sha256':digest(final),'sourceSha256':digest(source),'operation':'cover-first-frame'}
            self.write(out/'verification.json',report)
            from workspace import delivery_folder
            task = self.app.task(ident) if ident != 'reference' else {}
            output = delivery_folder(task,index,operation['id'])
            if output is not None:
                from publish_assets import workspace_product
                self.write(out/'delivery.json',workspace_product(out,output,self.read(out/'plan.json')['publication'],report['sha256']))
            else:
                self.package(out,final,out/'cover.png',self.read(out/'plan.json'),out/'delivery/workbench/发布包.zip')
            edition={'id':operation['id'],'created':time.time(),'status':'completed','label':'封面修改 · '+time.strftime('%m/%d %H:%M'),'job':str(out),'video':str(final),'parentVersion':operation['baseVersion']}
            self.write(out/'edition.json',edition)
            with self.app.transaction():
                if self.app.queue.item(operation['id'])['status']=='stopping':raise ValueError('已取消首帧更新，原版本保留')
                self.write(folder/'current.json',{'version':edition['id']})
                self.write(folder/'operation.json',dict(operation,status='completed',message='封面已应用到首帧，视频与发布包已更新。'))
                pending=self.read(folder/'cover-draft.json',{})
                if pending==operation['submittedDraft']:
                    (folder/'cover-draft.json').unlink(missing_ok=True)
                elif pending and pending.get('baseVersion')==operation['baseVersion']:
                    pending['baseVersion']=edition['id']
                    self.write(folder/'cover-draft.json',pending)
                if ident!='reference':self.app.update(ident, reviewStatus='pending')
        except Exception as exc:
            self.write(folder/'operation.json',dict(operation,status='failed',message=self.app.redact(str(exc))[:700]))

    def restore(self,ident,index,version):
        with self.app.transaction():
            self.app.acceptance.ensure_editable(ident)
            _,_,_,folder,edition=self.current(ident,index,version)
            if self.read(folder/'operation.json',{}).get('status') in ('queued','running','stopping'):raise ValueError('请等待当前首帧更新完成')
            self.write(folder/'current.json',{'version':edition['id']})
            (folder/'cover-draft.json').unlink(missing_ok=True)
            if ident!='reference':self.app.update(ident, reviewStatus='pending')
            return self.detail(ident,index)

    def revision(self,ident,index,values):
        with self.app.transaction():
            self.app.acceptance.ensure_editable(ident)
            return self._revision(ident,index,values)

    def _revision(self,ident,index,values):
        self.app.acceptance.ensure_editable(ident)
        task,job,video,folder,edition=self.current(ident,index)
        if values.get('baseVersion')!=edition['id']:raise ValueError('原视频已有新版本，请重新打开后提交')
        narration=str(values.get('narration','')).strip();requirements=str(values.get('requirements','')).strip()
        if len(narration)>20000 or len(requirements)>20000:raise ValueError('文案或修改要求过长')
        original='\n'.join(s.get('text','') for s in self.read(Path(edition['job'])/'plan.json',{}).get('sentences',[])).strip()
        if narration==original:
            narration=''  # Unedited source is context for rewriting, never an explicit script lock.
        person=task.get('person')
        cfg=self.app.pc.load()
        if person not in cfg['people']:person=cfg.get('default_person')
        source=self.app.acceptance.ensure_source(task)
        if not source.is_dir():raise ValueError('原视频的制作资料不可用，请从机构重新创建任务')
        music_enabled=task.get('music',True)
        if not task.get('musicTrack') and self.read(job/'runtime.json',{}).get('settings',{}).get('music',{}).get('enabled') is False:
            music_enabled=False
        original_music=self.app.music.original({**task,'music':music_enabled},job)
        new=self.app.create({'source':str(source),'person':person,'count':1,'title':str(values.get('title','')).strip() or self.detail(ident,index)['title']+' · 重做','requirements':task.get('requirements',''),'music':music_enabled,'facts':task.get('facts',''),'videoPreferences':[requirements]}, original_music=original_music)
        self.app.update(new['id'],kind='revision',revisionReady=False)
        # Freeze the original task input, never take a later library revision.
        frozen=self.app.task_path(new['id']).parent/'source'
        try:
            shutil.copytree(source,frozen,dirs_exist_ok=True)
        except Exception:
            self.app.update(new['id'],status='failed',message='原资料副本准备失败，请回原视频重新提交修改。')
            raise
        if task.get('sourceReleased') and source == self.app.task_path(ident).parent/'source':
            shutil.rmtree(source)
        revision={'taskId':ident,'index':int(index)}
        new=self.app.update(new['id'],source=str(frozen),kind='revision',revisionFromArchive=bool(task.get('intermediatesCleaned') or task.get('cleanupInterrupted')),revisionReady=True,revisionOf=revision,baseJob=str(job),basePlan=str(Path(edition['job'])/'plan.json'),baseVersion=edition['id'],revisedNarration=narration,publicationOverride={'title':str(values.get('publicationTitle','')).strip()[:200],'description':str(values.get('publicationDescription','')).strip()[:10000]},institutionId=task.get('institutionId'),institutionName=task.get('institutionName'),institutionRevision=task.get('institutionRevision'),materialSnapshot=task.get('materialSnapshot',[]),message='单条重做要求已保存；原视频保留。')
        self.save_draft(ident,index,values)
        return new
