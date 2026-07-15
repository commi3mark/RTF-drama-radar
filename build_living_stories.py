from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from radar_common import ROOT, load_json, save_json, path_for, now_iso, stable_id


STOPWORDS = {
    'the','a','an','and','or','of','to','in','on','for','with','from','by','is','was','are','be','this','that',
    'show','stream','video','episode','live','clip','clips','comicsgate','comics','gate','update','news'
}


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r'[^a-z0-9]+', '-', value)
    return value.strip('-') or 'unknown'


def words(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9']+", value.lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def merge_unique(rows: list[dict[str, Any]], additions: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    result = list(rows)
    seen = {tuple(str(row.get(key, '')) for key in keys) for row in result}
    for row in additions:
        marker = tuple(str(row.get(key, '')) for key in keys)
        if marker not in seen:
            result.append(row)
            seen.add(marker)
    return result


def seed_story(path: Path) -> dict[str, Any]:
    data = load_json(path, {})
    story_id = str(data.get('story_id') or path.stem)
    title = str(data.get('title') or story_id.replace('-', ' ').title())
    entity_names: set[str] = set()

    for key in ('people', 'participants', 'subjects', 'entities'):
        values = data.get(key, [])
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str):
                    entity_names.add(value)
                elif isinstance(value, dict):
                    name = value.get('name') or value.get('entity') or value.get('entity_name')
                    if name:
                        entity_names.add(str(name))

    seed_text = ' '.join([
        title,
        str(data.get('summary') or ''),
        str(data.get('current_phase') or ''),
        ' '.join(entity_names),
    ])

    return {
        'story_id': story_id,
        'title': title,
        'canonical_path': str(path.relative_to(ROOT)).replace('\\', '/'),
        'seed_words': sorted(words(seed_text)),
        'seed_entities': sorted(entity_names),
        'existing': data,
    }


