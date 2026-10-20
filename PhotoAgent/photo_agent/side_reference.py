"""Source-grounded body-side reference frames. No vision or model calls.

Parse noun-phrase roles before choosing a frame: a person's location, motion
direction and quoted printed content are not anatomical-side instructions.
All offsets refer to original_text; compilation replaces those offsets only.
"""
import copy
import hashlib
import re

VERSION=1
ERROR='unresolved_body_side_reference'
PARTS={'手':'hand','手掌':'hand','手臂':'arm','胳膊':'arm','手腕':'wrist','手指':'finger',
       '腳':'foot','腿':'leg','腳踝':'ankle','膝蓋':'knee','肩':'shoulder','肩膀':'shoulder',
       '臉':'cheek','臉頰':'cheek','眼':'eye','眼睛':'eye','眉毛':'eyebrow','耳':'ear','耳朵':'ear','手肘':'elbow'}
NAMES={'hand':'手','arm':'手臂','wrist':'手腕','finger':'手指','foot':'腳','leg':'腿',
       'ankle':'腳踝','knee':'膝蓋','shoulder':'肩膀','cheek':'臉頰','face':'臉',
       'eye':'眼睛','eyebrow':'眉毛','ear':'耳朵','elbow':'手肘'}
_PART='|'.join(sorted(PARTS,key=len,reverse=True))
_REF=re.compile(r'(?:(?P<image>畫面|照片|螢幕)(?:的)?)?(?P<side>左右|左|右)'
    r'(?P<middle>側可見的那隻|邊可見的那隻|側可見的|邊可見的|側那隻|邊那隻|側的那隻|邊的那隻|側的|邊的|側|半|邊)?(?P<part>'+_PART+r')')
_QUOTES=re.compile(r'「([^」]*)」|『([^』]*)』|"([^"\n]*)"|“([^”]*)”')
_CLAUSE=re.compile(r'[；;，,、。\n]')


def digest(text):return hashlib.sha256(text.encode()).hexdigest()


def _quoted(text):
    spans=[];literals=[]
    for m in _QUOTES.finditer(text):
        left=max([x.end() for x in _CLAUSE.finditer(text[:m.start()])]+[0])
        right=next((x.start()+m.end() for x in _CLAUSE.finditer(text[m.end():])),len(text))
        clause=text[left:right]
        if re.search(r'文字|字樣|寫|放.{0,25}(?:照片|圖片)|(?:照片|圖片).{0,12}放',clause):
            spans.append((m.start(),m.end()))
            value=next(g for g in m.groups() if g is not None)
            if value not in literals:literals.append(value)
    # Unquoted numeric strings are literal only in explicit write/place clauses.
    for m in re.finditer(r'(?:寫|放)(\d+)',text):
        if not any(a<=m.start(1)<b for a,b in spans):literals.append(m.group(1))
    for m in re.finditer(r'把(\d+)放(?:在|到)(?:照片|圖片|畫面)',text):literals.append(m.group(1))
    return spans,list(dict.fromkeys(literals))


