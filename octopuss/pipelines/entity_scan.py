from __future__ import annotations

import hashlib, json, re, shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OCT = ROOT / 'octopuss'
OUT = OCT / 'intelligence' / 'entities'
PEOPLE = OUT / 'people'
QUAR = OUT / 'quarantine'
CANDS = OUT / 'candidates'
CONFIG = OCT / 'config' / 'entity-seeds.json'
NOW = datetime.now(timezone.utc).isoformat()

HANDLE_RE = re.compile(r'(?<![\w@])@[A-Za-z0-9_]{2,32}\b')
TITLE_NAME_RE = re.compile(r"\b[A-Z][A-Za-z0-9'’.-]{1,28}(?:\s+(?:[A-Z][A-Za-z0-9'’.-]{1,28}|van|von|de|del|da|la|le)){1,3}\b")
PERSON_TRIGGER_RE = re.compile(r"\b(?:with|vs\.?|versus|featuring|guest|joined by|interview(?:s|ed)?|talks? to|responds? to|calls? out|on|from)\s+([A-Z][A-Za-z0-9'’.-]{1,28}(?:\s+(?:[A-Z][A-Za-z0-9'’.-]{1,28}|van|von|de|del|da|la|le)){0,3})", re.I)
ASR_PERSON_RE = re.compile(r"\b(?:he|she|him|her|his|hers|host|guest|creator|writer|artist|youtuber|streamer|said|says|told|asked|joined|interviewed|channel|account)\b", re.I)
URL_RE = re.compile(r"https?://[^\s<>\]\[\)\(\"']+", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)

STOP = {x.casefold() for x in '''the this that and but for with from live stream video episode channel comic comics book news today tonight monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september october november december youtube google twitter thank god new old big little good bad right okay yeah yes no hard art breaking exclusive update reaction review official trailer clip show podcast radio network media studio studios productions presents daily weekly monthly white house absolute batman sin city'''.split()}
NONPERSON_WORDS = {
    'show': {'show','podcast','radio','kings','night','live','after dark','cast','casino','party','commandos','ministry'},
    'organisation': {'media','network','press','studios','studio','productions','foundation','department'},
    'project_or_company': {'project','campaign','verse','universe','factory','radar','comics','publishing','clippaverse'},
    'comic_or_title': {'comic','comics','book','batman','superman','x-men','city','issue','volume','saga'},
    'institution': {'house','congress','court','police','fbi','government','department','mail'},
}
CHANNEL_SUFFIXES = {'clips','comics','media','network','podcast','show','tv','official','studios','studio','productions','radio'}


def load(path: Path, default: Any):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except Exception: return default

def save(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')

def norm(value: str) -> str:
    return re.sub(r'[^a-z0-9@]+', ' ', str(value).casefold().replace('’', "'")).strip()

def slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', norm(value)).strip('-')[:90] or 'unknown'

def clean(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value)).strip(" \t\r\n-–—:;,.!?()[]{}\"'")

def tsurl(url: str | None, seconds: float):
    return f"{url}{'&' if url and '?' in url else '?'}t={int(max(0, seconds))}s" if url else None

def evid(entity_id: str, video_id: str, seconds: float, text: str):
    return hashlib.sha1(f'{entity_id}|{video_id}|{seconds:.3f}|{text}'.encode('utf-8','ignore')).hexdigest()[:18]

def context(segments: list[dict], i: int, radius: int = 3):
    return ' '.join(str(x.get('text','')) for x in segments[max(0,i-radius):min(len(segments),i+radius+1)]).strip()

def plausible_name(value: str) -> bool:
    value = clean(value)
    words = value.split()
    plain = [re.sub(r'[^a-z0-9]','',w.casefold()) for w in words]
    if not value or len(words) > 4 or any(not x or x in STOP for x in plain): return False
    if len(words) == 1 and (len(plain[0]) < 4 or plain[0] in CHANNEL_SUFFIXES): return False
    if any(ch.isdigit() for ch in value) and not value.startswith('@'): return False
    if len(words) >= 3 and any(w.isupper() and len(w) >= 4 for w in words[1:]): return False
    if any(x in {'fired','arrested','destroyed','exposed','cancelled','canceled','officially'} for x in plain): return False
    return True

