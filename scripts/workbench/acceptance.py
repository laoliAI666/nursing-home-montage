"""Explicit acceptance and bounded, resumable cleanup of owned workbench jobs."""
from pathlib import Path
import hashlib
import json
import os
import re
import time
import threading
import uuid
from job_queue import TERMINAL
from product_archive import product_deletion


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class Acceptance:
    def __init__(self, app, read, write):
        self.app, self.read, self.write = app, read, write
        self.workers = {}
        self.worker_lock = threading.Lock()

    def eligibility(self, task, for_cleanup=False):
        if task.get('reviewStatus') == 'accepted' and not for_cleanup:
            return False, ''
        rows = self.app.queue.for_task(task['id'])
        if task['status'] not in ('completed', 'partial'):
            return False, ''
        if 'pending' in task.get('productDeletions', {}).values():
            return False, '请先在成品库完成这条视频的删除'
        if task.get('productDeletions') and not any(
                not product_deletion(task, index)
                for index in ([r['index'] for r in rows if r['status'] == 'completed']
                              if rows else range(task['count']))):
            return False, '这批成品已删除'
        if rows and not all(r['status'] in TERMINAL and r.get('settled') for r in rows):
            return False, '等待制作完全结束并确认后验收'
        busy = [r for r in self.app.queue.data()['items'] if r['taskId'] == task['id'] and not (r['status'] in TERMINAL and r.get('settled'))]
        if busy:
            return False, '等待本任务的封面更新或制作结束后验收'
        for other in self.app.tasks():
            if other.get('revisionOf', {}).get('taskId') == task['id'] and other['status'] not in TERMINAL | {'partial'}:
                return False, '有单条重做仍在引用这个工程，请等重做结束后验收'
        if not task.get('intermediatesCleaned'):
            job_root = Path(task['job'])
            if not job_root.is_dir():
                return False, '本地工程目录已移走或缺失，无法验收；可直接删除这条任务记录'
            from workspace import video_job
            indices = [r['index'] for r in rows if r['status'] == 'completed'] if rows else list(range(task['count']))
            active_indices = [i for i in indices if not product_deletion(task, i)]
            if active_indices and not any((video_job(task, i) / 'final.mp4').is_file() for i in active_indices):
                return False, '本地成品文件已移走或缺失，无法验收；可直接删除这条任务记录'
        return True, ''

    def submit(self, ident, *, actor=None):
        """Record the user's acceptance immediately; cleanup is a durable job."""
        with self.app.transaction():
            task = self.app.task(ident)
            self.app.archive.ensure_available(ident)
            if task.get('cleanupStatus') in ('queued','running'):
                return task
            if task.get('reviewStatus')=='accepted' and task.get('cleanupStatus')!='failed':
                return task
            ok, reason = self.eligibility(task, for_cleanup=True)
            if not ok:raise ValueError(reason or '任务尚未结束，不能验收')
            self.owned_job(task)
            acceptance=task.get('acceptance') if task.get('reviewStatus')=='accepted' else {'acceptedAt':time.time()}
            acceptance=acceptance or {'acceptedAt':time.time()}
            extra = {}
            if actor is not None:
                acceptance['acceptedBy'] = actor.user_id
                acceptance['acceptedByName'] = actor.display_name
                extra['acceptedBy'] = actor.user_id
                extra['acceptedByName'] = actor.display_name
            task=self.app.update(ident,reviewStatus='accepted',acceptance=acceptance,
                cleanupStatus='queued',cleanupRequest=uuid.uuid4().hex,cleanupOwnerPid=None,
                cleanupError=None,cleanupInterrupted=False,message='已验收，中间文件将在后台清理',**extra)
            who = f"（{actor.display_name}）" if actor is not None else ""
            self.app.event(ident,f'用户{who}已验收，后台清理已排队；成品可继续查看和下载','success')
        self.tick()
        return task

    def tick(self):
        """Resume queued work after service restart, without replacing live owners."""
        with self.worker_lock:
            if any(t.is_alive() for t in self.workers.values()):return
            for task in self.app.tasks():
                if task.get('cleanupStatus') not in ('queued','running'):continue
                with self.app.transaction():
                    current=self.app.task(task['id'])
                    if current.get('cleanupRequest')!=task.get('cleanupRequest') or current.get('cleanupStatus') not in ('queued','running'):continue
                    owner=current.get('cleanupOwnerPid')
                    if owner==os.getpid() and any(t.ident==current.get('cleanupOwnerThread') and t.is_alive() for t in threading.enumerate()):continue
                    if owner and owner!=os.getpid():
                        try:os.kill(owner,0)
                        except ProcessLookupError:pass
                        except (PermissionError,OSError):continue
                        else:continue
                    self.app.update(task['id'],cleanupStatus='running',cleanupOwnerPid=os.getpid(),cleanupOwnerThread=threading.get_ident())
                thread=threading.Thread(target=self._worker,args=(task['id'],task['cleanupRequest']),daemon=True)
                self.workers[task['id']]=thread;thread.start();return

    def _worker(self, ident, request):
        try:
            task=self.app.task(ident)
            if task.get('cleanupRequest')!=request:return
            with self.app.transaction():self.app.update(ident,cleanupOwnerThread=threading.get_ident())
            plan=self.prepare(ident,for_cleanup=True)
            self.accept(ident,plan['token'],for_cleanup=True)
        except Exception as exc:
            with self.app.transaction():
                if self.app.task(ident).get('cleanupRequest')==request:
                    self.app.update(ident,reviewStatus='accepted',cleanupStatus='failed',cleanupOwnerPid=None,cleanupOwnerThread=None,
                        cleanupInterrupted=True,cleanupError=self.app.redact(str(exc))[:600],
                        message='已验收，后台清理未完成；成品已保留，可重试清理')

    def ensure_editable(self, ident):
        if ident!='reference':self.app.archive.ensure_available(ident)
        if ident!='reference' and self.app.task(ident).get('cleanupStatus') in ('queued','running'):
            raise ValueError('正在后台清理中间文件，请稍后再修改；成品可正常查看和下载')

    def owned_job(self, task):
        from workspace import owned_batch
        job = owned_batch(self.app,task)
        if not job.is_dir():
            raise ValueError('任务工程不存在')
        baseline = self.read(self.app.project / '项目状态.json', {})
        protected = [baseline.get('activeEditableProject'), baseline.get('latestApprovedDelivery', {}).get('path')]
        if any(p and (Path(p).resolve() == job or job in Path(p).resolve().parents) for p in protected):
            raise ValueError('这个工程关联项目基线，不能通过任务验收清理')
        return job

    def inventory(self, roots, keep):
        files = []
        for root in roots:
            if root.is_symlink() or root.resolve() != root.absolute():
                raise ValueError('清理根目录不能是链接')
            if not root.exists():
                continue
            for folder, dirs, names in os.walk(root, followlinks=False):
                for name in list(dirs):
                    p = Path(folder) / name
                    if p.is_symlink():
                        names.append(name); dirs.remove(name)
                for name in names:
                    p = Path(folder) / name
                    if p in keep:
                        continue
                    st = p.lstat()
                    files.append({'path': str(p), 'bytes': st.st_size, 'mtime': st.st_mtime_ns, 'inode': st.st_ino, 'link': p.is_symlink()})
        return sorted(files, key=lambda x: x['path'])

    def prepare(self, ident, for_cleanup=False):
        with self.app.transaction():
            task = self.app.task(ident)
            for_cleanup=for_cleanup or task.get('cleanupStatus')=='failed'
            ok, reason = self.eligibility(task,for_cleanup=for_cleanup)
            if not ok:
                raise ValueError(reason or ('这个任务已经验收' if task.get('reviewStatus') == 'accepted' else '请在任务完成后验收'))
            from workspace import video_job
            root = self.owned_job(task)
            keep, products = set(), []
            rows = self.app.queue.for_task(ident)
            indices = [r['index'] for r in rows if r['status'] == 'completed'] if rows else list(range(task['count']))
            if not indices:
                raise ValueError('没有可以验收的成品')
            for index in indices:
                if product_deletion(task,index):continue
                job = video_job(task,index)
                self.app.verify_job(job)
                self._keep_product(ident, index, job, 'original', keep, products)
            edit_root = self.app.edits.root / ident
            for edition_file in edit_root.glob('*/versions/*/edition.json'):
                edition = self.read(edition_file, {})
                if edition.get('status') != 'completed':
                    continue
                job = edition_file.parent
                if Path(edition.get('video', '')).absolute() != job / 'final.mp4' or Path(edition.get('job', '')).absolute() != job:
                    raise ValueError('封面版本路径与任务不一致，未进行清理')
                self._keep_product(ident, int(edition_file.parents[2].name), job, edition['id'], keep, products)
                keep.add(edition_file)
            # Small records keep version navigation and future cover editing usable.
            for p in edit_root.glob('*/current.json'):
                keep.add(p)
            for p in edit_root.glob('*/original-cover.png'):
                keep.add(p)
            roots = [edit_root]
            if task.get('workspace'):
                roots.extend(video_job(task,index) for index in indices)
                roots.append(root/'batch-planning')
            else:
                roots.append(root)
            roots.extend(self.app.data/'queue-workers'/r['id'] for r in self.app.queue.data()['items'] if r['taskId']==ident)
            # Frozen library input copies can be reconstructed by immutable asset IDs.
            source = Path(task['source'])
            frozen = self.app.task_path(ident).parent / 'source'
            release_input = source == frozen and source.is_dir() and bool(task.get('materialSnapshot')) and bool(task.get('institutionId'))
            introduction = None
            if release_input:
                for asset in task['materialSnapshot']:
                    _, stored = self.app.library.asset(task['institutionId'], asset['id'])
                    if digest(stored) != asset['sha256']:
                        raise ValueError('资料库中的原版本不完整，暂不清理任务资料副本')
                intro_file = next(iter(source.glob('000_机构资料_*.txt')), None)
                introduction = intro_file.read_text(encoding='utf-8') if intro_file else task.get('materialIntroduction')
                if introduction is None:
                    release_input = False
                else:
                    roots.append(source)
            files = self.inventory(roots, keep)
            retained = [{'path': str(p), 'sha256': digest(p)} for p in sorted(keep) if p.is_file()]
            # Do not break a different unfinished project that links to this job.
            for other in self.app.tasks():
                if other['id'] == ident or other.get('reviewStatus') == 'accepted':
                    continue
                other_root = Path(other['job'])
                if not other_root.exists():
                    continue
                def threatened(value):
                    if isinstance(value, dict): return any(threatened(v) for v in value.values())
                    if isinstance(value, list): return any(threatened(v) for v in value)
                    if not isinstance(value, str) or not Path(value).is_absolute(): return False
                    p = Path(value).resolve()
                    return p not in keep and any(p == r or r in p.parents for r in roots)
                other_jobs = {other_root, *(video_job(other,index) for index in range(other['count']))}
                for other_job in other_jobs:
                    for name in ('inventory.json','runtime.json','plan.json','asset-sources.json'):
                        if threatened(self.read(other_job/name, {})):
                            raise ValueError('其他任务仍引用本任务的素材或资产，请先完成该任务的验收')
                for folder, dirs, names in os.walk(other_root, followlinks=False):
                    for name in dirs + names:
                        p = Path(folder) / name
                        if p.is_symlink() and any(p.resolve() == r or r in p.resolve().parents for r in roots):
                            raise ValueError('其他任务仍链接本工程的资产，请先完成该任务的验收')
                    dirs[:] = [x for x in dirs if x not in ('node_modules', '.hyperframes', '.transcode-cache')]
            token = uuid.uuid4().hex
            plan = {'token': token, 'created': time.time(), 'taskId': ident, 'roots': [str(p) for p in roots], 'keep': [str(p) for p in keep], 'files': files, 'retained': retained, 'products': products, 'releaseInput': release_input, 'introduction': introduction}
            self.write(self.app.task_path(ident).parent / 'acceptance-preview.json', plan)
            return {'token': token, 'files': len(files), 'bytes': sum(x['bytes'] for x in files), 'videos': len(products), 'retained': ['所有已完成版本的 MP4、封面和发布包', '发布文案、口播与最少任务记录', '机构资料库、人物和服务配置'], 'removes': ['渲染缓存、检查帧、运行日志与临时网页', '配音、数字人等可重新生成的中间资产；无其他任务引用的对应共享缓存'] + (['可从资料库恢复的本次素材副本'] if release_input else []), 'rebuildNotice': '验收后修改口播会新建任务，必要时重新配音和生成数字人。'}

    def _keep_product(self, ident, index, job, version, keep, products):
        if job.is_symlink() or not (job / 'final.mp4').is_file() or (job / 'final.mp4').is_symlink():
            raise ValueError('成品不能是外部链接，未进行清理')
        for name in ('final.mp4', 'cover.png', 'plan.json', 'state.json', 'verification.json',
                     'review-notices.json', 'narration.txt', 'cover-input.json', 'delivery.json'):
            p = job / name
            if p.is_symlink():
                raise ValueError('保留文件不能引用将被清理的外部资产')
            if p.is_file():
                keep.add(p)
        # Extract self-contained cover data before discarding the editor HTML.
        if not (job / 'cover-input.json').is_file():
            _, _, _, _, _ = self.app.edits.current(ident, index, version)
            html = (job / 'cover-editor.html').read_text(encoding='utf-8') if (job / 'cover-editor.html').is_file() else ''
            match = re.search(r'<script\b[^>]*id=["\']cover-data["\'][^>]*>(.*?)</script>', html, re.S)
            if match:
                self.write(job / 'cover-input.json', json.loads(match[1])); keep.add(job / 'cover-input.json')
        plan = self.read(job / 'plan.json', {})
        publication = plan.get('publication', {})
        text = str(publication.get('title', '')) + '\n\n' + str(publication.get('description', '')) + '\n\n' + ' '.join('#'+str(x).lstrip('#') for x in publication.get('topics', []))
        (job / '发布文案.txt').write_text(text, encoding='utf-8'); keep.add(job / '发布文案.txt')
        if self.read(job/'delivery.json', {}).get('mode') != 'workspace':
            package = self.app.edits.package(job, job / 'final.mp4', job / 'cover.png', plan, job / 'delivery' / '发布包.zip')
            keep.update((package, package.with_suffix('.json')))
        products.append({'index': index, 'version': version, 'job': str(job)})

    def accept(self, ident, token, for_cleanup=False):
        with self.app.transaction():
            task = self.app.task(ident)
            for_cleanup=for_cleanup or task.get('cleanupStatus')=='failed'
            if task.get('reviewStatus') == 'accepted' and not for_cleanup:
                return task
            ok, reason = self.eligibility(task,for_cleanup=for_cleanup)
            if not ok:
                raise ValueError(reason or '任务尚未结束，不能验收')
            self.owned_job(task)
            path = self.app.task_path(ident).parent
            plan = self.read(path / 'acceptance-preview.json', {})
            if not token or token != plan.get('token') or time.time() - plan.get('created', 0) > 1800:
                raise ValueError('验收预览已失效，请重新查看清理内容')
            roots, keep = [Path(x) for x in plan['roots']], {Path(x) for x in plan['keep']}
            from workspace import video_job
            allowed = {self.app.edits.root/ident, self.app.task_path(ident).parent/'source'}
            if task.get('workspace'):
                allowed.update(video_job(task,index) for index in range(task['count']))
                allowed.add(Path(task['job'])/'batch-planning')
            else:
                allowed.add(Path(task['job']))
            allowed.update(self.app.data/'queue-workers'/r['id'] for r in self.app.queue.data()['items'] if r['taskId']==ident)
            if not set(roots).issubset(allowed) or any(not any(p == r or r in p.parents for r in roots) for p in keep):
                raise ValueError('清理范围超出本任务，未进行清理')
            if self.inventory(roots, keep) != plan['files']:
                raise ValueError('任务文件发生变化，请重新预览后验收')
            for record in plan['retained']:
                if not Path(record['path']).is_file() or digest(record['path']) != record['sha256']:
                    raise ValueError('成品在预览后发生变化，未进行清理')
            self.write(path / 'acceptance-cleanup.json', {**plan, 'status': 'cleaning'})
            accepted_at=(task.get('acceptance') or {}).get('acceptedAt',time.time()) if task.get('reviewStatus')=='accepted' else time.time()
            patch = {'reviewStatus':'accepted','cleanupStatus':'running','cleanupOwnerPid':os.getpid(),'cleanupOwnerThread':threading.get_ident(),'acceptance':{'acceptedAt':accepted_at}}
            if plan['releaseInput']:
                patch.update(sourceReleased=True, materialIntroduction=plan['introduction'])
            self.app.update(ident, **patch)
            released_bytes=0
            try:
                for record in plan['files']:
                    p = Path(record['path'])
                    if not any(r == p.parent.resolve() or r in p.parent.resolve().parents for r in roots):
                        raise ValueError('中间文件路径发生变化，已停止清理')
                    st = p.lstat()
                    if (st.st_size, st.st_mtime_ns, st.st_ino) != (record['bytes'], record['mtime'], record['inode']):
                        raise ValueError('中间文件发生变化，请重新预览')
                    p.unlink()
                    if not record['link'] and st.st_nlink==1:released_bytes+=st.st_size
                for root in roots:
                    if not root.exists():
                        continue
                    for folder, dirs, _ in os.walk(root, topdown=False, followlinks=False):
                        for name in dirs:
                            p = Path(folder) / name
                            if not p.is_symlink():
                                try: p.rmdir()
                                except OSError: pass
                for record in plan['retained']:
                    if digest(record['path']) != record['sha256']:
                        raise ValueError('成品核验未通过')
                from shared_cache import SharedCache
                owners=[Path(task['job'])]+[video_job(task,i) for i in range(task['count'])]
                shared_released=SharedCache(self.app.project).release_owners(owners)
            except Exception:
                self.app.update(ident, reviewStatus='accepted',cleanupStatus='failed', cleanupInterrupted=True)
                self.write(path / 'acceptance-cleanup.json', {**plan, 'status': 'interrupted'})
                raise
            acceptance = {'acceptedAt': accepted_at,'cleanedAt':time.time(), 'removedFiles': len(plan['files']), 'releasedBytes': released_bytes+shared_released, 'products': plan['products'], 'retained': plan['retained']}
            self.write(path / 'acceptance.json', acceptance)
            self.write(path / 'acceptance-cleanup.json', {'status': 'completed', 'acceptedAt': acceptance['acceptedAt']})
            (path / 'acceptance-preview.json').unlink(missing_ok=True)
            patch = {'reviewStatus': 'accepted','cleanupStatus':'completed','cleanupOwnerPid':None,'cleanupError':None, 'acceptance': {k: acceptance[k] for k in ('acceptedAt','cleanedAt', 'removedFiles', 'releasedBytes')}, 'cleanupInterrupted': False, 'intermediatesCleaned': True, 'message': '已验收，成品已保留，中间文件已清理'}
            if plan['releaseInput']:
                patch.update(sourceReleased=True, materialIntroduction=plan['introduction'])
            self.app.update(ident, **patch)
            self.app.event(ident, '用户已验收，已清理 '+str(len(plan['files']))+' 个中间文件，保留全部成品版本', 'success')
            return self.app.task(ident)

    def ensure_source(self, task):
        source = Path(task['source'])
        if not task.get('sourceReleased'):
            return source
        self.app.ensure_dir(source)
        import shutil
        for asset in task['materialSnapshot']:
            _, stored = self.app.library.asset(task['institutionId'], asset['id'])
            if digest(stored) != asset['sha256']:
                raise ValueError('原资料版本缺失，请从机构重新创建制作')
            target = source / asset['name']
            if source.resolve() not in target.resolve().parents:
                raise ValueError('素材路径不合法')
            from publish_assets import link_or_copy
            link_or_copy(stored, target)
        (source / ('000_机构资料_'+task['institutionId']+'.txt')).write_text(task.get('materialIntroduction', ''), encoding='utf-8')
        return source
