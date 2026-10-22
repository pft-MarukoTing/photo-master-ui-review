"""Chat-only evidence for model-understood, user-authorized printed-text changes.

The LLM interprets the instruction. This module checks provenance and output
consistency; it never detects translation intent from keywords or translates.
"""
SCHEMA = {'type': 'array', 'items': {
    'type': 'object', 'additionalProperties': False,
    'required': ['operation', 'original', 'replacement', 'instruction'],
    'properties': {
        'operation': {'type': 'string', 'enum': ['translate', 'rewrite', 'correct']},
        'original': {'type': 'string'}, 'replacement': {'type': 'string'},
        'instruction': {'type': 'string'},
    }}}

INSTRUCTION = '''
AUTHORIZED PRINTED TEXT TRANSFORMATIONS:
Distinguish exact user-specified printed characters from a user's NEW instruction
that explicitly asks to translate, rewrite or correct existing printed content.
Reason from the current active wishes and latest instruction/clarification chain,
not keywords. If the requested operation and target text are clear, return the
complete updated edit, not another request for the user to supply a translation.
For each converted literal, return text_transformations with operation
(translate/rewrite/correct), original (exact active printed text), replacement
(exact final text), and instruction (copy the ENTIRE latest user message verbatim,
or the original instruction in the CURRENT pending clarification chain).
For session output put this array inside request; for upload review at top level.
For other edits, commands and questions use []. This is not permission to invent
text, pick among ambiguous target paragraphs, or reuse an older unrelated request.
If multiple paragraphs could be meant, ask which one with appropriate options.
Preserve all unrelated wishes and untouched printed strings exactly. Replace the
selected literal instead of adding a second version; retain both languages only
if the user requested bilingual content (ask if that intent is unclear).
In upload review, also replace/remove the old section using merged (1-based),
and write the new standalone request in line. Do not leave contradictory old text.
Synchronize line, semantics.literal_text and the active literal_texts patch.
Once accepted, the replacement becomes the exact protected literal for rendering
and QA. Without valid transformation evidence the existing exact-text rule applies.
'''


class TransformError(ValueError):
    def __init__(self, code, field, rule):
        super().__init__(code)
        self.feedback = dict(code=code, field=field, rule=rule)


def authorized_literals(changes, values, *, active_prompt, instructions,
                        active_literals=None, resulting_prompt=None):
    if not isinstance(changes, list) or len(changes) > 40:
        raise TransformError('invalid_text_transformations', 'text_transformations', 'Return an array of at most 40 transformations, or [].')
    allowed = set()
    originals = set()
    printed = {v['literal_text'] for v in values if v['feature'] == 'text_overlay' and v['literal_text']}
    for i, item in enumerate(changes):
        field = 'text_transformations[%d]' % i
        if (not isinstance(item, dict) or set(item) != set(SCHEMA['items']['required'])
                or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in item.values())
                or item['operation'] not in SCHEMA['items']['properties']['operation']['enum']):
            raise TransformError('invalid_text_transformation', field, 'Use the exact transformation schema with nonempty original, replacement and current instruction.')
        old, new = item['original'], item['replacement']
        active = old in active_literals if active_literals is not None else '「' + old + '」' in active_prompt
        if not active or old not in active_prompt:
            raise TransformError('inactive_text_transformation', field + '.original', 'Quote exact currently active printed text; historical or nonprinted text is not an active target.')
        if item['instruction'] not in instructions:
            raise TransformError('ungrounded_text_transformation', field + '.instruction', 'Quote the entire latest user instruction or the original instruction of the current pending clarification; never reuse unrelated history.')
        if old in originals or new not in printed:
            raise TransformError('inconsistent_text_transformation', field + '.replacement', 'Transform each original once and include its exact replacement as a complete text_overlay semantic.')
        if old != new and (old in printed or (resulting_prompt is not None and '「' + old + '」' in resulting_prompt)):
            raise TransformError('unreplaced_text_transformation', field + '.original', 'Replace the old printed request, including merged upload sections; do not retain both versions.')
        originals.add(old)
        allowed.add(new)
    return allowed
