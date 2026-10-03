"""Literal draft anchors for synthetic orchestration clients, not fact proofs."""
import json


def draft_quotes(prompt, section_id):
    view = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
    draft = view.get('draft', {})
    if draft.get('section_id') == section_id:
        text = draft['draft_markdown']
    else:
        text = next(row['markdown'] for row in view['sections'] if row['section_id'] == section_id)
    return [{'section_id': section_id, 'quote': text}]
