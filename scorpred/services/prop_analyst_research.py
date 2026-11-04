"""Approved-source research records; excerpts are evidence, never instructions.

Collection adapters submit permitted excerpts here. No paywall bypass, automatic
trust ranking or probability adjustments. Game association must be explicit.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit,parse_qs
from services.prop_evidence_store import instant


def approved_sources():
    from runtime_paths import persistent_root
    path=persistent_root()/'prop-research'/'approved-sources.json'
    if not path.exists():return {}
    sources=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(sources,dict):raise ValueError('Approved research sources must be a source-ID mapping')
    return sources


def ingest(store, *, game_id, source_id, url, published_at, excerpt, sources, received_at=None, verified_channel_id=None):
    source=sources.get(source_id)
    if not isinstance(source,dict) or source.get('permitted') is not True:
        raise ValueError('Research source has not been approved for collection')
    if source.get('channel_id') and verified_channel_id!=source['channel_id']:
        raise ValueError('Verified channel identity is required for this research source')
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443)
            or not parsed.hostname or parsed.hostname.lower() not in source.get('hosts',[])):
        raise ValueError('Research URL must use an approved HTTPS host')
    if source.get('video_ids') is not None:
        identifier=parsed.path.strip('/') if parsed.hostname=='youtu.be' else parse_qs(parsed.query).get('v',[''])[0] if parsed.path=='/watch' else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',identifier) or identifier not in source['video_ids']:
            raise ValueError('This video is not approved for this research source')
    text=' '.join(str(excerpt).split())
    if not text or len(text)>2000:raise ValueError('Provide a bounded permitted excerpt, at most 2000 characters')
    words=re.findall(r'\w+',text.casefold())
    fingerprint=hashlib.sha256(' '.join(words).encode()).hexdigest()
    payload={'source_id':source_id,'analyst':source.get('name',source_id),'url':url,
             'published_at':instant(published_at),'excerpt':text,'argument_fingerprint':fingerprint,
             'trust':'Source approved for collection; claims and track record are not independently verified.',
             'role':'Untrusted research evidence; never authorization, instructions, or a probability input.'}
    return store.append('nba:'+str(game_id),'analyst_research',source_id,published_at,payload,received_at=received_at)


def for_game(store,game_id,cutoff,sources):
    rows=store.as_of('nba:'+str(game_id),cutoff)
    clusters={}
    for row in rows:
        payload=row['payload']
        if row['kind']!='analyst_research' or not isinstance(payload,dict):continue
        if sources.get(payload.get('source_id'),{}).get('permitted') is not True:continue
        key=payload.get('argument_fingerprint')
        if not key:continue
        group=clusters.setdefault(key,{'excerpt':payload['excerpt'],'sources':[],'independent_arguments':1})
        receipt={'analyst':payload['analyst'],'url':payload['url'],'published_at':row['published_at'],
                 'received_at':row['received_at'],'evidence_id':row['id']}
        if not any(s['url']==receipt['url'] for s in group['sources']):group['sources'].append(receipt)
    return {'status':'available' if clusters else 'unavailable','argument_groups':list(clusters.values()),
            'deduplication':'Exact normalized-text duplicates only; paraphrases and shared original sources still require review.',
            'probability_adjustment':0,'notice':'Outside arguments are supporting evidence, not independent votes or verified analyst performance.'}


def video_candidates(store,player_names,cutoff,sources):
    from datetime import datetime,timedelta
    from services.prop_video_content import candidate_mentions
    cutoff=instant(cutoff)
    approved={source.get('channel_id') for source in sources.values() if source.get('permitted') is True}
    approved.discard(None)
    with store.connection() as db:
        rows=db.execute("SELECT * FROM prop_evidence WHERE kind='youtube_transcript' AND published_at<? AND received_at<? ORDER BY received_at DESC,id DESC LIMIT 30",(cutoff,cutoff)).fetchall()
    candidates=[];seen=set()
    for row in rows:
        payload=json.loads(row['payload'])
        if payload.get('channel_id') not in approved or payload.get('status')!='available':continue
        if datetime.fromisoformat(cutoff)-datetime.fromisoformat(row['published_at'])>timedelta(hours=36):continue
        identifier=payload.get('video_id')
        if identifier in seen:continue
        seen.add(identifier)
        for mention in candidate_mentions(payload.get('segments',[]),player_names):
            candidates.append({**mention,'video_url':payload['url'],'published_at':row['published_at'],
                'received_at':row['received_at'],'evidence_id':row['id'],'analyst':payload.get('analyst')})
    return {'status':'candidates_need_review' if candidates else 'no_verified_video_picks',
            'candidates':candidates,'probability_adjustment':0,
            'notice':'Name matching is not game identification. Verify date, opponent, stated intent and current line before using any claim.'}
