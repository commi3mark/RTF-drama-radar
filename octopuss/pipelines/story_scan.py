from __future__ import annotations
import json,re
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/'octopuss'/'intelligence'/'stories'
TERMS=['drama','controversy','lawsuit','strike','cancel','scam','grift','arrest','apology','response','exposed','feud','campaign','refund','fulfilment','fired','banned']
def load(p,d):
    try:return json.loads(p.read_text(encoding='utf-8'))
    except Exception:return d
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2,ensure_ascii=False),encoding='utf-8')
def main():
    radar=load(ROOT/'drama-radar.json',[]); buckets=defaultdict(list)
    for r in radar:
        hay=(str(r.get('title',''))+' '+str(r.get('description',''))).casefold()
        for t in TERMS:
            if t in hay:buckets[t].append({'id':r.get('id'),'title':r.get('title'),'source':r.get('source'),'published':r.get('published'),'url':r.get('url')})
    stories=[{'story_candidate':k,'item_count':len(v),'items':v[:20]} for k,v in sorted(buckets.items(),key=lambda x:len(x[1]),reverse=True)]
    save(OUT/'story-candidates.json',{'updated_at':datetime.now(timezone.utc).isoformat(),'stories':stories})
    print(f"Story scan: {len(stories)} narrative buckets updated.")
    return 0
if __name__=='__main__': raise SystemExit(main())
