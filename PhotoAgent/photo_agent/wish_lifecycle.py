"""Conservative operations on already bound people; never detect/rebind people."""
import copy
import re
from photo_agent import person_reference

VERSION = 1

def operation(text):
    t = text.translate(str.maketrans('张开镜发丝头视线调', '張開鏡髮絲頭視線調'))
    # Effect constraints are not withdrawals.
    if re.search(r'不要.*(?:過度|太直|太亮|太白|過頭)', t):
        return None
    if re.search(r'眼睛|張眼|睜眼|閉眼', t):
        if re.search(r'不要.*(?:張開|睜開|張眼|睜眼)|(?:取消|撤回).*?(?:張眼|張開|睜)', t): return ('eyes', 'cancel', '張開眼睛')
        if re.search(r'不要.*(?:閉眼|閉上)', t): return ('eyes', 'cancel', '閉上眼睛')
        if re.search(r'張開|睜開|張眼|睜眼|修正閉眼|閉眼修正', t): return ('eyes', 'set', '張開眼睛')
        if re.search(r'取消|撤回',t): return ('eyes','cancel',None)
        if re.search(r'閉眼|閉上', t): return ('eyes', 'set', '閉上眼睛')
    if re.search(r'視線|看鏡頭|看畫面|看左邊|看右邊', t):
        if re.search(r'畫面左|看左', t): return ('gaze', 'set', '視線看畫面左邊')
        if re.search(r'畫面右|看右', t): return ('gaze', 'set', '視線看畫面右邊')
        if '鏡頭' in t: return ('gaze', 'set', '視線看鏡頭')
    if re.search(r'髮絲|頭髮|張髮', t) and re.search(r'整理|保持原樣', t):
        if re.search(r'不要.*整理|不整理|整理.*不要|保持原樣|取消|撤回', t): return ('hair', 'cancel', '髮絲整理')
        return ('hair', 'set', '只整理露出的髮絲' if '露出' in t else '髮絲整理')
    if re.search(r'笑|微笑', t):
        if re.search(r'取消|撤回',t): return ('smile','cancel','平靜不微笑的表情' if re.search(r'不要笑|不微笑',t) else '自然笑容' if '自然' in t else None)
        if re.search(r'不要笑|不微笑|平靜', t): return ('smile', 'set', '平靜不微笑的表情')
        if re.search(r'自然', t): return ('smile', 'set', '自然笑容')
    return None


def project(state, people):
    """Project source order into per-person effective clauses, idempotently."""
    out = copy.deepcopy(state)
    out.pop('lifecycle_changes', None)
    labels = {p['preview_person_id']: p['label'] for p in people}
    slots = {}
    changes = []
    clauses = []
    for source in out['clauses']:
        ids = source.get('target_ids', []) or source.get('protected_ids', [])
        op = operation(source['text'])
        if source.get('status') != 'matched' or (source.get('scope') != 'edit' and not (op and op[1] == 'cancel' and source.get('scope') == 'protection')) or not ids or not op or source.get('edit_scope_pending'):
            clauses.append(source)
            continue
        feature, intent, action = op
        # Split shared edits, retaining the original request identity.
        for person in ids:
            c = copy.deepcopy(source)
            c['target_ids'] = [person]
            if len(ids) > 1:
                c['wish_id'] = source['wish_id'] + '-person-' + str(ids.index(person))
                c['parent_wish_id'] = source.get('parent_wish_id', source['wish_id'])
            c['operation'] = {'feature': feature, 'intent': intent, 'action': action}
            c['display_text'] = display(c, labels)
            c['display_target_ids'] = [person]
            prior = slots.get((person, feature))
            if intent == 'cancel':
                if prior and (feature not in ('eyes','smile') or action is None or prior['operation']['action']==action):
                    prior['status'] = 'cancelled'
                    changes.append({'mode':'cancel','text':prior['display_text']})
                    slots.pop((person, feature), None)
                    c['status'] = 'cancelled'
                    c['display_text'] = prior['display_text']
                    c['cancelled_from'] = {'target_ids':[person], 'text':prior['display_text']}
                else:
                    # No prior operation: keep the original constraint, never
                    # claim cancellation or invent a forced opposite effect.
                    c['scope'] = 'protection'
                    c['protected_ids'] = [person]
                    c['target_ids'] = []
                    c['display_text'] = labels[person] + '：' + source['text']
            else:
                if prior:
                    prior['status'] = 'superseded'
                    changes.append({'mode':'replace','text':c['display_text']})
                slots[(person, feature)] = c
            clauses.append(c)
    # Preserve a shared clause when all people retain the same status; split
    # only when a later operation actually affects a subset of that group.
    grouped = []
    groups = {}
    for c in clauses:
        parent = c.get('parent_wish_id')
        if not parent:
            grouped.append(c)
            continue
        key = (parent, c['status'], c['scope'], c['text'])
        if key not in groups:
            groups[key] = c
            grouped.append(c)
        else:
            prior = groups[key]
            prior['target_ids'] = list(dict.fromkeys(prior['target_ids'] + c['target_ids']))
            prior['protected_ids'] = list(dict.fromkeys(prior.get('protected_ids',[]) + c.get('protected_ids',[])))
            prior['display_text'] = display(prior, labels)
            prior['display_target_ids'] = prior['target_ids'] or prior.get('protected_ids',[])
    parent_counts = {}
    for c in grouped:
        if c.get('parent_wish_id'):
            parent_counts[c['parent_wish_id']] = parent_counts.get(c['parent_wish_id'],0)+1
    for c in grouped:
        if c.get('parent_wish_id') and parent_counts[c['parent_wish_id']] == 1:
            c['wish_id'] = c.pop('parent_wish_id')
    out['clauses'] = grouped
    out['lifecycle_version'] = VERSION
    if changes:
        out['lifecycle_changes'] = changes
    return out


