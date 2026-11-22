"""Bounded deterministic execution, with native Agent review gates.

This worker runs inside the authorized local service. It never starts an LLM,
accepts arbitrary shell text, or declares a visual review on the Agent's behalf.
"""
import hashlib
import html
import json
import os
from pathlib import Path
import re

from duration import measured_duration, within_target
import shutil
import signal
import subprocess
import sys
import time

STEPS = ['validate-plan', 'setup', 'content-review', 'preflight', 'voice', 'build',
         'avatars', 'provider-review', 'cutouts', 'cover', 'carve', 'check',
         'snapshot', 'layout-review', 'render', 'verify', 'final-review', 'package']
PHASE = {'validate-plan':1, 'setup':1, 'preflight':2, 'content-review':1, 'voice':2,
         'build':2, 'avatars':2, 'provider-review':2, 'cutouts':3, 'cover':3,
         'carve':3, 'check':3, 'snapshot':3, 'layout-review':3, 'render':4,
         'verify':4, 'final-review':4, 'package':5}
REVIEW_ACTIONS = {'content':'review-content', 'provider':'review-provider',
                  'final':'review', 'contrast':'review-contrast'}

class PreparationTimeout(ValueError):
    pass


def stop_process(process):
    if sys.platform == 'win32':
        try:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review_notices(job, read):
    records = read(job / 'review-notices.json', {})
    return [dict(stage=stage, text=text) for stage, record in records.items()
            for text in record.get('findings', [])]