def inspect(text,context=None):
    if not isinstance(text,str) or len(text)>12000:raise ValueError('invalid_side_source')
    context=copy.deepcopy(context or {})
    quoted,literals=_quoted(text);refs=[]
    directives=[]
    for pattern,frame in [(r'(?:以|左右皆以)(?:人物自己|人物自身|人物的左右|人物自己的左右)(?:的左右)?(?:為準|為主)','subject'),
                          (r'(?:以|左右皆以)(?:照片畫面|畫面|照片)(?:左右)?為準','image')]:
        directives.extend((m.start(),m.end(),frame) for m in re.finditer(pattern,text))
    directives.sort()
    matches=[m for m in _REF.finditer(text) if not any(a<=m.start()<b for a,b in quoted)
        and not (m.group('part')=='手' and text[m.end():].startswith('邊'))]
    incomplete=False;owner=''
    for i,m in enumerate(matches):
        end=next((x.start() for x in _CLAUSE.finditer(text,m.end())),len(text))
        if i+1<len(matches):end=min(end,matches[i+1].start())
        tail=text[m.end():end].strip(' 的與和跟、，,；;（()）')
        tail=re.sub(r'再?(?:把|讓)?$','',tail).strip()
        prefix_action=bool(re.search(r'(?:不要|別|保持|改動|抬高|放下|舉起).{0,20}$',text[:m.start()]))
        shared_action=(i+1<len(matches) and re.fullmatch(r'[與和跟及\s]*',text[m.end():matches[i+1].start()])
            and bool(text[matches[i+1].end():].strip()))
        if not tail and not prefix_action and not shared_action:
            incomplete=True;continue
        before=text[max(0,m.start()-35):m.start()]
        segment_start=max([x.end() for x in re.finditer(r'[；;。\n]',text[:m.start()])]+[0])
        segment_end=next((x.start()+m.end() for x in re.finditer(r'[；;。\n]',text[m.end():])),len(text))
        prior=[d for d in directives if segment_start<=d[0]<m.start()]
        later=[d for d in directives if m.end()<=d[0]<segment_end]
        global_frame=prior[-1][2] if prior else later[0][2] if later else None
        clause_start=max([x.end() for x in _CLAUSE.finditer(text[:m.start()])]+[0])
        owner_phrase=re.search(r'(.{0,30}(?:女生|女性|女孩|男生|男子|男性|軍人|小孩))(?:自己)?的?$',text[clause_start:m.start()])
        if owner_phrase:owner=owner_phrase.group(1).lstrip('把讓請')
        frame='image' if m.group('image') else global_frame
        if not frame and re.search(r'(?:自己(?:的)?|人物自身(?:角度)?(?:的)?|從人物視角|主體人物的)$',before):frame='subject'
        if not frame and context.get('same_live_request_chain') is True and context.get('explicit_prior_frame') in ('subject','image'):
            frame=context['explicit_prior_frame'];resolution='context_confirmed'
        else:resolution='explicit' if frame else 'pending'
        frame=frame or 'unresolved'
        part=PARTS[m.group('part')]
        if m.group('part')=='臉' and m.group('middle') in ('側','半'):part='face'
        sides=['left','right'] if m.group('side')=='左右' else ['left' if m.group('side')=='左' else 'right']
        for number,side in enumerate(sides):
            identity=digest(text+':'+str(m.start())+':'+side)[:20]
            refs.append(dict(version=VERSION,side_ref_id='sr_'+identity,request_id='sa_'+identity,
                part=part,side=side,reference_frame=frame,source_phrase=m.group(),resolution=resolution,
                start=m.start(),end=m.end(),operation_end=end,owner=owner))
    labels=context.get('selected_labels') or []
    if labels:
        bound=[]
        for r in refs:
            if r['owner']:bound.append(r);continue
            for label in labels:
                c=copy.deepcopy(r);c['owner']=label
                c['side_ref_id']='sr_'+digest(r['side_ref_id']+'|'+label)[:20]
                c['request_id']='sa_'+digest(r['request_id']+'|'+label)[:20]
                bound.append(c)
        refs=bound
    pending=[r for r in refs if r['reference_frame']=='unresolved']
    person_missing=(context.get('people_count',1)>1 and not context.get('selected_person_ids')
        and (context.get('referent_is_ambiguous') or not context.get('target_referent')))
    decision='clarify_action' if incomplete else 'clarify_person' if refs and person_missing else 'clarify_side' if pending else 'pass'
    result=dict(version=VERSION,original_text=text,canonical_text=text,side_references=refs,
        decision=decision,question_count=int(decision=='clarify_side'),literal_texts=literals,
        revision=context.get('source_revision',1),photo_identity=context.get('photo_id',''),context=context,source_digest=digest(text))
    if any(r['resolution']=='context_confirmed' for r in refs):result['resolution']='context_confirmed'
    if re.search(r'不要|別|不改|保持',text):result['scope']='protection'
    result['canonical_text']=compile_text(result)
    return result


def compile_text(state):
    text=state['original_text'];groups={}
    for r in state['side_references']:
        if r['reference_frame']=='unresolved':continue
        key=(r['start'],r['end']);name=NAMES[r['part']];side='左' if r['side']=='left' else '右'
        replacement=('人物自己的'+side+name if r['reference_frame']=='subject' else
            '畫面'+side+'側可見的那隻'+name)
        # Own-side qualifiers in the original belong to the same noun phrase.
        a,b=key
        if r['reference_frame']=='subject':
            prefix=re.search(r'(?:人物自己的|人物自身角度的|從人物自身角度的|從人物視角|自己的)$',text[:a])
            if prefix:a=prefix.start()
            elif a and text[a-1]=='的':
                a-=1;replacement='自己的'+side+name
        target=r.get('owner') or state.get('context',{}).get('target_label')
        if target and target not in text[max(0,a-40):a]:
            replacement=(target+'自己的'+side+name if r['reference_frame']=='subject' else target+'在畫面'+side+'側可見的那隻'+name)
        groups.setdefault((a,b),[]).append(replacement)
    for (a,b),values in sorted(groups.items(),reverse=True):text=text[:a]+'與'.join(values)+text[b:]
    return text


