"""One numeral-aware mapping shared by speech QA, build and caption reflow."""
import difflib
import re


def _per_char_phonemes(text, phonemes):
    keys = []
    for ch in text:
        ph = phonemes(ch)
        keys.append(ph[0] if ph else ch)
    return keys


def _arabic_numeral_spans(raw_text, clean):
    """Return list of (start_idx, end_idx) character ranges in clean(raw_text) originating from Arabic digits."""
    # Remove parenthetical notes first so index aligns with clean(raw_text).
    text = str(raw_text or '')
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r'（[^（）]*）|\([^()]*\)', '', text)
    spans = []
    cursor = 0
    pos = 0
    pattern = re.compile(r'\d{4}(?=年)|\d+\.\d+|\d+')
    for m in pattern.finditer(text):
        prefix = text[pos:m.start()]
        cursor += len(clean(prefix))
        token = m.group(0)
        if re.fullmatch(r'\d{4}', token) and text[m.end():m.end() + 1] == '年':
            spoken = clean(token + '年')[:-1]
        else:
            spoken = clean(token)
        if spoken:
            spans.append((cursor, cursor + len(spoken)))
            cursor += len(spoken)
        pos = m.end()
    return spans


def _map_dialect_characters(raw_chars, expected, raw_text, clean, phonemes):
    if not raw_chars or not expected:
        raise ValueError('词级映射与原稿不一致；查看 alignment-check.json 的 numericMapping，不删除数字或重配音绕过。')
    actual = ''.join(c['c'] for c in raw_chars)
    exp_keys = _per_char_phonemes(expected, phonemes)
    act_keys = _per_char_phonemes(actual, phonemes)
    opcodes = difflib.SequenceMatcher(None, exp_keys, act_keys, autojunk=False).get_opcodes()
    mapped = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'insert':
            continue
        ne = i2 - i1
        na = j2 - j1
        if tag == 'equal' or (tag == 'replace' and ne == na):
            for k in range(ne):
                src = raw_chars[j1 + k]
                mapped.append({
                    'c': expected[i1 + k],
                    'start': src['start'],
                    'end': src['end'],
                    'numberGroup': src['numberGroup'],
                })
        elif tag == 'replace':
            t0 = raw_chars[j1]['start']
            t1 = max(t0, raw_chars[j2 - 1]['end'])
            groups = {raw_chars[j]['numberGroup'] for j in range(j1, j2)}
            shared_ng = next(iter(groups)) if len(groups) == 1 and None not in groups else None
            for k in range(ne):
                if shared_ng is not None:
                    a, b = t0, t1
                else:
                    a = t0 + (t1 - t0) * k / ne
                    b = t0 + (t1 - t0) * (k + 1) / ne
                mapped.append({'c': expected[i1 + k], 'start': a, 'end': b, 'numberGroup': shared_ng})
        elif tag == 'delete':
            t_prev = raw_chars[j1 - 1]['end'] if j1 > 0 else raw_chars[0]['start']
            t_next = raw_chars[j1]['start'] if j1 < len(raw_chars) else raw_chars[-1]['end']
            if t_next > t_prev:
                for k in range(ne):
                    a = t_prev + (t_next - t_prev) * k / ne
                    b = t_prev + (t_next - t_prev) * (k + 1) / ne
                    mapped.append({'c': expected[i1 + k], 'start': a, 'end': b, 'numberGroup': None})
            elif mapped and mapped[-1]['numberGroup'] is None and mapped[-1]['end'] > mapped[-1]['start']:
                t0, t1 = mapped[-1]['start'], mapped[-1]['end']
                total_n = 1 + ne
                mapped[-1]['end'] = t0 + (t1 - t0) / total_n
                for k in range(ne):
                    a = t0 + (t1 - t0) * (k + 1) / total_n
                    b = t0 + (t1 - t0) * (k + 2) / total_n
                    mapped.append({'c': expected[i1 + k], 'start': a, 'end': b, 'numberGroup': None})
            else:
                for k in range(ne):
                    mapped.append({'c': expected[i1 + k], 'start': t_prev, 'end': t_prev, 'numberGroup': None})
    if len(mapped) != len(expected):
        raise ValueError('词级映射与原稿不一致；查看 alignment-check.json 的 numericMapping，不删除数字或重配音绕过。')
    # Ensure Arabic numeral spans never split across subtitle boundaries even if ASR spelled them in Chinese.
    next_group = max((c['numberGroup'] for c in mapped if c['numberGroup'] is not None), default=-1) + 1
    for a_idx, b_idx in _arabic_numeral_spans(raw_text, clean):
        if not (0 <= a_idx < b_idx <= len(mapped)):
            continue
        existing = {mapped[idx]['numberGroup'] for idx in range(a_idx, b_idx)}
        if len(existing) == 1 and None not in existing:
            continue
        span_start = mapped[a_idx]['start']
        span_end = max(span_start, mapped[b_idx - 1]['end'])
        gid = next_group
        next_group += 1
        for idx in range(a_idx, b_idx):
            mapped[idx]['start'] = span_start
            mapped[idx]['end'] = span_end
            mapped[idx]['numberGroup'] = gid
    return mapped


def character_map(data, text, clean, phonemes):
    words=[w for row in data['segments'] for w in row.get('words',[])]
    groups=[];i=0
    while i<len(words):
        word=words[i];raw=word['word'];end=word['end'];j=i+1
        numeric=bool(re.search(r'\d',raw))
        if numeric:
            while j<len(words) and re.search(r'[\d.]\s*$',raw) and re.match(r'\s*[\d.]',words[j]['word']) and words[j]['start']-end<.35:
                raw=raw.strip()+words[j]['word'].strip();end=words[j]['end'];j+=1
        following=words[j]['word'] if j<len(words) else ''
        letters=clean(raw+'年')[:-1] if re.search(r'\d{4}\s*$',raw) and following.lstrip(' ,，、').startswith('年') else clean(raw)
        groups.append((letters,float(word['start']),float(end),numeric));i=j
    chars=[]
    for group,(letters,start,end,numeric) in enumerate(groups):
        for k,c in enumerate(letters):
            # Numerals retain their measured group interval, never invented intra-number timing.
            a,b=(start,end) if numeric else (start+(end-start)*k/len(letters),start+(end-start)*(k+1)/len(letters))
            chars.append({'c':c,'start':a,'end':b,'numberGroup':group if numeric else None})
    actual=''.join(c['c'] for c in chars);expected=clean(text)
    if len(actual)==len(expected) and phonemes(actual)==phonemes(expected):
        return chars
    if data.get('alignmentMode')=='dialect' or data.get('dialect'):
        return _map_dialect_characters(chars, expected, text, clean, phonemes)
    raise ValueError('词级映射与原稿不一致；查看 alignment-check.json 的 numericMapping，不删除数字或重配音绕过。')


def safe_boundary(chars, index):
    return index<=0 or index>=len(chars) or chars[index].get('numberGroup') is None or chars[index].get('numberGroup')!=chars[index-1].get('numberGroup')
