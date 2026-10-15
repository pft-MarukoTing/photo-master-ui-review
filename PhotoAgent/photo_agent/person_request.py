"""Source-backed edit semantics, independent of person matching and prose caches.

No model or client-supplied completeness flag is trusted here. Unknown but
complete free-form edits keep their source text; body-part mentions ask for an
operation. Identity remains owned by the preview conversation.
"""
import copy
import re

VERSION = 1
FOLD = str.maketrans({'张':'張','开':'開','睁':'睜','头':'頭','发':'髮','丝':'絲','视':'視','线':'線','闭':'閉'})
ACTION_BOUNDARY = r'不要(?:張開|打開|睜開|閉上|閉眼)|張開|打開|睜開|閉上|閉眼|張眼|睜眼|眼睛|看向|望向|看鏡頭|看著鏡頭|看畫面|看左邊|看右邊|視線|髮絲|頭髮|調整|笑|整理|撥開'
PARTS = {'eyes': ('eyes','eye_state'), 'gaze': ('eyes','gaze_direction'),
         'hair': ('hair','hair_grooming'), 'smile': ('mouth','smile_expression')}
PERSON = r'女子|男子|女生|男生|女孩|男孩|女性|男性|人物|父親|母親|爸爸|媽媽|她|他|這位|這個人|路人'
PART = r'眼睛|張眼|睜眼|閉眼|頭髮|髮絲|視線|笑容|表情|膚色|身材|衣服'
FREE_EDIT = r'去除|移除|刪除|消除|去掉|拿掉|不要動|保持原樣|保留|調亮|調暗|亮一點|暗一點|模糊|換成|換為|換個|換件|換紅|改成|改為|改紅|改藍|染成|染為|降低|提高|增加|減少|清理|清除|綁起|綁成|撥開|撥到|修掉|磨皮|美白|瘦一點|修瘦|變瘦|變亮|放大|縮小|裁切|調整|補光'


def operation(text):
    t = text.translate(FOLD)
    t = re.sub(r'(?:自然)?(?:微笑|笑容)(?=的)', '', t)
    if re.search(r'不要.*(?:過度|太直|太亮|太白|過頭|太誇張)', t):
        return None
    if re.search(r'眼睛|張眼|睜眼|閉眼', t):
        if re.search(r'不要.*(?:張開|打開|睜開|張眼|睜眼)|(?:取消|撤回).*?(?:張眼|張開|打開|睜)', t): return ('eyes','cancel','張開眼睛')
        if re.search(r'不要.*(?:閉眼|閉上)', t): return ('eyes','cancel','閉上眼睛')
        if re.search(r'張開|打開|睜開|張眼|睜眼|修正閉眼|閉眼修正', t): return ('eyes','set','張開眼睛')
        if re.search(r'取消|撤回', t): return ('eyes','cancel',None)
        if re.search(r'閉眼|閉上', t): return ('eyes','set','閉上眼睛')
    if re.search(r'視線|看鏡頭|看畫面|看左邊|看右邊|望向|看向|看著鏡頭', t):
        if re.search(r'畫面左|看左|望向左|看向左',t):return ('gaze','set','視線看畫面左邊')
        if re.search(r'畫面右|看右|望向右|看向右',t):return ('gaze','set','視線看畫面右邊')
        if '鏡頭' in t:return ('gaze','set','視線看鏡頭')
    if re.search(r'髮絲|頭髮|張髮', t) and re.search(r'整理|保持原樣|撥開', t):
        if re.search(r'不要.*整理|不整理|整理.*不要|保持原樣|取消|撤回',t):return ('hair','cancel','髮絲整理')
        return ('hair','set','只整理露出的髮絲' if '露出' in t else '髮絲整理')
    if re.search(r'笑|微笑', t):
        if re.search(r'取消|撤回',t):return ('smile','cancel','平靜不微笑的表情' if re.search(r'不要笑|不微笑',t) else '自然笑容' if '自然' in t else None)
        if re.search(r'不要笑|不微笑|平靜',t):return ('smile','set','平靜不微笑的表情')
        if re.search(r'不要露齒|不露齒',t):return ('smile','set','微笑但不要露齒')
        if re.search(r'露齒笑',t):return ('smile','set','露齒笑')
        if re.search(r'自然',t):return ('smile','set','自然笑容')
        if re.search(r'微笑(?!的)|笑容收一點',t):return ('smile','set','微笑就好' if '微笑就好' in t else '輕微微笑' if '收一點' in t else '微笑')
    return None


