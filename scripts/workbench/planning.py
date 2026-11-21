"""One immutable editorial handoff per batch, before any video is dispatched."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import time


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


class BatchPlanning:
    def __init__(self, app, read, write):
        self.app, self.read, self.write = app, read, write

    def folder(self, task):
        return Path(task['job']) / 'batch-planning'

    def inputs(self, task):
        # Snapshots are immutable, but verify their actual bytes before accepting a plan.
        source = Path(task['source'])
        files = []
        for path in sorted(source.rglob('*')):
            if not path.is_file() or any(x.startswith('.') for x in path.relative_to(source).parts): continue
            if re.search(r'access.?key|secret|credential|凭据|密钥', path.name, re.I): continue
            files.append([str(path.relative_to(source)), file_digest(path)])
        settings = {'targetDurationSeconds': task['targetDurationSeconds']} if 'targetDurationSeconds' in task else {}
        return {**settings, 'source': str(source), 'files': files, 'facts': task.get('facts', ''),
                'requirements': task.get('requirements', ''), 'videoPreferences': task.get('videoPreferences', []),
                'people': task.get('videoSpecs') or [{'person':task['person']}] * task['count'],
                'music': task.get('musicTrack', {}), 'count': task['count']}

    def initialize(self, task):
        folder = self.folder(task)
        self.app.ensure_dir(folder / 'plans')
        value = self.inputs(task)
        path = folder / 'inputs.json'
        prior = self.read(path, {})
        fingerprint = digest(value)
        if prior and prior.get('fingerprint') != fingerprint:
            raise ValueError('批次固定输入发生变化，请新建批次；不覆盖已批准计划')
        if not prior: self.write(path, {'fingerprint': fingerprint, 'inputs': value})
        return folder, fingerprint

    def append_facts(self,task,folder):
        text=task.get('facts','').strip()
        if not text:return None
        name='用户补充事实_'+task['id']+'.txt'
        (folder/name).write_text(text,encoding='utf-8')
        inventory=self.read(folder/'inventory.json');row={'path':name,'kind':'text','text':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),'bytes':len(text.encode()),'userProvided':True}
        inventory['files']=[x for x in inventory['files'] if x['path']!=name]+[row]
        self.write(folder/'inventory.json',inventory)
        (folder/'source-text.txt').write_text('\n\n'.join('【'+x['path']+'】\n'+x['text'] for x in inventory['files'] if x['kind']=='text'),encoding='utf-8')
        return name

    def draft(self, task):
        folder, fingerprint = self.initialize(task)
        draft = self.read(folder / 'draft.json', {})
        if draft.get('inputFingerprint') != fingerprint: raise ValueError('策划草稿未绑定本批输入')
        facts = draft.get('coreFacts', [])
        if not isinstance(facts, list) or not facts: raise ValueError('缺少整批共同事实表')
        ids = []
        sources={x['path']:x.get('text','') for x in self.read(folder/'inventory.json',{}).get('files',[]) if x.get('kind')=='text'}
        for fact in facts:
            if not isinstance(fact, dict) or not fact.get('id') or not fact.get('text') or not fact.get('evidence'):
                raise ValueError('每项共同事实必须提供编号、内容和来源依据')
            evidence=fact['evidence']
            if not isinstance(evidence,dict) or not evidence.get('quote') or evidence['quote'] not in sources.get(evidence.get('source'), ''):raise ValueError('共同事实须引用共享索引中的实际原文')
            ids.append(fact['id'])
        if len(set(ids)) != len(ids): raise ValueError('共同事实编号重复')
        review = draft.get('review', {})
        if not all(isinstance(review.get(k), str) and review[k].strip() for k in ('facts', 'differences', 'preferences')):
            raise ValueError('策划子 Agent 须记录事实、差异和偏好的具体复核说明')
        items = draft.get('items')
        if not isinstance(items, list) or len(items) != task['count']: raise ValueError('策划数量与批次不一致')
        scripts, angles, parsed = set(), set(), []
        specs = task.get('videoSpecs') or [{'person':task['person']}] * task['count']
        for i, row in enumerate(items):
            if row.get('index') != i or row.get('person') != specs[i]['person']: raise ValueError('策划顺序或人物与提交不符')
            relative = Path(row.get('plan', ''))
            if relative.is_absolute() or '..' in relative.parts or relative.parent != Path('plans') or relative.suffix != '.json':
                raise ValueError('逐条计划必须位于 batch-planning/plans')
            plan_path = folder / relative
            if plan_path.is_symlink() or not plan_path.is_file(): raise ValueError('逐条计划缺失或不可用')
            plan = self.read(plan_path)
            if plan.get('person') != row['person']: raise ValueError('计划人物与批次身份不一致')
            text = '\n'.join(s.get('text', '') for s in plan.get('sentences', []))
            if not text.strip() or digest(text) in scripts: raise ValueError('口播为空或整篇完全重复')
            scripts.add(digest(text))
            direction = tuple(str(row.get(k, '')).strip() for k in ('openingAngle', 'structure', 'coverAngle'))
            if not all(direction) or direction in angles: raise ValueError('请明确区分各条的切入、结构与封面策划')
            angles.add(direction)
            if row.get('mode', 'comprehensive') != 'topic':
                if set(row.get('factCoverage', [])) != set(ids): raise ValueError('综合介绍须覆盖共同事实，并逐项记录覆盖关系')
            else:
                quote = row.get('topicAuthorization', '')
                user_text = task.get('requirements', '') + '\n' + (task.get('videoPreferences') or [''] * task['count'])[i]
                if not quote or quote not in user_text: raise ValueError('专题制作须引用用户的明确要求')
            covered=set(row.get('factCoverage',[]))
            used={f for sentence in plan.get('sentences',[]) for f in sentence.get('factIds',[])}
            if not covered.issubset(used):raise ValueError('共同事实覆盖关系未在本条口播的 factIds 中体现')
            parsed.append({'index': i, 'path': str(plan_path), 'sha256': file_digest(plan_path), 'plan': plan})
        return draft, parsed, fingerprint

    def validate_item(self, task, folder, index, plan, validate):
        from workspace import video_job
        job = video_job(task,index)
        self.app.ensure_dir(job)
        if (job / 'runtime.json').is_file(): raise ValueError('已有视频已进入执行，不允许重写整批策划')
        self.write(job / 'plan.json', plan)
        for name in ('inventory.json', 'source-text.txt'):
            shutil.copy2(folder / name, job / name)
        validate(job)
        # Validation can normalize Chinese text and resolve closing hooks.
        return self.read(job / 'plan.json')

    def check(self, task, validate):
        draft, parsed, fingerprint = self.draft(task)
        folder = self.folder(task)
        if not (folder / 'inventory.json').is_file(): raise ValueError('请先完成共享素材索引')
        receipts = []
        for item in parsed:
            normalized = self.validate_item(task, folder, item['index'], item['plan'], validate)
            self.write(Path(item['path']), normalized)
            receipts.append({'index':item['index'], 'path':item['path'], 'sha256':file_digest(item['path']),'planFingerprint':digest(normalized)})
        # Recheck after normalization, including duplicated complete scripts.
        self.draft(task)
        value = {'inputFingerprint':fingerprint, 'draftSha256':file_digest(folder / 'draft.json'),
                 'plans':receipts, 'review':draft['review'], 'checkedAt':time.time()}
        self.write(folder / 'approved.json', value)
        return {'approved':True, 'items':len(receipts), 'path':str(folder / 'approved.json')}

    def verify(self, task):
        folder = self.folder(task)
        value = self.read(folder / 'approved.json', {})
        if value.get('inputFingerprint') != digest(self.inputs(task)): raise ValueError('整批策划尚未通过或固定输入已变化')
        if value.get('draftSha256') != file_digest(folder / 'draft.json'): raise ValueError('策划草稿在复核后发生变化')
        if len(value.get('plans', [])) != task['count']: raise ValueError('逐条策划核验记录不完整')
        for row in value['plans']:
            expected = folder / 'plans' / Path(row['path']).name
            if Path(row['path']).resolve() != expected.resolve() or row['sha256'] != file_digest(expected):
                raise ValueError('逐条计划在复核后发生变化')
            from workspace import video_job
            job = video_job(task,row['index'])
            original = row.get('planFingerprint',digest(self.read(expected)))
            current = digest(self.read(job/'plan.json'))
            revision = self.read(job/'duration-revision.json', {})
            accepted_revision = (task.get('targetDurationSeconds') is not None
                                 and revision.get('target') == task['targetDurationSeconds']
                                 and revision.get('approvedPlan') == original
                                 and revision.get('replacement') == current)
            if current != original and not accepted_revision:
                raise ValueError('视频工程与批准计划不一致')
        return value

    def prompt(self, task, folder):
        work = self.folder(task)
        refs = self.app.skill / 'references'
        return f'''你是本批唯一的策划子 Agent，不制作视频、不调用付费生成、不派生 Agent。
先读 {self.app.skill/'SKILL.md'}、{refs/'narration.md'}、{refs/'plan-schema.md'} 和 {refs/'batch-planning.md'}。
请求：{folder/'request.json'}；固定输入与指纹：{work/'inputs.json'}。
用本条文件通道请求 prepare-planning 建立共享素材索引，读取 {work/'inventory.json'} 与 source-text.txt，实际检查媒体。
统一提炼共同事实与各条差异；默认每篇完整介绍共同核心，只有用户明确要求专题时按专题制作。
在 {work/'plans'} 写各条正常格式的完整计划，在 {work/'draft.json'} 写整批事实表、差异矩阵及具体复核说明。
调用 planning-check，修正返回的问题；批准后 report completed 并结束。规划与核验不等于用户验收。
详细草稿、材料和正文留文件，主 Agent 只收计划路径、指纹和短摘要。'''
