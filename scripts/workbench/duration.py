"""Measure workbench duration before avatar generation and recheck an adjusted script."""
from copy import deepcopy
from pathlib import Path
import re
import shutil

from planning import digest, file_digest
from narration_timing import MINUTE_TARGET, TOLERANCE, duration_rule, frame_duration
from narration_prompts import outline_omission_limit

MAX_REVISIONS = 3


def parse_target(value):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,3}', value):
        value = int(value)
    if type(value) is not int or not 10 <= value <= 290:
        raise ValueError('请填写 10 至 290 之间的目标秒数。')
    return value


def initial_budget(target, person=None):
    # Initial estimate only. Once speech exists we use the same person's measured pace.
    lower, upper = (195, 245) if target == MINUTE_TARGET else (
        round((target - 4) * 3.8), round((target + 2) * 3.8))
    if target == MINUTE_TARGET and person:
        reference = person.get('narration_reference', {})
        if (reference.get('speaker') == person.get('speaker') and
                reference.get('speech_rate') == person.get('speech_rate')):
            bounds = reference.get('draft_character_range', [])
            cps = reference.get('characters_per_second')
            if (isinstance(bounds, list) and len(bounds) == 2 and
                    all(type(value) is int and value > 0 for value in bounds) and bounds[0] <= bounds[1]):
                lower, upper = bounds
            elif isinstance(cps, (int, float)) and 1.5 <= cps <= 8.0:
                lower, upper = max(140, round(54 * cps)), round(68 * cps)
    opening, closing = round(upper * .12), round(upper * .09)
    return {'targetSeconds': target, 'minUnits': lower, 'maxUnits': upper,
            'sections': {'hook': opening, 'body': upper - opening - closing, 'cta': closing}}


def budget_issue(units, budget, *, section='正文'):
    lower, upper = budget['minUnits'], budget['maxUnits']
    if units > upper:
        return f'程序实数 {units} 字，超出 {upper} 字上限。请压短{section}并保留共同要点。'
    if units < lower:
        return f'程序实数 {units} 字，低于 {lower} 字下限。请补充有依据的内容。'
    return ''


def measured_duration(job, read):
    duration = read(job/'voice.json')['duration']
    settings = read(job/'runtime.json')['settings']
    fps = settings['video']['fps']
    return frame_duration(duration, settings['voice']['start'], fps)


def script_units(plan):
    from workflow import spoken_characters
    return sum(len(spoken_characters(sentence['text'])) for sentence in plan['sentences'])


def revised_plan(plan, edits):
    if not isinstance(edits, list) or len(edits) < 2:
        raise ValueError('修改稿必须保留开场和咨询结尾。')
    edited = deepcopy(plan)
    rows = plan['sentences']
    new = []
    previous = 0
    for edit in edits:
        index = edit.get('index') if isinstance(edit, dict) else None
        text = edit.get('text') if isinstance(edit, dict) else None
        if type(index) is not int or not previous < index <= len(rows) or not isinstance(text, str) or not text.strip():
            raise ValueError('修改稿需按原顺序提供各句的小节编号及口播。')
        if not new and index != 1:
            raise ValueError('请保留第 1 个开场句。')
        sentence = deepcopy(rows[index - 1])
        sentence['text'] = text.strip()
        sentence.pop('captions', None)
        new.append(sentence)
        previous = index
    if previous != len(rows):
        raise ValueError('请保留最后一个咨询小节。')
    source_ids = lambda sentences: {ident for row in sentences for ident in row.get('factIds', [])}
    mandatory = {ident for ident in source_ids(rows) if re.fullmatch(r'f[0-9]+', ident)}
    if not mandatory.issubset(source_ids(new)):
        raise ValueError('修改稿须保留所有原提纲的共同核心。')
    edited['sentences'] = new
    return edited