def change_reply(changes, remaining):
    parts = [('已取消「' if c['mode']=='cancel' else '已改為「') + c['text'] + '」的需求。' for c in changes]
    parts.append('目前保留：' + remaining + '。' if remaining else '目前沒有其他個人化需求。')
    return ''.join(parts)


def display(c, labels):
    ids = c.get('target_ids') or c.get('protected_ids') or []
    who = '、'.join(labels[i] for i in ids)
    line = c['text']
    if c.get('edit_scope') == 'visible_hair':
        return who + '：只整理露出的髮絲'
    # Preserve the action/limits verbatim while replacing the whole validated
    # source descriptor, including verb-first hair requests.
    hair = re.match(r'^((?:請|麻煩|幫我|請幫我)*整理)(.+?)(的(?:飛散)?(?:髮絲|頭髮).*)$',line)
    if hair and c.get('scope')=='edit':
        return hair[1] + who + hair[3]
    prefix = person_reference.identity_prefix(line)
    action = line[len(prefix):]
    has_person = c.get('referent') and c['referent'] in prefix
    if action and (has_person or c.get('binding_source') in ('user_confirmation','user_correction','explicit_revision')):
        if prefix.endswith('不要'): action='不要'+action
        lead = re.match(r'^(?:(?:請|麻煩|幫我|請幫我)*把?)',prefix).group()
        return lead + who + ('的' if prefix.endswith('的') else '') + action
    return line.replace(c['referent'], who, 1) if c.get('referent') else who + '：' + line


