"""Conservative evidence checks for text-only preview person references.

Positions are display-image coordinates, never detector/thumbnail order. Labels
are evidence, not complete inventories: a missing attribute means unknown.
This guard complements the model's structured evidence; it does not detect faces.
"""
import re
from photo_agent.person_request import ACTION_BOUNDARY

VERSION = 2
GENERIC = {'女生','女孩','女性','男孩','男生','男人','男性','先生','小朋友','孩子','小孩'}
_FOLD = str.maketrans({'张':'張','开':'開','睁':'睜','带':'帶','镜':'鏡','红':'紅','头':'頭','发':'髮','边':'邊','后':'後',
    '妈':'媽','儿':'兒','线':'線','浅':'淺','蓝':'藍','绿':'綠','丝':'絲','卷':'捲','马':'馬','阳':'陽','胡':'鬍'})


def identity_prefix(text):
    # Desired changes (hair colour, smile intensity, etc.) are not identity.
    prefix = re.split(ACTION_BOUNDARY, text.translate(_FOLD), maxsplit=1)[0]
    return text[:len(prefix)]


def position(people):
    result = {}
    for p in people:
        # face_box is validated server detector evidence. AI-only people have
        # no detector face box; keep the weaker person-box provenance explicit.
        box = p.get('face_box') or p.get('box')
        if not isinstance(box, dict) or not all(isinstance(box.get(k), (int, float)) for k in ('x', 'y', 'w', 'h')):
            continue
        result[p['preview_person_id']] = {'center_x': box['x'] + box['w'] / 2,
            'center_y': box['y'] + box['h'] / 2,
            'basis': 'detector_face' if p.get('face_box') else 'preview_person',
            'depth': 'unknown'}
    return result