def resolve(state,frame,ref_ids=None):
    if frame not in ('subject','image'):raise ValueError('invalid_reference_frame')
    new=copy.deepcopy(state);pending=[r for r in new['side_references'] if r['reference_frame']=='unresolved']
    group=[r for r in pending if r.get('owner','')==pending[0].get('owner','')] if pending else []
    wanted=set(ref_ids) if ref_ids is not None else {r['side_ref_id'] for r in group}
    if not wanted or not wanted<={r['side_ref_id'] for r in pending}:raise ValueError('invalid_side_ref_ids')
    if wanted!={r['side_ref_id'] for r in group}:raise ValueError('incomplete_side_group')
    for r in group:r.update(reference_frame=frame,resolution='user_confirmed')
    more=any(r['reference_frame']=='unresolved' for r in pending)
    new.update(canonical_text=compile_text(new),decision='clarify_side' if more else 'pass',question_count=int(more),revision=state['revision']+1)
    return new


def change(state,ref_id,frame,side):
    if frame not in ('subject','image') or side not in ('left','right'):raise ValueError('invalid_side_change')
    new=copy.deepcopy(state)
    found=[r for r in new['side_references'] if r['side_ref_id']==ref_id]
    if len(found)!=1:raise ValueError('invalid_side_ref_id')
    found[0].update(reference_frame=frame,side=side,resolution='user_confirmed')
    new.update(canonical_text=compile_text(new),revision=state['revision']+1)
    return new


def remove(state,request_ids):
    ids=set(request_ids);refs=[r for r in state['side_references'] if r['request_id'] in ids]
    if len(refs)!=len(ids):raise ValueError('invalid_side_request_ids')
    new=copy.deepcopy(state);remaining=[r for r in new['side_references'] if r['request_id'] not in ids]
    cuts=[]
    for r in refs:
        if any(x['start']==r['start'] and x['end']==r['end'] for x in remaining):continue
        a=r['start'];b=r['operation_end']
        prefix=re.search(r'(?:人物自己的|自己的)$',state['original_text'][:a])
        if prefix:a=prefix.start()
        if b<len(state['original_text']) and state['original_text'][b] in '；;、，,':b+=1
        cuts.append((a,b))
    text=state['original_text']
    for a,b in sorted(set(cuts),reverse=True):text=text[:a]+text[b:]
    for r in remaining:
        for key in ('start','end','operation_end'):
            r[key]-=sum(b-a for a,b in cuts if b<=r[key])
    new.update(original_text=text,side_references=remaining,revision=state['revision']+1,source_digest=digest(text))
    new['canonical_text']=compile_text(new)
    new['decision']='clarify_side' if any(r['reference_frame']=='unresolved' for r in remaining) else 'pass'
    new['question_count']=int(new['decision']=='clarify_side')
    return new


def cancelled_requests(state,text):
    if not re.search(r'不用|不拿|取消|不要.{0,3}拿',text):return []
    ids=[]
    for r in state['side_references']:
        operation=state['original_text'][r['end']:r['operation_end']]
        noun=re.sub(r'^(?:也|再|分別)?(?:拿|持|撐|戴|放|抬|舉)(?:著|着|上|起|高)?','',operation).strip()
        if noun and any(re.search(r'不用|不拿|取消|不要.{0,3}拿',clause) and noun in clause
                for clause in re.split(r'[，,；;、。]',text)):
            ids.append(r['request_id'])
    return ids


def validate_answer(state,revision,photo_identity):
    if type(revision) is not int or revision!=state['revision'] or photo_identity!=state['photo_identity']:
        raise ValueError('stale_side_question')


def answer_frame(text):
    if not isinstance(text,str):return None
    subject=bool(re.search(r'人物自己|人物自身|人物視角|自己的',text))
    image=bool(re.search(r'畫面|照片(?:的)?左右|螢幕',text))
    return 'subject' if subject and not image else 'image' if image and not subject else None


def executable(state):
    return state.get('decision')=='pass' and all(r['reference_frame']!='unresolved' for r in state['side_references'])


def guard(text,state=None):
    if state is not None:
        state=validate_state(state)
        if not executable(state):raise ValueError(ERROR)
    parsed=inspect(text)
    if parsed['decision']!='pass' or any(r['reference_frame']=='unresolved' for r in parsed['side_references']):
        raise ValueError(ERROR)
    return parsed


