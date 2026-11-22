"""Structured editing and vision calls. The model cannot execute local tools."""
from collections import Counter
from copy import deepcopy
import math
from pathlib import Path
import re
import subprocess

from model_api import ModelResponseError, image_part
from duration import initial_budget, script_units
from narration_timing import MINUTE_TARGET
from narration_editor import VOICE_STYLE


NARRATOR_CONTEXT = (
    '任务中已选的人物是已配置的数字人解说角色。仅用机构资料和实拍做介绍，'
    '无需补充解说人的现实身份、机构关系、到访日期、实际行程或授权信息。'
    '未提供的非必需信息直接略过，只有缺少完成用户必需要求的资料才提问。'
)


NARRATION_OUTLINE = (
    '先照着共同提纲写口播，综合稿每条都要实际讲全共同要点。'
    'show 是可以展开的重点，只展开确认能直接呈现的细节或具体例子。'
    'brief 是重要但缺少直接画面的内容，一句话带过，相关院内场景只作背景。'
    '展柜不能当作照护方案的实拍，缺少画面时省去该方案的工具、操作和效果细节。'
    '区位可紧随院名简短交代。地铁线路、地址等补充细节按各条切入取舍，'
    '对家属探望有用时可在一句内说具体，不套用同一句。'
    '同批各条通过开头、叙述顺序、句式和少量例子区分。'
)


def narration_length_instruction(budget):
    lower, upper = budget['minUnits'], budget['maxUnits']
    target = (lower + upper) // 2
    return (
        f'全文含开头、正文和结尾，必须在 {lower}–{upper} 个可读字之间，目标 {target} 字。'
        '标点和空格不计，数字按实际读法计。'
    )


class NeedsInput(ValueError):
    pass


class EditorialFeedback(ValueError):
    """The facts, requirements and visuals passed; the remaining issue is stylistic."""


class PlanError(ValueError):
    def __init__(self, message, candidate=None):
        super().__init__(message)
        self.candidate = candidate


def chunks(items, count):
    for start in range(0, len(items), count):
        yield items[start:start + count]


def source_evidence(sources):
    evidence = {}
    for source, text in sources.items():
        for quote in re.findall(r'[^。！？\r\n]+[。！？]*', text):
            if quote.strip():
                evidence[f'e{len(evidence) + 1}'] = {'source': source, 'quote': quote.strip()}
    return evidence


def frame(path, second=None, *, timeout=45):
    seek = ['-ss', str(second)] if second is not None else []
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *seek, '-protocol_whitelist', 'file,pipe',
                             '-i', str(path), '-frames:v', '1',
                             '-vf', 'scale=768:768:force_original_aspect_ratio=decrease',
                             '-f', 'image2pipe', '-c:v', 'mjpeg', '-threads', '1', '-q:v', '4', 'pipe:1'],
                            capture_output=True, timeout=timeout)
    if result.returncode or not result.stdout.startswith(b'\xff\xd8'):
        raise ValueError('无法读取媒体检查帧，请确认本地素材可正常播放。')
    return result.stdout


