"""Presentation choices for an unclear intent; accepting them still needs the LLM."""

LITERAL_CHOICE_INSTRUCTION = '''
For a meaning clarification the application also offers putting the user's exact
original input on the photo as printed text. If the user selects that option,
combine the original input and answer into a complete text_overlay edit. Preserve
the original characters, case, spaces, digits and punctuation in literal_text;
the option's surrounding instruction is not printed content. Preserve unrelated
active wishes. Do not ask for the text again or reinterpret it as a ratio.
'''


def with_literal_choice(options, original):
    """Keep up to three alternatives plus an exact, app-owned literal choice."""
    if not isinstance(original, str) or not original.strip():
        return options
    choice = '把「' + original + '」放到照片中'
    return [o for o in options if o != choice][:3] + [choice]


def is_literal_choice(text):
    return isinstance(text, str) and text.startswith('把「') and text.endswith('」放到照片中')


def validate_selected_literal(values, answer, history):
    """Validate the selected app-owned choice, not arbitrary quoted user prose."""
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
            rule='Print exactly the original input inside the selected app option; not the option instruction. Revise the complete line and semantics together.')
        raise exc