def validate_state(state):
    required={'version','original_text','canonical_text','side_references','decision','question_count',
        'literal_texts','revision','photo_identity','context','source_digest'}
    if not isinstance(state,dict) or not required<=set(state) or set(state)-required-{'scope','resolution'}:
        raise ValueError('invalid_side_state')
    if type(state['version']) is not int or state['version']!=VERSION or type(state['revision']) is not int or state['revision']<1:
        raise ValueError('invalid_side_version')
    if not isinstance(state['original_text'],str) or len(state['original_text'])>12000 or state['source_digest']!=digest(state['original_text']):raise ValueError('invalid_side_digest')
    if not isinstance(state['context'],dict) or not isinstance(state['photo_identity'],str) or state['decision'] not in ('pass','clarify_side','clarify_person','clarify_action'):
        raise ValueError('invalid_side_context')
    if type(state['question_count']) is not int or not isinstance(state['literal_texts'],list) or any(not isinstance(s,str) or s not in state['original_text'] for s in state['literal_texts']):raise ValueError('invalid_side_literals')
    if not isinstance(state['side_references'],list) or len(state['side_references'])>100:raise ValueError('invalid_side_refs')
    fields={'version','side_ref_id','request_id','part','side','reference_frame','source_phrase','resolution','start','end','operation_end','owner'}
    seen=set()
    for r in state['side_references']:
        if not isinstance(r,dict) or set(r)!=fields or type(r['version']) is not int or r['version']!=VERSION:raise ValueError('invalid_side_ref')
        if not isinstance(r['part'],str) or r['part'] not in NAMES or r['side'] not in ('left','right') or r['reference_frame'] not in ('subject','image','unresolved') or r['resolution'] not in ('pending','explicit','context_confirmed','user_confirmed'):
            raise ValueError('invalid_side_frame')
        if not isinstance(r['owner'],str) or len(r['owner'])>4000:raise ValueError('invalid_side_owner')
        if any(type(r[k]) is not int for k in ('start','end','operation_end')) or not 0<=r['start']<r['end']<=r['operation_end']<=len(state['original_text']):raise ValueError('invalid_side_span')
        if any(not isinstance(r[k],str) or not re.fullmatch(r'[\w-]{1,160}',r[k]) for k in ('side_ref_id','request_id')):raise ValueError('invalid_side_id')
        if state['original_text'][r['start']:r['end']]!=r['source_phrase'] or r['side_ref_id'] in seen:raise ValueError('invalid_side_evidence')
        if (r['reference_frame']=='unresolved') != (r['resolution']=='pending'):raise ValueError('invalid_side_resolution')
        seen.add(r['side_ref_id'])
    if state['canonical_text']!=compile_text(state):raise ValueError('side_canonical_mismatch')
    return copy.deepcopy(state)


def question(state,question_id):
    refs=[r for r in state['side_references'] if r['reference_frame']=='unresolved']
    if not refs:return None
    refs=[r for r in refs if r.get('owner','')==refs[0].get('owner','')]
    if len(refs)==1:
        r=refs[0];side='左' if r['side']=='left' else '右';name=NAMES[r['part']]
        label=(r.get('owner','')+'的' if r.get('owner') else '')+r['source_phrase'];labels=['人物自己的'+side+name,'畫面'+side+'邊的'+name]
    else:
        label='、'.join(state['original_text'][r['start']:r['operation_end']].strip('；;、，, ') for r in refs)
        if refs[0].get('owner'):label=refs[0]['owner']+'：'+label
        labels=['以人物自己左右為準','以照片畫面左右為準']
    return dict(id=question_id,ref_ids=[r['side_ref_id'] for r in refs],source_revision=state['revision'],
        photo_identity=state['photo_identity'],original_text=state['original_text'],text='你說的「'+label+'」，是以哪個角度為準？' if len(refs)>1 else '你說的「'+label+'」是以哪個角度為準？',
        choices=[dict(value=v,label=label) for v,label in zip(('subject','image'),labels)])


def editor_instruction(text):
    state=guard(text)
    if not state['side_references']:return ''
    return ('\nBODY SIDE REFERENCE CONTRACT (takes precedence over generic pose suggestions):\n'
        +text+'\nSubject-relative sides refer to that person\'s own anatomy; image-relative sides '
        'refer to the visible side of the supplied input photograph. Never swap left/right, infer '
        'a front-facing mapping, mirror the image, or substitute a different limb. Keep each '
        'operation attached to its named person. If the requested limb is occluded, do not invent '
        'a visible limb or additional arm. Preserve every exact printed string and unrelated edit.\n')