def constraints(text, people):
    """Return recognized original qualifiers and their supported identity sets.

    Unknown qualifiers are not negative facts. Each set is in roster order.
    Model evidence handles descriptions outside this intentionally bounded
    vocabulary; recognised qualifiers cannot be silently omitted by the model.
    """
    prefix = identity_prefix(text)
    # In '媽媽抱著的寶寶', 媽媽 describes the carrier, not the target.
    prefix = re.sub(r'(?:媽媽|母親|爸爸|父親)(?:抱著的|肩上的)', '', prefix)
    prefix = re.sub(r'抱著(?:寶寶|嬰兒|孩子|小朋友)的?', '', prefix)
    source_prefix = prefix
    prefix = prefix.translate(_FOLD)
    labels = {p['preview_person_id']: p['label'].translate(_FOLD) for p in people}
    out = []
    def add(fragment, pattern, excluded=None):
        ids = [i for i,l in labels.items() if re.search(pattern,l) and not (excluded and re.search(excluded,re.sub(r'被.*?抱著的', '', l)))]
        start = prefix.index(fragment)
        out.append({'text': source_prefix[start:start+len(fragment)], 'candidate_ids': ids})
    for pattern, evidence in [
        (r'女孩|女生|女性|女子', r'女孩|女生|女性|女子|母親|媽媽'),
        (r'男孩|男生|男人|男性|男子|先生', r'男孩|男生|男人|男性|男子|先生|父親|爸爸'),
        (r'小朋友|小孩|孩子', r'小朋友|小孩|孩子|男孩|女孩|嬰兒|寶寶'),
        (r'寶寶|嬰兒', r'寶寶|嬰兒'),
        (r'爸爸|父親', r'爸爸|父親'), (r'媽媽|母親', r'媽媽|母親'),
        (r'(?:有|戴)?鬍(?:子|鬚)', r'鬍(?:子|鬚)'),
        (r'粉紅(?:色)?(?:太陽眼鏡|墨鏡)', r'粉(?:紅|紫).*?(?:太陽眼鏡|墨鏡)'),
        (r'(?:頭上|頭頂).*?(?:太陽眼鏡|墨鏡)', r'(?:頭上|頭頂).*?(?:太陽眼鏡|墨鏡)|(?:太陽眼鏡|墨鏡).*?(?:頭上|頭頂)'),
        (r'(?:透明|黑框)眼鏡', r'(?:透明|黑框).*?眼鏡'),
        (r'(?:紅|紅色)(?:針織)?帽', r'紅(?:色)?(?:針織)?帽'),
        (r'棒球帽', r'棒球帽'), (r'頭巾', r'頭巾'),
        (r'小馬尾', r'小馬尾'), (r'髮箍|髮帶', r'髮箍|髮帶|頭箍'),
        (r'紅(?:色)?(?:長)?捲髮', r'紅.*?捲髮'),
        (r'金色長髮', r'(?:金色|淺金).*長髮'), (r'紫(?:色)?(?:頭髮|髮)', r'紫.*?(?:短髮|髮)'),
        (r'白(?:色)?(?:外套|上衣|衣)', r'白(?:色)?(?:外套|上衣|衣)'),
        (r'紅(?:色)?(?:上衣|衣)', r'紅(?:色)?(?:上衣|衣)'),
        (r'粉紅(?:色)?外套', r'粉紅(?:色)?外套'),
        (r'紫(?:色)?(?:襯衫|衣)', r'紫(?:色)?(?:襯衫|衣)'),
        (r'牛仔吊帶', r'牛仔吊帶'), (r'拿(?:黑色)?手機', r'手機'),
        (r'拿披薩', r'拿披薩'),
    ]:
        found = re.search(pattern,prefix)
        if found:
            excluded = r'男性|女性|爸爸|媽媽|父親|母親' if found.group() in ('寶寶','嬰兒','小朋友','小孩','孩子') else None
            add(found.group(), evidence, excluded)
    # Bare '眼鏡' does not establish that sunglasses on the head are glasses
    # worn on the face. Ask rather than silently broaden a conflicting phrase.
    glasses = re.search(r'(?<!陽)(?:[戴帶])?眼鏡',prefix)
    if glasses: add(glasses.group(), r'眼鏡', r'未戴|沒戴|太陽眼鏡|墨鏡')
    shoulder = re.search(r'肩(?:膀)?上',prefix)
    if shoulder: add(shoulder.group(), r'坐.*?肩上|肩上的男孩')
    centers = position(people)
    spatial = []
    ordinal = re.search(r'(?:從)?(左|右)(?:邊|側)?(?:數(?:來)?)?第\s*([一二三四五六七八九十0-9]+)\s*(?:位|個|人)', prefix)
    if ordinal:
        number = ordinal[2]
        rank = int(number) if number.isdigit() else {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}.get(number)
        ordered = sorted(centers, key=lambda i: centers[i]['center_x'], reverse=ordinal[1]=='右')
        ids = [ordered[rank-1]] if rank and 0 < rank <= len(ordered) and len(ordered)==len(people) else []
        spatial.append({'text': ordinal.group(), 'candidate_ids':ids})
    for axis, words, extremes in [
        ('center_x', r'左(?:側|邊|下角|上角)?|右(?:側|邊|下角|上角)?', ('左','右')),
        ('center_y', r'上方|下方|左下角|右下角|左上角|右上角|左下|右下', ('上','下')),
    ]:
        found = re.search(words,prefix)
        if ordinal:
            continue
        if not found: continue
        fragment = found.group()
        # Object-relative side ('左邊髮夾附近') is not the person's location.
        if axis == 'center_x' and re.search(r'(?:左|右)邊.*?(?:髮夾|手機).*?(?:附近|旁)', prefix): continue
        side = extremes[0] if extremes[0] in fragment else extremes[1]
        evidence = side + r'(?:側|邊|方|下|上)' if axis=='center_x' else (r'上方|上角|左上|右上' if side=='上' else r'下方|下角|左下|右下')
        ids = [i for i,l in labels.items() if re.search(evidence, re.split(r'搭著|搭在|看著', l)[0])]
        if centers and len(centers) == len(people) and len(people) > 1:
            values = [p[axis] for p in centers.values()]
            span = max(values)-min(values)
            if span > .08:
                mid = (min(values)+max(values))/2
                ids = [i for i,p in centers.items() if (p[axis] < mid-span*.08 if side==extremes[0] else p[axis] > mid+span*.08)]
        spatial.append({'text': fragment, 'candidate_ids': ids})
    # Front/back are depth/scene descriptions, not a y-axis inference.
    for pattern in (r'前(?:方|面|排)',r'後(?:方|面|排)'):
        found=re.search(pattern,prefix)
        if found:
            spatial.append({'text': found.group(), 'candidate_ids': [i for i,l in labels.items() if re.search(pattern,l)]})
    if spatial:
        # '右下角' / '前排左下' is one position constraint. Do not union its
        # separate axes and accidentally include everyone on the right/front.
        ids=set.intersection(*(set(q['candidate_ids']) for q in spatial))
        start=min(prefix.find(q['text']) for q in spatial)
        end=max(prefix.find(q['text'])+len(q['text']) for q in spatial)
        out.append({'text':source_prefix[start:end], 'candidate_ids':[i for i in labels if i in ids]})
    return out