def score_match(story: dict[str, Any], text: str, entities: set[str], explicit_matches: list[dict[str, Any]]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0

    for match in explicit_matches:
        if str(match.get('story_id')) == story['story_id']:
            explicit_score = float(match.get('score') or 0)
            score += 0.65 + min(0.25, explicit_score * 0.25)
            reasons.append(f"pre-analysis story match {explicit_score:.3f}")

    text_words = words(text)
    seed_words = set(story['seed_words'])
    overlap = text_words & seed_words
    if overlap:
        coverage = len(overlap) / max(1, len(seed_words))
        score += min(0.35, coverage * 0.7)
        reasons.append('keyword overlap: ' + ', '.join(sorted(overlap)[:8]))

    seed_entities = {name.lower() for name in story['seed_entities']}
    entity_overlap = seed_entities & {name.lower() for name in entities}
    if entity_overlap:
        score += min(0.4, 0.18 * len(entity_overlap))
        reasons.append('entity overlap: ' + ', '.join(sorted(entity_overlap)))

    return min(score, 1.0), reasons


def receipt_rows(episode: dict[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for subject in episode.get('subjects', [])[:8]:
        for receipt in subject.get('receipts', [])[:2]:
            receipts.append({
                'timestamp': receipt.get('timestamp'),
                'entity': subject.get('entity'),
                'excerpt': receipt.get('excerpt'),
            })
    for claim in episode.get('claims', [])[:5]:
        receipts.append({
            'timestamp': claim.get('timestamp'),
            'entity': ', '.join(claim.get('subjects', [])),
            'excerpt': claim.get('receipt') or claim.get('claim'),
            'kind': 'claim_candidate',
        })
    return receipts[:12]


def episode_development(episode: dict[str, Any], score: float, reasons: list[str]) -> dict[str, Any]:
    participants = [row.get('entity') for row in episode.get('participant_candidates', []) if row.get('entity')]
    subjects = [row.get('entity') for row in episode.get('subjects', []) if row.get('entity')]
    major = [row.get('entity') for row in episode.get('subjects', []) if row.get('importance_guess') == 'major']

    return {
        'development_id': stable_id('episode', str(episode.get('video_id')), str(score)),
        'kind': 'transcript_evidence',
        'evidence_status': 'candidate_transcript_analysis',
        'match_confidence': round(score, 3),
        'match_reasons': reasons,
        'published': episode.get('published'),
        'source': episode.get('source'),
        'title': episode.get('title'),
        'video_id': episode.get('video_id'),
        'url': f"https://www.youtube.com/watch?v={episode.get('video_id')}" if episode.get('video_id') else None,
        'participants': participants,
        'subjects': subjects,
        'major_subjects': major,
        'claims': episode.get('claims', [])[:10],
        'quotes': episode.get('quotes', [])[:10],
        'relationships': episode.get('relationships', [])[:10],
        'receipts': receipt_rows(episode),
    }


def radar_development(item: dict[str, Any], score: float, reasons: list[str]) -> dict[str, Any]:
    return {
        'development_id': stable_id('radar', str(item.get('id')), str(score)),
        'kind': 'radar_lead',
        'evidence_status': 'metadata_lead',
        'match_confidence': round(score, 3),
        'match_reasons': reasons,
        'published': item.get('published'),
        'source': item.get('source'),
        'title': item.get('title'),
        'video_id': item.get('youtube_id'),
        'url': item.get('url'),
        'description': str(item.get('description') or '')[:900],
        'transcript_status': item.get('transcript_status'),
        'receipts': [],
    }


def heat_for(developments: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    heat = 0.0
    recent_24 = 0
    recent_72 = 0
    sources = set()
    evidence_count = 0

    for row in developments:
        published = parse_date(row.get('published'))
        age_hours = 9999.0 if not published else max(0.0, (now - published).total_seconds() / 3600)
        recency = 0.0
        if age_hours <= 24:
            recency = 20
            recent_24 += 1
        elif age_hours <= 72:
            recency = 12
        elif age_hours <= 168:
            recency = 6
        elif age_hours <= 720:
            recency = 2

        if age_hours <= 72:
            recent_72 += 1

        confidence = float(row.get('match_confidence') or 0)
        evidence_weight = 1.35 if row.get('kind') == 'transcript_evidence' else 0.65
        heat += recency * confidence * evidence_weight

        if row.get('source'):
            sources.add(str(row['source']))
        if row.get('kind') == 'transcript_evidence':
            evidence_count += 1
            heat += min(8, len(row.get('receipts', [])) * 0.8)
            heat += min(8, len(row.get('claims', [])) * 1.0)
            heat += min(6, len(row.get('relationships', [])) * 0.75)

    heat += min(18, len(sources) * 3)
    heat += min(12, recent_72 * 2)

    return {
        'heat': round(min(100.0, heat), 1),
        'recent_24h': recent_24,
        'recent_72h': recent_72,
        'unique_sources': len(sources),
        'transcript_evidence_items': evidence_count,
    }


def phase_for(developments: list[dict[str, Any]], now: datetime) -> str:
    latest = max((parse_date(row.get('published')) for row in developments), default=None)
    if not latest:
        return 'dormant'
    age_days = (now - latest).total_seconds() / 86400
    if age_days <= 1:
        return 'breaking'
    if age_days <= 3:
        return 'developing'
    if age_days <= 7:
        return 'active'
    if age_days <= 30:
        return 'cooling'
    return 'dormant'


def describe_change(development: dict[str, Any]) -> str:
    source = development.get('source') or 'Unknown source'
    title = development.get('title') or 'Untitled item'
    major = development.get('major_subjects', [])
    if development.get('kind') == 'transcript_evidence' and major:
        return f"{source} substantially discussed {', '.join(major[:4])} in “{title}”."
    if development.get('kind') == 'transcript_evidence':
        return f"{source} added transcript-backed discussion in “{title}”."
    return f"{source} published “{title}”, a new lead awaiting transcript-level confirmation."


def story_markdown(story: dict[str, Any]) -> str:
    lines = [
        f"# {story['title']}", '',
        f"- **Status:** {story['status']}",
        f"- **Current phase:** {story['current_phase']}",
        f"- **Heat:** {story['heat']['heat']}/100",
        f"- **Last updated:** {story.get('last_updated') or 'Unknown'}", '',
        '## Current picture', '', story.get('current_picture') or '_No summary available._', '',
        '## Latest developments', ''
    ]

    for row in story.get('developments', [])[:30]:
        receipt = 'transcript evidence' if row.get('kind') == 'transcript_evidence' else 'metadata lead'
        lines.append(f"### {row.get('published') or 'Unknown date'} — {row.get('source') or 'Unknown source'}")
        lines.append('')
        lines.append(f"**{row.get('title') or 'Untitled'}** — {receipt}; match {float(row.get('match_confidence') or 0):.0%}")
        lines.append('')
        if row.get('url'):
            lines.append(f"Source: {row['url']}")
            lines.append('')
        for rec in row.get('receipts', [])[:5]:
            stamp = rec.get('timestamp') or 'timestamp unavailable'
            entity = rec.get('entity') or 'Evidence'
            excerpt = rec.get('excerpt') or ''
            lines.append(f"- **{stamp} — {entity}:** {excerpt}")
        lines.append('')

    lines.extend(['## People and shows in this story', ''])
    for name, count in story.get('entity_counts', {}).items():
        lines.append(f"- **{name}:** {count} supporting items")

    lines.extend(['', '## Unresolved questions', ''])
    for question in story.get('unresolved_questions', []):
        lines.append(f"- {question}")

    return '\n'.join(lines).rstrip() + '\n'


def briefing_markdown(briefing: dict[str, Any]) -> str:
    lines = [
        '# Dongs Story Briefing', '',
        f"Coverage starts: **{briefing['since']}**", '',
        '> Editorial rule: report what changed in the stories below. Do not mention software, pipelines, transcript retrieval, candidate folders, confidence engines, or processing status.', '',
    ]

    for index, story in enumerate(briefing.get('stories', []), start=1):
        lines.extend([
            f"## {index}. {story['title']}", '',
            f"- **Heat:** {story['heat']}/100",
            f"- **Phase:** {story['phase']}",
            f"- **New items:** {story['new_items']}",
            f"- **Sources involved:** {', '.join(story['sources']) or 'Unknown'}", '',
            story['change_summary'], '',
            '### Receipts', ''
        ])
        for receipt in story.get('receipts', [])[:8]:
            line = f"- **{receipt.get('source')} — {receipt.get('title')}**"
            if receipt.get('timestamp'):
                line += f" at {receipt['timestamp']}"
            if receipt.get('url'):
                line += f" — {receipt['url']}"
            if receipt.get('excerpt'):
                line += f"\n  - {receipt['excerpt']}"
            lines.append(line)
        lines.append('')

    return '\n'.join(lines).rstrip() + '\n'


def main() -> int:
    canonical_root = path_for('octopuss_stories')
    candidate_root = path_for('octopuss_candidates') / 'episodes'
    output_root = path_for('octopuss_living_stories')
    output_root.mkdir(parents=True, exist_ok=True)

    radar_path = path_for('radar_output')
    radar_items = load_json(radar_path, [])
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=4)

    stories = [seed_story(path) for path in canonical_root.glob('*.json')]
    if not stories:
        print('Living stories: no canonical story seeds found.')
        return 0

    developments: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for path in candidate_root.glob('*.json'):
        episode = load_json(path, {})
        text = ' '.join([
            str(episode.get('title') or ''),
            str(episode.get('source') or ''),
            ' '.join(str(row.get('entity') or '') for row in episode.get('subjects', [])),
            ' '.join(str(row.get('claim') or '') for row in episode.get('claims', [])),
        ])
        entities = {str(row.get('entity')) for row in episode.get('subjects', []) if row.get('entity')}
        explicit = episode.get('story_matches', [])

        ranked = []
        for story in stories:
            score, reasons = score_match(story, text, entities, explicit)
            if score >= 0.42:
                ranked.append((score, story, reasons))
        ranked.sort(key=lambda row: row[0], reverse=True)

        for score, story, reasons in ranked[:2]:
            developments[story['story_id']].append(episode_development(episode, score, reasons))

    known_video_ids = {
        row.get('video_id')
        for rows in developments.values()
        for row in rows
        if row.get('video_id')
    }

    for item in radar_items:
        if item.get('youtube_id') in known_video_ids:
            continue
        text = ' '.join([str(item.get('title') or ''), str(item.get('description') or ''), str(item.get('source') or '')])
        entities: set[str] = set()
        ranked = []
        for story in stories:
            score, reasons = score_match(story, text, entities, [])
            if score >= 0.33:
                ranked.append((score, story, reasons))
        ranked.sort(key=lambda row: row[0], reverse=True)
        for score, story, reasons in ranked[:1]:
            developments[story['story_id']].append(radar_development(item, score, reasons))

    active_rows = []
    briefing_rows = []

    for story_seed in stories:
        story_id = story_seed['story_id']
        rows = merge_unique([], developments.get(story_id, []), ('development_id',))
        rows.sort(key=lambda row: parse_date(row.get('published')) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        heat = heat_for(rows, now)
        phase = phase_for(rows, now)
        latest = rows[0].get('published') if rows else None

        entity_counter: Counter[str] = Counter()
        for row in rows:
            entity_counter.update(str(name) for name in row.get('participants', []) if name)
            entity_counter.update(str(name) for name in row.get('subjects', []) if name)
            if row.get('source'):
                entity_counter.update([str(row['source'])])

        recent = [row for row in rows if (parse_date(row.get('published')) or datetime.min.replace(tzinfo=timezone.utc)) >= since]
        change_lines = [describe_change(row) for row in recent[:5]]
        current_picture = ' '.join(change_lines) if change_lines else 'No material change during the current reporting window.'

        story = {
            'schema_version': 'octopuss-living-story-v0.1',
            'story_id': story_id,
            'title': story_seed['title'],
            'status': 'active' if phase in {'breaking','developing','active'} else phase,
            'current_phase': phase,
            'heat': heat,
            'last_updated': latest,
            'current_picture': current_picture,
            'developments': rows,
            'entity_counts': dict(entity_counter.most_common(25)),
            'unresolved_questions': story_seed['existing'].get('unresolved_questions', []),
            'canonical_story_path': story_seed['canonical_path'],
            'generated_at': now_iso(),
        }
        save_json(output_root / f'{story_id}.json', story)
        (output_root / f'{story_id}.md').write_text(story_markdown(story), encoding='utf-8')

        if heat['heat'] > 0:
            active_rows.append({
                'story_id': story_id,
                'title': story_seed['title'],
                'heat': heat['heat'],
                'phase': phase,
                'last_updated': latest,
                'recent_24h': heat['recent_24h'],
                'recent_72h': heat['recent_72h'],
                'unique_sources': heat['unique_sources'],
                'new_since_window': len(recent),
                'path': f'intelligence/living-stories/{story_id}.json',
            })

        if recent:
            sources = sorted({str(row.get('source')) for row in recent if row.get('source')})
            receipt_list = []
            for row in recent[:8]:
                if row.get('receipts'):
                    for rec in row['receipts'][:2]:
                        receipt_list.append({
                            'source': row.get('source'),
                            'title': row.get('title'),
                            'url': row.get('url'),
                            'timestamp': rec.get('timestamp'),
                            'excerpt': rec.get('excerpt'),
                            'evidence_status': row.get('evidence_status'),
                        })
                else:
                    receipt_list.append({
                        'source': row.get('source'),
                        'title': row.get('title'),
                        'url': row.get('url'),
                        'timestamp': None,
                        'excerpt': None,
                        'evidence_status': row.get('evidence_status'),
                    })
            briefing_rows.append({
                'story_id': story_id,
                'title': story_seed['title'],
                'heat': heat['heat'],
                'phase': phase,
                'new_items': len(recent),
                'sources': sources,
                'change_summary': current_picture,
                'receipts': receipt_list[:12],
            })

    active_rows.sort(key=lambda row: (row['heat'], row['last_updated'] or ''), reverse=True)
    briefing_rows.sort(key=lambda row: row['heat'], reverse=True)

    active_payload = {
        'schema_version': 'octopuss-active-stories-v0.1',
        'generated_at': now_iso(),
        'count': len(active_rows),
        'stories': active_rows,
    }
    save_json(output_root / 'active-stories.json', active_payload)
    active_md = ['# Active Stories', '']
    for row in active_rows:
        active_md.append(f"- **{row['heat']}/100 — {row['title']}** — {row['phase']}; {row['new_since_window']} new items in reporting window")
    (output_root / 'active-stories.md').write_text('\n'.join(active_md).rstrip() + '\n', encoding='utf-8')

    briefing = {
        'schema_version': 'dongs-story-briefing-v0.1',
        'generated_at': now_iso(),
        'since': since.isoformat(),
        'editorial_rules': [
            'Name names.',
            'Lead with the stories that changed most, not upload counts.',
            'Use transcript receipts before metadata-only leads.',
            'Do not mention software, automation, transcript retrieval, queues, confidence engines or GitHub.',
            'Distinguish appearances from people merely discussed.',
            'Attribute allegations and interpretations to the person or show making them.',
        ],
        'stories': briefing_rows[:12],
    }
    save_json(output_root / 'dongs-briefing.json', briefing)
    (output_root / 'dongs-briefing.md').write_text(briefing_markdown(briefing), encoding='utf-8')

    print(f"Living stories rebuilt: {len(stories)}")
    print(f"Active stories:         {len(active_rows)}")
    print(f"Dongs briefing stories: {len(briefing_rows[:12])}")
    if active_rows:
        print(f"Hottest story:          {active_rows[0]['title']} ({active_rows[0]['heat']}/100)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
