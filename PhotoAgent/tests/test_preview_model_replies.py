"""Shared test helper adapting authored legacy scene replies to the wire schema.

No expected subject lists/boxes/IDs are computed here. Legacy snapshot
fixtures remain v1/v2 to continue exercising their read compatibility.
"""
import copy


def scene_reply_v3(reply):
    value = copy.deepcopy(reply)
    value['scene_intent'] = value['summary']
    value['low_confidence_reviews'] = []
    value.setdefault('additional_people', [])
    for item in value.get('detections', []):
        item.pop('face_box', None)
        item.setdefault('selection_evidence', 'intended_subject')
        item.setdefault('reason', '手寫場景中的拍攝人物，並非因候選框或分數而納入。')
    for item in value.get('additional_people', []):
        item.setdefault('selection_evidence', 'intended_subject')
        item.setdefault('reason', '全圖場景中的共同入鏡人物。')
        item.setdefault('thumbnail_candidate_id', None)
    return value