class DurationRevision:
    def __init__(self, worker, task, answer):
        self.worker, self.task, self.answer = worker, task, answer
        self.job = worker.job
        self.read, self.write = worker.read, worker.write
        self.receipt = self.job/'duration-revision.json'

    def draft_folder(self, attempt):
        return self.job/'duration-revisions'/f'{attempt:02d}'

    def apply_pending(self):
        receipt = self.read(self.receipt, {})
        if not receipt or receipt.get('applied'):
            return
        job = self.job
        draft = self.draft_folder(receipt['attempt'])
        plan = self.read(draft/'plan.json')
        if digest(plan) != receipt.get('replacement'):
            raise ValueError('时长调整计划与复核记录不一致。')
        planfile = job/'plan.json'
        journal_path = job/'pipeline.json'
        journal = self.read(journal_path)
        current = digest(self.read(planfile))
        if ('build' in journal['steps'] or current not in (receipt['previous'], receipt['replacement'])
                or receipt['target'] != self.task.get('targetDurationSeconds')):
            raise ValueError('当前工程已变化，无法应用这次时长调整。')
        for path in (job/'voice.json', job/'runs/voice'):
            archived = draft/('previous-' + path.name)
            if not archived.exists() and path.exists():
                path.rename(archived)
        self.write(planfile, plan)
        for name in ('content-audit.json', 'content-review.json', 'review-notices.json'):
            path = draft/name
            if path.is_file():
                self.write(job/name, self.read(path))
        temporary = job/'narration.duration.tmp'
        temporary.write_bytes((draft/'narration.txt').read_bytes())
        temporary.replace(job/'narration.txt')
        journal['steps'] = [step for step in journal['steps'] if step != 'voice']
        journal['plan'] = file_digest(planfile)
        journal.pop('waiting', None)
        self.write(journal_path, journal)
        self.write(self.receipt, {**receipt, 'applied':True})

    def revise(self, seconds, target):
        from auto_editor import EditorialFeedback, NeedsInput
        from model_api import ModelConnectionError

        job, worker = self.job, self.worker
        prior = self.read(self.receipt, {})
        attempt = prior.get('attempt', 0) + 1
        if attempt > MAX_REVISIONS or self.task.get('revisedNarration'):
            raise NeedsInput(f'配音实测 {seconds:.1f} 秒，本条要求 {duration_rule(target)}。'
                             '请确认可删减的段落，或调整时长要求。')

        worker.progress(f'配音实测 {seconds:.1f} 秒，正在调整口播至 {target} 秒左右（{attempt}/{MAX_REVISIONS}）')
        current = self.read(job/'plan.json')
        source = worker.app.planning.folder(self.task) if self.task.get('planningRequired') else job
        context = worker.editor.context(self.task, source, self.answer)
        outline = self.read(source/'independent-planning.json', {}).get('facts', [])
        refs = {row['id']:row['quote'] for row in current['facts']}
        draft = self.draft_folder(attempt)
        draft.mkdir(parents=True,exist_ok=True)
        for name in ('inventory.json', 'runtime.json'):
            shutil.copy2(job/name, draft/name)

        planned = script_units(current)
        settings = self.read(job/'runtime.json', {}).get('settings', {})
        lead = settings.get('voice', {}).get('start', 0)
        pace = planned / max(seconds - lead, .01)
        estimated_units = round(pace * max(target - lead, 1))
        # The word estimate guides editing. The actual exported duration is
        # checked separately, so allow almost the full timing window here.
        slack = max(3, round(pace * (TOLERANCE - .5)))
        lower, upper = max(1, estimated_units - slack), estimated_units + slack
        original_rows = current['sentences']
        sizes = [script_units({'sentences': [row]}) for row in original_rows]
        rows = {i: {'index':i, 'label':row.get('label',''), 'original':row['text'],
                    'goal':max(3, round(size * estimated_units / planned)),
                    'sources':[{'id':key,'quote':refs[key]} for key in row.get('factIds',[])],
                    'core':[fact['text'] for fact in outline if fact['id'] in row.get('factIds',[])],
                    'shots':[shot.get('description','') for shot in row.get('shots',[])]}
                for i,(row,size) in enumerate(zip(original_rows,sizes),1)}
        edits_by_index = {i: {'index':i,'text':row['original']} for i,row in rows.items()}
        media = {row['path']: row for row in context.get('media', [])}
        requested = list(rows)
        if self.task.get('narrationPolicy'):
            requested = [i for i in rows if 1 < i < len(rows)] or requested
        feedback = ''
        preferences = self.task.get('videoPreferences') or [''] * self.task['count']
        custom_copy = self.task.get('requirements') or preferences[worker.row['index']] or self.task.get('publicationOverride')
        refresh_description = False
        use_short_headline = False
        for _ in range(2):
            try:
                proposal = worker.editor.ask(
                    '针对 lines 中每个小节，单独写一条更简洁或更完整的中文口播，严格接近该行 goal 字数。'
                    '例如一行 goal 为 18，就写约 18 个读出来的字；每行只说最重要的一件事。'
                    '保留每段原本的主要事实。保留原文限定、共同要点和指定要求，去掉标题式开头、同义解释、列表和累赘的形容词。'
                    '屏幕只看得到部分内容时，选一个有实拍的例子。在没有直接画面时只用短句交代，不罗列做法或效果。'
                    '只按原文说临床服务和设施性能。没有能明确对应的画面时，机构方案只提名称，'
                    '普通步道、展柜和康复训练画面用作背景，不能添加防滑性质、展区用途或医务室等场所身份。'
                    '第 1 行交代机构与开场，最后一行只保留一个咨询动作。每行都应与自己的分镜相符。'
                    '向家属直接介绍机构。事实出处和背景镜头留在后台，口播避免资料里说、根据介绍、画面里可以看到。'
                    '正文如需更换分镜，可以额外填写一个 media 中真实存在的 path；否则沿用原镜头并删掉镜头不能支持的承诺。'
                    '只输出请求的小节，每个 index 一次，格式 {"sentences":[{"index":1,"text":"简短口播。"}]}。',
                    {'actualSeconds':round(seconds,2),'targetSeconds':target,'minUnits':lower,'maxUnits':upper,
                     'adjustment':f'当前稿 {planned} 字，目标约 {estimated_units} 字。'
                                  f'{"补充有依据的内容" if estimated_units > planned else "压缩重复和次要例子"}。加减字数集中在正文。',
                     'required':self.task.get('requirements',''),'preference':preferences[worker.row['index']],
                     'coverTitle':current['cover']['main_title'], 'feedback':feedback,
                     'media':{path:shot['description'] for path,shot in media.items()},
                     'lines':[dict(rows[index],previous=edits_by_index[index]['text']) for index in requested]}, max_tokens=3000)
                edits = proposal.get('sentences')
                if not isinstance(edits, list) or any(not isinstance(edit, dict) for edit in edits):
                    raise ValueError('每个指定小节都要返回新的 text。')
                updates = {edit['index']:edit for edit in edits if type(edit.get('index')) is int}
                if len(edits) != len(updates) or set(updates) != set(requested) or any(
                        not isinstance(edit.get('text'),str) or not edit['text'].strip() for edit in updates.values()):
                    raise ValueError('按指定序号逐项改写，保留每一行。')
                edits_by_index.update(updates)
                plan = revised_plan(current,list(edits_by_index.values()))
                for i, edit in edits_by_index.items():
                    path = edit.get('path')
                    if isinstance(path, str) and path in media and 1 < i < len(rows):
                        plan['sentences'][i - 1]['shots'] = [{
                            'path':path, 'sourceStart':0, 'weight':1,
                            'description':media[path]['description']}]
                counts = {i:script_units({'sentences':[row]}) for i,row in enumerate(plan['sentences'],1)}
                units = sum(counts.values())
                if not lower <= units <= upper:
                    # Keep the rows already close to the budget and return only
                    # the rows that most need to shrink or grow.
                    direction = 1 if units > upper else -1
                    requested = [i for i, n in counts.items() if direction * (n - rows[i]['goal']) > 1]
                    requested = requested or list(rows)
                    feedback = f'全稿实数 {units} 字，需 {lower}–{upper} 字。继续按各行 goal 字数修改。'
                    continue
                if not custom_copy:
                    refresh_description = refresh_description or any(word in feedback for word in ('publication', '发布描述', '发布文案'))
                    use_short_headline = use_short_headline or any(word in feedback for word in ('cover.main_title', '封面主'))
                    if refresh_description:
                        spoken = [row for row in plan['sentences'] if row.get('kind','fact') == 'fact']
                        if spoken:
                            plan['publication']['description'] = ''.join(row['text'] for row in spoken)
                            plan['publication']['factIds'] = list(dict.fromkeys(
                                ident for row in spoken for ident in row.get('factIds', [])))
                    if use_short_headline:
                        plan['cover']['main_title'] = {'text':plan['institution']['headline'],'highlights':[]}
                self.write(draft/'plan.json',plan)
                try:
                    omissions = outline_omission_limit(self.task.get('narrationPolicy'), len(outline))
                    review_context = {**context, 'outlineOmissionsAllowed': omissions,
                                      'preference': preferences[worker.row['index']]} if omissions else context
                    worker.review_candidate(draft, review_context, outline)
                except EditorialFeedback as exc:
                    worker.invoke('review-content',job=draft,extra=['--note',str(exc)])
                    worker.pipe.save_notices(draft,'content',[str(exc)])
                plan = self.read(draft/'plan.json')
                if not lower <= script_units(plan) <= upper:
                    raise ValueError('按已绑定片尾归一化后字数超标，请重新调整正文长度。')
                if self.task.get('narrationPolicy'):
                    previous = []
                    if self.task.get('planningRequired'):
                        for item in worker.app.queue.for_task(self.task['id']):
                            path = Path(item['job'])/'plan.json'
                            if item['index'] != worker.row['index'] and path.is_file():
                                previous.append(self.read(path))
                    worker.app.planning.check_narration(self.task,worker.row['index'],plan,previous,
                                                        budget={'targetSeconds':target,'minUnits':lower,'maxUnits':upper})
                break
            except ModelConnectionError:
                raise
            except ValueError as exc:
                feedback = worker.app.redact(str(exc))[:1000]
                flagged = {int(index)+1 for index in re.findall(r'sentences\[(\d+)\]',feedback)}
                requested = [i for i in rows if i in flagged] or list(rows)
        else:
            raise NeedsInput(f'配音实测 {seconds:.1f} 秒，本条要求 {duration_rule(target)}。自动改稿卡在以下复核问题，请确认可删减或替换的内容。{feedback}')

        worker.checkpoint()
        previous = digest(current)
        self.write(self.receipt, {'attempt':attempt,'target':target,'secondsBefore':seconds,
                                  'approvedPlan':prior.get('approvedPlan', previous),'previous':previous,
                                  'replacement':digest(plan),'applied':False})
        self.apply_pending()
