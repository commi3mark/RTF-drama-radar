from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/'octopuss'/'intelligence'/'report-context'
def load(p,d):
    try:return json.loads(p.read_text(encoding='utf-8'))
    except Exception:return d
def main():
    mentions=load(ROOT/'octopuss'/'entities'/'commi3-mark'/'mention-index.json',{})
    entities=load(ROOT/'octopuss'/'intelligence'/'entities'/'entity-candidates.json',{})
    stories=load(ROOT/'octopuss'/'intelligence'/'stories'/'story-candidates.json',{})
    payload={'updated_at':datetime.now(timezone.utc).isoformat(),'commi3_watch':mentions,'notable_entities':entities.get('entities',[])[:50],'active_story_candidates':stories.get('stories',[])[:20]}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'dongs-context.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Report context built: intelligence/report-context/dongs-context.json')
    return 0
if __name__=='__main__': raise SystemExit(main())