class Editor:
    def __init__(self, skill, client, read, write, checkpoint):
        self.skill, self.client = skill, client
        self.read, self.write, self.checkpoint = read, write, checkpoint
        self.time_budget = lambda maximum: maximum

    def read_frame(self, path, second=None):
        return frame(path, second, timeout=self.time_budget(45))

    def rules(self, *names):
        return '\n\n'.join((self.skill / 'references' / (name + '.md')).read_text(encoding='utf-8')
                           for name in names)

    def ask(self, instruction, data, visuals=(), max_tokens=2400):
        self.checkpoint()
        images = []
        for label, data_bytes in visuals:
            images.extend([{'type': 'text', 'text': label}, image_part(data_bytes)])
        result = self.client.ask(instruction, data, images, max_tokens)
        self.checkpoint()
        return result

    def media(self, folder, inventory):
        root = Path(inventory['source']).resolve()
        visual = [x for x in inventory['files'] if x['kind'] in ('image', 'video')]
        cache = folder / 'independent-media-review.json'
        stored = self.read(cache, {})
        candidates = {f'M{i}': row for i, row in enumerate(visual, 1)}
        frames = {}

        def observe(ids):
            visuals = []
            for ident in ids:
                row = candidates[ident]
                if ident not in frames:
                    p = (root / row['path']).resolve()
                    if not p.is_relative_to(root):
                        raise ValueError('素材路径越出了所在机构目录。')
                    times = (.2 * row['duration'], .65 * row['duration']) if row['kind'] == 'video' else (None,)
                    frames[ident] = [(f'{ident} 图 {index}', self.read_frame(p, time)) for index, time in enumerate(times, 1)]
                visuals.extend(frames[ident])
            try:
                response = self.ask(
                    '按素材短编号观察实图。每个编号返回一条可见内容，视频对照两个时点描述可见运动。'
                    '不凭图片推断护理水平或身份。JSON {"media":[{"id":"M1","description":"可见细节","motion":true}]}。',
                    {'media': {i: candidates[i]['kind'] for i in ids}}, visuals)
            except ModelResponseError:
                return
            items = response.get('media')
            if not isinstance(items,list):return
            observed = set()
            for item in items:
                if not isinstance(item,dict):continue
                ident,note = item.get('id'),item.get('description')
                if not isinstance(ident,str) or ident not in ids or ident in observed or not isinstance(note,str) or not note.strip():continue
                row = candidates[ident]
                stored[row['sha256']] = {'description':note.strip()[:700], 'motion':row['kind']=='video' and item.get('motion') is True}
                observed.add(ident)
            if observed:self.write(cache,stored)

        for group in chunks(list(candidates),4):
            missing = [i for i in group if candidates[i]['sha256'] not in stored]
            if missing:observe(missing)
            for ident in missing:
                if candidates[ident]['sha256'] not in stored:observe([ident])
        result = [{k:x[k] for k in ('path','kind','duration','width','height')} | stored[x['sha256']]
                  for x in visual if x['sha256'] in stored]
        if not any(x['kind'] == 'video' and x['motion'] for x in result):
            raise NeedsInput('当前素材观察未找到适合开头和结尾的移动视频，请补充实拍移动素材或说明哪段可用。')
        return result

    def context(self, task, folder, answer):
        inventory = self.read(folder / 'inventory.json')
        sources = {x['path']: x['text'] for x in inventory['files'] if x['kind'] == 'text'}
        target = task.get('targetDurationSeconds')
        return {
            'sources': sources, 'media': self.media(folder, inventory),
            'institutionName': task.get('institutionName') or '',
            'requirements': task.get('requirements', ''), 'answer': answer,
            'durationBudget': initial_budget(target) if target is not None else {},
        }

    def batch_outline(self, context, evidence):
        data = {k: v for k, v in context.items() if k not in ('sources', 'media')}
        minute = context.get('durationBudget', {}).get('targetSeconds') == MINUTE_TARGET
        compact = minute and not context.get('requirements')
        observed = {f'M{i}': {k: shot[k] for k in ('path', 'kind', 'description')}
                    for i, shot in enumerate(context['media'], 1)}
        schema = ('返回 JSON {"facts":[{"text":"一个共同要点","evidenceId":"e1",'
                  '"treatment":"show","mediaIds":["M1"],"visibleDetail":"从观察记录照抄的字句"}],"question":null}。')
        constraints = (
            '共同提纲涵盖照护、居住和院内生活。原文明确区分照护分区时，先把分区和本院的特色照护纳入共同提纲。'
            '逐项写成一个简短要点，综合稿每条都要讲全。'
            'show 只写画面直接呈现且原文支持的细节，列出对应素材编号，并从其中一份 description 原样复制一段 visibleDetail。'
            'text 必须由 evidenceId 对应的原句支持。文字只说适老化设施时，具体扶手等实拍例子只写在 visibleDetail。'
            '画面只支持一部分时，show 必须删去没拍到的并列内容。比如步道镜头可讲院内步道，不能带出未确认的空中花园。'
            '缺少直接画面但重要的事实标 brief，只用一句交代，少列细节。背景素材可以留空。'
            '服务方案、分区安排、服务时长和就医保障通常只在文字中有依据。'
            '普通展柜和相框不能确定是怀旧展区或记忆工具箱，普通房间不能确定属于某个照护分区，'
            '康复器械不能证明有医务室或绿色就医通道。'
            '地点只引出机构，不列为展开点。开业年份、规模、交通和数字清单通常不作为共同重点，由逐条按切入取舍。'
            '有辨识度的照护方案属于可简述的核心；没有方案画面时，只录名称和照护类型，省去工具、方法和效果。'
            '从 evidence 选择实际原文编号，不自造原文。durationBudget.sections.body 是每条正文的可读字数。'
            '先用两到三个短段分配正文容量，合并照护分区和特色照护等能同段交代的要点。'
            '优先保留用户必讲和实拍能说明的事实，把次要例子留在 supportingEvidence，避免共同提纲越来越长。'
        )
        data.update(evidence=evidence, observedMedia=observed)
        if compact:
            constraints += (
                '此批每篇至少 3 项、最多 5 项，每项只写一件事。共同事实要写成约 20 字的编辑提示，'
                '保留三类照护分区、机构特色照护、居室和公共生活中最有依据的内容。'
                '一句话里列两种以上设备、服务方法或设施性质的，一律改为有直接依据的一个例子。')
        feedback = ''
        for _ in range(3):
            result = self.ask(
                NARRATOR_CONTEXT + '先看 observedMedia 实际拍到了什么，再结合 evidence 选取三到六个共同要点。'
                + constraints + '资料不足时说明具体缺项。' + schema,
                {**data, 'feedback': feedback})
            try:
                self.validate_outline(result, evidence, observed)
                checked = self.ask(
                    '逐项核对 candidateFacts 对应的原文和实拍。若标为 show，审核素材观察文字是否真的能看到该要点，'
                    '可只保留有画面的部分；必须交代但镜头不直接支持的事实改为 brief；'
                    '次要、臃肿、重复或缺乏依据的条目删除。通常保留一篇能覆盖的三到六项共同核心，用户明确要求的要点照常保留。'
                    + constraints + '输出修正后的完整列表。' + schema,
                    {**data, 'candidateFacts': result['facts']})
                facts = self.validate_outline(checked, evidence, observed)
                if compact and len(facts) > 5:
                    raise PlanError('一分钟正文最多放 5 个共同事实。请合并最相关的项或删去次要项。')
                return facts
            except PlanError as exc:
                feedback = str(exc)
        question(result)
        raise ValueError('共同事实提炼未通过原文及对应素材校验。' + feedback)

    @staticmethod
    def validate_outline(result, evidence, observed):
        facts = result.get('facts')
        if not isinstance(facts, list) or not 1 <= len(facts) <= 24:
            raise PlanError('请在 facts 中列出实际可核对的共同事实。')
        output = []
        for i, fact in enumerate(facts, 1):
            if not isinstance(fact, dict):
                raise PlanError(f'第 {i} 项必须填写事实、原文编号和素材关系。')
            text, key = fact.get('text'), fact.get('evidenceId')
            if not isinstance(text, str) or not text.strip() or not isinstance(key, str) or key not in evidence:
                raise PlanError(f'第 {i} 项必须包含非空的事实内容和 evidence 中实际存在的 evidenceId。')
            treatment, media_ids = fact.get('treatment'), fact.get('mediaIds')
            if (treatment not in ('show', 'brief') or not isinstance(media_ids, list)
                    or any(not isinstance(ident, str) or ident not in observed for ident in media_ids)
                    or (treatment == 'show' and not media_ids)):
                raise PlanError(f'第 {i} 项需要标注 show 或 brief，并使用 observedMedia 的编号。'
                                'show 至少要有一个直接相关的素材；缺少画面的重要事实用 brief，可留空素材。')
            detail = ''
            if treatment == 'show':
                detail = fact.get('visibleDetail')
                if not isinstance(detail, str) or not detail.strip() or not any(
                    detail.strip() in observed[ident]['description'] for ident in media_ids
                ):
                    raise PlanError(f'第 {i} 项的 visibleDetail 需要从该项选定素材的观察描述中原样摘录。')
                detail = detail.strip()
            paths = list(dict.fromkeys(observed[ident]['path'] for ident in media_ids))
            output.append({'id': f'f{i}', 'text': text.strip(), **evidence[key],
                           'treatment': treatment, 'mediaPaths': paths, 'visibleDetail': detail})
        return output

    def rebind_fact_references(self, sentences, references, candidate, *, editable=False, omit_unsupported=False):
        affected = {str(i):s for i,s in enumerate(sentences,1)
                    if any(ident not in references for ident in s.get('factIds',[]))
                    or (s.get('kind','fact')=='fact' and not s.get('factIds'))}
        if not affected:return
        response = self.ask(
            '只修复这些句子的事实引用。逐句选择能直接支持其中全部事实及限定词的原文编号。'
            '只能使用 evidence 中现有的编号，正文核心要点优先使用对应的 f 编号。'
            '允许修改文案时，如果原文只能支持句中的部分内容，在 rewrites 中给出简短的修订句，'
            '保留能证实的内容和原有主题。references 填修订句对应的编号。不能验证时填空数组。'
            '返回 {"references":{"句号":["原文编号"]},"rewrites":{"句号":"修订句"}}，使用输入的数字键。',
            {'sentences':{i:s['text'] for i,s in affected.items()},'evidence':references,'mayRewrite':editable},max_tokens=3000)
        bindings = response.get('references')
        rewrites = response.get('rewrites', {})
        if not isinstance(bindings,dict) or bindings.keys()!=affected.keys():
            raise PlanError('请按现有原文逐句纠正不存在的 factIds。',candidate)
        if not isinstance(rewrites,dict) or rewrites.keys()-affected.keys() or (rewrites and not editable):
            raise PlanError('请只修改指定句子的引用，保留不能改动的文案。',candidate)
        if any(not isinstance(value,str) or not value.strip() for value in rewrites.values()):
            raise PlanError('请用有出处的完整句子修复缺少引用的文案。',candidate)
        discarded=[]
        for i,s in affected.items():
            values=bindings[i]
            if not isinstance(values,list) or not values or any(not isinstance(v,str) or v not in references for v in values):
                if (omit_unsupported and 1<int(i)<len(sentences) and s.get('kind','fact')=='fact'
                        and not any(ident in references for ident in s.get('factIds',[]))):
                    discarded.append(int(i)-1)
                    continue
                raise PlanError(f'第 {i} 句仍缺少可核验的原文引用，请重新选择原文或改写。',candidate)
        for i,s in affected.items():
            if int(i)-1 in discarded:
                continue
            s['factIds']=list(dict.fromkeys(bindings[i]))
            if i in rewrites:
                s['text']=rewrites[i].strip()
        for index in sorted(discarded,reverse=True):
            del sentences[index]

    def fit_institution_labels(self, institution, context, candidate):
        fields = ('displayName', 'headline')
        invalid = [key for key in fields if not self.short_label(institution.get(key))]
        if not invalid:
            return
        labels = self.ask(
            '只修正片中标题条的字段，每项 1 至 12 个字符，空格和标点也计数。'
            'displayName 用真实机构简称，headline 用简短的实地探访主题。保留原意和原文限定，'
            '禁止新增事实。按 labels 给出的键返回 JSON，值是字符串。',
            {'institutionName': institution.get('name', ''), 'labels': {key: institution.get(key) for key in invalid},
             'requirements': context['requirements']}, max_tokens=256)
        for key in invalid:
            label = labels.get(key)
            if self.short_label(label):
                institution[key] = label.strip()
            elif key == 'headline':
                institution[key] = '养老机构实地探访'
            else:
                raise PlanError('请给出最多 12 字、能对应真实机构名称的 displayName。', candidate)

    @staticmethod
    def short_label(value):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= 12 and not any(c in value for c in '\r\n')

    def complete_common_facts(self, sentences, facts, missing, media, length):
        needed = {row['id']: row for row in facts if row['id'] in missing}
        response = self.ask(
            '只补齐 missing 中没有在口播覆盖的共同要点，保留其他句子。'
            '已有句子说清这个要点时，可填现有句号并保持该句原字，仅补原文编号。'
            '优先用现有句子承载事实。需要补新句时，sentence 填 null，text 写最短的口语表达，'
            '保留原文的数值、适用条件和范围，不加其他细节。path 从素材清单选一个直接画面或相关院内背景。'
            '各条合计须符合 durationBudget。空间不足时，把补句写短，后续会就地压缩其他正文。'
            '返回 JSON {"edits":{"共同事实编号":{"sentence":null,"text":"口播句","path":"素材路径"}}}。'
            '句号从 1 开始，首句及结尾不得修改。',
            {'missing': needed, 'durationBudget':length, 'spokenUnits':script_units({'sentences':sentences}),
             'sentences': {str(i): row['text'] for i, row in enumerate(sentences, 1)},
             'media': {path: info['description'] for path, info in media.items()}},
            max_tokens=2400)
        edits = response.get('edits')
        edits = edits if isinstance(edits, dict) else {}
        background = next(row['shots'][0] for row in sentences if row.get('shots'))
        additions = []
        for ident, fact in needed.items():
            edit = edits.get(ident)
            edit = edit if isinstance(edit, dict) else {}
            index = edit.get('sentence')
            text = edit.get('text')
            text = text.strip() if isinstance(text, str) else ''
            if type(index) is int and 1 < index < len(sentences) and (
                not text or text == sentences[index - 1]['text']
            ):
                row = sentences[index - 1]
                row['factIds'] = list(dict.fromkeys([*row.get('factIds', []), ident]))
                continue
            path = edit.get('path')
            if not isinstance(path, str) or path not in media:
                path = next((p for p in fact.get('mediaPaths', []) if p in media), None)
            shot = ({'path': path, 'sourceStart': 0, 'weight': 1,
                     'description': media[path]['description']} if path else dict(background))
            additions.append({'text': (text or fact['text']).rstrip('。！？') + '。',
                              'kind': 'fact', 'factIds': [ident], 'shots': [shot]})
        # A real CTA stays last; other flows append to the end.
        insertion = len(sentences) - (sentences[-1].get('kind') == 'cta')
        sentences[insertion:insertion] = additions

    @staticmethod
    def repair_shots(sentence, selected, fact_media, *, moving=False):
        available = {path: row for path, row in selected.items() if not moving or row['motion']}
        if not available:
            raise PlanError('开头和结尾需要已核验的实拍移动镜头。')
        preferred = [path for ident in sentence.get('factIds', []) if isinstance(ident, str)
                     for path in fact_media.get(ident, []) if path in available]
        entrances = [path for path, row in available.items()
                     if any(word in row['description'] for word in ('门头', '门牌', '入口', '外立面'))]
        fallback = next(iter(preferred or entrances or available))
        shots = sentence['shots'] or [{}]
        repaired = []
        for shot in shots:
            path = shot.get('path') if isinstance(shot, dict) else None
            if isinstance(path, str):
                path = path.strip()
                if path not in available:
                    name = path.replace('\\', '/').rsplit('/', 1)[-1]
                    matches = [key for key in available if key.rsplit('/', 1)[-1] == name]
                    if len(matches) == 1:
                        path = matches[0]
            if not isinstance(path, str) or path not in available:
                chosen = available[fallback]
                repaired.append({'path': fallback, 'sourceStart': 0, 'weight': 1, 'match': 'context',
                                 'description': chosen['description']})
                continue
            source_start = shot.get('sourceStart', 0)
            try:
                start = float(source_start)
            except (ValueError, TypeError, OverflowError):
                start = 0
            if available[path]['kind'] != 'video' or not 0 <= start < available[path]['duration']:
                start = 0
            weight = shot.get('weight', 1)
            if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
                weight = 1
            repaired.append({**shot, 'path': path, 'sourceStart': start, 'weight': weight})
        sentence['shots'] = repaired

    def rebuild_narration(self, reference_plan, context, facts, evidence, previous, index, preference):
        result = self.ask(
            '上一轮完整策划缺少口播结构，本轮只重写口播并选择真实镜头。'
            + NARRATION_OUTLINE +
            '完整口播受 durationBudget 限制。用 sections 分配开头、正文和咨询结尾，事例每段最多一个。'
            '使用实际 facts 和 evidence 的编号，每个共同事实一定写进正文并在对应句标明 f 编号。'
            'preference 是本条要求。开头和结尾选观察到运动的视频，各句也可填写一个相关背景的 path。'
            '写成与 previous 切入和叙述顺序有区别的完整版本。'
            '返回 JSON {"openingAngle":"切入","structure":"顺序","coverAngle":"主题","title":"本条标题",'
            '"sentences":[{"text":"口播句","kind":"hook|fact|cta","factIds":["f1"],"path":"素材路径"}]}。',
            {'institutionName': context['institutionName'], 'facts': facts, 'evidence': evidence,
             'media': context['media'], 'previous': previous, 'index': index + 1, 'preference': preference,
             'durationBudget':context.get('durationBudget', {})},
            max_tokens=4500)
        if not isinstance(result.get('sentences'), list) or not result['sentences']:
            raise PlanError('模型的独立口播补稿没有返回句子，请重新生成。')
        plan = deepcopy(reference_plan)
        sentences = []
        for item in result['sentences']:
            if not isinstance(item, dict):
                raise PlanError('独立口播补稿中的句子需要文字和事实引用。')
            sentence = {key: item[key] for key in ('text', 'kind', 'factIds') if key in item}
            sentence['shots'] = [{'path': item.get('path'), 'sourceStart': 0, 'weight': 1}]
            sentences.append(sentence)
        plan['sentences'] = sentences
        title = result.get('title')
        if isinstance(title, str) and title.strip() and not any(char.isdigit() for char in title):
            plan['publication']['title'] = title.strip()
        return {**result, 'plan': plan}

    def draft_narration(self, context, facts, preference, budget, index, feedback=''):
        # A rotation changes the entry point while every script keeps the same facts.
        ordered = facts[index % len(facts):] + facts[:index % len(facts)]
        goal = (budget['minUnits'] + budget['maxUnits']) // 2
        per_topic = (goal - 45) // len(ordered)
        result = self.ask(
            '写一篇养老院探访口播。'
            + narration_length_instruction(budget) + '内容限于已核对清单里的事实与可见细节。'
            + VOICE_STYLE +
            f'开头交代院名，约 25 字。按清单顺序写 {len(ordered)} 个正文短段，每段约 {per_topic} 字，'
            '一个重点说一两句。最后约 20 字，邀请留言了解参观。'
            '每段先讲清一件事，再说明它对长辈日常有什么直接用途。保留正式名称。'
            '正文只说清单中给出的事实，可自然改述，勿添加细节或承诺。'
            '省略清单以外的开业年份、规模、疗法、就医、膳食、历史和宣传词。'
            '局部画面只讲眼前能核实的细节，避免推断材质和功效。'
            '只交口播正文。使用 JSON {"paragraphs":["开头","正文段","结尾"]}，每段是实际会读的话。'
            '用户明确要求专题时可聚焦专题。', {
                'institutionName':context['institutionName'],
                'requirements':context['requirements'], 'preference':preference,
                'coreFacts':[row['text'] for row in ordered],
                # Full source is useful when the user specifies particular content.
                **({'original':context['sources']} if preference or context['requirements'] else {}),
                'feedback':feedback
            }, max_tokens=2500)
        parts = result.get('paragraphs')
        if (not isinstance(parts, list) or not 3 <= len(parts) <= 12
                or any(not isinstance(part, str) or not part.strip() for part in parts)):
            raise PlanError('口播需要以独立开头、正文和结尾段落返回。')
        return [part.strip() for part in parts]

    def revise_narration(self, parts, targets, issues, context, facts, budget):
        result = self.ask(
            '局部修订一篇养老院探访口播。' + narration_length_instruction(budget) + VOICE_STYLE +
            '只改 targets 中编号的段落，优先解决 issues 列出的问题。'
            '保留所有必讲事实、开头的机构语境和最后一个咨询动作。增加字数时补有依据的细节，'
            '减少时先压重复或次要细节。相似问题按原意重组句式与衔接。'
            '返回 {"edits":[{"index":2,"text":"修订后的段落"}]}，index 从 1 起，每个指定编号返回一次。',
            {'paragraphs':parts,'targets':[i + 1 for i in targets],'issues':issues,
             'requirements':context['requirements'],'facts':[row['text'] for row in facts]},
            max_tokens=2600)
        edits = result.get('edits')
        if not isinstance(edits, list) or any(not isinstance(edit, dict) for edit in edits):
            raise PlanError('返修需返回指定段落。')
        replacements = {edit['index'] - 1:edit.get('text') for edit in edits if type(edit.get('index')) is int}
        if (len(edits) != len(replacements) or set(replacements) != set(targets)
                or any(not isinstance(text, str) or not text.strip() for text in replacements.values())):
            raise PlanError('返修需逐一修改指定段落并保留有效正文。')
        return [replacements.get(i, part).strip() for i, part in enumerate(parts)]

    def plan(self, task, index, context, facts, previous, feedback='', *, evidence=None, candidate=None,
             reference_plan=None, spoken=None):
        prefs = (task.get('videoPreferences') or [''] * task['count'])[index]
        person = (task.get('videoSpecs') or [{'person': task['person']}] * task['count'])[index]['person']
        revised = task.get('revisedNarration', '')
        evidence = evidence or {}
        target = task.get('targetDurationSeconds')
        length = initial_budget(target) if target is not None else {}
        compact = ('一分钟默认稿以共同事实为主。只用直接拍到的细节举例。'
                   '先写 6 至 7 个口播小节，总计以 205 个可读字为起稿目标，留下润色和补句余量。'
                   '每个正文小节只说一个主题，长度约 25–32 字，片尾只留一个咨询动作。'
                   '额外的就医安排、数字清单、工具、疗法与效果等细节，只有用户指定或已列为共同事实才写入。'
                   '写设施时直接描述镜头里的物件和位置，写服务时沿用原文范围和条件。'
                   if target == MINUTE_TARGET and not (revised or prefs or task.get('requirements')) else '')
        if spoken is not None:
            result = self.ask(
                '为已经写好的 paragraphs 配真实镜头、事实编号、封面和发布文案。'
                'paragraphs 的每个段落对应 plan.sentences 的一项，顺序和数量一致。'
                '段落中的文字已经锁定，由工作台填入；这里只交段落 label、factIds 和 shots。'
                '首尾使用有运动的实拍视频。原文描述的分区或方案允许配院内环境。'
                '配景必须在 shots 填 match 为 context，并在 description 说明仅作背景。'
                '原文编号 f 必须对应原文所支持的段落。局部设施观察只说明该镜头拍到的一处配置。'
                '封面和发布文案只使用已确认事实。按 {"plan":{"institution":{},"sentences":[],"cover":{},'
                '"publication":{}},"openingAngle":"","structure":"","coverAngle":"","topicAuthorization":""} 输出。\n'
                + self.rules('plan-schema', 'covers'),
                {**context,'paragraphs':spoken,'facts':facts,'supportingEvidence':evidence,'preference':prefs,
                 'feedback':feedback,'candidate':candidate,'publicationOverride':task.get('publicationOverride',{})},
                max_tokens=6500)
        else:
            result = self.ask(
            NARRATOR_CONTEXT + '以 durationBudget 为本次长度目标，优先合并相关重点，舍弃次要例子。'
            '它明确给出字数范围，优先于技能中默认的一分钟建议。若未给 durationBudget 则沿用默认。'
            '按 sections 的容量写开头、正文和结尾，各部分加起来不能超过 maxUnits。结尾预留约一短句。'
            '你负责写一条中文养老机构探访视频。按下列技能规则先写整篇口语稿并做语言润色，再分句选镜和定封面。'
            '仅使用真实素材，选片基于提供的实看结果。开头和结尾选有运动的源视频；在短片剩余时长足够的区间取镜，'
            '预计每秒 3.5 至 4 个汉字，给足分镜，sourceStart 使用秒数。不要指定资源路径、任何运行设置或关闭审核。'
            '有原文依据的院名按自然口语念。'
            '无需生成 captions。工作台使用配音时间和字宽排字幕。'
            '各事实写进实际口播后，在对应句的 factIds 数组中逐项标明传入的编号，如 ["f1","f2"]。'
            '综合稿的 sentences 必须覆盖 facts 中全部 f 编号。额外细节可用 supportingEvidence 中支持该句的 e 编号，'
            '这些细节无需全部写入。句子有多个事实时可标多个编号。封面标签或 publication 写了数字时也提供 factIds。'
            + NARRATION_OUTLINE + compact +
            'show 优先配该项 mediaPaths，依据 visibleDetail 核对能够直接展示的具体内容。'
            '收到 feedback 时逐项修正。candidate 是待修订的上一个草稿，保留其中已符合要求的内容。'
            '用对应原文编号支持每个事实句，不用无关编号凑数。'
            '仅在用户偏好明确指定专题时返回 topicAuthorization 原话。'
            '若 revisedNarration 非空，逐字保留用户文案及标点，可重新断句但不删改文字。不够信息时填 question。'
            '输出一个 JSON 对象。顶层结构为 {"plan":{},"openingAngle":"本条如何开场","structure":"本条展开顺序",'
            '"coverAngle":"本条封面主题","topicAuthorization":"","question":null}。三项角度均填写具体内容。'
            'plan 必须同时包含 institution、sentences、cover 和 publication。'
            'asrPrompt 可给出本稿实际出现的少见专名，供配音识别复核使用，不能换掉任何口播用词。'
            'institution.displayName 和 institution.headline 各最多 12 字，标点也计入。'
            '封面放在 plan.cover，包含 main_title 和三个 labels；发布文案放在 plan.publication，'
            '包含 title、description 和五个不同的 topics。两个字段都按同一条完整口播撰写。\n'
            + self.rules('plan-schema', 'narration', 'narration-polish', 'covers') + '\n' + compact,
            {**context, 'facts': facts, 'supportingEvidence': evidence, 'person': person, 'index': index + 1, 'total': task['count'],
             'preference': prefs, 'previousAngles': previous, 'feedback': feedback, 'candidate': candidate,
             'durationBudget': length,
             'revisedNarration': revised, 'publicationOverride': task.get('publicationOverride', {})},
                max_tokens=12000)
        raw = result.get('plan')
        if spoken is None and reference_plan and not revised and not prefs and not task.get('requirements') and (
            not isinstance(raw, dict) or not isinstance(raw.get('sentences'), list)
            or not 1 <= len(raw['sentences']) <= 100
        ):
            result = self.rebuild_narration(reference_plan, context, facts, evidence, previous, index, prefs)
            raw = result['plan']
        if not isinstance(raw, dict):
            question(result)
            raise PlanError('模型必须在顶层 plan 字段返回完整的单条制作计划。')
        if not isinstance(raw.get('institution'), dict):
            raise PlanError('plan.institution 必须填写为对象。')
        plan = {key: raw[key] for key in ('institution', 'sentences', 'asrPrompt', 'cover', 'publication') if key in raw}
        references = {x['id']: {k: x[k] for k in ('id', 'source', 'quote')} for x in facts}
        references.update({ident: {'id': ident, **quote} for ident, quote in evidence.items()})
        plan.update(version=1, person=person, facts=list(references.values()))
        plan['settings'] = {'music': {'enabled': task['music']}}
        if task.get('musicTrack'):
            plan['settings']['music']['resource'] = task['musicTrack']['resource']
        if context['institutionName']:
            plan['institution']['name'] = context['institutionName']
        self.fit_institution_labels(plan['institution'], context, result)
        selected = {x['path']: x for x in context['media']}
        fact_media = {fact['id']: fact.get('mediaPaths', []) for fact in facts}
        sentences = plan.get('sentences', [])
        if not isinstance(sentences, list) or not 1 <= len(sentences) <= 100:
            raise PlanError('plan.sentences 必须是包含口播句子和分镜的数组。')
        if spoken is not None:
            if len(sentences) != len(spoken) or any(not isinstance(row, dict) for row in sentences):
                raise PlanError(f'分镜须按锁定的 {len(spoken)} 个段落逐一返回。')
            for i, (row, text) in enumerate(zip(sentences, spoken)):
                row.update(text=text,kind='hook' if i == 0 else 'cta' if i == len(spoken) - 1 else 'fact')
        for i, sentence in enumerate(sentences):
            if not isinstance(sentence, dict) or not isinstance(sentence.get('text'), str) or not isinstance(sentence.get('shots'), list):
                raise PlanError(f'第 {i + 1} 句须包含口播 text 和 shots 数组。')
            # Layout uses the measured speech and glyph geometry in the render engine.
            sentence.pop('captions', None)
            if not revised and sentence.get('kind') == 'cta' and not (prefs or task.get('requirements')):
                # These interchangeable CTA forms avoid a known rhotic ASR boundary.
                sentence['text'] = sentence['text'].replace('在这儿', '在这里').replace('来这儿', '来这里')
            ids = sentence.get('factIds', [])
            if not isinstance(ids, list) or any(not isinstance(f, str) for f in ids):
                raise PlanError(f'第 {i + 1} 句的 factIds 必须是编号字符串数组，如 ["f1","f2"]。', result)
            self.repair_shots(sentence, selected, fact_media, moving=i == 0 or sentence.get('kind') == 'cta')
        if revised:
            text = ''.join(s['text'] for s in sentences)
            if ''.join(revised.split()) != ''.join(text.split()):
                raise PlanError('修订稿必须保留用户原话。')
        for k, value in task.get('publicationOverride', {}).items():
            if k in ('title', 'description') and value:
                if not isinstance(plan.get('publication'), dict):
                    raise PlanError('plan.publication 必须填写为对象，以保留用户的发布文案。')
                plan['publication'][k] = value
        common_ids = {fact['id'] for fact in facts}
        cited_ids = {ident for sentence in sentences for ident in sentence.get('factIds', [])}
        omit_unsupported = spoken is None and not (revised or task.get('requirements') or prefs) and (
            task['count'] == 1 or common_ids.issubset(cited_ids)
        )
        self.rebind_fact_references(sentences,references,result,editable=not revised and spoken is None,omit_unsupported=omit_unsupported)
        authorization = result.get('topicAuthorization') or ''
        if authorization and (not isinstance(authorization, str) or authorization not in task.get('requirements', '') + '\n' + prefs):
            raise PlanError('专题需能引用用户明确提出的要求。')
        angles = {k: result.get(k) for k in ('openingAngle', 'structure', 'coverAngle')}
        missing_angles = [k for k, v in angles.items() if not isinstance(v, str) or not v.strip()]
        if missing_angles:
            raise PlanError('请补齐顶层策划角度字段 ' + '、'.join(missing_angles) + '。', result)
        expected = common_ids
        used = {ident: None for sentence in sentences for ident in sentence.get('factIds', [])}
        if spoken is None and expected - used.keys() and not (authorization or revised):
            self.complete_common_facts(sentences, facts, expected - used.keys(), selected, length)
            used = {ident: None for sentence in sentences for ident in sentence.get('factIds', [])}
        unknown = sorted(set(used) - references.keys())
        missing = sorted(expected - set(used)) if not authorization else []
        issues = []
        if missing:
            issues.append('口播缺少共同事实 ' + '、'.join(missing) + '，请在对应句写清事实并标注编号')
        if unknown:
            issues.append('factIds 使用了未提供的编号 ' + '、'.join(unknown))
        if issues:
            raise PlanError('。'.join(issues) + '。', result)
        return plan, {**angles, 'factCoverage': [f for f in used if f in expected], 'mode': 'topic' if authorization else 'comprehensive',
                      'topicAuthorization': authorization}

    def shorten_narration(self, plan, budget, outline, preference, *, may_drop_optional=False):
        rows = plan['sentences']
        counts = [script_units({'sentences':[row]}) for row in rows]
        total = sum(counts)
        eligible = [i for i in range(1, len(rows) - 1) if rows[i].get('kind', 'fact') == 'fact']
        removed = set()
        if may_drop_optional:
            must_say = {row['id'] for row in outline}
            references = [must_say.intersection(row.get('factIds', [])) for row in rows]
            uses = Counter(key for keys in references for key in keys)
            for i in sorted(eligible, key=lambda i: (len(references[i]), -counts[i])):
                if total <= budget['maxUnits']:
                    break
                if len(eligible) - len(removed) > 1 and all(uses[key] > 1 for key in references[i]):
                    removed.add(i)
                    uses.subtract(references[i])
                    total -= counts[i]
            if removed:
                plan['sentences'] = rows = [row for i, row in enumerate(rows) if i not in removed]
                if total <= budget['maxUnits']:
                    return True
                counts = [count for i, count in enumerate(counts) if i not in removed]
                eligible = [i for i in range(1, len(rows) - 1) if rows[i].get('kind', 'fact') == 'fact']
        excess = total - budget['maxUnits'] + 8
        selected, capacity = [], 0
        for i in sorted(eligible, key=counts.__getitem__, reverse=True):
            selected.append(i)
            capacity += counts[i]
            if capacity >= excess * 2.5:
                break
        if not selected or capacity <= excess:
            return bool(removed)
        limit = capacity - excess
        evidence = {row['id']:row['quote'] for row in plan['facts']}
        core = {row['id']:row['text'] for row in outline}
        response = self.ask(
            '只压缩列出的正文句，全部加起来至多 maxUnits 个读出的字。'
            '删掉导语、重复总结、并列例子和未拍到的细节，每句保留对应的必讲事实、限定词及和当前镜头的对应。'
            '只返回 {"sentences":[{"index":2,"text":"改写句"}]}，每个给定编号恰好出现一次。',
            {'maxUnits':limit, 'preference':preference, 'lines':[
                {'index':i + 1, 'text':rows[i]['text'], 'goal':max(1, round(counts[i] * limit / capacity)),
                 'facts':[{'quote':evidence[key], 'mustSay':core.get(key)} for key in rows[i].get('factIds', [])],
                 'shots':[shot.get('description','') for shot in rows[i].get('shots', [])]} for i in sorted(selected)]},
            max_tokens=3000)
        edits = response.get('sentences')
        if not isinstance(edits, list) or any(not isinstance(edit, dict) for edit in edits):
            return bool(removed)
        updates = {edit['index'] - 1:edit.get('text') for edit in edits if type(edit.get('index')) is int}
        if len(updates) != len(edits) or set(updates) != set(selected) or any(
                not isinstance(text, str) or not text.strip() for text in updates.values()):
            return bool(removed)
        for i, text in updates.items():
            rows[i]['text'] = text.strip()
            rows[i].pop('captions', None)
        return True

    def repair_review_sentences(self, plan, context, outline, feedback, preference):
        rows = plan['sentences']
        indexes = sorted({int(i) for i in re.findall(r'sentences\[(\d+)\]', feedback)
                          if int(i) < len(rows)})
        if not indexes:
            return False
        facts = {row['id']:row['quote'] for row in plan['facts']}
        core = {row['id']:row['text'] for row in outline}
        media = {row['path']:row for row in context.get('media', [])}
        result = self.ask(
            '按复核意见只修改列出的句子。保留所有 mustSay。省略没有原文或镜头支撑的可选细节，'
            '保留原文的服务范围、限定和程度。选入新镜头时从 media 提供精确 path。'
            '开头和咨询结尾沿用原镜头。每句控制在原长度内。'
            '返回 {"sentences":[{"index":0,"text":"改写后的句子"}]}，只用给定的下标。'
            '没有 mustSay 的正文若属可选细节且没有依据，可返回 text 为空字符串，随后删除该句。',
            {'feedback':feedback, 'preference':preference, 'requirements':context.get('requirements', ''),
             'media':{k:v['description'] for k,v in media.items()},
             'lines':[{'index':i,'text':rows[i]['text'],'kind':rows[i].get('kind','fact'),
                       'shots':rows[i]['shots'],'facts':[{'quote':facts[k], 'mustSay':core.get(k)}
                           for k in rows[i].get('factIds', [])]} for i in indexes]},
            max_tokens=3000)
        edits = result.get('sentences')
        if not isinstance(edits, list) or any(not isinstance(edit, dict) for edit in edits):
            return False
        updates = {edit['index']:edit for edit in edits if type(edit.get('index')) is int}
        if set(updates) != set(indexes) or len(edits) != len(indexes) or any(
                not isinstance(edit.get('text'), str) for edit in updates.values()):
            return False
        removed = []
        for i, edit in updates.items():
            row = rows[i]
            replacement = edit['text'].strip()
            if not replacement:
                if (0 < i < len(rows) - 1 and row.get('kind','fact') == 'fact'
                        and not (context.get('requirements') or preference or set(row.get('factIds', [])) & core.keys())):
                    removed.append(i)
                continue
            row['text'] = replacement
            row.pop('captions', None)
            path = edit.get('path')
            if isinstance(path, str) and path in media and 0 < i < len(rows) - 1 and row.get('kind','fact') == 'fact':
                row['shots'] = [{'path':path,'sourceStart':0,'weight':1,'description':media[path]['description']}]
        for i in sorted(removed, reverse=True):
            del rows[i]
        return True

    def repair_numeric_references(self, plan, audit):
        numeric_error = ' 的数字需要可核对的 factIds 原文引用。'
        failing = {error.removesuffix(numeric_error) for error in audit.get('structuralErrors', [])
                   if error.endswith(numeric_error)}
        rows = {row['location']: row['text'] for row in audit.get('sourceMatrix', [])
                if row['location'] in failing}
        if not rows:
            return False
        refs = {row['id']: row['quote'] for row in plan['facts']}
        response = self.ask(
            '只补下面这些字段的数字引用。选择能支持该数字及其实际含义、对象、单位和限定范围的原文编号。'
            '保留文案原字。没有合适原文就返回空数组，交回上一步改稿。返回 JSON'
            ' {"references":{"字段名":["原文编号"]}}。',
            {'fields': rows, 'evidence': refs})
        bindings = response.get('references')
        if not isinstance(bindings, dict) or bindings.keys() != rows.keys() or any(
            not isinstance(ids, list) or not ids or any(not isinstance(ident, str) or ident not in refs
                                                         for ident in ids)
            for ids in bindings.values()
        ):
            return False
        for location, ids in bindings.items():
            if location.startswith('sentences['):
                field = plan['sentences'][int(location.removeprefix('sentences[').removesuffix(']'))]
            elif location.startswith('cover.labels['):
                field = plan['cover']['labels'][int(location.removeprefix('cover.labels[').removesuffix(']'))]
            else:
                field = plan['publication']
            field['factIds'] = list(dict.fromkeys([*field.get('factIds', []), *ids]))
        return True

    def content_review(self, job, answer):
        plan = self.read(job / 'plan.json')
        context = self.context({'requirements': ''}, job, answer)
        return self.review_plan(context, plan, self.read(job / 'content-audit.json', {}))

    def review_plan(self, context, plan, audit, *, outline=()):
        response = self.ask(
            NARRATOR_CONTEXT + '核对每句口播与事实原文，核对语义及所有限定词。用已有实看素材描述核对文画关系及封面三槽。'
            '原文概括了同一卖点时，可以举一个已核对的实拍例子；镜头里的扶手或球桌可如实描述，'
            '不能扩大成所有房间都配备、某种疗法或某种材质性能。纯画面封面标签允许用对应实拍作依据。'
            '原文支持的重要服务可用一句简述，并明确是环境背景，勿把普通展柜或康复空间说成该服务的现场。'
            '打回时在审核说明中用 audit 的 sentences[下标] 精确指明需要修改的句子。'
            '环境背景已在分镜备注标注，且口播未把镜头中的场所说成事实现场时，可直接通过，不要求口播念出处或画面说明。'
            '审核口语质量、覆盖范围、结尾咨询、封面及发布文案。材料未支持或文画明显不合时拒绝。'
            '如有共同提纲，核对每条都在实际口播讲全，实拍可呈现的重点展开，缺少对应画面的重要内容一句带过。'
            '成片时长由工作台按实际语音单独核验；不要凭稿件猜实测秒数。'
            '事实、原文限定、共同要点、用户要求、封面承诺和文画明显错配是必须修正的问题。'
            '区分原文支持的可用设施与当前画面里正在发生的动作。只说可以使用已拍到的设施时，不要求画面有人使用。'
            '需要修正时，开头先列必须修正的具体下标和原因，已通过的观察省略。'
            '只有这些均通过，才可将措辞、重复、段落节奏或目标时长单独记为风格问题。'
            'JSON {"ok":true,"hardChecksPassed":true,"note":"具体到口播、事实和素材的审核观察",'
            '"styleIssues":[]}。有任何必须修正的问题，hardChecksPassed 填 false；'
            '只有风格问题时 ok 填 false、hardChecksPassed 填 true、styleIssues 填具体修改建议。\n'
            + self.rules('content-review'),
            {**context, 'plan': plan, 'audit': audit, 'outline': outline})
        issues = response.get('styleIssues')
        if (response.get('ok') is False and response.get('hardChecksPassed') is True
                and isinstance(issues, list) and issues and all(isinstance(issue, str) and issue.strip() for issue in issues)
                and isinstance(response.get('note'), str) and response['note'].strip()):
            raise EditorialFeedback(response['note'][:600] + '。' + '；'.join(issues)[:400])
        if response.get('ok') is True and response.get('hardChecksPassed') is False:
            raise PlanError('复核发现必须修正的问题：' + str(response.get('note', ''))[:850])
        return approved(response)

    def review_frames(self, job, paths, instructions, context=None, *, findings=None):
        job = job.resolve()
        known = sorted(set(Path(p).resolve() for p in paths))
        if not known or any(not p.is_relative_to(job) or not p.is_file() for p in known):
            raise ValueError('当前工程缺少可验证的检查帧。')
        notes = []
        times = {}
        for index, path in enumerate(known, 1):
            match = re.search(r'-([0-9.]+)s$', path.stem)
            if match:
                times[f'F{index}'] = float(match[1])

        def inspect(visuals):
            ids = [ident for ident, _ in visuals]
            result = self.ask(instructions + '查看每张图，发现可见的显著问题时拒绝。'
                              '返回 JSON 对象，ok 为布尔值，seen 为 frames 中实际看过的短编号数组，'
                              'note 为具体可见观察，若提供了秒数请写清对应时间。',
                              {'frames': ids, 'seconds': {ident: times[ident] for ident in ids if ident in times},
                               'context': context}, visuals)
            seen = result.get('seen')
            if not isinstance(seen, list) or any(not isinstance(x, str) for x in seen) or set(seen) != set(ids):
                return None
            if findings is not None and result.get('ok') is False:
                note = result.get('note')
                if isinstance(note, str) and note.strip():
                    note = note.strip()[:600]
                    findings.append(note)
                    return note
            return approved(result)

        for group in chunks(list(enumerate(known, 1)), 6):
            visuals = [(f'F{index}', self.read_frame(path)) for index, path in group]
            note = inspect(visuals)
            if note is not None:
                notes.append(note)
                continue
            for visual, (_, path) in zip(visuals, group):
                note = inspect([visual])
                if note is None:
                    raise ValueError(f'视觉复核回传缺少帧确认：{path.relative_to(job)}。')
                notes.append(note)
        return [str(p) for p in known], '\n'.join(notes)


def question(result):
    text = result.get('question')
    if isinstance(text, str) and text.strip():
        raise NeedsInput(text[:1200])


def approved(response):
    note = response.get('note')
    if not isinstance(note, str) or not note.strip():
        raise ValueError('模型未返回具体审核结论。')
    if response.get('ok') is not True:
        raise NeedsInput('本条审核需你确认，请补充或修正后继续。' + note[:900])
    return note[:600]
