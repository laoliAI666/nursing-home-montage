"""Expose source evidence and require explicit semantic review before paid generation."""
import re
from workflow import read,write,digest,clean,state


def fingerprint(job):
    return digest({'plan':read(job/'plan.json'),'inventory':read(job/'inventory.json')})


def audit(job):
    plan=read(job/'plan.json');facts={f['id']:f for f in plan.get('facts',[])};items=[];warnings=[];errors=[]
    def add(location,text,ids):
        unknown=set(ids)-set(facts)
        if unknown:errors.append(location+' 引用未知事实：'+','.join(sorted(unknown)))
        quotes=[facts[x]['quote'] for x in ids if x in facts]
        numbers=re.findall(r'\d+(?:\.\d+)?',text)
        source_numbers=set(re.findall(r'\d+(?:\.\d+)?',' '.join(quotes)))
        # Match complete numeral phrases, never a substring (700 is not 8700).
        source_phrases={clean(n).replace('两','二') for n in re.findall(r'[零〇一二两三四五六七八九十百千万亿点]+',' '.join(quotes))}
        if numbers and (not ids or any(n not in source_numbers and clean(n).replace('两','二') not in source_phrases and clean(n+'年')[:-1] not in source_phrases for n in numbers)):errors.append(location+' 的数字需要可核对的 factIds 原文引用。')
        if any(w in text for w in ('排得满','全覆盖','就在','保证','治愈','改善','延缓','㎡院区')):warnings.append(location+'：核对是否扩大频率、距离、面积口径或效果承诺。')
        if ('建筑面积' in ''.join(quotes)) and re.search(r'\d+(?:\.\d+)?\s*(?:㎡|平方米)\s*院区',text):errors.append(location+'：建筑面积不能改写为院区或占地面积。')
        if any('约'+n in ''.join(quotes) for n in numbers) and not any(w in text for w in ('约','大约','左右')):warnings.append(location+'：原文数值为约数，请保留确定程度。')
        items.append({'location':location,'text':text,'factIds':ids,'quotations':quotes})
    for i,s in enumerate(plan.get('sentences',[])):
        if s.get('kind','fact')=='fact':add(f'sentences[{i}]',s['text'],s.get('factIds',[]))
    cover=plan.get('cover',{})
    for i,label in enumerate(cover.get('labels',[])):add(f'cover.labels[{i}]',label['text'],label.get('factIds',[]))
    publication=plan.get('publication',{})
    for key in ('title','description'):
        if publication.get(key):add('publication.'+key,publication[key],publication.get('factIds',[]))
    report={'inputFingerprint':fingerprint(job),'structuralErrors':errors,'semanticWarnings':warnings,'sourceMatrix':items,'topic':plan.get('topic',{}),'coverTitle':cover.get('main_title'),
            'shots':[{'sentence':s['text'],'shots':s.get('shots',[])} for s in plan.get('sentences',[])],
            'reviewRequired':['主标题承诺由哪些正文段落兑现','逐项改写保留事实含义、约数与条件','逐镜确认实际素材，说明环境铺垫与直接展示的区别'],'semanticTruthProved':False}
    write(job/'content-audit.json',report);return report


def record(job,note):
    if not note.strip():raise ValueError('记录具体的主题、事实改写和文画对应复核结论。')
    report=audit(job)
    if report['structuralErrors']:raise ValueError('先修复 content-audit.json：'+'；'.join(report['structuralErrors']))
    result={'inputFingerprint':report['inputFingerprint'],'note':note,'semanticReviewRecorded':True,'automaticFactProof':False}
    write(job/'content-review.json',result);state(job,phase='content_reviewed',next='setup --install and preflight before voice');return result


def require_review(job):
    report=audit(job);p=job/'content-review.json'
    if report['structuralErrors']:raise ValueError('内容结构检查失败：'+'；'.join(report['structuralErrors']))
    if not p.is_file() or read(p).get('inputFingerprint')!=report['inputFingerprint']:raise ValueError('先查看 content-audit.json，核对原素材和事实，执行 review-content --note；计划变化需重新复核。')


def shot_audit(job):
    shots=read(job/'shot-list.json');usage={};overlaps=[];adjacent_same=[]
    for i,shot in enumerate(shots):
        if i>0 and shots[i-1]['asset']==shot['asset']:
            prev=shots[i-1]
            ov=max(0.0,min(shot['sourceStart']+shot['duration'],prev['sourceStart']+prev['duration'])-max(shot['sourceStart'],prev['sourceStart']))
            adjacent_same.append({'first':prev['id'],'second':shot['id'],'asset':shot['asset'],'sourceOverlapSeconds':round(ov,3)})
        for previous in usage.get(shot['asset'],[]):
            duration=min(shot['sourceStart']+shot['duration'],previous['sourceStart']+previous['duration'])-max(shot['sourceStart'],previous['sourceStart'])
            if duration>.1:overlaps.append({'first':previous['id'],'second':shot['id'],'sourceOverlapSeconds':round(duration,3),'adjacent':i>0 and shots[i-1]['id']==previous['id']})
        usage.setdefault(shot['asset'],[]).append(shot)
    adjacent_overlaps=[o for o in overlaps if o.get('adjacent')]
    short_shots=[{'id':s['id'],'duration':round(s['duration'],3)} for s in shots if s['duration']<1.0]
    result={'sourceUsageSeconds':{k:round(sum(s['duration'] for s in v),3) for k,v in usage.items()},
            'overlaps':overlaps,'adjacentOverlaps':adjacent_overlaps,'adjacentSameAsset':adjacent_same,
            'shortShots':short_shots,'requiresVisualReview':True}
    write(job/'shot-audit.json',result);return result
