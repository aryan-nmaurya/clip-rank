"""Publishing text drawn from the selected, reviewed footage, never a search topic."""
import re


STOP_WORDS=set('a an the and or but so to of in on at for with from by into toward towards before after '
    'up off out over under through around it its this that these those he she his her they their them '
    'is are was were be being been has have had does did do will can could would should as while then '
    'actually just very really also performs performer completes shows showing seen visible footage '
    'video videos clip clips short shorts watch look test fixture'.split())
FORMAT_TAGS=['shorts','youtube shorts','short video','vertical video','shortform','video clip',
    'short clip','yt shorts','short videos','short clips','youtube short','vertical short',
    'vertical clip','mobile video','portrait video','short format','short form video','youtube clip',
    'shortform video','mobile short']


def selected_records(clip,project):
    result=project.get('result_data',{})
    variant=next((v for v in result.get('variants',[]) if v.get('clip_id')==clip['id']),None)
    if variant:return variant.get('moments',[])
    matching=[m for m in result.get('moments',[]) if m.get('clip_id')==clip['id']]
    return matching


def action_text(record):
    script=record.get('script') or {}
    text=script.get('observed_action')
    if not text:
        review=record.get('final_review') or {}
        observations=review.get('observations',[]) if review.get('passed') is True else []
        text=' '.join(dict.fromkeys(o.get('visible_event','') for o in observations if o.get('visible_event')))
    return text or record.get('observed_action') or ''


def scene_description(clip,project):
    records=selected_records(clip,project)
    ranked=any(type(r.get('assigned_rank')) is int for r in records)
    if ranked:records=sorted(records,key=lambda r:r.get('assigned_rank',0),reverse=True)
    lines=[]
    for record in records[:7]:
        text=' '.join(str(action_text(record)).split())
        # Preserve complete words; long source analyses are not public essays.
        text=' '.join(text.split()[:55]).rstrip('. ')
        if text:
            line=(f"#{record['assigned_rank']}: " if ranked and record.get('assigned_rank') else '')+text+'.'
            if line not in lines:lines.append(line)
    if lines:return ('This Short ranks these moments:\n' if ranked else '')+'\n'.join(lines)
    # Older reviewed exports may only retain the clip's action summary/title.
    return ' '.join(str(clip.get('reason') or clip['title']).split()).rstrip('. ')+'.'


def tag_characters(tags):
    # Commas and the API's implicit quotes around multiword tags count, too.
    return sum(len(t)+(2 if ' ' in t else 0) for t in tags)+max(0,len(tags)-1)


def scene_tags(description):
    words=re.findall(r"[^\W\d_]+(?:[-'][^\W\d_]+)*",description.lower())
    terms=list(dict.fromkeys(w for w in words if w not in STOP_WORDS and 2<=len(w)<=30))
    phrases=[]
    for size in (2,3):
        for start in range(len(words)-size+1):
            group=words[start:start+size]
            if group[0] not in STOP_WORDS and group[-1] not in STOP_WORDS:
                phrase=' '.join(group)
                if len(phrase)<=35:phrases.append(phrase)
    # Prioritize the actual scene, then its short-form presentation. No universal
    # "fails", "viral" or trending-subject tags that could misdescribe footage.
    specific=[]
    if re.search(r'\bbackflips?\b',description.lower()):
        specific+=['backflip','backflips','backflip stunt','backflip shorts','backflip video','backflip skills',
                   'acrobatics','acrobatic flip','acrobatic skills','flip','flips','body control','landing']
    if 'wall' in words and any(w.startswith('climb') or w=='scaling' for w in words):
        specific+=['wall climb','wall climbing','climbing','climb','climbing skills','wall movement','climbing shorts']
    if 'aerial' in words and 'flip' in words:
        specific+=['aerial flip','aerial acrobatics','acrobatics','flip','flips','flip shorts']
    if any(w in ('cat','cats') for w in words):
        specific+=['cat','cats','feline','cat shorts','cat video','cat clip','cat movement']
    if description.startswith('This Short ranks'):
        specific+=['ranking','countdown','ranked clips','ranking shorts','countdown shorts']
    candidates=specific+terms[:12]+list(dict.fromkeys(phrases))[:12]+FORMAT_TAGS[:10]
    for term in terms[:8]:
        candidates.extend(f'{term} {suffix}' for suffix in
            ('shorts','clip','video','footage','scene','moment','short','short video','video clip','short clip','highlight','shortform'))
    candidates+=FORMAT_TAGS[10:]
    candidates=list(dict.fromkeys(candidates))
    tags=[]
    for candidate in candidates:
        if tag_characters(tags+[candidate])<=500:tags.append(candidate)
        if len(tags)==32:return tags
    # Very verbose observations can consume the budget early. Prefer concise,
    # still-grounded alternatives rather than exceeding YouTube's hard limit.
    tags=[]
    for candidate in sorted(candidates,key=lambda t:(len(t)+(2 if ' ' in t else 0),candidates.index(t))):
        if tag_characters(tags+[candidate])<=500:tags.append(candidate)
        if len(tags)==32:return tags
    raise ValueError('The reviewed Short needs more concise visual details to generate 32 relevant tags within YouTube’s limit.')
