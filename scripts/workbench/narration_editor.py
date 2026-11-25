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
    r'|实拍(?:的|这一|这处|中|里|室内|户外|淋浴)|按(?:照)?(?:正式|实际)名称介绍'
)

def paragraphs(plan):
    return [row['text'] for row in plan['sentences'] if row.get('kind') != 'cta']


PAREN_PATTERN = re.compile(r'[（()）]')


def narration_issues(lines):
    issues = {}
    for i, line in enumerate(lines):
        if PAREN_PATTERN.search(line):
            issues[i] = f'第 {i + 1} 段包含括号。删掉括号及括号内的文字，括号里的内容不需要念出来。'
        elif (match := REPORT_LANGUAGE.search(line)):
            issues[i] = f'第 {i + 1} 段使用了核验口吻「{match.group()}」。改成直接向家属介绍。'
    return issues


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
