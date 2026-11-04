"""Bounded public captions. No cookies, proxy bypass, audio download or AI."""
import math
import re


def fetch(video_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):raise ValueError('Invalid video ID')
    from youtube_transcript_api import YouTubeTranscriptApi
    transcript=YouTubeTranscriptApi().fetch(video_id)
    raw=transcript.to_raw_data()
    segments=[];characters=0
    for segment in raw[:4000]:
        start=segment.get('start');text=segment.get('text')
        if (not isinstance(text,str) or not isinstance(start,(int,float)) or isinstance(start,bool)
                or not math.isfinite(start) or start<0):continue
        characters+=len(text)
        if characters>100000:break
        segments.append({'start':start,'text':text[:1000]})
    return {'video_id':video_id,'segments':segments,'language':getattr(transcript,'language_code',None),
            'generated':getattr(transcript,'is_generated',None),'status':'available' if segments else 'empty',
            'scope':'Captions can contain transcription errors and promotion. No picks are automatically verified.'}


def candidate_mentions(segments,player_names):
    """Queue explicit numeric points statements for review; never recommend."""
    found=[];seen=set()
    for index,segment in enumerate(segments):
        window=' '.join(s['text'] for s in segments[max(0,index-2):index+4])
        for name in player_names:
            pattern=r'\b'+re.escape(name)+r'\b.{0,100}?\b(over|under)\s+(\d+(?:\.\d+)?)\s+points\b'
            match=re.search(pattern,window,re.I)
            if not match:continue
            identity=(name.casefold(),match[1].lower(),float(match[2]))
            if identity in seen:continue
            seen.add(identity)
            found.append({'player':name,'side':identity[1],'line':identity[2],'market':'points',
                'timestamp_seconds':segment['start'],'excerpt':window[:1000],
                'status':'unverified_caption_candidate','warning':'May refer to a past result, a lean or a different price; review the source context.'})
    return found
