"""Independent FIFO coordinator for the existing production pipeline."""
import os
import re
from pathlib import Path
import threading
import time
import uuid

from auto_editor import Editor, EditorialFeedback, NeedsInput, PlanError, approved, source_evidence
from model_api import MODEL_OUTPUT_GUARD, ModelClient, ModelResponseError
from pipeline import Pipeline, PreparationTimeout, fingerprint
from planning import digest
from job_queue import BILLING_MESSAGE, is_minimax_balance_error
from duration import DurationRevision, budget_issue, initial_budget, script_units
from narration_timing import MINUTE_TARGET
from narration_editor import SIMILARITY_LIMIT, narration_issues, paragraphs, similarity_issue
from narration_prompts import COMPLETE_NARRATION_POLICY, outline_omission_limit, variations
from workflow import spoken_characters


class StageError(ValueError):
    pass


class LocalCoordinator:
    def __init__(self, app, read, write):
        self.app, self.read, self.write = app, read, write
        self.thread = None

    def tick(self):
        if self.app.workbench_host != 'local':
            return
        if self.thread and self.thread.is_alive():
            return
        self.app.queue.park_minimax_balance()
        head = self.app.queue.head() or {}
        owner = head.get('localExecution')
        if owner and not head.get('settled'):
            if process_alive(owner['pid']) or self.app.channel.has_active(head['id']):
                return
            evidence = '独立工作台进程和本阶段子进程均已退出，按已有检查点恢复'
            self.app.queue.recover(head['id'], head['lease'], evidence, local=owner)
        if not self.app.models.public()['configured']:
            return
        owner = {'id': uuid.uuid4().hex, 'pid': os.getpid()}
        row = self.app.queue.claim_next(local=owner)
        if row:
            self.thread = threading.Thread(target=self.run, args=(row,), daemon=True)
            self.thread.start()

    def run(self, row):
        queue = self.app.queue
        ident, lease = row['id'], row['lease']
        worker = Production(self.app, row, self.read, self.write)
        try:
            queue.accept(ident, lease, row['localExecution']['id'])
            worker.execute()
        except NeedsInput as exc:
            note = self.app.redact(str(exc))[:1200]
            worker.terminal('needs_input', '本条需要你补充或核对', note)
        except Exception as exc:
            worker.terminal('failed', self.app.redact(str(exc))[:1000])
        finally:
            queue.settle(ident, lease, local=row['localExecution'])


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Production:
    def __init__(self, app, row, read, write):
        self.app, self.row, self.read, self.write = app, row, read, write
        self.item, self.lease = row['id'], row['lease']
        self.job = Path(row['job'])
        self.pipe = Pipeline(app, read, write)
        self.editor = None

    def status(self):
        return self.app.queue.item(self.item)

    def checkpoint(self):
        self.app.queue.expire_head()
        row = self.status()
        if row['status'] == 'stopping':
            self.app.queue.report(self.item, self.lease, min(5, row['phase']), 'running', '已到停止检查点')
        self.pipe.row(self.item, self.lease)

    def time_budget(self, maximum):
        self.checkpoint()
        return max(.001, min(maximum, self.status().get('deadline', float('inf')) - time.time()))

    def terminal(self, status, note, question=None):
        row = self.status()
        if row['status'] not in ('running', 'stopping'):
            return
        try:
            self.app.queue.report(self.item, self.lease, min(5, row['phase']), status, note, question)
        except ValueError:
            if self.status()['status'] not in ('cancelled', 'timed_out'):
                raise

    def progress(self, message):
        row = self.status()
        self.app.queue.report(self.item, self.lease, min(5, row['phase']), 'running', message)

    def invoke(self, action, *, job=None, cli_args=None, extra=None):
        for attempt in range(2):
            self.checkpoint()
            try:
                code, result = self.pipe.invoke(self.row, self.lease, action, extra=extra, job=job, cli_args=cli_args)
                break
            except PreparationTimeout:
                if attempt or action != 'inventory':
                    raise
                self.progress('素材准备超时，正在从现有文件自动重试（1/1）')
        if code:
            raise StageError(str(result.get('error') or result))
        return result

    def inventory(self, task, folder):
        if not (folder / 'inventory.json').is_file():
            self.progress('正在整理机构素材')
            self.invoke('inventory', job=folder, cli_args=['inventory', task['source'], '--out', folder])
        if task.get('facts'):
            self.app.planning.append_facts(task, folder)

    def execute(self):
        client = self.app.models.client()
        if isinstance(client, ModelClient):
            client.time_budget = self.time_budget
            client.on_retry = self.progress
        self.editor = Editor(self.app.skill, client, self.read, self.write, self.checkpoint)
        self.editor.time_budget = self.time_budget
        task = self.app.task(self.row['taskId'])
        answer = {'question': self.row.get('answerQuestion'), 'reply': self.row.get('answer')}
        if self.row['kind'] == 'planning':
            self.plan_batch(task, answer)
            self.terminal('completed', '整批策划及逐条结构校验已通过')
            return
        self.prepare_video(task, answer)
        duration = DurationRevision(self, task, answer)
        duration.apply_pending()
        if task.get('narrationPolicy') and not self.read(duration.receipt,{}).get('applied'):
            try:
                self.app.planning.check_narration(task,self.row['index'],self.read(self.job/'plan.json'))
            except ValueError as exc:
                raise NeedsInput(str(exc)) from exc
        while True:
            result = self.pipe.advance(self.item, self.lease)
            state = result['state']
            if state == 'completed':
                return
            if state == 'needs_review':
                self.review(result['gate'], answer)
            elif state == 'revise_duration':
                duration.revise(result['duration'], result['target'])
            elif state == 'failed':
                detail = result.get('result', {})
                if result['step'] == 'voice' and is_minimax_balance_error(detail.get('error')):
                    self.terminal('billing_hold', BILLING_MESSAGE)
                    return
                raise ValueError(f"{result['step']} 阶段暂停：" + str(detail.get('error') or detail))
            elif state not in ('checkpoint', 'pending'):
                raise ValueError('制作阶段返回了未知状态。')

    def plan_batch(self, task, answer):
        folder, input_fingerprint = self.app.planning.initialize(task)
        if (folder / 'approved.json').is_file():
            self.app.planning.verify(task)
            return
        self.inventory(task, folder)
        complete = task.get('narrationPolicy') == COMPLETE_NARRATION_POLICY
        if complete:
            answer = {'reply': answer.get('reply')}
        self.progress('正在整理机构原文' if complete else '正在观察这批实拍素材')
        context = self.editor.context(task, folder, answer, observe=not complete)
        cache_path = folder / 'independent-planning.json'
        evidence = source_evidence(context['sources'])
        identity = digest({'input': input_fingerprint, 'answer': answer.get('reply') if complete else answer,
                           'media': context.get('media', []),
                           'editorialVersion': 11 if complete else 8 if task.get('narrationPolicy') else 7})
        cache = self.read(cache_path, {})
        if cache.get('input') != identity:
            self.progress('正在逐段核对全文必讲要点' if complete else '正在结合实拍与原文确定共同提纲')
            outline = self.editor.complete_outline if complete else self.editor.batch_outline
            cache = {'input': identity, 'facts': outline(context, evidence), 'plans': []}
            self.write(cache_path, cache)
        facts = cache['facts']
        if complete:
            for index in range(task['count']):
                self.checkpoint()
                self.draft_spoken_plan(task, index, context, facts, evidence, None, (), folder, draft_only=True)
        items = []
        for index in range(task['count']):
            self.checkpoint()
            path = folder / 'plans' / f'{index + 1:04d}.json'
            if index < len(cache['plans']):
                item = cache['plans'][index]
                if path.is_file() and item['sha256'] == fingerprint(path):
                    items.append(item['item'])
                    continue
                cache['plans'] = cache['plans'][:index]
            self.progress(f'整批策划：正在编排第 {index + 1}/{task["count"]} 条')
            def validate(plan, review):
                return self.app.planning.validate_item(task, folder, index, plan, review)
            plan, angles = self.draft_with_feedback(task, index, context, facts, evidence, validate,
                                                    items, folder)
            self.write(path, plan)
            item = {**angles, 'index': index, 'plan': str(path.relative_to(folder)), 'person': plan['person']}
            cache['plans'].append({'sha256': fingerprint(path), 'item': item})
            self.write(cache_path, cache)
            items.append(item)
        omission_limit = outline_omission_limit(task.get('narrationPolicy'), len(facts))
        coverage_rule = (f'综合稿允许每条漏讲最多 {omission_limit} 个普通提纲要点。'
                         '用户明确要求的内容必须保留，不能因为普通漏项在容差内而拒绝。'
                         if omission_limit else '综合稿要求覆盖共同核心。')
        review = self.editor.ask(
            '审核整批事实和明确的用户要求。' + coverage_rule + '专题需能引用用户授权。'
            '完全重复由程序检查。措辞、切入差异程度或顺序的优化意见写入 differences，不能据此拒绝整批。'
            '按 JSON {"ok":true,"note":"真实判断","facts":"事实范围","differences":"差异检查","preferences":"偏好检查"}。'
            '逐条真实性与文画还将在每条付费前另行核验。',
            {'facts': facts, 'items': items, 'requirements': task.get('requirements', ''),
             'preferences': task.get('videoPreferences', [])}, max_tokens=2500)
        approved(review)
        draft = {'inputFingerprint': input_fingerprint, 'items': items, 'review': review,
                 'coreFacts': [{'id': x['id'], 'text': x['text'], 'evidence': {k: x[k] for k in ('source', 'quote')}} for x in facts]}
        self.write(folder / 'draft.json', draft)
        try:
            self.app.planning.check(task, lambda job: self.invoke('validate-plan', job=job))
        except Exception:
            # A fresh request may repair structurally invalid proposals.
            cache_path.unlink(missing_ok=True)
            raise

    def draft_with_feedback(self, task, index, context, facts, evidence, validate, items=(), folder=None):
        if task.get('narrationPolicy') and not task.get('revisedNarration'):
            return self.draft_spoken_plan(task, index, context, facts, evidence, validate, items, folder)

        previous_plans = [self.read(folder / item['plan']) for item in items]
        previous_scripts = {tuple(row['text'] for row in plan['sentences']) for plan in previous_plans}
        angle_keys = ('openingAngle', 'structure', 'coverAngle')
        previous_angles = {tuple(item[key] for key in angle_keys) for item in items}
        target = task.get('targetDurationSeconds')
        length = initial_budget(target) if target is not None and not task.get('revisedNarration') else None
        preferences = task.get('videoPreferences') or [''] * task['count']
        feedback, candidate, fallback = '', None, None
        for attempt in range(5):
            try:
                plan, angles = self.editor.plan(task, index, context, facts, items[-12:], feedback,
                                                evidence=evidence, candidate=candidate,
                                                reference_plan=previous_plans[-1] if items else None)
                candidate = {**angles, 'plan': {key: val for key, val in plan.items() if key != 'facts'}}
                if tuple(angles[key] for key in angle_keys) in previous_angles:
                    raise PlanError('这条与前序口播或切入完全重复。请保留所有核心事实，改换开场和顺序。')

                def prepare(job):
                    nonlocal candidate
                    current = self.read(job/'plan.json')
                    if length:
                        units = script_units(current)
                        if units > length['maxUnits'] and self.editor.shorten_narration(
                                current, length, facts, preferences[index],
                                may_drop_optional=target == MINUTE_TARGET and not (task.get('requirements') or preferences[index])):
                            self.write(job/'plan.json', current)
                            self.invoke('validate-plan', job=job)
                            current = self.read(job/'plan.json')
                        candidate = {**angles, 'plan': {key: val for key, val in current.items() if key != 'facts'}}
                        issue = budget_issue(script_units(current), length)
                        if issue:
                            raise PlanError(issue, candidate)
                    if tuple(row['text'] for row in current['sentences']) in previous_scripts:
                        raise PlanError('这条与前序口播重复，请在保留共同事实的前提下调整开场和叙述顺序。')

                def review(job):
                    nonlocal fallback
                    try:
                        try:
                            self.review_candidate(job, context, facts, prepare=prepare)
                        except PlanError as exc:
                            if not length:
                                raise
                            current = self.read(job/'plan.json')
                            if not self.editor.repair_review_sentences(current, context, facts,
                                                                       str(exc), preferences[index]):
                                raise
                            self.write(job/'plan.json', current)
                            self.review_candidate(job, context, facts, prepare=prepare)
                    except EditorialFeedback as exc:
                        checked = self.read(job/'plan.json')
                        if not length or not budget_issue(script_units(checked), length):
                            fallback = (checked, angles, self.app.redact(str(exc))[:1000])
                        raise
                return validate(plan, review), angles
            except (PlanError, StageError, ModelResponseError, EditorialFeedback, NeedsInput) as exc:
                if isinstance(exc, PlanError) and exc.candidate:
                    candidate = exc.candidate
                feedback = self.app.redact(str(exc))[:1000]
                if isinstance(exc, NeedsInput):
                    feedback = '略过材料未提及的次要细节，按已核验的原文完成。' + feedback
            if attempt < 4:
                self.progress(f'正在修正第 {index + 1}/{task["count"]} 条的文案与分镜（{attempt + 1}/4）')
        if fallback is None:
            raise ValueError(f'第 {index + 1} 条已尝试 5 稿，字数、事实或分镜仍未通过复核。{feedback}')
        plan, angles, note = fallback
        notice = '文案试写 5 轮后，采用最近一份通过事实和分镜核验的稿件。待验收建议：' + note
        def record_fallback(job):
            self.invoke('review-content', job=job, extra=['--note', notice])
            self.pipe.save_notices(job, 'content', [notice])
        validated = validate(plan, record_fallback)
        self.progress(f'第 {index + 1} 条已保留文案优化提醒，继续制作')
        return validated, angles

    def draft_spoken_plan(self, task, index, context, facts, evidence, validate, items, folder, *, draft_only=False):
        preference = (task.get('videoPreferences') or [''] * task['count'])[index]
        budget = self.app.planning.narration_budget(task, index)
        prior = [paragraphs(self.read(folder/item['plan'])) for item in items]
        receipt = (folder or self.job)/f'narration-draft-{index + 1:04d}.json'
        log = self.read(receipt, {'firstReturn':None,'repairs':0})
        complete = task.get('narrationPolicy') == COMPLETE_NARRATION_POLICY
        if complete and log.get('coreFacts') and log['coreFacts'] != facts:
            history = log.pop('previousOutlines', [])
            log = {'firstReturn': None, 'repairs': 0, 'previousOutlines': [*history, log]}
        log['coreFacts'] = facts
        feedback = ''
        variation = variations(facts, task['id'], task['count'])[index] if complete else None

        def remember(system, user):
            log.setdefault('requests', []).append({
                'system': system + MODEL_OUTPUT_GUARD, 'user': user,
            })
            self.write(receipt, log)

        prompt_options = {'variation': variation, 'remember': remember} if complete else {}
        if complete:
            log['variation'] = variation

        def paragraph_sizes(parts):
            return [script_units({'sentences': [{'text': text}]}) for text in parts]

        def record_attempt(stage, parts):
            sizes = paragraph_sizes(parts)
            attempt = {'stage': stage, 'units': sum(sizes), 'paragraphUnits': sizes}
            if complete:
                attempt.update(paragraphs=list(parts), issues=issues_for(parts)[0])
            log.setdefault('attempts', []).append(attempt)
            self.write(receipt, log)

        def issues_for(parts):
            style = narration_issues(parts)
            notes = list(style.values())
            positions = set(style)
            name = spoken_characters(context['institutionName'])
            if complete and name:
                mentions = [index for index, part in enumerate(parts) if name in spoken_characters(part)]
                if not mentions or mentions[0] != 0:
                    notes.append('开头需要自然说出机构全名。')
                    positions.add(0)
                if sum(spoken_characters(part).count(name) for part in parts) > 1:
                    notes.append('机构全名只说一次，保留开头的介绍。')
                    positions.update(index for index in mentions[1:])
                    if len(mentions) == 1 and mentions[0] == 0:
                        positions.add(0)
            sizes = paragraph_sizes(parts)
            total = sum(sizes)
            if issue := budget_issue(total, budget):
                goal = (budget['minUnits'] + budget['maxUnits']) // 2
                notes.append(f'{issue} 全稿目标约 {goal} 字，正文约{"补" if goal > total else "删"} {abs(goal-total)} 字。')
                middle = range(1, len(parts) - 1)
                count = len(middle) if abs(goal-total) > 28 else 2
                if complete and abs(total - goal) <= 24:
                    if total > goal and sizes[0] > budget['sections']['hook'] + 8:
                        positions.add(0)
                    else:
                        positions.add(max(middle, key=sizes.__getitem__))
                else:
                    positions.update(sorted(middle, key=sizes.__getitem__,reverse=total > goal)[:count])
                    if complete and total > goal:
                        if sizes[0] > budget['sections']['hook'] + 8:
                            positions.add(0)
                        if sizes[-1] > budget['sections']['cta']:
                            positions.add(len(sizes) - 1)
            for previous in prior:
                match = similarity_issue(parts[:-1], previous, context['institutionName'])
                if match:
                    similarity, repeated = match
                    positions.update(repeated or range(0, len(parts) - 1))
                    sections = '、'.join(str(i + 1) for i in repeated)
                    notes.append(f'正文和本批已有稿的相似度为 {similarity:.1f}%，达到 {SIMILARITY_LIMIT}%。'
                                 f'重新组织第 {sections or "已指定"} 段的句式和衔接，保持事实和顺序。')
            return notes, sorted(positions)

        def generate(stage):
            parts = self.editor.draft_narration(context, facts, preference, budget, index, feedback, **prompt_options)
            if log['firstReturn'] is None:
                issues, _ = issues_for(parts)
                log['firstReturn'] = {'paragraphs': list(parts), 'issues': issues, 'passed': False}
            record_attempt(stage, parts)
            return parts

        ready = next((attempt.get('paragraphs') for attempt in reversed(log.get('attempts', []))
                      if attempt.get('paragraphs') and not issues_for(attempt['paragraphs'])[0]), None) if complete else None
        first = log['firstReturn']['paragraphs'] if complete and len(log.get('attempts', [])) == 1 else None
        if draft_only:
            self.progress(f'正在写并核对第 {index + 1}/{task["count"]} 条独立口播')
        for generation in range(2):
            if generation == 0 and (ready or first):
                parts = ready or first
            else:
                try:
                    parts = generate('first_draft' if generation == 0 else 'regeneration')
                except (PlanError, ModelResponseError) as exc:
                    feedback = str(exc)
                    continue
            repairs = 2 if generation == 0 or complete else 0
            for repair in range(repairs + 1):
                notes, targets = issues_for(parts)
                if not notes and draft_only:
                    log['textReadyUnits'] = sum(paragraph_sizes(parts))
                    self.write(receipt, log)
                    return
                if not notes:
                    candidate = None
                    for _ in range(2):
                        try:
                            if 'media' not in context:
                                self.progress('口播字数已通过，正在观察素材和配镜头')
                                context['media'] = self.editor.media(folder or self.job, self.read((folder or self.job)/'inventory.json'))
                            spoken = parts
                            if complete:
                                spoken = [parts[0], *(line.strip() for paragraph in parts[1:-1]
                                          for line in re.split(r'(?<=[。！？；])', paragraph) if line.strip()), parts[-1]]
                            plan, angles = self.editor.plan(task,index,context,facts,[],feedback,
                                                            evidence=evidence,candidate=candidate,spoken=spoken)
                            candidate = {'plan':plan,**angles}

                            def review(job):
                                nonlocal parts
                                def verify_copy(prepared):
                                    nonlocal parts
                                    sealed = self.read(prepared/'plan.json')['sentences']
                                    parts = [row['text'] for row in sealed]
                                    errors, _ = issues_for(parts)
                                    if errors:
                                        raise PlanError('。'.join(errors))
                                try:
                                    review_context = context
                                    if complete:
                                        review_context = {**context, 'preference': preference,
                                                          'outlineOmissionsAllowed': outline_omission_limit(
                                                              task.get('narrationPolicy'), len(facts))}
                                    self.review_candidate(job,review_context,facts,prepare=verify_copy)
                                except EditorialFeedback as exc:
                                    self.invoke('review-content',job=job,extra=['--note',str(exc)])
                                    self.pipe.save_notices(job,'content',[str(exc)])

                            accepted = validate(plan,review)
                            log['firstReturn']['passed'] = not generation and not repair and not log['firstReturn']['issues']
                            log['submittedUnits'] = script_units(accepted)
                            self.write(receipt,log)
                            return accepted, angles
                        except (PlanError, StageError, ModelResponseError, NeedsInput) as exc:
                            feedback = self.app.redact(str(exc))[:1100]
                            if complete:
                                log.setdefault('planningIssues', []).append(feedback)
                                self.write(receipt, log)
                            if isinstance(exc, PlanError) and exc.candidate:
                                candidate = exc.candidate
                            notes, targets = issues_for(parts)
                            if notes:
                                break
                    if not notes:
                        flagged = sorted({int(n) for n in re.findall(r'sentences\[(\d+)\]',feedback)
                                          if int(n) < len(parts)})
                        if flagged:
                            notes, targets = [feedback], flagged
                        elif complete and '口播缺少共同事实' in feedback:
                            missing = set(re.findall(r'\bf\d+\b', feedback))
                            details = '；'.join(row['text'] for row in facts if row['id'] in missing)
                            notes = [feedback, '务必实际念到：' + details + '。可替换开业年份、规模或其他次要介绍。']
                            targets = list(range(len(parts) - 1))
                feedback = '。'.join(notes) or feedback
                if repair == repairs or not targets:
                    break
                self.progress(f'第 {index + 1} 条正在定点返修口播（{repair + 1}/2）')
                try:
                    parts = self.editor.revise_narration(parts, targets, notes, context, facts, budget, preference,
                                                         **prompt_options)
                except (PlanError, ModelResponseError) as exc:
                    feedback = str(exc)
                    break
                log['repairs'] += 1
                record_attempt('local_repair', parts)
        if draft_only:
            log['draftIssue'] = feedback
            self.write(receipt, log)
            return
        raise NeedsInput(f'第 {index + 1} 条口播仍有问题，请确认对应事实或可调整的内容。{feedback}')

    def review_candidate(self, job, context, outline=(), *, prepare=None):
        self.invoke('validate-plan', job=job)
        if prepare:
            prepare(job)
        plan = self.read(job / 'plan.json')
        audit = self.read(job / 'content-audit.json', {})
        if self.editor.repair_numeric_references(plan, audit):
            self.write(job / 'plan.json', plan)
            self.invoke('validate-plan', job=job)
            plan = self.read(job / 'plan.json')
            audit = self.read(job / 'content-audit.json', {})
        try:
            note = self.editor.review_plan(context, plan, audit, outline=outline)
        except NeedsInput as exc:
            raise PlanError(str(exc)) from exc
        self.invoke('review-content', job=job, extra=['--note', note])
        self.pipe.save_notices(job, 'content', [])

    def prepare_video(self, task, answer):
        plan_path = self.job / 'plan.json'
        if (self.job / 'runtime.json').is_file() or (plan_path.is_file() and task.get('planningRequired')):
            return
        marker = self.job / 'independent-plan.json'
        if plan_path.is_file():
            if not marker.exists():
                return  # An earlier approved or manually authored plan.
            plan_hash = fingerprint(plan_path)
            if plan_hash in (self.read(marker, {}).get('validated'), self.read(self.job / 'pipeline.json', {}).get('plan')):
                return
        is_revision = task.get('kind') == 'revision' and not task.get('revisionFromArchive')
        folder = self.app.data / 'queue-workers' / self.item / 'revision-input' if is_revision else self.job
        self.inventory(task, folder)
        complete = task.get('narrationPolicy') == COMPLETE_NARRATION_POLICY and not is_revision
        if complete:
            answer = {'reply': answer.get('reply')}
        context = self.editor.context(task, folder, answer, observe=not complete)
        evidence = source_evidence(context['sources'])
        if complete:
            outline_path = self.job / 'narration-outline.json'
            identity = digest(context)
            outline = self.read(outline_path, {})
            if outline.get('input') != identity:
                outline = {'input': identity, 'facts': self.editor.complete_outline(context, evidence)}
                self.write(outline_path, outline)
            facts = outline['facts']
        else:
            facts = self.editor.batch_outline(context, evidence)
        if is_revision:
            plan, _ = self.editor.plan(task, self.row['index'], context, facts, [], evidence=evidence)
            revised = folder.parent / 'revised-plan.json'
            self.write(revised, plan)
            self.invoke('revise', cli_args=['revise', task['baseJob'], '--out', self.job, '--plan', revised])
            self.app.planning.append_facts(task, self.job)
            self.invoke('validate-plan')
            return

        def validate(plan, review):
            self.write(marker, {'generated': True})
            self.write(plan_path, plan)
            review(self.job)
            return self.read(plan_path)
        self.draft_with_feedback(task, self.row['index'], context, facts, evidence, validate)
        self.write(marker, {'validated': fingerprint(plan_path)})

    def review(self, gate, answer):
        name = gate.removesuffix('-review')
        self.progress('正在审核' + {'content': '文案和分镜', 'provider': '数字人原片',
                                     'layout': '封面和排版', 'final': '成片检查帧'}[name])
        if name == 'content':
            self.pipe.review(self.item, self.lease, name, self.editor.content_review(self.job, answer))
            return
        if name == 'provider':
            frames = []
            for avatar in self.read(self.job / 'avatars.json'):
                source = self.read(self.job / 'assets' / ('avatar-' + avatar['id']) / 'generation-source.json')
                frames.extend(source['visualReviewFrames'])
            instructions = (
                '审核数字人原片抽帧的嘴部、脸、手、构图、水印和明显变形。'
                '单帧闭眼可以是说话时的自然眨眼，单凭这一帧不可判为异常。'
                '持续闭眼、动作是否僵硬和口型是否同步需要连续画面判断。'
                '仅根据静态帧中实际可见的显著缺陷判定失败。'
            )
        elif name == 'layout':
            frames = sorted((self.job / 'qc-layout').glob('*.png'))
            instructions = '查看实际封面、正文、字幕与分镜。检查文字是否裁切、遮挡、可读，画面是否与内容协调。'
        else:
            frames = self.read(self.job / 'verification.json')['renderedReviewFrames']
            instructions = '核查所有实际成片帧，包括封面首帧、正文次帧、切镜与字幕差异。发现黑屏、错人、裁切或遮挡应拒绝。'
        findings = []
        try:
            evidence, note = self.editor.review_frames(self.job, frames, instructions, answer, findings=findings)
        except (OSError, ValueError) as exc:
            self.checkpoint()
            self.pipe.visual_advice(self.item, self.lease, name, findings,
                                    unavailable=self.app.redact(str(exc))[:600])
        else:
            self.pipe.review(self.item, self.lease, name, note, evidence, findings=findings)