def sound_key(value: str) -> str:
    s = re.sub(r'[^a-z]', '', norm(value))
    if not s: return ''
    groups = {'bfpv':'1','cgjkqsxz':'2','dt':'3','l':'4','mn':'5','r':'6'}
    out = s[0]
    last = ''
    for ch in s[1:]:
        code = next((v for chars,v in groups.items() if ch in chars), '')
        if code and code != last: out += code
        last = code
    return (out + '000')[:4]

def similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b: return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    tok = len(ta & tb) / max(1, len(ta | tb))
    phon = 1.0 if sound_key(a) == sound_key(b) else 0.0
    return max(seq, 0.72 * seq + 0.18 * tok + 0.10 * phon)

def classify_nonperson(name: str, seeded_nonpeople: dict[str, tuple[str,str]]):
    n = norm(name)
    if n in seeded_nonpeople: return seeded_nonpeople[n][0], 1.0, 'seeded_non_person'
    words = set(n.split())
    for typ, tokens in NONPERSON_WORDS.items():
        if words & tokens: return typ, .86, 'type_word'
    if words & CHANNEL_SUFFIXES and len(words) > 1: return 'channel_or_show', .72, 'channel_suffix'
    return None

def canonical_choice(spellings: Counter, handles: Counter) -> str:
    choices = [x for x,_ in spellings.most_common() if not x.startswith('@')]
    if choices: return max(choices[:4], key=lambda x: (len(x.split()) >= 2, spellings[x], len(x)))
    if handles: return handles.most_common(1)[0][0].lstrip('@')
    return 'Unknown'

def add_observation(bucket: dict, observed: str, row: dict, weight: int = 1):
    if observed.startswith('@'): bucket['handles'][observed] += weight
    else: bucket['spellings'][observed] += weight
    bucket['sources'].add(row['source']); bucket['videos'].add(row['video_id']); bucket['rows'].append(row)


