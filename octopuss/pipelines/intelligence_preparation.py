from __future__ import annotations
import json,re,hashlib
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'octopuss'/'intelligence'/'corpus'
def load(p,d):
    try:return json.loads(p.read_text(encoding='utf-8'))
    except Exception:return d
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2,ensure_ascii=False),encoding='utf-8')
def main():
    idx=load(ROOT/'transcripts'/'transcript-index.json',{})
    rows=idx.get('transcripts',[]) if isinstance(idx,dict) else idx
    inv=defaultdict(list); docs=[]; missing=[]
    for m in rows:
        p=ROOT/str(m.get('path','')); data=load(p,None)
        if not isinstance(data,dict): missing.append(str(m.get('path',''))); continue
        text=' '.join(str(s.get('text','')) for s in data.get('segments',[]))
        words=sorted(set(re.findall(r"[a-z0-9@']{3,}",text.casefold())))
        vid=str(data.get('video_id') or m.get('video_id') or '')
        docs.append({'video_id':vid,'title':data.get('title'),'source':data.get('source'),'published':data.get('published'),'path':str(m.get('path')),'word_count':len(text.split())})
        for w in words: inv[w].append(vid)
    payload={'updated_at':datetime.now(timezone.utc).isoformat(),'documents':docs,'missing':missing,'inverted_index':dict(inv)}
    save(OUT/'corpus-index.json',payload)
    print(f"Intelligence preparation: {len(docs)} transcripts indexed; {len(missing)} missing.")
    return 0
if __name__=='__main__': raise SystemExit(main())
