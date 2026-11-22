"""Spoken-copy checks before a workbench plan can enter production."""
from collections import Counter
import re

from workflow import spoken_characters

SIMILARITY_LIMIT = 90
FIXED_TERMS = ('自理生活区', '生活照护区', '专属失智照护楼层', '失智照护楼层')
REPORT_LANGUAGE = re.compile(
    r'(?:按|据|根据|按照)(?:机构|院方|本院|养老院)?(?:资料|原文|介绍)'
    r'|(?:机构|院方|本院)?(?:资料|原文|介绍)(?:里|中)?(?:说|写|介绍|提到|显示|列|载明)'
    r'|(?:画面|实拍|镜头)(?:里|中|上)?(?:可见|(?:可以|能够|能)?(?:看见|看到))'
)

VOICE_STYLE = (
    '用客观的销售带看口吻，向长辈和家属直接介绍空间、照护和日常生活。'
    '口语简明自然。把事实出处和纯配景标记留在后台，不在正文念“资料里说”“根据介绍”“画面里可以看到”。'
    '保留原文的实际范围与条件。只描述对应实拍的一处配置，不能扩大成全院。'
    '只由文字支持的服务可直接简述，公共空间只作背景，不把它说成实施现场。'
)


def paragraphs(plan):
    return [row['text'] for row in plan['sentences'] if row.get('kind') != 'cta']


def narration_issues(lines):
    return {i: f'第 {i + 1} 段使用了核验口吻「{match.group()}」。改成直接向家属介绍。'
            for i, line in enumerate(lines) if (match := REPORT_LANGUAGE.search(line))}


def fragments(text, ignored):
    for line in re.split(r'[。！？!?]+', text):
        for segment in ignored.split(spoken_characters(line)):
            yield from (segment[i:i + 4] for i in range(len(segment) - 3))


def similarity_issue(lines, previous, institution):
    fixed = {spoken_characters(institution), *FIXED_TERMS}
    text = '\n'.join([*lines, *previous])
    named = r'[“「]([\u3400-\u9fff]{2,16})[”」]'
    fixed.update(re.findall(r'(?:叫|名为|称为|方案|项目)[^。！？\n]{0,8}' + named, text))
    fixed.update(re.findall(named + r'(?:方案|项目|服务)', text))
    fixed.discard('')
    company = re.search(r'[（(]([^）)]+)[）)]', institution)
    if company:
        fixed.add(company[1])
        fixed.add(re.sub(r'[（(][^）)]+[）)]', '', institution))
    ignored = re.compile('|'.join(map(re.escape, sorted(fixed, key=len, reverse=True)))) if fixed else re.compile(r'(?!)')
    a = [Counter(fragments(line, ignored)) for line in lines]
    b = Counter(fragments('\n。'.join(previous), ignored))
    whole = Counter()
    for parts in a:
        whole.update(parts)
    overlap, size = (whole & b).total(), whole.total() + b.total()
    if size and 200 * overlap >= SIMILARITY_LIMIT * size:
        matches = [i for i, parts in enumerate(a) if parts and (parts & b).total() * 5 >= parts.total() * 4]
        return 200 * overlap / size, matches
    return None