class Pipeline:
    def __init__(self, app, read, write, invoke=None):
        self.app, self.read, self.write = app, read, write
        self.invoke_override = invoke

    def row(self, ident, lease):
        self.app.queue.expire_head()
        _, row = self.app.queue.checked(ident, lease)
        if row['status'] not in ('running', 'stopping'): raise ValueError('请先由当前子 Agent 领取作业')
        return row

    def engine(self, job):
        runtime = self.read(job / 'runtime.json', {})
        skill = Path(runtime.get('engine', self.app.skill))
        entry = skill / 'scripts/montage.py'
        if not entry.is_file(): raise ValueError('原冻结引擎缺失，不改用新版继续付费生成')
        return entry

    def command(self, entry, *args):
        if getattr(self.app,'service_mode','standalone')=='host':
            # Retain the target's declared uv environment and the narrow mkdir
            # compatibility fix; native host permissions still apply unchanged.
            return [sys.executable,str(Path(__file__).with_name('runtime_compat.py')),str(entry),*map(str,args)]
        return ['uv','run','--python','3.11',str(entry),*map(str,args)]

    def invoke(self, row, lease, action, extra=None, job=None, cli_args=None):
        if self.invoke_override: return self.invoke_override(row, lease, action, extra or [], job)
        job = Path(job or row['job'])
        if action == 'repair-audio-peak':
            args = [sys.executable, str(Path(__file__).with_name('audio_peak.py')), str(job)]
        else:
            args = self.command(self.engine(job), *(cli_args or [action, job, *(extra or [])]))
        if action == 'setup': args.append('--install')
        if action == 'avatars': args += ['--wait-seconds','0']
        if shutil.which('rtk'): args = ['rtk','proxy',*args]
        env = {**os.environ, 'MONTAGE_PROJECT_ROOT':str(self.app.project),
               'UV_CACHE_DIR':str(self.app.data / 'uv-cache'),
               'MONTAGE_QUEUE_ITEM':row['id'], 'MONTAGE_QUEUE_LEASE':lease}
        from workspace import delivery_folder
        task = self.app.task(row['taskId'])
        target = task.get('targetDurationSeconds')
        env.pop('MONTAGE_TARGET_DURATION_SECONDS', None)
        if action == 'voice' and target is not None:
            env['MONTAGE_TARGET_DURATION_SECONDS'] = str(target)
        if row['kind'] == 'video' and (output := delivery_folder(task,row['index'])):
            env['MONTAGE_WORKBENCH_DELIVERY'] = str(output)
        else:
            env.pop('MONTAGE_WORKBENCH_DELIVERY', None)
        owner=Path(row['job'])
        if action=='revise':
            owner=self.app.data/'queue-workers'/row['id']
        log_dir = (owner if action=='revise' else job) / 'pipeline-logs'; self.app.ensure_dir(log_dir)
        stdout, stderr = log_dir / (action+'.out'), log_dir / (action+'.err')
        receipt=owner/'pipeline-process.json';prior=self.read(receipt,{})
        if prior.get('state')=='running':
            try:os.kill(prior['pid'],0)
            except ProcessLookupError:pass
            else:raise ValueError('上次本地执行进程尚未确认退出，不能重复启动阶段')
        with stdout.open('w') as out, stderr.open('w') as err:
            process = subprocess.Popen(args, cwd=self.app.project, env=env, stdout=out, stderr=err,start_new_session=sys.platform!='win32')
            self.write(receipt,{'pid':process.pid,'state':'running','action':action,'leaseHash':hashlib.sha256(lease.encode()).hexdigest(),'started':time.time()})
            last_report = time.monotonic()
            deadline = last_report + 600 if action in ('inventory','revise') else None
            while process.poll() is None:
                self.app.queue.expire_head()
                current = self.app.queue.item(row['id'])
                timed_out = deadline is not None and time.monotonic() >= deadline
                if current.get('lease') != lease or current['status']!='running' or timed_out:
                    stop_process(process)
                    self.write(receipt,{'pid':process.pid,'state':'exited','action':action,'cancelled':True,'ended':time.time()})
                    if timed_out and current['status']=='running':
                        raise PreparationTimeout('素材准备超时。')
                    raise ValueError('当前阶段已停止，云端任务 ID 与已有资产保留')
                if time.monotonic()-last_report >= 60:
                    try:
                        self.app.queue.report(row['id'],lease,PHASE.get(action,0),'running','仍在执行本地步骤：'+action)
                    except ValueError:
                        stop_process(process)
                        self.write(receipt,{'pid':process.pid,'state':'exited','action':action,'cancelled':True,'ended':time.time()})
                        raise
                    last_report=time.monotonic()
                time.sleep(.5)
        self.write(receipt,{'pid':process.pid,'state':'exited','action':action,'returnCode':process.returncode,'ended':time.time()})
        try: result = json.loads(stdout.read_text())
        except ValueError: result = {'error':self.app.redact(stderr.read_text()[-1500:] or stdout.read_text()[-1500:])}
        return process.returncode, result

    def gate(self, job, step):
        if step == 'content-review':
            review=self.read(job/'content-review.json',{})
            value={'plan':self.read(job/'plan.json'),'inventory':self.read(job/'inventory.json')}
            expected=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            return review.get('inputFingerprint')==expected
        if step == 'provider-review':
            fallback = self.read(job / 'review-notices.json', {}).get('provider', {}).get('incomplete')
            sources = self.verified_providers(job)
            return (not any(data.get('visualReviewPending', True) for data in sources)
                    or fallback == [data['videoSha256'] for data in sources])
        if step == 'layout-review':
            review=self.read(job/'layout-review.json',{})
            composition = fingerprint(job/'index.html')
            fallback = self.read(job / 'review-notices.json', {}).get('layout', {}).get('incomplete')
            if fallback == composition: return True
            if review.get('composition')!=composition: return False
            return bool(review.get('frames')) and all(Path(f['file']).is_file() and fingerprint(f['file'])==f['sha256'] for f in review['frames'])
        if step == 'final-review':
            verify=self.read(job/'verification.json',{})
            if verify.get('visualReviewPending',True) or not verify.get('fullDecode'): return False
            path=Path(verify.get('video',''))
            return path.is_file() and fingerprint(path)==verify.get('sha256')
        return False

    def advance(self, ident, lease, max_seconds=600):
        row=self.row(ident,lease)
        if row['kind']!='video': raise ValueError('策划任务不运行视频流水线')
        job=Path(row['job']);self.app.ensure_dir(job)
        journal_path=job/'pipeline.json';journal=self.read(journal_path,{'steps':[]})
        if not (job/'plan.json').is_file(): raise ValueError('缺少本条批准计划')
        if journal.get('plan') and journal['plan']!=fingerprint(job/'plan.json'):
            raise ValueError('执行期间计划发生变化，请另存重做或先复核受影响资产')
        deadline=time.monotonic()+max_seconds
        target = self.app.task(row['taskId']).get('targetDurationSeconds')
        for step in STEPS:
            current=self.row(ident,lease)
            if current['status']=='stopping':
                self.app.queue.report(ident,lease,PHASE[step],'running','已到停止检查点')
            if step in journal['steps']: continue
            if step == 'build' and target is not None and (job/'voice.json').is_file():
                duration = measured_duration(job, self.read)
                if not within_target(duration, target):
                    return {'state':'revise_duration','duration':duration,'target':target}
            if step in ('final-review', 'package') and target is not None:
                rendered = self.read(job/'verification.json', {}).get('duration')
                if rendered is not None and not within_target(rendered, target):
                    raise ValueError(f'成片实测 {rendered:.1f} 秒，超出 {target} 秒前后 5 秒的范围。')
            if step=='check' and journal.get('reviewedCheck') and (job/'check.json').is_file() and journal['reviewedCheck']==fingerprint(job/'check.json'):
                journal.pop('reviewedCheck')
            elif step.endswith('-review'):
                if not self.gate(job,step):
                    self.write(journal_path,{**journal,'plan':fingerprint(job/'plan.json'),'waiting':step})
                    return {'state':'needs_review','gate':step,'job':str(job),'phase':PHASE[step]}
            else:
                self.app.queue.report(ident,lease,PHASE[step],'running','正在执行：'+step)
                code,result=self.invoke(row,lease,step)
                if not code and step == 'voice' and target is not None and 'durationAdjustmentRequired' in result:
                    return {'state':'revise_duration','duration':result['durationAdjustmentRequired'],'target':target}
                while (code==2 or result.get('pending')) and time.monotonic()+10<deadline:
                    # Poll the same registered provider task; no model round trip.
                    time.sleep(max(0, min(10, current.get('deadline', float('inf')) - time.time())))
                    current=self.row(ident,lease)
                    if current['status']=='stopping':self.app.queue.report(ident,lease,PHASE[step],'running','已到停止检查点')
                    code,result=self.invoke(row,lease,step)
                if code==2 or result.get('pending'):
                    self.write(journal_path,{**journal,'plan':fingerprint(job/'plan.json'),'waiting':step})
                    return {'state':'pending','step':step,'result':result}
                if code and step == 'check':
                    if self.fit_overflowing_headers(job):
                        self.app.event(row['taskId'], '片中标题或机构名超出画布，已适配宽度并重新检查。')
                        code, result = self.invoke(row,lease,step)
                    if code and self.accept_contrast_notice(row, lease, job):
                        code = 0
                if code and step == 'verify' and result.get('error') == 'Audio peak too high':
                    self.app.queue.report(ident, lease, PHASE[step], 'running', '混音峰值超标，正在修正音轨并保留原画面')
                    code, result = self.invoke(row, lease, 'repair-audio-peak')
                    if not code:
                        if result.get('changed'):
                            note = f"混音峰值由 {result['beforeDbtp']:.2f} 调整为 {result['afterDbtp']:.2f} dBTP，画面与时间轴保持一致。"
                            self.app.event(row['taskId'], note)
                        code, result = self.invoke(row, lease, step)
                if code:
                    return {'state':'failed','step':step,'result':result,'job':str(job)}
                if step == 'cover':
                    layout = self.read(job/'cover.json', {}).get('layout', {})
                    if layout.get('titleAdjustment'):
                        note = '原封面标题超出两行空间，已自动排入两行：' + layout['title_text'].replace('\n', ' ')
                        self.save_notices(job, 'cover', [note])
                        self.app.event(row['taskId'], note)
                if step == 'package':
                    verified, _ = self.verified_render(job)
                    final = job / 'final.mp4'
                    if not final.is_file() or fingerprint(final) != verified['sha256']:
                        raise ValueError('交付视频与技术核验记录不一致。')
                    self.write(job / 'state.json', {**self.read(job / 'state.json', {}),
                               'phase': 'complete', 'video': str(final), 'sha256': verified['sha256'],
                               'next': 'Deliver the publish-ready files'})
            journal['steps'].append(step);journal['plan']=fingerprint(job/'plan.json');journal.pop('waiting',None)
            self.write(journal_path,journal)
            if time.monotonic()>=deadline: return {'state':'checkpoint','step':step,'job':str(job)}
        self.app.verify_job(job)
        if self.app.task(row['taskId']).get('workspace'):
            try:
                self.prune_checks(job)
            except OSError as exc:
                self.app.event(row['taskId'], '成品已保存，检查缓存暂未精简：'+self.app.redact(str(exc)), 'warning')
        self.app.queue.report(ident,lease,5,'completed','视频已核验，交付产物已保存，等待用户验收')
        return {'state':'completed','job':str(job)}

    def fit_overflowing_headers(self, job):
        check = self.read(job/'check.json', {})
        if check.get('browserSkipped') is not False:
            return False
        errors = [item for item in check.get('layout', {}).get('findings', [])
                  if item.get('severity') == 'error']
        institution = self.read(job/'institution.json', {})
        headers = {
            institution.get('headline'): 'TitleFace',
            institution.get('displayName', '').replace('·',' · '): 'BrandFace',
        }
        if not errors or any(item.get('code') != 'text_box_overflow'
                or item.get('sourceFile') != 'compositions/orange-ui.html'
                or not item.get('text') or item['text'] not in headers for item in errors):
            return False
        scales = {}
        for item in errors:
            rect = item.get('rect', {})
            container = item.get('containerRect', {})
            if any(not isinstance(bounds.get(k), (int,float)) for bounds in (rect,container) for k in ('left','right')):
                return False
            width = rect['right']-rect['left']
            center = (rect['right']+rect['left'])/2
            room = min(center-container['left'],container['right']-center)
            if width <= 0 or room <= 24:
                return False
            text = item['text']
            scales[text] = min(scales.get(text,0.98), 2*(room-24)/width)
        path = job/'compositions/orange-ui.html'
        markup = path.read_text(encoding='utf-8')
        for text, scale in scales.items():
            if scale < 0.72:
                return False
            font = headers[text]
            title = re.search(rf'(<text\b[^>]*font-family="{font}"[^>]*>)([^<]+)</text>',markup)
            if not title or html.unescape(title[2]) != text:
                return False
            tag, count = re.subn(r'(font-size|textLength)="([0-9.]+)"',
                        lambda match: f'{match[1]}="{float(match[2])*scale:.3f}"', title[1])
            if count != 2:
                return False
            markup = markup[:title.start(1)] + tag + markup[title.end(1):]
        temporary = path.with_suffix('.html.tmp')
        temporary.write_text(markup, encoding='utf-8')
        temporary.replace(path)
        self.write(job/'header-fit.json', {'scales':scales, 'source':'browser-layout-check'})
        return True

    def prune_checks(self, job):
        notices = self.read(job / 'review-notices.json', {})
        receipt = job / 'qc-retention.json'
        if receipt.is_file():
            return
        kept = {}
        remove = []
        for folder, kind in (('qc-layout','layout'), ('qc-final','final')):
            frames = sorted((job/folder).glob('*.png'))
            required = {f for f in frames[:2]}
            timed = [(frame, float(match[1])) for frame in frames
                     if (match := re.search(r'-(?:at-)?(\d+(?:\.\d+)?)s\.png$',frame.name))]
            for note in notices.get(kind, {}).get('findings', []):
                indices = {int(number) for number in re.findall(r'(?<![A-Za-z0-9])F(\d{1,3})',note)}
                for start, end in re.findall(r'F(\d{1,3})\s*[-–—～至]\s*F(\d{1,3})',note):
                    indices.update(range(int(start),min(len(frames),int(end))+1))
                required.update(frames[index-1] for index in indices if 1 <= index <= len(frames))
                times = [float(n) for n in re.findall(r'(?<![\d:])(\d+(?:\.\d+)?)\s*(?:秒|s\b)',note)]
                times.extend(int(m)*60+float(s) for m,s in re.findall(r'(?<!\d)(\d+):(\d+(?:\.\d+)?)',note))
                for moment in times:
                    if timed:
                        required.add(min(timed,key=lambda item:abs(item[1]-moment))[0])
                if not indices and not times:
                    # No exact location was reported. Keep representative frames.
                    required.update(frames[::max(1, len(frames)//5)])
            kept[folder] = [p.name for p in sorted(required)]
            remove.extend(p for p in frames if p not in required)
        for frame in remove:
            frame.unlink(missing_ok=True)
        self.write(receipt, {'retained': kept, 'removedCount':len(remove),
                              'contrastEvidence':str(job/'qc-layout/contrast-evidence')})

    def save_notices(self, job, kind, findings, *, incomplete=None):
        if not isinstance(findings, list) or any(not isinstance(text, str) for text in findings):
            raise ValueError('画面提醒须为文字数组。')
        path = job / 'review-notices.json'
        records = self.read(path, {})
        records[kind] = {'findings': list(dict.fromkeys(self.app.redact(text).strip()[:900]
                                                       for text in findings if text.strip()))[:80]}
        if incomplete is not None:
            records[kind]['incomplete'] = incomplete
        self.write(path, records)

    def verified_render(self, job):
        report = self.read(job / 'verification.json', {})
        video = Path(report.get('video', '')).resolve()
        if (report.get('fullDecode') is not True or not video.is_relative_to(job.resolve())
                or not video.is_file() or fingerprint(video) != report.get('sha256')):
            raise ValueError('实际成片未通过完整性核验。')
        return report, video

    def verified_providers(self, job):
        sources = []
        for avatar in self.read(job / 'avatars.json', []):
            data = self.read(job / 'assets' / ('avatar-' + avatar['id']) / 'generation-source.json', {})
            video = Path(data.get('video', ''))
            if not video.is_file() or fingerprint(video) != data.get('videoSha256'):
                raise ValueError('数字人原片未通过完整性校验。')
            sources.append(data)
        return sources

    def finalize_without_full_visual_review(self, job, findings, unavailable):
        report, video = self.verified_render(job)
        final = job / 'final.mp4'
        if final.is_symlink() or (final.exists() and fingerprint(final) != report['sha256']):
            raise ValueError('已有交付视频与技术核验记录不一致。')
        if not final.exists():
            temporary = job / '.final.mp4.tmp'
            temporary.unlink(missing_ok=True)
            try:
                try: os.link(video, temporary)
                except OSError: shutil.copy2(video, temporary)
                temporary.replace(final)
            finally:
                temporary.unlink(missing_ok=True)
        self.save_notices(job, 'final', findings + ([f'成片画面复核未完整结束。{unavailable}'] if unavailable else []))
        self.write(job / 'verification.json', {**report, 'visualReviewPending': False,
                   'visualReview': {'status': 'incomplete', 'videoSha256': report['sha256']}})

    def visual_advice(self, ident, lease, kind, findings, *, unavailable):
        row = self.row(ident, lease)
        job = Path(row['job'])
        if kind == 'final':
            self.finalize_without_full_visual_review(job, findings, unavailable)
            return
        if kind == 'provider':
            binding = [data['videoSha256'] for data in self.verified_providers(job)]
        elif kind == 'layout':
            binding = fingerprint(job / 'index.html')
        else:
            raise ValueError('未知画面复核阶段。')
        self.save_notices(job, kind, findings + [f'画面复核未完整结束。{unavailable}'], incomplete=binding)

    def accept_contrast_notice(self, row, lease, job):
        check = self.read(job / 'check.json', {})
        if check.get('browserSkipped') is not False or any(
                value.get('errorCount') for name, value in check.items()
                if name not in ('contrast', 'visualContrastReview') and isinstance(value, dict)):
            return False
        errors = [f for f in check.get('contrast', {}).get('findings', []) if f.get('severity') == 'error']
        if not errors or any(f.get('code') != 'contrast_aa_failure' or not isinstance(f.get('time'), (int, float))
                             for f in errors):
            return False
        times = sorted({round(f['time'], 3) for f in errors})
        code, _ = self.invoke(row, lease, 'snapshot', extra=['--at', ','.join(map(str, times))])
        if code: return False
        frames = []
        for path in sorted((job / 'qc-layout').glob('*.png')):
            sample = re.search(r'-at-([0-9.]+)s', path.name)
            if sample and any(abs(float(sample[1])-t)<.004 for t in times):
                frames.append(path)
        if not frames: return False
        evidence = job / 'qc-layout' / 'contrast-evidence'
        evidence.mkdir(exist_ok=True)
        kept = [str(shutil.copy2(path, evidence / path.name)) for path in frames]
        note = '浏览器仅报告对比度提示，测量和对应画面已保留，随成片预览展示。'
        code, result = self.invoke(row, lease, 'review-contrast', extra=['--note',note,'--frames',*kept])
        if code or result.get('renderAcceptedWithVisualContrastReview') is not True: return False
        self.save_notices(job, 'contrast', [f'{f["time"]:.3f} 秒 · {f.get("text") or "画面文字"}：'
                           f'浏览器测得的对比度低于阈值{("（" + str(f["ratio"]) + ":1）") if f.get("ratio") else ""}。'
                           for f in errors])
        return True

    def review(self, ident, lease, kind, note, frames=None, *, findings=None):
        row=self.row(ident,lease);job=Path(row['job'])
        if not isinstance(note,str) or not note.strip(): raise ValueError('请记录实际查看后的具体观察')
        if not isinstance(frames or [],list): raise ValueError('检查帧格式错误')
        if kind in ('provider','layout','final') and not frames and findings is not None:
            self.visual_advice(ident, lease, kind, findings, unavailable=note)
            return {'recorded': True, 'visualReviewIncomplete': True}
        for path in frames or []:
            if not Path(path).resolve().is_relative_to(job.resolve()) or not Path(path).is_file():
                raise ValueError('仅接受当前工程的实际检查帧')
        if kind=='layout':
            if not frames or any(not Path(p).resolve().is_relative_to((job/'qc-layout').resolve()) for p in frames):
                raise ValueError('请提供当前工程 qc-layout 的实际排版检查帧')
            self.write(job/'layout-review.json',{'composition':fingerprint(job/'index.html'),
                       'frames':[{'file':str(Path(p).resolve()),'sha256':fingerprint(p)} for p in frames],'note':note})
            if findings is not None: self.save_notices(job, kind, findings)
            return {'recorded':True}
        if kind not in REVIEW_ACTIONS: raise ValueError('未知审核类型')
        if kind == 'final':
            self.verified_render(job)
        if kind in ('provider','final') and findings is not None:
            self.save_notices(job, kind, findings)
        extra=['--note',note]
        if kind!='content':extra+=['--frames',*(frames or [])]
        code,result=self.invoke(row,lease,REVIEW_ACTIONS[kind],extra)
        if code: raise ValueError(str(result))
        if kind=='contrast' and result.get('renderAcceptedWithVisualContrastReview'):
            journal_path=job/'pipeline.json'
            journal=self.read(journal_path,{})
            if journal.get('plan')==fingerprint(job/'plan.json') and 'carve' in journal.get('steps',[]) and 'check' not in journal['steps']:
                self.write(journal_path,{**journal,'reviewedCheck':fingerprint(job/'check.json')})
        return result
