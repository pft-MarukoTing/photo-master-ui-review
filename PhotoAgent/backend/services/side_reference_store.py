"""24h server-owned questions independent of preview; immutable resolved snapshots."""
import copy
import json
import re
import uuid
from backend.services import chat_session as store
from backend.services.account import _user_base
from photo_agent import side_reference as side

TTL=24*3600


def _key(user,token):
    if not isinstance(token,str) or not re.fullmatch(r'bs_[a-f0-9]{32}',token):raise ValueError('invalid_side_token')
    return _user_base(user)+':body-side:'+token


def _read(key):
    raw=store._client().get(key)
    if raw is None:raise ValueError('stale_side_question')
    return json.loads(raw)


def create(user,state,source_hash,model,scope='',base_prompt=''):
    if not isinstance(source_hash,str) or not re.fullmatch('[a-f0-9]{64}',source_hash):raise ValueError('invalid_side_source_hash')
    token='bs_'+uuid.uuid4().hex
    state=side.validate_state(state);state['photo_identity']=source_hash
    head=_user_base(user)+':body-side-head:'+side.digest(source_hash+'|'+str(model)+'|'+scope)
    try:
        previous=_read(_key(user,_read(head)))
        state['revision']=max(state['revision'],previous['state']['revision']+1)
    except ValueError:pass
    q=side.question(state,token)
    if state['decision']=='clarify_person':
        q=dict(id=token,ref_ids=[],source_revision=state['revision'],photo_identity=source_hash,original_text=state['original_text'],choices=[])
    data=dict(token=token,state=state,source_sha256=source_hash,analysis_model=model,scope=scope,
        base_prompt=base_prompt,head=head,question=q,consumed=False)
    redis=store._client();redis.set(_key(user,token),json.dumps(data,ensure_ascii=False),ex=TTL)
    redis.set(head,json.dumps(token),ex=TTL)
    return data


def load(user,token,*,source_hash=None,model=None,current=False):
    data=_read(_key(user,token))
    data['state']=side.validate_state(data['state'])
    if source_hash is not None and data['source_sha256']!=source_hash:raise ValueError('side_photo_changed')
    if model is not None and data['analysis_model']!=model:raise ValueError('side_model_changed')
    if current and _read(data['head'])!=token:raise ValueError('stale_side_question')
    return data


def answer(user,token,question_id,revision,frame,*,source_hash=None,model=None,validate_result=None):
    lock=_key(user,token)+':lock';redis=store._client()
    if not redis.set(lock,'1',nx=True,ex=10):raise ValueError('side_answer_in_progress')
    try:
        data=load(user,token,source_hash=source_hash,model=model,current=True)
        if data['state']['decision']=='clarify_person':raise ValueError('person_confirmation_required')
        if data['consumed'] or not data['question'] or data['question']['id']!=question_id:raise ValueError('stale_side_question')
        side.validate_answer(data['state'],revision,data['source_sha256'])
        resolved=side.resolve(data['state'],frame,data['question']['ref_ids'])
        if validate_result is not None:validate_result({**data,'state':resolved})
        next_question=side.question(resolved,token)
        data.update(state=resolved,question=next_question,consumed=next_question is None)
        redis.set(_key(user,token),json.dumps(data,ensure_ascii=False),ex=TTL)
        return data
    finally:redis.delete(lock)


def reply(data):
    state=data['state'];q=data['question']
    person=state['decision']=='clarify_person'
    return dict(accepted=side.executable(state),ask=not side.executable(state),line=state['canonical_text'] if side.executable(state) else '',
        reply='這項需求要調整哪一位人物？' if person else q['text'] if q else '已確認：'+state['canonical_text'],
        question_kind='person' if person else 'content',answer_mode='' if person else 'choices',clarification_subtype='body_side_reference',
        side_question=q,side_reference_token=data['token'],
        options=[c['label'] for c in q['choices']] if q and not person else [],
        literal_texts=state['literal_texts'],side_references=state['side_references'])
