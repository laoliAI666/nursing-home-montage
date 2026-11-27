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


def narration_issues(lines, *, rules=None, skip_banned_last=False):
    forbid_parens = True
    forbid_report = True
    banned = None
    if isinstance(rules, dict):
        from text_styles import extract_rules
        style_rules, banned = extract_rules(rules)
        forbid_parens = bool(style_rules['guard'].get('forbidParentheses', True))
        forbid_report = bool(style_rules['guard'].get('forbidReportTone', True))
    issues = {}
    for i, line in enumerate(lines):
        if forbid_parens and PAREN_PATTERN.search(line):
            issues[i] = f'第 {i + 1} 段包含括号。删掉括号及括号内的文字，括号里的内容不需要念出来。'
        elif forbid_report and (match := REPORT_LANGUAGE.search(line)):
            issues[i] = f'第 {i + 1} 段使用了核验口吻「{match.group()}」。改成直接向家属介绍。'
    if isinstance(banned, dict) and banned.get('enabled') and banned.get('action') == 'repair':
        from text_styles import find_banned_hits
        check_lines = lines[:-1] if skip_banned_last and len(lines) > 1 else lines
        for idx, words in find_banned_hits(check_lines, banned).items():
            msg = f'第 {idx + 1} 段命中全站禁词「{"、".join(words)}」，请删去或改用合规表述。'
            issues[idx] = (issues[idx] + ' ' + msg) if idx in issues else msg
    return issues


def banned_word_warnings(lines, rules=None, *, skip_last=False):
    if not isinstance(rules, dict):
        return []
    from text_styles import extract_rules, find_banned_hits
    _, banned = extract_rules(rules)
    if not banned.get('enabled') or banned.get('action') != 'warn':
        return []
    check_lines = lines[:-1] if skip_last and len(lines) > 1 else lines
    hits = find_banned_hits(check_lines, banned)
    return [
        f'全站公共禁词提醒：第 {idx + 1} 段包含「{"、".join(words)}」，验收前请确认是否需要改写。'
        for idx, words in sorted(hits.items())
    ]


def fragments(text, ignored):
    for line in re.split(r'[。！？!?]+', text):
        for segment in ignored.split(spoken_characters(line)):
            yield from (segment[i:i + 4] for i in range(len(segment) - 3))


def similarity_issue(lines, previous, institution, *, limit=SIMILARITY_LIMIT):
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
    if size and 200 * overlap >= limit * size:
        matches = [i for i, parts in enumerate(a) if parts and (parts & b).total() * 5 >= parts.total() * 4]
        return 200 * overlap / size, matches
    return None
