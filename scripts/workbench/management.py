"""Confirmed catalog removal and asynchronous task deletion preserving deliverables."""
import secrets
import hashlib
import re
from pathlib import Path
import json
import time


class Management:
    def __init__(self, app, read, write):
        self.app, self.read, self.write = app, read, write

    def state(self):
        return {'service':'montage-management','entries':[], 'hiddenAssets':[], 'deletions':[{k:r.get(k) for k in ('taskId','title','status','error')} for r in self.app.archive.pending()], 'activity':self.activity(), 'diagnostics':self.diagnostics()}

    def activity(self):
        phases = {'inventoried':(0,'已整理素材'), 'voice_ready':(2,'配音已生成，准备数字人'),
                  'avatars_pending':(2,'已提交数字人，等待服务返回'), 'avatars_ready':(3,'数字人已返回，准备画面与封面'),
                  'composed':(3,'画面工程已生成，正在检查排版'), 'rendered':(4,'视频已渲染，正在检查成片'),
                  'verified_visual_review_pending':(4,'程序核验已完成，等待画面复核'), 'complete':(5,'交付文件已生成，等待制作结果确认')}
        result = {}
        for row in self.app.queue.data()['items']:
            if row.get('kind') != 'video' or row.get('status') != 'running': continue
            try:
                job = Path(row['job']).resolve()
                if not job.is_relative_to(self.app.project): continue
                file = job/'state.json'
                stamp = file.stat().st_mtime
                if stamp < row.get('started',row.get('assigned',row.get('created',0))): continue
                state = self.read(file,{})
                if state.get('phase') not in phases: continue
                phase,message = phases[state['phase']]
                plan=job/'plan.json'
                if plan.is_file() and plan.stat().st_mtime>stamp:
                    phase=min(phase,3)
                    message='工程方案已更新，等待新的制作检查点'
                    stamp=plan.stat().st_mtime
                for name in ('qc-layout','qc-rendered'):
                    folder=job/name
                    if folder.is_dir(): stamp=max(stamp,folder.stat().st_mtime)
                if phase < row.get('phase',0) or stamp <= row.get('seen',0): continue
                result[row['id']]={'phase':phase,'message':message,'updated':stamp,'source':'本地工程检查点'}
            except (OSError,ValueError,TypeError,KeyError): continue
        return result

    def diagnostics(self):
        result = {}
        for row in self.app.queue.data()['items']:
            if row.get('settled') or row.get('kind') != 'video': continue
            latest = 0
            try:
                job = Path(row['job']).resolve()
                if job.is_relative_to(self.app.project):
                    for name in ('state.json','plan.json','index.html','final.mp4','qc-layout','qc-rendered'):
                        file = job/name
                        if file.exists(): latest=max(latest,file.stat().st_mtime)
            except (KeyError,OSError,ValueError): pass
            pending = bool(row.get('recoveryRequested'))
            dispatched = bool(row.get('recoveryDispatched'))
            result[row['id']] = {'reportedAt':row.get('seen'), 'lastArtifactAt':latest or None,
                'recoveryState':'awaiting_agent' if pending and dispatched else 'awaiting_coordinator' if pending else row.get('recoveryState','none'),
                'recoveryRequestedAt':row.get('recoveryRequestedAt') or (row.get('updated') if pending else None),
                'recoveryResult':row.get('recoveryResult'),
                'recoveryMessage':'核查请求已交给 Agent，等待实际检查结果；执行锁保持不变' if pending and dispatched else '核查请求尚未被 Agent 领取，主对话可能仍在等待制作子任务；这不会暂停当前制作' if pending else '',
                'workerState':'unconfirmed', 'leaseHeld':True}
        return result

    def ensure_available(self, values):
        return  # Deleted catalog entries are removed from their actual indexes.

    def catalog(self, kind, parent=''):
        if kind == 'task': return {x['id']: x['title'] for x in self.app.tasks()}
        if kind == 'institution': return {x['id']: x['name'] for x in self.app.library.records()}
        if kind == 'person': return {k: v['name'] for k, v in self.app.pc.load()['people'].items()}
        if kind == 'music': return {x['id']: x['name'] for x in self.app.music.public()['tracks']}
        if kind == 'asset': return {x['id']: x['name'] for x in self.app.library.get(parent)['assets'] if x['current']}
        raise ValueError('不支持管理此类内容')

    def blocked(self, kind, ident, parent):
        if kind=='task':
            task=next((t for t in self.app.tasks() if t['id']==ident),None)
            if task:
                reason=self.app.archive.blocked(task)
                if reason:return reason
                self.app.archive.roots(task)
        rows = self.app.queue.data()['items']
        pending = [x for x in rows if x['status'] in ('queued','assigned','running','stopping','needs_input') or not x.get('settled')]
        tasks = self.app.tasks()
        active_ids = {x['taskId'] for x in pending}
        active_ids.update(t['id'] for t in tasks if t['status'] in ('queued','running','stopping','needs_input'))
        active = [t for t in tasks if t['id'] in active_ids]
        if kind in ('task','video') and (ident if kind == 'task' else parent) in active_ids:
            return '仍在排队、制作或等待处理，请先结束该任务'
        if kind=='person' and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ident):return '人物标识无效'
        if kind=='institution' and any(t.get('institutionId')==ident for t in tasks):return '仍有关联任务使用这家机构的资料，请先删除关联任务；成品会保留'
        if kind=='asset' and any(a.get('id')==ident for t in tasks for a in t.get('materialSnapshot',[])):return '已有任务仍引用这个素材版本，请先删除关联任务'
        if kind in ('institution','asset') and any(t.get('institutionId') == (ident if kind == 'institution' else parent) for t in active):
            return '当前有任务正在使用这家机构的资料'
        cfg = self.app.pc.load()
        if kind == 'person':
            training = getattr(self.app, 'voice_training', None)
            if training and training.active(ident):
                return '这位数字人正在复刻音色，请等待结果。'
            owned=self.app.pc.directory()/'people'/ident if hasattr(self.app.pc,'directory') else None
            if owned:
                for other,value in cfg.get('people',{}).items():
                    if other==ident:continue
                    for key in ('portrait','sample','cover_portrait'):
                        p=self.app.pc.asset_path(value.get(key))
                        if p and p.resolve().is_relative_to(owned.resolve()):return '其他数字人仍引用这个人物的文件'
            if ident == cfg.get('default_person'): return '请先将另一位数字人设为默认'
            if any(t.get('person') == ident or any(s.get('person') == ident for s in t.get('videoSpecs', [])) for t in active):
                return '当前有任务正在使用这个数字人'
        if kind == 'music':
            if ident == self.app.music.public()['defaultId']: return '请先选择另一首默认配乐'
            if any(t.get('musicTrack', {}).get('id') == ident for t in active): return '当前有任务正在使用这首配乐'
        return ''

    def selection(self, values):
        kind, parent = values.get('kind'), str(values.get('parent',''))
        ids = values.get('ids')
        if not isinstance(ids,list) or not ids or len(ids)>200 or any(not isinstance(x,str) for x in ids):
            raise ValueError('请选择 1–200 项内容')
        ids = list(dict.fromkeys(ids))
        catalog = self.catalog(kind, parent)
        deleted = {(e['kind'],e['parent'],e['id']) for e in self.state()['entries']}
        items = []
        for ident in ids:
            if ident not in catalog or (kind,parent,ident) in deleted: raise ValueError('内容已变化，请刷新后重新选择')
            record={}
            if kind=='institution':record=self.app.library.get(ident)
            elif kind=='asset':record=next(a for a in self.app.library.get(parent)['assets'] if a['id']==ident)
            elif kind=='person':record=self.app.pc.load()['people'][ident]
            elif kind=='music':record=next(a for a in self.app.music.public()['tracks'] if a['id']==ident)
            fingerprint=hashlib.sha256(json.dumps(record,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            items.append({'kind':kind,'parent':parent,'id':ident,'name':catalog[ident],'blocked':self.blocked(kind,ident,parent),'fingerprint':fingerprint})
        return items

    def preview(self, values):
        with self.app.transaction():
            items = self.selection(values)
            token = secrets.token_hex(24)
            folder = self.app.data / 'delete-previews'
            self.app.ensure_dir(folder)
            self.write(folder / (token+'.json'), {'expires':time.time()+600, 'values':values,'items':items})
            return {'token':token,'items':items,'canDelete':not any(x['blocked'] for x in items),
                    'notice':('删除后在后台清理任务工程、缓存、日志及任务记录。全部成品版本、封面和发布包独立保留在成品库；原始素材不受影响。任务数据无法恢复。' if values.get('kind')=='task' else '删除所选资料及工作台保存的副本，原始外部文件与已完成成品保留。此操作无法恢复。')}

    def delete(self, token):
        if not isinstance(token,str) or len(token)!=48 or any(c not in '0123456789abcdef' for c in token): raise ValueError('请先预览删除范围')
        with self.app.transaction():
            file = self.app.data / 'delete-previews' / (token+'.json')
            preview = self.read(file,{})
            if preview.get('expires',0)<time.time(): raise ValueError('确认已过期，请重新选择')
            items = self.selection(preview['values'])
            if items != preview['items'] or any(x['blocked'] for x in items): raise ValueError('任务状态或内容有变化，未删除任何内容，请重新预览')
            if preview['values']['kind']=='task':self.app.archive.submit(items)
            else:
                for item in items:self.remove_catalog(item)
            file.unlink()
            return self.state()

    def remove_catalog(self,item):
        kind,ident,parent=item['kind'],item['id'],item['parent']
        if kind=='institution':
            self.app.archive.remove_tree(self.app.library.path(ident).parent)
            if hasattr(self.app, 'store'):
                self.app.store.sync_institution(ident)
        elif kind=='asset':
            value=self.app.library.get(parent)
            asset=next(a for a in value['assets'] if a['id']==ident)
            # Historical snapshots may need this version after acceptance.
            if any(a['id']==ident for t in self.app.tasks() for a in t.get('materialSnapshot',[])):
                raise ValueError('已有任务仍引用这个素材版本，请先删除关联任务')
            _,path=self.app.library.asset(parent,ident)
            if path.is_symlink() or path.resolve().parent!=(self.app.library.path(parent).parent/'assets').resolve():raise ValueError('素材路径无效')
            path.unlink(missing_ok=True)
            value['assets']=[a for a in value['assets'] if a['id']!=ident]
            for versions in value.get('sourceVersions',{}).values():versions.pop(asset['name'],None)
            self.app.library.commit(value,'删除素材：'+asset['name'])
            (self.app.library.path(parent).parent/'thumbnails'/(ident+'.jpg')).unlink(missing_ok=True)
        elif kind=='person':
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ident):raise ValueError('人物标识无效')
            cfg=self.app.pc.load();cfg['people'].pop(ident)
            folder=self.app.pc.directory()/'people'/ident
            self.app.archive.remove_tree(folder)
            self.write(self.app.pc.directory()/'project.json',cfg)
        elif kind=='music':
            data=self.app.music.data();row,path=self.app.music.track(ident)
            if path.resolve().parent!=(self.app.music.root/ident).resolve():raise ValueError('配乐路径无效')
            self.app.archive.remove_tree(self.app.music.root/ident)
            data['tracks']=[r for r in data['tracks'] if r['id']!=ident];self.write(self.app.music.index,data)

    def reveal_credential(self, field):
        names=('api_key','ak','sk')
        if field not in names: raise ValueError('不支持查看这个配置项')
        cfg=self.app.pc.load()
        keys=self.app.pc.connection_keys(cfg.get('connections',{}).get('main',{}))
        value=keys[names.index(field)]
        if not value: raise ValueError('当前没有保存这项凭据，请先填写并保存')
        return {'field':field,'value':value}

    def dispatch(self, action, values):
        if action == 'credential':
            field = values.get('field')
            if field == 'model_key':
                value = self.app.models.reveal_key(values.get('provider'), values.get('baseUrl'))
                return {'field': field, 'value': value}
            if field == 'yixiaoer_api_key':
                value = self.app.publish.api_key()
                if not value:
                    raise ValueError('当前没有保存蚁小二 API Key，请先填写并保存')
                return {'field': field, 'value': value}
            return self.reveal_credential(field)
        if action == 'preview': return self.preview(values)
        if action == 'delete': return self.delete(values.get('token'))
        if action == 'retry-delete':
            request=self.read(self.app.archive.request_path(values.get('id','')),{})
            if request.get('status')!='failed':raise ValueError('没有失败的清理可以重试')
            self.app.archive.submit([{'id':request['taskId']}]);return self.state()
        raise ValueError('操作不存在')
