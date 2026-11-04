"""Public upload discovery. Metadata is not a transcript or a verified pick."""
from datetime import UTC,datetime,timedelta
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from services.prop_evidence_store import EvidenceStore
from runtime_paths import persistent_root

CHANNEL='UCwvzzWorj_-_d792FUo3gmg'
FEED='https://www.youtube.com/feeds/videos.xml?channel_id='+CHANNEL
NS={'a':'http://www.w3.org/2005/Atom','yt':'http://www.youtube.com/xml/schemas/2015'}


MAX_CONTENT_ATTEMPTS=4
CONTENT_RETRY=timedelta(hours=1)


def collect(*, now=None):
    now=now or datetime.now(UTC)
    with urllib.request.urlopen(FEED,timeout=20) as response:
        raw=response.read(1000001)
    if len(raw)>1000000:raise ValueError('Upload feed exceeds size limit')
    root=ET.fromstring(raw)
    if root.findtext('yt:channelId',namespaces=NS) not in (CHANNEL,CHANNEL[2:]):raise ValueError('Unexpected channel identity')
    store=EvidenceStore(persistent_root()/'prop-research'/'evidence.sqlite3')
    with store.connection() as db:
        rows=db.execute("SELECT payload FROM prop_evidence WHERE event_id=? AND kind='youtube_upload'",('youtube:'+CHANNEL,)).fetchall()
    seen={json.loads(row['payload'])['video_id'] for row in rows}
    new=[]
    for entry in root.findall('a:entry',NS):
        identifier=entry.findtext('yt:videoId',namespaces=NS)
        if not identifier or not re.fullmatch(r'[A-Za-z0-9_-]{11}',identifier) or identifier in seen:continue
        published=entry.findtext('a:published',namespaces=NS)
        payload={'video_id':identifier,'channel_id':CHANNEL,'analyst':'Daft Previews',
                 'url':'https://www.youtube.com/watch?v='+identifier,'title':entry.findtext('a:title',namespaces=NS),
                 'published_at':published,'content_status':'metadata_only_pending_content_review',
                 'notice':'No picks inferred from titles; transcript, game association and current line validity require verification.'}
        store.append('youtube:'+CHANNEL,'youtube_upload','Daft Previews public upload feed',published,payload)
        seen.add(identifier);new.append(payload)
    from services.prop_video_content import fetch
    from services.prediction_lifecycle import parse_kickoff
    with store.connection() as db:
        uploads=[json.loads(r['payload']) for r in db.execute("SELECT payload FROM prop_evidence WHERE event_id=? AND kind='youtube_upload' ORDER BY published_at DESC",('youtube:'+CHANNEL,))]
        attempts={}
        completed=set()
        for row in db.execute("SELECT payload,received_at FROM prop_evidence WHERE event_id=? AND kind IN ('youtube_transcript','youtube_content_status') ORDER BY id",('youtube:'+CHANNEL,)):
            payload=json.loads(row['payload']);identifier=payload['video_id']
            if payload.get('status')=='available' and payload.get('segments'):
                completed.add(identifier)
            prior=attempts.get(identifier,{'count':0})
            attempts[identifier]={'count':prior['count']+1,'at':parse_kickoff(row['received_at'])}
    content=[]
    for video in uploads:
        published=parse_kickoff(video.get('published_at'))
        prior=attempts.get(video['video_id'],{})
        if (video['video_id'] in completed or prior.get('count',0)>=MAX_CONTENT_ATTEMPTS
                or (prior.get('at') and now-prior['at']<CONTENT_RETRY)
                or not published or not timedelta(0)<=now-published<=timedelta(days=2)
                or 'nba' not in (video.get('title') or '').lower()):continue
        try:
            payload=fetch(video['video_id']);kind='youtube_transcript'
        except Exception as error:
            payload={'video_id':video['video_id'],'status':'unavailable','reason_type':type(error).__name__};kind='youtube_content_status'
        payload.update(url=video['url'],published_at=video['published_at'],analyst='Daft Previews',channel_id=CHANNEL)
        payload['attempt']=prior.get('count',0)+1
        store.append('youtube:'+CHANNEL,kind,'Daft Previews public captions',video['published_at'],payload,received_at=now)
        content.append({'video_id':video['video_id'],'status':payload['status']})
        break  # At most one transcript attempt per check; failures never trigger a paid fallback.
    return {'checked_at':now.isoformat(),'new_uploads':new,'content_updates':content,'api_ai_cost':0}


if __name__=='__main__':
    print(json.dumps(collect(),indent=2))
