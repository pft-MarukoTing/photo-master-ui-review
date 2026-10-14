"""Owned immutable mapping snapshots; explicit confirmations supersede a token.

Tokens carry source/model/preview identities and exact effective-wish digest.
They cannot be used with another photo or another user's session.
"""
import copy
import hashlib
import json
import re
import uuid
from backend.services import chat_session as store
from backend.services.account import _user_base
from photo_agent import preview_conversation as domain

TTL = 24 * 3600


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _key(user, token):
    if not isinstance(token, str) or not re.fullmatch(r'pc_[a-f0-9]{32}', token):
        raise ValueError('invalid_conversation_token')
    return f'{_user_base(user)}:preview-conversation:{token}'


def _field(fields, name):
    return fields.get(name, fields.get(name.encode()))


def _head(user, binding, generation):
    return f'{_user_base(user)}:preview-conversation-head:{binding["preview_job_id"]}:{digest(generation)}'


def begin(user, binding, generation, revision, context):
    if generation is None:
        return None
    if not isinstance(generation, str) or not 1 <= len(generation) <= 512:
        raise ValueError('invalid_conversation_generation')
    key = _head(user, binding, generation)
    redis = store._client()
    field = f'revision:{revision}'
    fingerprint = digest(context)
    if not redis.hsetnx(key, field, fingerprint):
        old = _field(redis.hgetall(key), field)
        if store._decode(old) != fingerprint:
            raise ValueError('conversation_revision_reused')
    redis.zadd(key + ':versions', {str(revision): revision})
    for name in (key, key + ':versions'):
        redis.expire(name, TTL)
    head = {'generation': generation, 'revision': revision}
    assert_current(user, binding, head)
    return head


def assert_current(user, binding, head):
    if not head:
        return
    latest = store._client().zrevrange(_head(user, binding, head['generation']) + ':versions', 0, 0)
    if not latest or int(latest[0]) != head['revision']:
        raise ValueError('stale_conversation_revision')


def consume(user, state, new_token):
    redis = store._client()
    head = state.get('head')
    if head and hasattr(redis, 'eval'):
        # Both keys share the account's Redis Cluster hash tag. Validation and
        # consuming the question are atomic against a concurrent new request.
        script = """local latest = redis.call('ZREVRANGE', KEYS[1], 0, 0)
        if #latest == 0 or tonumber(latest[1]) ~= tonumber(ARGV[1]) then return 0 end
        return redis.call('HSETNX', KEYS[2], 'superseded', ARGV[2])"""
        accepted = redis.eval(script, 2, _head(user, state['binding'], head['generation']) + ':versions',
                              _key(user, state['token']), head['revision'], new_token)
    else:
        assert_current(user, state['binding'], head)
        accepted = redis.hsetnx(_key(user, state['token']), 'superseded', new_token)
    if not accepted:
        raise ValueError('conversation_superseded_or_stale')


def load_previous(user, token, binding):
    # Previous snapshots are evidence for unchanged clauses, never authority
    # to confirm a stale question. Follow explicit supersedes first.
    for _ in range(50):
        fields = store._client().hgetall(_key(user, token))
        raw = _field(fields, 'state')
        if not raw:
            raise ValueError('conversation_expired')
        data = json.loads(raw)
        if data['binding'] != binding:
            raise ValueError('conversation_source_changed')
        newer = _field(fields, 'superseded')
        if not newer:
            return data
        token = store._decode(newer)
    raise ValueError('conversation_revision_chain_too_long')


def save(user, binding, state, people, revision, wishes_digest, *, previous=None):
    state = domain.wish_lifecycle.project(state, people)
    if state.get('lifecycle_changes'):
        state['reply'] = domain.wish_lifecycle.change_reply(state['lifecycle_changes'], domain.executable(state, people))
    assert_current(user, binding, state.get('head'))
    token = 'pc_' + uuid.uuid4().hex
    data = {**copy.deepcopy(state), 'token': token, 'binding': binding, 'people': people,
            'revision': revision, 'wishes_digest': wishes_digest, 'supersedes': previous}
    for clause in data['clauses']:
        if clause['status'] in ('matched','not_person') and not clause.get('edit_scope_pending'):
            clause['display_text'] = domain.executable({'clauses':[clause]}, people)
            clause['display_target_ids'] = clause.get('target_ids') or clause.get('protected_ids') or []
    data['question'] = domain.question(data, people)
    data['effective_prompt'] = domain.executable(data, people)
    redis = store._client()
    redis.hset(_key(user, token), 'state', json.dumps(data, ensure_ascii=False))
    redis.expire(_key(user, token), TTL)
    return data


def load(user, token, binding=None):
    fields = store._client().hgetall(_key(user, token))
    raw = fields.get('state', fields.get(b'state'))
    if not raw:
        raise ValueError('conversation_expired')
    data = json.loads(raw)
    if binding is not None and data['binding'] != binding:
        raise ValueError('conversation_source_changed')
    if fields.get('superseded', fields.get(b'superseded')):
        raise ValueError('conversation_superseded')
    assert_current(user, data['binding'], data.get('head'))
    return data


def confirm(user, token, binding, question_id, wish_ids, ids, revision, protection_scope=None, selected_wish=None, edit_scope=None):
    state = load(user, token, binding)
    q = state['question']
    known = {p['preview_person_id'] for p in state['people']}
    if (not q or q['question_id'] != question_id or q['wish_ids'] != wish_ids
            or state['revision'] != revision or not isinstance(ids, list) or (not ids and q.get('kind') not in ('scope', 'wish', 'edit_scope'))
            or any(not isinstance(i, str) or i not in known for i in ids)):
        raise ValueError('stale_or_invalid_confirmation')
    updated = copy.deepcopy(state)
    if q.get('kind') == 'wish':
        if selected_wish not in q['wish_ids']:
            raise ValueError('invalid_correction_wish')
        updated.pop('revision_question', None)
        if q.get('lifecycle_text'):
            candidate=domain.confirm_revision_choice(updated,state['people'],selected_wish,q['lifecycle_text'])
            new=save(user,binding,candidate,state['people'],revision,state['wishes_digest'],previous=token)
            consume(user,state,new['token'])
            return new
        for c in updated['clauses']:
            if c['wish_id'] == selected_wish:
                c.update(status='pending', target_ids=[], protected_ids=[])
    if q.get('kind') == 'scope':
        if protection_scope not in ('features', 'all'):
            raise ValueError('invalid_protection_scope')
        updated['protection_scope'] = protection_scope
    for c in updated['clauses']:
        if c['wish_id'] in wish_ids and q.get('kind') == 'edit_scope':
            domain.confirm_edit_scope(c, edit_scope)
        elif c['wish_id'] in wish_ids and q.get('kind') not in ('scope', 'wish'):
            c.update(status='matched', candidate_ids=[], awaiting_confirmation=False, binding_source='user_confirmation',
                     target_ids=list(dict.fromkeys(ids)) if c['scope'] != 'protection' else [],
                     protected_ids=list(dict.fromkeys(ids)) if c['scope'] == 'protection' else [])
    updated['reply'] = domain.acknowledged_reply(updated['clauses'], updated['people'])
    if q.get('kind') == 'edit_scope' and edit_scope == 'cancel':
        updated['reply'] = '已取消這項髮絲整理需求。'
    new = save(user, binding, updated, state['people'], revision, state['wishes_digest'], previous=token)
    # hsetnx is atomic: only one confirmation of the current question wins.
    consume(user, state, new['token'])
    return new


def public(state):
    return {k: state[k] for k in ('token', 'revision', 'wishes_digest', 'reply', 'clauses', 'question', 'effective_prompt')}
