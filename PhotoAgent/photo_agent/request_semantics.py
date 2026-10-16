"""Validated model semantics. IDs/state remain owned by the application.

This module validates evidence and structure, never guesses verbs or cuts person
names at a keyword. Legacy language rules are isolated in person_request for
reading old snapshots; current model responses use this contract.
"""
import copy
import hashlib

VERSION = 2
_FIELDS = {
    'source': {'type': 'string'},
    'target': {'type': 'string'},
    'action': {'type': 'string'},
    'complete': {'type': 'boolean'},
    'target_kind': {'type': 'string', 'enum': ['person', 'outside_person', 'object', 'region', 'image', 'unknown']},
    'owner_required': {'type': 'boolean'},
    'scope': {'type': 'string', 'enum': ['edit', 'protection', 'query']},
    # Open strings: an unfamiliar action does not need a new enum/regex.
    'feature': {'type': 'string'},
    'part': {'type': 'string'},
    'intent': {'type': 'string', 'enum': ['set', 'cancel', 'constraint', 'query']},
    'value': {'type': 'string'},
    'constraints': {'type': 'array', 'items': {'type': 'string'}},
}
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'required': list(_FIELDS), 'properties': _FIELDS}
INSTRUCTION = '''
Parse the meaning of each user request, not a list of allowed verbs. Return
semantic_version=2 and semantic for EVERY resolution. An unfamiliar but explicit
edit is complete. Bare person/body part/object nouns are incomplete. Infer only
from this source and explicitly supplied conversation, never invent an action.
semantic.source is an EXACT contiguous source fragment containing one atomic
request. For several independently targeted actions, repeat the parent wish_id
with distinct source fragments; never split a comma inside a person's name.
semantic.target is the EXACT current identity/object description in source;
semantic.action is the EXACT requested operation phrase in source, without the
person name. Keep negatives and limits verbatim. For verb-first requests where
an actor interrupts the operation, keep the whole exact operation phrase; the
server substitutes only semantic.target. Empty action means incomplete.
complete describes the ACTION only, independently of whether its person/owner
is known. owner_required=true when an object/body-part edit needs a particular
person as its owner or wearer. This is independent of target_kind=object.
Example 手上的戒指拿掉 => target=手上的戒指, target_kind=object,
action=拿掉, complete=true, owner_required=true. Ask WHO owns the ring, not
what action to perform. 桌上的戒指拿掉 is an object edit with
owner_required=false. Removing a person's necklace, changing their clothes or
adjusting a held item can need an owner even if the person is implicit.
For owner_required=true with no specified owner, use ambiguous and no target
IDs. Never guess an owner from the requested operation or desired appearance.
For an explicitly named owner, referent/identity_groups describe that owner
within semantic.target. Do not treat the object itself as a person's identity.
Examples: 右邊男子戴上眼鏡 => target=右邊男子, action=戴上眼鏡, complete=true.
戴眼鏡男子張開眼睛 => target=戴眼鏡男子, action=張開眼睛.
戴粉紅墨鏡的女生換成黑框眼鏡 => current target includes pink sunglasses,
while black glasses are the DESIRED RESULT, never a matching qualifier.
semantic.feature is a stable property key shared by requests about the same
property (eyes, gaze, hair, smile, eyewear, clothing, or another descriptive key).
Use eyes value=open/closed, gaze value=camera/left/right when appropriate;
otherwise use an open, precise value. Adding/removing eyewear changes eyewear,
not the person's identity. A removal edit is set, not cancellation of a wish.
intent=cancel only withdraws an existing operation, constraint modifies a limit.
Preservation is scope=protection. Query is not an executable edit.
Only CURRENT identity evidence belongs in referent and identity_groups. These
must be substrings of semantic.target, not of desired appearance/action.
Match uniquely supported targets directly; ask only for missing information.
Do not ask for a new action after the user already supplied a complete one.
Keep existing user-confirmed identity fixed. A person clarification changes only
the person; an action answer changes only the requested action of that wish.
'''