def split_requests(text):
    """A comma inside a person's description is not a new request."""
    result=[]
    for paragraph in re.split(r'[；;\n]+',text):
        parts=re.split(r'[，,]',paragraph)
        current=parts[0].strip()
        for part in parts[1:]:
            part=part.strip()
            # Split actual independent wishes, retaining modifiers/constraints
            # attached to the same clause (e.g. '不要太直').
            complete=bool(operation(current) or re.search(FREE_EDIT,current))
            modifier=bool(re.match(r'(?:但|不過|而且|只要|不要太|不要.*(?:太|過度))',part))
            if complete and not modifier:
                if current:result.append(current)
                current=part
            else:current+='，'+part
        if current.strip():result.append(current.strip())
    return result


def _part(text):
    if re.search(r'眼睛|張眼|睜眼|閉眼|視線',text):return 'eyes'
    if re.search(r'髮絲|頭髮',text):return 'hair'
    if re.search(r'笑容|表情|笑',text):return 'mouth'
    return None


def semantics(clause):
    """Derive action only from stored source evidence, never cached display."""
    text=clause['text']
    source=clause.get('reference_text') or text
    answer=clause.get('action_answer') or clause.get('outside_description')
    evidence=source
    if answer:
        # A short answer can omit the body part already asked about.
        evidence=source+'；補充：'+answer
    op=operation(evidence)
    if answer:
        answer_op=operation(answer)
        if not answer_op and _part(source)=='eyes':
            answer_op=operation('眼睛'+answer)
        if answer_op:op=answer_op
    # An explicit revision owns a new source text. Older sources cannot
    # override it, even when legacy records retained a reference_text.
    if clause.get('binding_source')=='explicit_revision':
        evidence=text;op=operation(text)
    part=_part(evidence)
    complete=bool(op or re.search(FREE_EDIT,evidence) or re.search(r'整理|修正|變成',evidence))
    constraint=bool(re.search(r'不要.*(?:過度|太直|太亮|太白|過頭|太誇張)',evidence))
    if constraint:
        complete=True
        if not op:op=operation(evidence.split('不要',1)[0])
    # Questions are not edits; non-person photo requests are not restricted
    # to the portrait vocabulary.
    if clause.get('scope')=='query':complete=True
    if not complete and not re.search(PERSON+'|'+PART,evidence):complete=True
    result={'action_version':VERSION,'action_status':'complete' if complete else 'incomplete',
            'part':part,'action_evidence':evidence}
    if op:
        feature,intent,action=op
        part,kind=PARTS[feature]
        result.update(part=part,operation={'feature':feature,'part':part,'kind':kind,
            'intent':intent,'action':action,'value':('open' if action=='張開眼睛' else 'closed' if action=='閉上眼睛' else action),
            'source_text':evidence})
    if constraint:result['action_constraint']=evidence
    return result


def annotate(clause):
    parsed=semantics(clause)
    clause.update(parsed)
    if 'operation' not in parsed:clause.pop('operation',None)
    if 'action_constraint' not in parsed:clause.pop('action_constraint',None)
    return clause


def answer_action(clause, text):
    if not isinstance(text,str) or not text.strip() or len(text)>1000:
        raise ValueError('請補充想調整的動作。')
    text=text.strip()
    # A person change/new request is not an answer about the bound person.
    prefix=re.split(ACTION_BOUNDARY,text,maxsplit=1)[0]
    prefix=re.sub(r'請幫我|幫我|麻煩|改成|改為|請|把|讓|她|他|這位|這個人|的|先|再|[\s，。]','',prefix)
    if prefix and re.search(PERSON+'|戴|髮|左|右|前|後',prefix):
        raise ValueError('new_action_message')
    updated=copy.deepcopy(clause)
    updated['action_answer']=text
    annotate(updated)
    if updated['action_status']!='complete':raise ValueError('請再說明想怎麼調整這個部位。')
    # The answer must specify an action itself, not borrow an older complete
    # action from the question's source.
    answer_evidence=text
    if (clause.get('part') or _part(clause['text']))=='eyes' and re.fullmatch(r'(?:改成|改為|請|先|再)?(?:閉上|張開|打開|睜開)[。！!]?',text):answer_evidence='眼睛'+text
    if not operation(answer_evidence) and not re.search(FREE_EDIT+'|整理',answer_evidence):
        raise ValueError('請再說明想怎麼調整這個部位。')
    return updated


def outside_text(clause):
    """A confirmed wish reference is not a synthetic person identity."""
    if clause.get('action_answer') and clause.get('operation'):
        source=clause.get('outside_reference') or clause['text']
        boundary=re.search(ACTION_BOUNDARY,source)
        actor=source[:boundary.start()] if boundary else ''
        action=clause['operation']['action']
        if actor and action:return actor+action
    answer=clause.get('action_answer') or clause.get('outside_description')
    return clause['text']+('；補充：'+answer if answer else '')
