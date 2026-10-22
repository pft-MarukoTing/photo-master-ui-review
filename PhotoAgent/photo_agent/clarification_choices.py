"""Presentation choices for an unclear intent; accepting them still needs the LLM."""
import json


def choice_repair_instruction(question):
    return ('\nCOMPLETE CLARIFICATION CHOICES ONLY:\n'
        'Keep this question and the original user intent unchanged. Return 2-4 distinct, '
        'short, actionable answers matching this question in options, answer_mode=choices. '
        'Do not execute, accept or rewrite the editing request. If the question requires '
        'original free text rather than alternatives, return answer_mode=free_text, options=[]. '
        'Never invent names, literal text or people.\nQUESTION: ' + question)


def question_presentation(result, repaired, options):
    """Only copy answer presentation; patches, commands and new replies are ignored."""
    out = dict(result)
    req = dict(result.get('request') or {})
    choices = options(repaired.get('options'))
    mode = (repaired.get('request') or {}).get('answer_mode', repaired.get('answer_mode'))
    # A validated question is still answerable by typing if presentation repair
    # fails. This is not accepting an edit or inventing a missing user value.
    free_text = mode == 'free_text' or len(choices) < 2
    req['answer_mode'] = 'free_text' if free_text else 'choices'
    out.update(request=req, options=[] if free_text else choices)
    return out

ANSWER_COMPLETION_INSTRUCTION = '''
SELECTED ANSWER COMPLETION:
Reason from the original input, the pending question and the user's answer as one
conversation. selected_option=true means the user, not the model, chose that outcome.
An edit outcome such as choosing to wear sunglasses completes the action: write a
standalone edit (for example 讓照片中的人物戴上太陽眼鏡) with aligned complete semantics.
Apply the same reasoning to rain, background objects, and unfamiliar explicit edits;
these are examples, not keyword mappings. Never turn a selected editing action into
printed text unless the user explicitly selected the printed-text alternative.
If only the person is unknown, keep the completed action and ask WHO, not WHAT to do.
Only clarify a genuinely missing user detail. missing_information must name that
specific missing fact (such as the new style, exact name, or person), not a malformed
model field, strength, implementation detail, or a request to explain the action again.
For a complete edit, commands, or an acknowledgement, missing_information is empty.
Repair line, semantics, and patch together; never accept malformed output or invent
an owner/value to pass validation. Preserve unrelated wishes and exact printed text.
'''


def answer_context(history, answer):
    """Structural provenance only; the LLM still interprets the selected words."""
    turns=[t for t in history if (t.get('card') or {}).get('type')!='personalized_snapshot']
    if turns and turns[-1].get('role')=='user' and turns[-1].get('text')==answer:
        turns=turns[:-1]
    if not turns or turns[-1].get('role')!='assistant':return {}
    def question(turn):
        card=turn.get('card') or turn
        return card if not card.get('offer') and (card.get('question') or card.get('question_kind')) else None
    current=question(turns[-1])
    if not current:return {}
    original=''
    for i in range(len(turns)-2,-1,-1):
        turn=turns[i]
        if turn.get('role')=='user':original=turn.get('text') or original
        elif turn.get('role')=='assistant' and not question(turn):break
    saved=current.get('clarification') or {}
    return dict(original=saved.get('original') or original,question=current.get('text') or turns[-1].get('text') or '',
        answer=answer,options=current.get('options') or [],selected_option=answer in (current.get('options') or []))


def answer_instruction(context):
    return ANSWER_COMPLETION_INSTRUCTION+'\nSELECTED ANSWER CONTEXT:\n'+json.dumps(context,ensure_ascii=False)


def completion_retry(context):
    answer=context.get('answer') or ''
    options=[answer] if context.get('selected_option') and answer else []
    return dict(reply='已收到你的回答，但需求整理暫時失敗，尚未套用；可以重試這項回答。',
        question_kind='content',answer_mode='choices' if options else 'free_text',options=options,request_error=True)

LITERAL_CHOICE_INSTRUCTION = """
LLM-OWNED CLARIFICATION OPTIONS:
The application does not append a printed-text option. For an initial meaning
clarification, consider whether the user may want their original input printed on
the photo. If plausible, include ONE clear printed-text alternative in options
alongside distinct editing alternatives. Keep button labels short; the full original
text remains in the conversation and need not be repeated in a long label.
Choose its wording from the conversation;
never duplicate that intent with another differently worded printed-text choice.
Do not offer printing an already clear editing command or a selected editing action.
If the user selects the printed-text alternative, combine the original input,
pending question and answer into a complete text_overlay edit. Preserve the original
characters, case, spaces, digits and punctuation in literal_text; the option's
surrounding instruction is not printed content. Preserve unrelated active wishes.
Do not ask for the text again or reinterpret it as a ratio.
"""


def is_literal_choice(text):
    return isinstance(text, str) and text.startswith('把「') and text.endswith('」放到照片中')


def validate_selected_literal(values, answer, history):
    """Support exact-text validation for legacy choices in existing conversations."""
    turns=[t for t in history if (t.get('card') or {}).get('type')!='personalized_snapshot']
    if turns and turns[-1].get('role')=='user' and turns[-1].get('text')==answer:
        turns=turns[:-1]
    if not turns or turns[-1].get('role')!='assistant':
        return
    question=turns[-1].get('card') or turns[-1]
    if not is_literal_choice(answer) or answer not in (question.get('options') or []):
        return
    expected=answer[len('把「'):-len('」放到照片中')]
    if not any(v.get('literal_text')==expected for v in values):
        exc=ValueError('selected_literal_not_preserved')
        exc.feedback=dict(code=str(exc),field='semantics[].literal_text',expected=expected,
            rule='Print exactly the original input inside the selected legacy option; not the option instruction. Revise the complete line and semantics together.')
        raise exc