def validate(raw, source):
    # Existing version-2 snapshots predate owner_required. New model schemas
    # require it, while historical snapshots keep their previous interpretation.
    if isinstance(raw, dict) and set(raw) == set(_FIELDS) - {'owner_required'}:
        raw = {**raw, 'owner_required': False}
    if not isinstance(raw, dict) or set(raw) != set(_FIELDS):
        raise ValueError('invalid_request_semantics')
    for key in _FIELDS.keys() - {'complete', 'constraints', 'owner_required'}:
        if not isinstance(raw[key], str) or len(raw[key]) > 2000:
            raise ValueError('invalid_semantic_text')
    if type(raw['complete']) is not bool:
        raise ValueError('invalid_semantic_completeness')
    if type(raw['owner_required']) is not bool:
        raise ValueError('invalid_semantic_owner_requirement')
    if raw['target_kind'] not in _FIELDS['target_kind']['enum'] or raw['scope'] not in _FIELDS['scope']['enum'] or raw['intent'] not in _FIELDS['intent']['enum']:
        raise ValueError('invalid_semantic_scope')
    fragment = raw['source']
    if not fragment.strip() or fragment not in source:
        raise ValueError('semantic_source_not_grounded')
    if raw['target'] and raw['target'] not in source:
        raise ValueError('semantic_target_not_grounded')
    # Verb-first clauses may have their target interrupt the quoted action.
    # Accept only exact removal of that target (and its possessive 的), not
    # paraphrases, token subsequences or invented verbs. Display keeps source.
    without_target = fragment.replace(raw['target'], '', 1) if raw['target'] else fragment
    without_possessive = fragment.replace(raw['target'] + '的', '', 1) if raw['target'] else fragment
    if raw['action'] and raw['action'] not in fragment and raw['action'] not in (without_target, without_possessive):
        raise ValueError('semantic_span_not_grounded')
    if raw['complete'] and raw['scope'] != 'query' and not raw['action'].strip():
        raise ValueError('semantic_action_missing')
    limits = raw['constraints']
    if not isinstance(limits, list) or len(limits) > 20 or any(not isinstance(s, str) or not s or s not in fragment for s in limits):
        raise ValueError('invalid_semantic_constraints')
    return {**copy.deepcopy(raw), 'version': VERSION,
            'evidence_digest': hashlib.sha256(source.encode()).hexdigest()}


def evidence(clause):
    return clause.get('reference_text') or clause['text']


def read(clause):
    value = clause.get('semantic')
    if not isinstance(value, dict) or value.get('version') != VERSION:
        return None
    source = evidence(clause)
    if value.get('evidence_digest') != hashlib.sha256(source.encode()).hexdigest():
        return None
    # Revalidate saved evidence; client fields never reach this function via wishes().
    try:
        return validate({k: value.get(k, False) if k == 'owner_required' else value[k] for k in _FIELDS}, source)
    except (ValueError, KeyError):
        return None


def project(clause):
    value = read(clause)
    if value is None:
        return {'action_version': VERSION, 'action_status': 'pending', 'part': None,
                'action_evidence': evidence(clause)}
    result = {'action_version': VERSION, 'action_status': 'complete' if value['complete'] else 'incomplete',
              'part': value['part'] or None, 'action_evidence': value['source']}
    if value['complete'] and value['feature'] and value['intent'] in ('set', 'cancel') and value['scope'] != 'query':
        result['operation'] = {'feature': value['feature'], 'part': value['part'],
            'kind': value['feature'], 'intent': value['intent'], 'action': value['action'],
            'value': value['value'], 'source_text': value['source']}
    if value['constraints']:
        result['action_constraint'] = '，'.join(value['constraints'])
    return result


def confirm_target_action(clause, raw):
    """Recheck the original action after a target pick, never parse the pick as an edit."""
    old = read(clause)
    if raw is None:
        clause.pop('semantic', None)
        clause['semantic_required'] = True
        return
    value = validate(raw, evidence(clause))
    if not old or any(value[key] != old[key] for key in
                      ('source', 'target', 'scope', 'intent', 'constraints')):
        raise ValueError('target_confirmation_changed_request')
    # Identity is owned by the explicit selection, never by this model output.
    value['target_kind'] = old['target_kind']
    value['owner_required'] = old['owner_required']
    clause.update(semantic=value, semantic_required=True)


