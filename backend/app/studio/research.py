"""Primary-source research. A research page is evidence, never a visual reuse grant."""
import hashlib
import ipaddress
import re
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
import requests
from app.studio import store
from app.studio.models import now

PRIMARY_DOMAINS = ('nasa.gov', 'nist.gov', 'deepmind.google', 'blog.google', 'research.google',
                   'microsoft.com', 'nvidia.com', 'mit.edu', 'arxiv.org', 'huggingface.co', 'openai.com')
FEEDS = (
    ('https://blogs.nvidia.com/feed/', 'future_technology'),
    ('https://news.mit.edu/rss/topic/robotics', 'robotics'),
    ('https://blogs.microsoft.com/ai/feed/', 'ai_tools'),
    ('https://www.nasa.gov/feed/', 'engineering'),
    ('https://blog.google/technology/ai/rss/', 'automation'),
)


def primary_url(url):
    p = urlparse(url)
    host = (p.hostname or '').lower()
    return p.scheme == 'https' and not p.username and not p.password and p.port in (None,443) and any(host==d or host.endswith('.'+d) for d in PRIMARY_DOMAINS)


def fetch(url, maximum=2_000_000):
    for _ in range(4):
        if not primary_url(url):
            raise ValueError('Research is limited to configured HTTPS primary sources.')
        addresses = socket.getaddrinfo(urlparse(url).hostname,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise ValueError('Research cannot access a private network address.')
        with requests.get(url,timeout=(8,25),stream=True,allow_redirects=False,
                          headers={'User-Agent':'ClipRank/2.0 (+research; no media reuse)'}) as r:
            if r.status_code in (301,302,303,307,308):
                url=urljoin(url,r.headers.get('Location',''))
                continue
            r.raise_for_status()
            content=bytearray()
            for part in r.iter_content(65536):
                content.extend(part)
                if len(content)>maximum:
                    raise ValueError('Research document exceeds its size limit.')
            return bytes(content),url
    raise ValueError('Research redirect limit reached.')


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocked=0
        self.blocks=[]
        self.current=None
        self.title=''
        self.in_title=False

    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','nav','footer','noscript','svg'):
            self.blocked+=1
        if tag=='title': self.in_title=True
        if tag in ('p','h1','h2','h3','li') and not self.blocked:
            self.current=[]

    def handle_endtag(self,tag):
        if tag in ('script','style','nav','footer','noscript','svg'):
            self.blocked=max(0,self.blocked-1)
        if tag=='title': self.in_title=False
        if tag in ('p','h1','h2','h3','li') and self.current is not None:
            text=' '.join(' '.join(self.current).split())
            if len(text)>45: self.blocks.append(text)
            self.current=None

    def handle_data(self,data):
        if self.in_title: self.title+=data
        if self.current is not None and not self.blocked: self.current.append(data)


class ResearchEngine:
    @staticmethod
    def collect(item):
        raw,url=fetch(item['source_url'])
        parser=ArticleParser(); parser.feed(raw.decode('utf-8',errors='replace'))
        paragraphs=list(dict.fromkeys(parser.blocks))[:45]
        if sum(map(len,paragraphs))<250:
            raise ValueError('The primary source has too little readable evidence. No factual script will be invented.')
        return {'source_url':url,'publisher':urlparse(url).hostname,'title':parser.title[:300],
                'fetched_at':now(),'sha256':hashlib.sha256(raw).hexdigest(),
                'release_context':item.get('published_at'),
                'evidence':[{'id':f'e{i+1}','text':text[:1800],'kind':'primary_source_statement'} for i,text in enumerate(paragraphs)],
                'media_rights':'Research access does not grant visual reuse. Only original ClipRank graphics are used.'}

    @classmethod
    def collect_ranking(cls,item):
        alternatives=[candidate for candidate in store.opportunities() if candidate['category']==item['category'] and candidate['id']!=item['id']]
        if len(alternatives)<2: raise ValueError('A technology ranking needs three distinct primary-source opportunities in this pillar. Discover more stories first.')
        candidates=[item,*alternatives[:2]];evidence=[];records=[]
        for index,candidate in enumerate(candidates):
            source=cls.collect(candidate)
            local=[]
            for entry in source['evidence'][:22]:
                verified={**entry,'id':f'r{index+1}_{entry["id"]}','source_url':source['source_url']}
                evidence.append(verified);local.append(verified)
            records.append({'id':candidate['id'],'topic':candidate['topic'],'source_url':source['source_url'],
                            'publisher':source['publisher'],'evidence':local})
        return {'source_url':item['source_url'],'publisher':'Primary technology sources','fetched_at':now(),
                'evidence':evidence,'ranking_candidates':records,'release_context':'Independent editorial ranking of reported developments.',
                'media_rights':'Original graphics only. Research access does not grant visual reuse.'}


class OpportunityDiscovery:
    @staticmethod
    def classify(title,default):
        t=title.lower()
        if any(s in t for s in ('robot','humanoid','autonomous vehicle')): return 'robotics'
        if re.search(r'\bai\b|machine learning|artificial intelligence|language model|agentic',t): return 'ai_tools'
        if any(s in t for s in ('automat','workflow')): return 'automation'
        if any(s in t for s in ('engineer','machine','battery','magnet','material')): return 'engineering'
        return default

    @classmethod
    def discover(cls,limit=20):
        results=[]; errors=[]
        profile=store.profile()
        for url,default in FEEDS:
            try:
                raw,_=fetch(url)
                root=ET.fromstring(raw)
                entries=root.findall('.//item') or root.findall('.//{http://www.w3.org/2005/Atom}entry')
                for entry in entries[:20]:
                    title=(entry.findtext('title') or entry.findtext('{http://www.w3.org/2005/Atom}title') or '').strip()
                    link=entry.findtext('link')
                    if not link:
                        element=entry.find('{http://www.w3.org/2005/Atom}link')
                        link=element.get('href','') if element is not None else ''
                    if not title or not primary_url(link): continue
                    if re.search(r'latest .+news|new experts|ecosystem|return on investment|announced in|research bench|insights from|ai day|societal impact',title,re.I): continue
                    category=cls.classify(title,default)
                    if category not in profile.pillars: continue
                    # NASA/MIT general news is useful only when it actually concerns technology.
                    if not re.search(r'robot|\bAI\b|intelligen|engineer|technolog|machine|invent|software|model|battery|automat|comput|quantum|sensor|material',title,re.I): continue
                    published=entry.findtext('pubDate') or entry.findtext('{http://www.w3.org/2005/Atom}updated')
                    age=None
                    if published:
                        try:
                            dt=parsedate_to_datetime(published) if ',' in published else datetime.fromisoformat(published.replace('Z','+00:00'))
                            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                            age=max(0,(datetime.now(timezone.utc)-dt).days)
                        except ValueError: pass
                    # Transparent prioritization heuristics, never viral probabilities.
                    dimensions={'freshness':max(20,100-min(age,80)) if age is not None else 35,
                                'audience_fit':90,'novelty':65,'visual_potential':80 if category in ('robotics','engineering') else 65,
                                'originality_potential':85,'explanation_potential':85,'ranking_potential':65,
                                'competition':50,'evergreen_value':70}
                    results.append(store.put_opportunity({'topic':title[:180],'category':category,
                        'opportunity_type':'emerging' if age is not None and age<14 else 'evergreen',
                        'source_url':link,'published_at':published,'dimensions':dimensions,
                        'reason':'Primary-source technology story; requires factual review before production.',
                        'rights_status':'research_only','score_basis':'Deterministic editorial heuristics; not a prediction of views.'}))
            except (requests.RequestException,ValueError,ET.ParseError,OSError) as exc:
                errors.append({'source':url,'error':type(exc).__name__})
        from app.studio.director import ContentDirector
        return {'opportunities':ContentDirector.rank(results)[:limit], 'warnings':errors}