def check(text, people, ids, groups=None, explicit=False):
    """Restrict proposals using all qualifiers; never select new targets here."""
    known = [p['preview_person_id'] for p in people]
    checks = constraints(text, people)
    # Explicit plural references are evaluated as separate mentions by the
    # model. Global intersection would confuse 'left girl AND right boy'.
    plural = bool(re.search(r'和|以及|跟|、', identity_prefix(text)))
    if groups:
        pools, partial, uncertain = [], set(), False
        for group in groups:
            qualifiers=group['qualifiers']
            local=constraints(group['referent'], people)
            # Recognised atomic facts use the server's full support set, not
            # a model's arbitrarily narrowed version ('小朋友' => first child).
            facts = {q['text']: q['candidate_ids'] for q in local}
            evidence = [{**q, 'candidate_ids': facts.get(q['text'], q['candidate_ids'])} for q in qualifiers] + local
            supports=[set(q['candidate_ids']) for q in evidence]
            pool=set.intersection(*supports) if supports else set()
            pools.append(pool)
            partial.update(set.union(*supports) if supports else set())
            uncertain |= not qualifiers or any(not q['candidate_ids'] for q in evidence)
        support=set.union(*pools) if pools else set()
        if all(not q['candidate_ids'] for g in groups for q in g['qualifiers']):
            return 'unmatched', [], checks
        if uncertain or not support or (not set(ids)<=support if explicit else set(ids)!=support):
            specific=[set(q['candidate_ids']) for q in checks if q['text'] not in GENERIC and q['candidate_ids']]
            if specific:
                narrowed=set.union(*specific)
                # One position vs a gender can itself be the conflict (pool
                # front boy / girl). Multiple specific facts already provide
                # more useful partial candidates than every same-gender face.
                if len(specific)==1:
                    narrowed.update(i for q in checks if q['text'] in GENERIC for i in q['candidate_ids'])
                partial=narrowed
            return ('ambiguous' if partial else 'unmatched'), [i for i in known if i in partial], checks
        # The original reference cannot lose a recognized qualifier, even when
        # a model supplied a smaller group/referent substring.
        if any(q['text'] not in ' '.join(g['referent'] for g in groups) for q in checks):
            return 'ambiguous', [i for i in known if i in partial or i in ids], checks
    if checks and not plural:
        sets=[set(c['candidate_ids']) for c in checks]
        supported=set.intersection(*sets)
        if not supported or not set(ids)<=supported or (len(supported)>1 and len(ids)==1 and not groups and not explicit):
            candidates=set.union(*sets)
            # Broad gender is not useful as a candidate list when more precise
            # position/appearance supports exist. Prefer the partial specifics.
            specifics=[set(c['candidate_ids']) for c in checks if c['text'] not in GENERIC and c['candidate_ids']]
            if specifics: candidates=set.union(*specifics)
            unknown = any(not s for s in sets)
            return ('unmatched' if unknown and not specifics else 'ambiguous'), [i for i in known if i in candidates], checks
    return 'matched', [], checks