def local_revision(state, people, text, selected=()):
    op = operation(text)
    if not op: return None
    feature, intent, action = op
    active = [c for c in state['clauses'] if c.get('status')=='matched' and c.get('scope')=='edit'
              and operation(c['text']) and operation(c['text'])[0]==feature and (not c.get('edit_scope_pending') or intent=='cancel')]
    if intent=='cancel' and selected and not any(set(c['target_ids']) & set(selected) for c in active):
        cancelled = [c for c in state['clauses'] if c.get('status')=='cancelled' and operation(c['text']) and operation(c['text'])[0]==feature
                     and set(c.get('target_ids') or c.get('cancelled_from',{}).get('target_ids') or []) & set(selected)]
        if cancelled:
            out=copy.deepcopy(state)
            out['revision_mode']='no_change'
            out['affected_request_ids']=[]
            out['reply']='這項需求已取消，其他目前有效需求維持不變。'
            return out
    if not active:
        if intent=='cancel' and action is None:
            out=copy.deepcopy(state);out.update(revision_mode='no_change',affected_request_ids=[],reply='目前沒有這項已確認修改需求。')
            return out
        return None
    known = {p['preview_person_id'] for p in people}
    if set(selected)-known: raise ValueError('invalid_revision_identity')
    targets = list(dict.fromkeys(selected))
    if targets and person_reference.check(text, people, targets, explicit=True)[0] != 'matched':
        return clarify(state, people, text, active)
    if not targets:
        evidence = person_reference.constraints(text, people)
        if evidence:
            support = set.intersection(*(set(q['candidate_ids']) for q in evidence))
            if len(support)==1: targets=list(support)
            else: return clarify(state, people, text, active)
        else:
            candidates = set(i for c in active for i in c['target_ids'])
            if len(candidates)==1: targets=list(candidates)
            else: return clarify(state, people, text, active)
    if intent=='set' and not re.search(r'改成|改為|改看|不要笑|不微笑', text): return None
    affected = [c for c in active if set(c['target_ids']) & set(targets) and (intent!='cancel' or feature not in ('eyes','smile') or action is None or operation(c['text'])[2]==action)]
    if not affected: return None
    updated = copy.deepcopy(state)
    if intent=='cancel':
        for c in updated['clauses']:
            if c['wish_id'] in {a['wish_id'] for a in affected}: c.pop('edit_scope_pending',None)
    # One new clause per target; anchor to the source request so removing that
    # request also removes its later versions on a subsequent raw replay.
    for index, person in enumerate(targets):
        prior = next((c for c in affected if person in c['target_ids']), None)
        if not prior: continue
        c = copy.deepcopy(prior)
        c.update(wish_id=f"revision-{state.get('revision',0)+1}-{index}",
                 origin_wish_id=prior.get('origin_wish_id', prior.get('parent_wish_id',prior['wish_id'])),
                 lifecycle_revision=True, text=text, source_text=text, reference_text=text,
                 referent=person_reference.identity_prefix(text), target_ids=[person], protected_ids=[],
                 status='matched', scope='edit', binding_source='user_correction')
        c.pop('display_text', None)
        c.pop('operation', None)
        c.pop('edit_scope_pending',None)
        # A selected avatar is authority even when the correction is just
        # "不要張開眼睛"; keep the operation phrase instead of dropping 不要.
        c['referent']=''
        c['binding_source']='explicit_revision'
        updated['clauses'].append(c)
    updated=project(updated,people)
    updated['revision_mode']='cancel' if intent=='cancel' else 'replace'
    updated['affected_request_ids']=list(dict.fromkeys(c['request_id'] for c in affected))
    return updated


def clarify(state, people, text, candidates):
    out=copy.deepcopy(state)
    labels={p['preview_person_id']:p['label'] for p in people}
    out['revision_question']={'question_id':'lifecycle-choice', 'kind':'wish',
        'wish_ids':[c['wish_id'] for c in candidates], 'lifecycle_text':text,
        'text':'這次要變更哪一項已確認需求？請選擇人物與操作；其他需求先保留。',
        'wish_options':[{'wish_id':c['wish_id'],'text':display(c,labels)} for c in candidates]}
    out['reply']=out['revision_question']['text']
    out['revision_mode']='ambiguous'
    out['affected_request_ids']=[]
    return out


def bind_unambiguous(clauses, people):
    """Bind only a unique intersection of server-known textual facts.

    This supports rebase during a concurrent reply; no model guess, new ID,
    detector order or last-mentioned pronoun becomes identity authority.
    """
    out=copy.deepcopy(clauses)
    for c in out:
        if c['status'] not in ('pending','ambiguous','unmatched') or c.get('target_ids') or c.get('awaiting_confirmation'): continue
        facts=person_reference.constraints(c.get('reference_text',c['text']),people)
        if not facts: continue
        residue=person_reference.identity_prefix(c.get('reference_text',c['text']))
        for fact in facts: residue=residue.replace(fact['text'],'')
        residue=re.sub(r'請幫我|幫我|麻煩|不要動|不要|保持原樣|讓|請|把|的|[\s，、]','',residue)
        if residue: continue
        support=set.intersection(*(set(q['candidate_ids']) for q in facts))
        if len(support)!=1: continue
        ids=list(support)
        if person_reference.check(c.get('reference_text',c['text']),people,ids)[0]!='matched':continue
        c.update(status='matched',binding_source='validated_text_facts',candidate_ids=[],
                 target_ids=ids if c['scope']!='protection' else [],protected_ids=ids if c['scope']=='protection' else [])
    return out


def generic_withdrawal(text, people, clauses):
    residue=text
    for c in clauses:
        if c['text'] in residue: residue=residue.replace(c['text'],'',1)
    for p in people:
        residue=residue.replace(p['label'],'')
    for fact in person_reference.constraints(text,people):
        residue=residue.replace(fact['text'],'')
    residue=re.sub(r'取消|撤回|不做了|不用了|不要了|保持原樣|剛才|剛剛|之前|這項|那項|這個|那個|這位|全部|所有|需求|要求|修改|動作|修圖|請|他|她|的|先|都|[，。！？\s]','',residue)
    return not residue