def main() -> int:
    started = datetime.now(timezone.utc)
    seed = load(CONFIG, {'people':[], 'non_people':{}})
    seeded_people = {p['entity_id']: p for p in seed.get('people', [])}
    seeded_nonpeople = {norm(name):(typ,name) for typ,names in seed.get('non_people',{}).items() for name in names}

    index = load(ROOT/'transcripts'/'transcript-index.json', {})
    metas = index.get('transcripts', []) if isinstance(index, dict) else index
    radar = load(ROOT/'drama-radar.json', [])
    radar = radar if isinstance(radar, list) else []

    candidates = defaultdict(lambda: {'spellings':Counter(), 'handles':Counter(), 'sources':set(), 'videos':set(), 'rows':[], 'source_identity':False})
    nonpeople_rows = defaultdict(list)
    corpus = []
    missing = []

    # Pass 1: high-value source/title discovery.
    for meta in metas:
        doc = load(ROOT/str(meta.get('path','')), None)
        if not isinstance(doc, dict): missing.append(str(meta.get('path',''))); continue
        segs = doc.get('segments', []) if isinstance(doc.get('segments'), list) else []
        base = {
            'video_id': str(doc.get('video_id') or meta.get('video_id') or ''),
            'title': str(doc.get('title') or meta.get('title') or ''),
            'source': str(doc.get('source') or meta.get('source') or 'Unknown'),
            'published': doc.get('published') or meta.get('published'),
            'url': doc.get('url') or f"https://www.youtube.com/watch?v={doc.get('video_id') or meta.get('video_id') or ''}",
            'path': str(meta.get('path') or ''), 'segments': segs,
        }
        corpus.append(base)
        source = clean(base['source'])
        if plausible_name(source):
            np = classify_nonperson(source, seeded_nonpeople)
            row = {**base, 'kind':'source', 'timestamp_seconds':0.0, 'matched_name':source, 'context':source}
            if np: nonpeople_rows[(np[0],source)].append(row)
            else:
                key = norm(source); add_observation(candidates[key], source, row, 5); candidates[key]['source_identity'] = True
        title_names = set(TITLE_NAME_RE.findall(base['title'])) | set(HANDLE_RE.findall(base['title']))
        for m in PERSON_TRIGGER_RE.finditer(base['title']): title_names.add(clean(m.group(1)))
        for observed in title_names:
            observed = clean(observed)
            if not plausible_name(observed): continue
            np = classify_nonperson(observed, seeded_nonpeople)
            row = {**base, 'kind':'title', 'timestamp_seconds':0.0, 'matched_name':observed, 'context':base['title']}
            if np: nonpeople_rows[(np[0],observed)].append(row)
            else: add_observation(candidates[norm(observed)], observed, row, 3)

    # Seeded entities become fixed clusters.
    clusters: dict[str, dict] = {}
    alias_to_id: dict[str,str] = {}
    for eid, p in seeded_people.items():
        cluster = {'entity_id':eid, 'seeded':True, 'canonical_name':p['canonical_name'], 'aliases':set(), 'accounts':set(p.get('accounts',[])), 'projects':list(p.get('projects',[])), 'members':set(), 'rows':[], 'sources':set(), 'videos':set()}
        for a in [p.get('canonical_name'), *p.get('aliases',[]), *p.get('accounts',[])]:
            if a: cluster['aliases'].add(a); alias_to_id[norm(a)] = eid
        clusters[eid] = cluster

    # Pass 2: attach direct source/title candidates to seeds or create provisional clusters.
    provisional = []
    for key, data in candidates.items():
        observed = canonical_choice(data['spellings'], data['handles'])
        matched = alias_to_id.get(key)
        if not matched:
            best_id, best_score = None, 0.0
            for eid, c in clusters.items():
                for alias in c['aliases']:
                    score = similarity(observed, alias)
                    if score > best_score: best_id, best_score = eid, score
            if best_score >= .86: matched = best_id
        if matched:
            c = clusters[matched]
            c['aliases'].update(data['spellings']); c['accounts'].update(data['handles']); c['rows'].extend(data['rows']); c['sources'].update(data['sources']); c['videos'].update(data['videos'])
        else:
            provisional.append((key, data, observed))

    # Merge open-world candidates with one another.
    used = set()
    for i, (key, data, observed) in enumerate(provisional):
        if i in used: continue
        merged = {'spellings':Counter(data['spellings']), 'handles':Counter(data['handles']), 'sources':set(data['sources']), 'videos':set(data['videos']), 'rows':list(data['rows']), 'source_identity':data['source_identity']}
        used.add(i)
        for j in range(i+1, len(provisional)):
            if j in used: continue
            _, other, other_name = provisional[j]
            shared_last = bool(set(norm(observed).split()[-1:]) & set(norm(other_name).split()[-1:]))
            score = similarity(observed, other_name)
            shared_context = bool(merged['sources'] & other['sources']) or bool(merged['videos'] & other['videos'])
            if score >= .90 or (score >= .80 and shared_last and shared_context):
                merged['spellings'].update(other['spellings']); merged['handles'].update(other['handles']); merged['sources'].update(other['sources']); merged['videos'].update(other['videos']); merged['rows'].extend(other['rows']); merged['source_identity'] = merged['source_identity'] or other['source_identity']; used.add(j)
        canonical = canonical_choice(merged['spellings'], merged['handles'])
        evidence_score = (5 if merged['source_identity'] else 0) + min(6, len(merged['videos'])) + min(4, len(merged['sources'])) + min(3, sum(merged['spellings'].values())//3)
        if merged['source_identity'] or (len(canonical.split()) >= 2 and len(merged['videos']) >= 2 and evidence_score >= 6):
            eid = slug(canonical)
            suffix = 2
            while eid in clusters:
                eid = f"{slug(canonical)}-{suffix}"; suffix += 1
            clusters[eid] = {'entity_id':eid, 'seeded':False, 'canonical_name':canonical, 'aliases':set(merged['spellings']), 'accounts':set(merged['handles']), 'projects':[], 'members':set(), 'rows':merged['rows'], 'sources':merged['sources'], 'videos':merged['videos']}
        else:
            nonpeople_rows[('unresolved_name',canonical)].extend(merged['rows'])

    # Build a single alias matcher, then search all transcripts for mentions and ASR variants.
    alias_pairs = []
    for eid,c in clusters.items():
        for a in {c['canonical_name'], *c['aliases'], *c['accounts']}:
            if a and len(norm(a)) >= 3: alias_pairs.append((norm(a),eid,a))
    alias_pairs.sort(key=lambda x: len(x[0]), reverse=True)
    alias_re = re.compile(r'(?<!\w)(' + '|'.join(re.escape(x[0]) for x in alias_pairs) + r')(?!\w)', re.I) if alias_pairs else None
    alias_lookup = {x[0]:x[1] for x in alias_pairs}
    surname_buckets = defaultdict(list)
    for eid, c in clusters.items():
        for alias in {c['canonical_name'], *c['aliases']}:
            parts = norm(alias).split()
            if len(parts) >= 2:
                surname_buckets[sound_key(parts[-1])].append((eid, alias))

    for doc in corpus:
        segs = doc['segments']
        for i, seg in enumerate(segs):
            text = str(seg.get('text','')); nt = norm(text); sec = float(seg.get('start',0) or 0)
            if alias_re:
                for m in alias_re.finditer(nt):
                    eid = alias_lookup.get(norm(m.group(1)))
                    if not eid: continue
                    ctx = context(segs, i, 4)
                    clusters[eid]['rows'].append({**doc, 'kind':'transcript', 'timestamp_seconds':sec, 'matched_name':m.group(1), 'context':ctx})
                    clusters[eid]['sources'].add(doc['source']); clusters[eid]['videos'].add(doc['video_id'])
            # Candidate bad-transcript variants only near a known surname or strong person grammar.
            if ASR_PERSON_RE.search(text):
                words = re.findall(r"[A-Za-z][A-Za-z'’-]{2,}", text)
                for width in (2,3):
                    for k in range(len(words)-width+1):
                        phrase = ' '.join(words[k:k+width])
                        if not plausible_name(phrase): continue
                        phrase_parts = norm(phrase).split()
                        possible = surname_buckets.get(sound_key(phrase_parts[-1]), []) if phrase_parts else []
                        if not possible:
                            continue
                        best_id, best_score = None, 0.0
                        for eid, alias in possible:
                            score = similarity(phrase, alias)
                            if score > best_score: best_id, best_score = eid, score
                        if best_id and best_score >= .88 and norm(phrase) not in {norm(x) for x in clusters[best_id]['aliases']}:
                            clusters[best_id]['aliases'].add(phrase)
                            clusters[best_id]['rows'].append({**doc, 'kind':'transcript_variant', 'timestamp_seconds':sec, 'matched_name':phrase, 'context':context(segs,i,4), 'variant_confidence':round(best_score,3)})

    valid = set(clusters)
    migrations = []
    if PEOPLE.exists():
        for folder in PEOPLE.iterdir():
            if folder.is_dir() and folder.name not in valid:
                dst = QUAR / folder.name
                if dst.exists(): shutil.rmtree(dst)
                dst.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(folder), str(dst))
                migrations.append({'from':f'people/{folder.name}','to':f'quarantine/{folder.name}','reason':'not resolved as person by open-world resolver'})

    registry = []
    for eid,c in sorted(clusters.items(), key=lambda x:x[1]['canonical_name'].casefold()):
        rows = c['rows']; folder = PEOPLE/eid
        if not rows and not folder.exists(): continue
        folder.mkdir(parents=True, exist_ok=True)
        aliases = []
        for a in [c['canonical_name'], *sorted(c['aliases']), *sorted(c['accounts'])]:
            if a and norm(a) not in {norm(x) for x in aliases}: aliases.append(a)
        mentions=[]; evidence=[]; associates=Counter(); appearances=[]; quotes=[]
        for r in rows:
            sec=float(r.get('timestamp_seconds',0) or 0); ev=evid(eid,r.get('video_id',''),sec,r.get('context',''))
            rec={'evidence_id':ev,'kind':r.get('kind'),'source':r.get('source'),'video_id':r.get('video_id'),'title':r.get('title'),'published':r.get('published'),'transcript_path':r.get('path'),'timestamp_seconds':sec,'timestamp_url':tsurl(r.get('url'),sec),'matched_name':r.get('matched_name'),'context':r.get('context'),'status':'confirmed_alias' if r.get('kind')!='transcript_variant' else 'probable_transcription_variant'}
            mentions.append(rec); evidence.append(rec)
            if len(str(r.get('context','')).split()) >= 8: quotes.append({'quote_id':ev,'text':r.get('context'),'source':r.get('source'),'video_id':r.get('video_id'),'timestamp_seconds':sec,'timestamp_url':rec['timestamp_url'],'status':'candidate','evidence_id':ev})
            if r.get('kind')=='title' and norm(str(r.get('source'))) not in {norm(x) for x in aliases}:
                appearances.append({'appearance_id':'appearance-'+str(r.get('video_id')),'video_id':r.get('video_id'),'show_or_channel':r.get('source'),'title':r.get('title'),'published':r.get('published'),'role':'subject_or_possible_guest','status':'candidate','evidence_ids':[ev]})
        for other_id, other in clusters.items():
            if other_id == eid: continue
            other_aliases = [other['canonical_name'], *other['aliases']]
            count = sum(1 for r in rows if any(re.search(r'(?<!\w)'+re.escape(norm(a))+r'(?!\w)', norm(str(r.get('context','')))) for a in other_aliases if len(norm(a))>=3))
            if count: associates[other['canonical_name']] += count
        profile = {
            'entity_id':eid,'canonical_name':c['canonical_name'],'entity_type':'person_or_persona','status':'confirmed' if c['seeded'] else 'provisional_open_world','auto_detected':not c['seeded'],'first_seen':min((str(r.get('published')) for r in rows if r.get('published')), default=None),'last_seen':max((str(r.get('published')) for r in rows if r.get('published')), default=None),'mention_count':len(mentions),'independent_sources':len({r.get('source') for r in rows if r.get('source')}),'video_count':len({r.get('video_id') for r in rows if r.get('video_id')}),'confidence':1.0 if c['seeded'] else round(min(.96,.58+.05*len(c['sources'])+.025*len(c['videos'])+(.12 if any(r.get('kind')=='source' for r in rows) else 0)),2),'updated_at':NOW,
        }
        projects=[{'name':p,'relationship':'known_project_cue','status':'confirmed_relationship','is_alias':False} for p in c.get('projects',[])]
        save(folder/'profile.json', profile); save(folder/'aliases.json', {'canonical_name':c['canonical_name'],'aliases':aliases,'accounts':sorted(c['accounts']),'status':'mixed_confirmed_and_learned','updated_at':NOW})
        save(folder/'mentions.json', {'count':len(mentions),'mentions':mentions,'updated_at':NOW}); save(folder/'evidence.json', {'count':len(evidence),'evidence':evidence,'updated_at':NOW})
        save(folder/'appearances.json', {'count':len(appearances),'appearances':appearances,'updated_at':NOW}); save(folder/'associates.json', {'associates':[{'name':n,'co_mentions':v,'status':'candidate'} for n,v in associates.most_common(40)],'updated_at':NOW})
        save(folder/'projects.json', {'projects':projects,'updated_at':NOW}); save(folder/'quotes.json', {'quotes':quotes[:250],'updated_at':NOW})
        for fname, payload in {
            'websites.json':{'websites':[]}, 'socials.json':{'socials':[{'handle':h,'status':'observed'} for h in sorted(c['accounts'])]}, 'emails.json':{'emails':[]}, 'shows.json':{'shows':[]}, 'comics.json':{'comic_history':[]}, 'timeline.json':{'events':[]}, 'terminology.json':{'terms':[]}, 'behaviour.json':{'patterns':[]}, 'activity.json':{'activity':[]}, 'stories.json':{'stories':[]}, 'influence.json':{'score':min(100,round(len(c['sources'])*8+len(c['videos'])*1.5)),'status':'provisional'}, 'threat.json':{'overall':'unassessed','status':'provisional'}, 'claims.json':{'claims':[]}, 'communities.json':{'communities':[]}, 'relationships.json':{'relationships':[]}, 'candidates.json':{'candidates':[]}, 'rejected.json':{'rejected':[]}, 'history.json':{'history':[{'at':NOW,'action':'open_world_profile_build'}]}, 'quality-control.json':{'review_required':not c['seeded'],'warnings':[] if c['seeded'] else ['Automatically discovered person/persona; verify canonical name and aliases.']}
        }.items():
            current=load(folder/fname,{})
            save(folder/fname,{**payload,**({'manual':current.get('manual')} if isinstance(current,dict) and 'manual' in current else {}),'updated_at':NOW})
        registry.append({'entity_id':eid,'canonical_name':c['canonical_name'],'entity_type':'person_or_persona','status':profile['status'],'confidence':profile['confidence'],'aliases':aliases,'profile_path':f'people/{eid}/profile.json','mention_count':len(mentions),'independent_sources':profile['independent_sources'],'video_count':profile['video_count']})

    nonperson_index = []
    unresolved = []
    for (typ,name), rows in sorted(nonpeople_rows.items(), key=lambda x:(x[0][0],x[0][1].casefold())):
        entry={'name':name,'classification':typ,'observation_count':len(rows),'sources':sorted({r.get('source') for r in rows if r.get('source')}),'videos':sorted({r.get('video_id') for r in rows if r.get('video_id')}),'evidence':rows[:30]}
        if typ=='unresolved_name': unresolved.append(entry)
        else: nonperson_index.append(entry)
    save(OUT/'entity-registry.json', {'updated_at':NOW,'entity_count':len(registry),'entities':registry,'mode':'open_world'})
    save(OUT/'non-people.json', {'updated_at':NOW,'count':len(nonperson_index),'entities':nonperson_index})
    save(CANDS/'unresolved-names.json', {'updated_at':NOW,'count':len(unresolved),'candidates':unresolved})
    save(OUT/'migration-log.json', {'updated_at':NOW,'migrations':migrations})
    save(OUT/'entity-scan-summary.json', {'updated_at':NOW,'transcripts_opened':len(corpus),'missing_transcripts':len(missing),'people_profiles':len(registry),'seeded_people':sum(1 for r in registry if r['status']=='confirmed'),'open_world_people':sum(1 for r in registry if r['status']=='provisional_open_world'),'non_people':len(nonperson_index),'unresolved_names':len(unresolved),'duration_seconds':round((datetime.now(timezone.utc)-started).total_seconds(),2)})
    print(f"Entity scan complete: {len(registry)} people/personas ({sum(1 for r in registry if r['status']=='provisional_open_world')} newly discovered), {len(nonperson_index)} non-people, {len(unresolved)} unresolved.")
    return 0

if __name__=='__main__': raise SystemExit(main())