def display(clause, labels):
    value = read(clause)
    if not value:
        return clause['text']
    ids = clause.get('target_ids') or clause.get('protected_ids') or []
    who = '、'.join(labels[i] for i in ids)
    source, target, action = value['source'], value['target'], value['action']
    if clause.get('referent') and clause['referent'] in target:
        target = clause['referent']
    if clause.get('semantic_answer'):
        # Person labels and edited parts occupy different roles. Never replace
        # an object/part referent (e.g. 眼睛) with the selected owner's name.
        part = value['part'] or (value['target'] if value['target_kind'] == 'object' else '')
        if value['feature'] == 'eyes' and value['value'] in ('open', 'closed'):
            action = '張開眼睛' if value['value'] == 'open' else '閉上眼睛'
        elif value['target_kind'] == 'object':
            action = value['target'] + '，' + action
        elif part and part not in action:
            action = part + '：' + action
        return (who + '：' if who else '') + action + ('（' + '，'.join(value['constraints']) + '）' if value['constraints'] else '')
    if not ids:
        return source
    if value['target_kind'] == 'object':
        # Selecting a wearer/owner does not turn the object into an identity
        # span. A saved referent can itself be "眼鏡"; replacing it would
        # delete WHAT to edit. Replace a separate owner span only when it
        # has validated identity evidence; otherwise preserve the source.
        ref = clause.get('referent')
        owner_evidence = any(e.get('text') and e['text'] in ref and
            set(e.get('candidate_ids', [])) & set(ids)
            for e in clause.get('identity_evidence', [])) if ref else False
        if ref and ref != value['target'] and ref in value['target'] and owner_evidence:
            return source.replace(ref, who, 1)
        if who in source:
            return source
        return who + '：' + source
    # Whole-source replacement preserves verb-first requests and limits. If a
    # target is implicit (an explicit avatar pick), prefix its current label.
    if target and target in source:
        return source.replace(target, who, 1)
    return who + '：' + (action or source)


def check_identity(value, groups, known, ids, explicit=False):
    """Check model identity evidence as sets, not language keyword rules."""
    if value['target_kind'] not in ('person', 'unknown', 'outside_person') and not value['owner_required'] and not ids:
        return 'not_person', [], []
    if not groups:
        return ('matched', [], []) if explicit and ids and (not value['target'] or value['owner_required']) else ('ambiguous', list(ids), [])
    supports, partial, evidence = [], set(), []
    for group in groups:
        referent = group.get('referent')
        if not referent or referent not in value['target']:
            raise ValueError('invalid_semantic_identity_group')
        qualifiers = group.get('qualifiers')
        if not isinstance(qualifiers, list) or not qualifiers:
            return 'ambiguous', list(ids), []
        pools = []
        for q in qualifiers:
            if not isinstance(q, dict) or set(q) != {'text', 'candidate_ids'} or not q['text'] or q['text'] not in referent:
                raise ValueError('invalid_semantic_identity_evidence')
            candidates = q['candidate_ids']
            if not isinstance(candidates, list) or any(not isinstance(i, str) or i not in known for i in candidates) or len(set(candidates)) != len(candidates):
                raise ValueError('unknown_semantic_candidate')
            pools.append(set(candidates)); partial.update(candidates); evidence.append(copy.deepcopy(q))
        supports.append(set.intersection(*pools))
    # Complete target descriptions must be represented, not just whichever
    # qualifier yielded a convenient match. Plural groups may partition them.
    if len(groups) == 1 and groups[0]['referent'] != value['target'] and value['target_kind'] in ('person', 'outside_person', 'unknown'):
        return 'ambiguous', [i for i in known if i in partial], evidence
    support = set.union(*supports)
    if not support or any(not pool for pool in supports) or (not set(ids) <= support if explicit else set(ids) != support):
        return 'ambiguous' if partial else 'unmatched', [i for i in known if i in partial], evidence
    return 'matched', [], evidence

ACTION_SCHEMA = {'type': 'json_schema', 'name': 'chat_request_action_answer', 'strict': True,
    'schema': {'type': 'object', 'additionalProperties': False,
        'required': ['confirmation_intent', 'semantic'],
        'properties': {'confirmation_intent': {'type': 'string', 'enum': ['answer', 'new_message']},
                       'semantic': SCHEMA}}}


def apply_answer(clause, text, raw):
    if not isinstance(raw, dict) or set(raw) != {'confirmation_intent', 'semantic'}:
        raise ValueError('invalid_action_answer')
    if raw['confirmation_intent'] == 'new_message':
        raise ValueError('new_action_message')
    if raw['confirmation_intent'] != 'answer':
        raise ValueError('invalid_action_intent')
    old = read(clause)
    if not old:
        raise ValueError('semantic_request_not_ready')
    source = evidence(clause) + '；補充：' + text
    value = validate(raw['semantic'], source)
    if any(value[key] != old[key] for key in ('target', 'target_kind', 'owner_required')):
        raise ValueError('action_answer_changed_target')
    # A model cannot "complete" a bare new answer by borrowing an older verb.
    if value['complete'] and not (value['action'] in text or text in value['action']):
        raise ValueError('action_answer_not_grounded')
    result = copy.deepcopy(clause)
    result.update(reference_text=source, semantic=value, semantic_required=True,
                  semantic_answer=text, scope=value['scope'])
    result.pop('display_text', None)
    result.pop('display_target_ids', None)
    return result
