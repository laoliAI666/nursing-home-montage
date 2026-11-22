"""Keep deliverables independently; remove only the selected task's owned data."""
from pathlib import Path
import hashlib
import os
import re
import shutil
import threading
import time
import uuid


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def product_deletion(task, index):
    return task.get('productDeletions', {}).get(str(index))


class ProductArchive:
    def __init__(self,app,read,write):
        self.app,self.read,self.write=app,read,write
        self.root=app.data/'products'
        self.requests=app.data/'task-deletions'
        self.lock=threading.Lock()
        self.worker=None
        if any(p.is_symlink() or p.resolve()!=p.absolute() for p in (self.root,self.requests)):raise ValueError('成品和清理请求目录不能是链接')

    def request_path(self,ident):
        self.app.task_path(ident)  # Validate without accessing task contents.
        return self.requests/(ident+'.json')

    def ensure_available(self,ident):
        if self.request_path(ident).exists():raise ValueError('任务正在删除清理或等待重试，请先完成清理；成品将保留在成品库')

    def pending(self):
        return [self.read(p,{}) for p in self.requests.glob('*.json')]

    def records(self):
        return [self.read(p) for p in self.root.glob('*/manifest.json')]

    def public(self):
        result=[]
        for item in self.records():
            task={k:item.get(k) for k in ('id','title','institutionId','institutionName','personName','created','updated')}
            task['results']=[]
            versions=[]
            for p in item['products']:
                row={k:p[k] for k in ('index','version','label','title')}
                row.update({kind:f"/media/archive/{item['id']}/{p['index']}/{p['version']}/{kind}" for kind in ('video','cover','package')})
                versions.append(row)
            for index in sorted({p['index'] for p in versions}):
                options=[p for p in versions if p['index']==index]
                current=item.get('currentVersions',{}).get(str(index),'original')
                selected=next((p for p in options if p['version']==current),options[-1])
                task['results'].append({**selected,'versions':options})
            if task['results']:result.append(task)
        return sorted(result,key=lambda x:x['updated'],reverse=True)

    def product(self, ident, index, version):
        self.app.task_path(ident)
        item=self.read(self.root/ident/'manifest.json',{})
        product=next((p for p in item.get('products',[]) if p['index']==int(index) and p['version']==version),None)
        if product is None:
            raise ValueError('归档成品不存在')
        return item, product

    def workspace_media(self, ident, index, version):
        item, product = self.product(ident,index,version)
        if not item.get('workspace'):
            return None
        from workspace import delivery_folder, owned_batch
        owned_batch(self.app, item)
        folder = delivery_folder(item,product['index'],product['version'])
        if folder.resolve() != folder.absolute() or not all((folder/name).is_file() and not (folder/name).is_symlink()
                for name in ('final.mp4','cover.png','发布文案.txt')):
            raise ValueError('工作区的归档成品缺失')
        return folder, product['title']

    def media(self,ident,index,version,kind):
        item, product = self.product(ident,index,version)
        workspace = self.workspace_media(ident,index,version) if item.get('workspace') else None
        if workspace:
            if kind == 'video':return workspace[0]/'final.mp4'
            if kind == 'cover':return workspace[0]/'cover.png'
            raise ValueError('请通过下载入口生成发布包')
        names={'video':'final.mp4','cover':'cover.png','package':'发布包.zip'}
        if not product or kind not in names:raise ValueError('归档成品不存在')
        path=self.root/ident/str(product['index'])/product['version']/names[kind]
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):raise ValueError('成品路径无效')
        return path

    def blocked(self,task,*,deleting_product=False):
        if not deleting_product and 'pending' in task.get('productDeletions', {}).values():
            return '请先在成品库完成这条视频的删除'
        if task.get('cleanupStatus') in ('queued','running'):return '正在验收清理，请等清理完成后再删除任务'
        for row in self.app.queue.data()['items']:
            if row['taskId']==task['id'] and (row['status'] in ('queued','assigned','running','stopping','needs_input') or not row.get('settled')):
                return '仍在排队、制作或等待结束确认，请先结束该任务'
        if task['status'] in ('queued','running','stopping','needs_input'):
            return '任务尚未结束，请先结束该任务'
        return ''

    def delete_product(self, ident, index):
        from workspace import delivery_folder, owned_batch, video_job

        with self.app.transaction():
            self.ensure_available(ident)
            task = self.read(self.app.task_path(ident), {})
            manifest_path = self.root / ident / 'manifest.json'
            archive_folder = manifest_path.parent / str(index)
            if any(p.resolve() != p.absolute() for p in (manifest_path, archive_folder)):
                raise ValueError('归档成品路径无效')
            manifest = self.read(manifest_path, {})
            versions = [p for p in manifest.get('products', []) if p['index'] == index]
            if task:
                if not 0 <= index < task['count']:
                    raise ValueError('成品不存在')
                status = product_deletion(task, index)
                if status == 'deleted' and not versions:
                    return
                reason = self.blocked(task, deleting_product=True)
                if reason:
                    raise ValueError(reason)
                rows = self.app.queue.for_task(ident)
                ready = task['status'] == 'completed' or any(
                    row['index'] == index and row['status'] == 'completed' for row in rows)
                job = video_job(task, index)
                if not versions and not status and not (ready and (job / 'final.mp4').is_file()):
                    raise ValueError('成品不存在')
                owned_batch(self.app, task)
                from job_queue import TERMINAL
                if any(other.get('revisionOf') == {'taskId': ident, 'index': index}
                       and other['status'] not in TERMINAL | {'partial'} for other in self.app.tasks()):
                    raise ValueError('这条视频还有关联的单条重做，请先结束重做任务')
            elif not versions:
                return

            files = []
            trees = [archive_folder]
            if task:
                trees.extend((self.app.edits.root / ident / str(index), job / 'delivery'))
                files.extend(job / name for name in ('final.mp4', 'renders/final.mp4', 'cover.png',
                                                     '发布文案.txt', 'delivery.json'))
            for record in (task, manifest):
                if not record.get('workspace'):
                    continue
                owned_batch(self.app, record)
                folder = delivery_folder(record, index)
                # A workbench output folder may also contain files added by the user.
                editions = [p for p in folder.glob('封面版本_*')
                            if re.fullmatch(r'封面版本_[a-f0-9]{12}', p.name)]
                for output in (folder, *editions):
                    if output.resolve() != output.absolute() or (output.exists() and not output.is_dir()):
                        raise ValueError('工作区的交付目录无效')
                    files.extend(output / name for name in ('final.mp4', 'cover.png', '发布文案.txt', '发布包.zip', '发布包.json'))

            baseline = self.read(self.app.project / '项目状态.json', {})
            protected = [Path(p).resolve() for p in (
                baseline.get('activeEditableProject'), (baseline.get('latestApprovedDelivery') or {}).get('path')) if p]
            for path in (*trees, *files):
                if path.resolve() != path.absolute():
                    raise ValueError('成品路径不能经过链接')
                if any(p == path or p in path.parents or path in p.parents for p in protected):
                    raise ValueError('已认可的样片或活动工程受保护')
            if any(path.exists() and not path.is_file() for path in files):
                raise ValueError('成品文件路径无效')
            if any(path.exists() and not path.is_dir() for path in trees):
                raise ValueError('成品目录路径无效')

            if task:
                deletions = {**task.get('productDeletions', {}), str(index): 'pending'}
                self.app.update(ident, productDeletions=deletions)
            for path in dict.fromkeys(files):
                path.unlink(missing_ok=True)
            for path in trees:
                self.remove_tree(path)
            if versions:
                manifest['products'] = [p for p in manifest['products'] if p['index'] != index]
                manifest.get('currentVersions', {}).pop(str(index), None)
                if manifest['products']:
                    self.write(manifest_path, manifest)
                else:
                    manifest_path.unlink()
            if task:
                self.app.update(ident, productDeletions={**deletions, str(index): 'deleted'})

    def roots(self,task):
        ident=task['id'];self.app.task_path(ident)
        from workspace import owned_batch, video_job
        expected = owned_batch(self.app,task)
        roots=[self.app.edits.root/ident,self.app.task_path(ident).parent]
        if task.get('workspace'):
            roots += [expected/'batch-planning'] + [video_job(task,i) for i in range(task['count'])]
        else:
            roots.append(expected)
        if any(not re.fullmatch(r'[a-f0-9]{12}',row['id']) for row in self.app.queue.data()['items'] if row['taskId']==ident):raise ValueError('任务队列记录无效')
        roots += [self.app.data/name/row['id'] for row in self.app.queue.data()['items'] if row['taskId']==ident for name in ('queue-workers','pipeline-runs')]
        baseline=self.read(self.app.project/'项目状态.json',{})
        protected=[baseline.get('activeEditableProject'),baseline.get('latestApprovedDelivery',{}).get('path')]
        for root in roots:
            if root.is_symlink() or root.resolve()!=root.absolute():raise ValueError('清理目录不能是链接')
            if any(p and (root==Path(p).resolve() or root in Path(p).resolve().parents) for p in protected):raise ValueError('已认可或活动工程不能删除')
        return roots

    def check_references(self,task,roots):
        from workspace import video_job
        def threatened(value):
            if isinstance(value,dict):return any(threatened(v) for v in value.values())
            if isinstance(value,list):return any(threatened(v) for v in value)
            if not isinstance(value,str) or not Path(value).is_absolute():return False
            p=Path(value).resolve()
            return any(p==r or r in p.parents for r in roots)
        for other in self.app.tasks():
            if other['id']==task['id']:continue
            if threatened({k:other.get(k) for k in ('source','baseJob','basePlan')}):raise ValueError('其他任务仍引用这个工程，请先处理关联任务')
            root=Path(other['job'])
            other_jobs = {root, *(video_job(other,index) for index in range(other['count']))}
            for other_job in other_jobs:
                for name in ('inventory.json','runtime.json','plan.json','asset-sources.json'):
                    if threatened(self.read(other_job/name,{})):raise ValueError('其他任务仍引用这个工程的资产')
            for folder,dirs,names in os.walk(root,followlinks=False):
                for name in dirs+names:
                    p=Path(folder)/name
                    if p.is_symlink() and threatened(str(p.resolve())):raise ValueError('其他任务仍链接这个工程的资产')
                dirs[:]=[d for d in dirs if d not in ('node_modules','.hyperframes','.transcode-cache')]

    def submit(self,items):
        with self.app.transaction():
            tasks=[self.read(self.app.task_path(item['id']),{}) or self.read(self.request_path(item['id']),{}).get('task') for item in items]
            if any(not task for task in tasks):raise ValueError('任务不存在')
            # Validate every selection before creating any durable deletion request.
            for task in tasks:
                reason=self.blocked(task)
                if reason:raise ValueError(reason)
                roots=self.roots(task);self.check_references(task,roots)
            for task in tasks:
                path=self.request_path(task['id']);old=self.read(path,{})
                if old.get('status') in ('queued','running'):continue
                self.write(path,{'taskId':task['id'],'title':task['title'],'task':task,'status':'queued','created':time.time(),'ownerPid':None,'ownerThread':None})
        self.tick()

    def tick(self):
        with self.lock:
            if self.worker and self.worker.is_alive():return
            for request in self.pending():
                if request.get('status') not in ('queued','running'):continue
                with self.app.transaction():
                    path=self.request_path(request['taskId']);current=self.read(path,{})
                    if current.get('status') not in ('queued','running'):continue
                    owner=current.get('ownerPid')
                    if owner==os.getpid() and any(t.ident==current.get('ownerThread') and t.is_alive() for t in threading.enumerate()):continue
                    if owner and owner!=os.getpid():
                        try:os.kill(owner,0)
                        except ProcessLookupError:pass
                        except OSError:continue
                        else:continue
                    current.update(status='running',ownerPid=os.getpid(),ownerThread=threading.get_ident());self.write(path,current)
                self.worker=threading.Thread(target=self._worker,args=(current,),daemon=True)
                self.worker.start();return

    def _worker(self,request):
        try:
            with self.app.transaction():
                request.update(ownerThread=threading.get_ident());self.write(self.request_path(request['taskId']),request)
                self.remove_task(request)
        except Exception as exc:
            with self.app.transaction():
                request.update(status='failed',ownerPid=None,ownerThread=None,error=self.app.redact(str(exc))[:600]);self.write(self.request_path(request['taskId']),request)

    def copy_product(self,source,target):
        if source.is_symlink():raise ValueError('成品不能是外部链接')
        fingerprint=sha(source)
        if target.exists():
            if target.is_symlink() or sha(target)!=fingerprint:raise ValueError('归档成品与原文件不一致，未清理任务')
            return fingerprint
        target.parent.mkdir(parents=True,exist_ok=True)
        tmp=target.with_name('.'+target.name+'.'+uuid.uuid4().hex)
        try:
            try:os.link(source,tmp)
            except OSError:shutil.copy2(source,tmp)
            if sha(tmp)!=fingerprint:raise ValueError('成品归档校验失败')
            os.replace(tmp,target)
        finally:tmp.unlink(missing_ok=True)
        return fingerprint

    def preserve(self,task):
        ident=task['id'];path=self.root/ident/'manifest.json'
        manifest=self.read(path,{})
        products={(p['index'],p['version']):p for p in manifest.get('products',[])}
        from workspace import delivery_folder, video_job
        jobs=[(i,'original','原始交付',video_job(task,i)) for i in range(task['count'])]
        for file in (self.app.edits.root/ident).glob('*/versions/*/edition.json'):
            edition=self.read(file,{})
            if edition.get('status')=='completed':jobs.append((int(file.parents[2].name),file.parent.name,edition.get('label','封面修改版'),file.parent))
        for index,version,label,job in jobs:
            key=(index,version)
            if product_deletion(task,index):continue
            if not (job/'final.mp4').exists():continue
            if not re.fullmatch(r'original|[a-f0-9]{12}',version):raise ValueError('成品版本无效')
            if key in products:
                if sha(job/'final.mp4')!=products[key]['sha256']['final.mp4']:raise ValueError('原成品在清理期间变化')
                continue
            if version=='original':self.app.verify_job(job)
            else:
                report=self.read(job/'verification.json',{})
                if not report.get('fullDecode') or report.get('sha256')!=sha(job/'final.mp4'):raise ValueError('封面成品尚未核验')
            plan=self.read(job/'plan.json',{})
            publication=plan.get('publication',{})
            target=delivery_folder(task,index,version)
            if target is not None:
                from publish_assets import workspace_product
                digest = self.read(job/'verification.json',{})['sha256']
                workspace_product(job,target,{'title':task['title'], **publication},digest)
                hashes = {name:sha(target/name) for name in ('final.mp4','cover.png','发布文案.txt')}
            else:
                from publish_assets import publication_text
                package=self.app.edits.package(job,job/'final.mp4',job/'cover.png',plan,job/'delivery/发布包.zip')
                target=self.root/ident/str(index)/version
                hashes={name:self.copy_product(source,target/name) for name,source in [('final.mp4',job/'final.mp4'),('cover.png',job/'cover.png'),('发布包.zip',package)]}
                (target/'发布文案.txt').write_text(publication_text(publication),encoding='utf-8')
            products[key]={'index':index,'version':version,'label':label,'title':publication.get('title') or task['title'],'sha256':hashes}
        for p in products.values():
            for name,fingerprint in p['sha256'].items():
                folder=delivery_folder(task,p['index'],p['version']) or self.root/ident/str(p['index'])/p['version']
                target=folder/name
                if target.is_symlink() or sha(target)!=fingerprint:raise ValueError('归档成品校验失败，未清理任务')
        current_versions=manifest.get('currentVersions',{})
        for index in range(task['count']):
            current_versions[str(index)]=self.read(self.app.edits.root/ident/str(index)/'current.json',{}).get('version',current_versions.get(str(index),'original'))
        manifest={k:task.get(k) for k in ('id','title','institutionId','institutionName','personName','created')}
        manifest.update(updated=time.time(),products=list(products.values()),currentVersions=current_versions)
        if task.get('workspace'):
            manifest.update(workspace=task['workspace'], job=task['job'])
        if products:self.write(path,manifest)

    def remove_tree(self,root):
        if not root.exists():return
        if root.is_symlink() or root.resolve()!=root.absolute():raise ValueError('清理目录已变化')
        for folder,dirs,names in os.walk(root,topdown=False,followlinks=False):
            for name in names:(Path(folder)/name).unlink()
            for name in dirs:
                p=Path(folder)/name
                if p.is_symlink():p.unlink()
                else:p.rmdir()
        root.rmdir()

    def remove_task(self,request):
        task=self.read(self.app.task_path(request['taskId']),{}) or request['task']
        reason=self.blocked(task)
        if reason:raise ValueError(reason)
        roots=self.roots(task);self.check_references(task,roots)
        self.preserve(task)  # All versions are hash checked before deleting any task file.
        # Keep the task request until all other owned directories have been removed.
        task_root=self.app.task_path(task['id']).parent
        for root in roots:
            if root!=task_root:self.remove_tree(root)
        from shared_cache import SharedCache
        from workspace import video_job
        owners=[Path(task['job'])]+[video_job(task,i) for i in range(task['count'])]
        SharedCache(self.app.project).release_owners(owners)
        data=self.app.queue.data()
        for row in data['items']:
            if row['taskId']==task['id']:
                for p in self.app.queue.inbox.glob(row['id']+'-*'):
                    if p.is_file():p.unlink()
        data['items']=[x for x in data['items'] if x['taskId']!=task['id']];self.app.queue.save(data)
        self.remove_tree(task_root)
        for preview in (self.app.data/'delete-previews').glob('*.json'):
            value=self.read(preview,{})
            if value.get('values',{}).get('kind')=='task' and task['id'] in value['values'].get('ids',[]):preview.unlink(missing_ok=True)
        self.request_path(task['id']).unlink(missing_ok=True)
