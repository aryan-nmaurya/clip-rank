import asyncio
import shutil
from pathlib import Path
import pytest
from PIL import Image, ImageDraw
from app.sources.ranking_policy import RankingSourcePolicy
from app.sources.discovery import SourceDiscovery
from app.sources.web_search import ResultParser, WebVideoSearch, video_page
from app.sources.ingestion import SourceIngestion
from app.media.source_screening import SourceScreening
from app.media.captions import font
from app.core.runtime import run_process
from app.core.database import create_project, create_job, get_project
from app.pipelines.ranking.ranking_pipeline import RankingPipeline
from app.ai.router import AIRouter


@pytest.mark.parametrize('title', ['TOP FIVE cat reactions', 'My ranked cat list', 'Funny cats compilation', 'Tier list', 'Top 3 saves'])
def test_existing_rankings_rejected_by_metadata(title):
    assert RankingSourcePolicy.metadata_reason({'title': title})


def test_ordinary_clip_and_scoreboard_are_allowed():
    assert RankingSourcePolicy.metadata_reason({'title': 'Cat jumps onto a shelf'}) is None
    assert RankingSourcePolicy.overlay_reason([[{'text': 'HOME 2 AWAY 1', 'x': .1, 'y': .1},
                                               {'text': '1:25', 'x': .4, 'y': .1}]]) is None
    assert RankingSourcePolicy.overlay_reason([[{'text': '#1', 'x': .2}], [{'text': '#2', 'x': .2}]])


def test_visual_numbered_list_rejected_even_without_ranking_title(tmp_path):
    image = Image.new('RGB', (720, 1280), '#222222')
    draw = ImageDraw.Draw(image)
    draw.text((80,40), 'Funny cats', font=font(48), fill='white')
    for rank in range(1,6):
        draw.text((25,150+rank*100), f'{rank}. Cat clip {rank}', font=font(38), fill='yellow')
    path = tmp_path / 'innocent-title.png'
    image.save(path)
    video = tmp_path / 'innocent-title.mp4'
    run_process(['ffmpeg','-v','error','-y','-loop','1','-i',str(path),'-t','1.5',
                 '-c:v','libx264','-pix_fmt','yuv420p',str(video)])
    result = SourceScreening.inspect({'file_path': str(video), 'duration': 1.5}, tmp_path / 'screen')
    if not result['ocr_available']:
        pytest.skip('OCR requires macOS Vision access or installed Tesseract')
    assert result['rejected']
    assert 'numbered ranking list' in result['reason']


def test_supplied_uploads_are_metadata_screened(footage, tmp_path):
    rejected = []
    sources = SourceDiscovery.discover_candidate_videos('Cats',3,tmp_path,
        source_files=[str(p) for p in [*footage, footage[0]]],
        source_titles=['Jump','Reaction','Chase','Top five cats'], rejections=rejected)
    assert len(sources) == 3
    assert len(rejected) == 1
    assert 'ranking' in rejected[0]['reason']


def test_search_interleaves_sites_and_filters_ranking_pages(monkeypatch, footage, tmp_path):
    calls = []
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', lambda q: [
        {'id':'raw','url':'https://youtube.com/shorts/raw','title':'Cat jump'},
        {'id':'bad','url':'https://youtube.com/shorts/bad','title':'Top 5 cats'}])
    monkeypatch.setattr(WebVideoSearch, 'search', lambda q,p: [{'url':f'https://{p}.com/raw','title':'Cat reaction'}])
    def download(url, destination, reject_rankings=False):
        assert reject_rankings
        calls.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(footage[len(calls)-1], destination)
        return destination, {'source_id':url,'url':url,'title':'Cat moment'}
    monkeypatch.setattr(SourceIngestion, 'download_video', download)
    rejected = []
    SourceDiscovery.discover_candidate_videos('Cats',3,tmp_path, rejections=rejected)
    assert calls == ['https://youtube.com/shorts/raw','https://reddit.com/raw','https://dailymotion.com/raw']
    assert all('bad' not in url for url in calls)
    assert rejected


def test_web_search_only_accepts_actual_video_pages():
    parser = ResultParser()
    parser.feed('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.reddit.com%2Fr%2Fcats%2Fcomments%2Fabc%2Fcat_jump%2F">Cat <b>jump</b></a>')
    assert parser.results[0]['title'] == 'Cat jump'
    assert video_page(parser.results[0]['url'], 'reddit')
    assert not video_page('https://www.reddit.com/r/cats/', 'reddit')
    assert not video_page('https://reddit.com.evil.invalid/r/cats/comments/abc/', 'reddit')
    assert video_page('https://www.dailymotion.com/video/xabc', 'dailymotion')


def test_download_metadata_rejections_are_recorded(monkeypatch, footage, tmp_path):
    from app.sources.errors import RankedSourceRejected
    def download(url,destination,**kwargs):
        raise RankedSourceRejected('Existing countdown metadata','Top five cats')
    monkeypatch.setattr(SourceIngestion,'download_video',download)
    rejected=[]
    sources=SourceDiscovery.discover_candidate_videos('Cats',3,tmp_path,
        source_files=[str(p) for p in footage],source_urls=['https://example.org/clip.mp4'],rejections=rejected)
    assert len(sources)==3
    assert rejected[0]['title']=='Top five cats'


def test_unverified_footage_cannot_render(isolated_app, footage, monkeypatch):
    async def no_ai(*args, **kwargs): return None, 'fallback'
    monkeypatch.setattr(AIRouter, 'get_active_provider', no_ai)
    monkeypatch.setattr(SourceScreening, 'read_text', lambda images: None)
    create_project('unverified','ranking','Cats')
    create_job('unverified_job','unverified')
    with pytest.raises(ValueError, match='Ranking needs a connected vision model'):
        asyncio.run(RankingPipeline.run('unverified_job','unverified','Cats',3,
            {'source_files':[str(p) for p in footage]}, lambda *a: None))
    assert get_project('unverified')['status'] == 'FAILED'
    assert not list(isolated_app['ranking'].glob('*.mp4'))


def test_existing_ranking_does_not_fill_a_missing_slot(isolated_app, footage, monkeypatch, ranking_vision):
    def inspect(source,folder):
        rejected = source['id'] == 'source_0'
        return {'rejected':rejected,'reason':'Existing numbered ranking list','ocr_available':True,
                'sheet':Path('unused.jpg'),'sample_count':4,'method':'Test OCR'}
    monkeypatch.setattr(SourceScreening,'inspect',inspect)
    create_project('reject_test','ranking','Cats')
    create_job('reject_job','reject_test')
    with pytest.raises(ValueError, match='Existing rankings are never used as replacements'):
        asyncio.run(RankingPipeline.run('reject_job','reject_test','Cats',3,
            {'source_files':[str(p) for p in footage], 'segment_duration':3}, lambda *a: None))
    project = get_project('reject_test')
    assert project['status'] == 'FAILED'
    assert len(project['result_data']['rejected_sources']) == 1
    assert not list(isolated_app['ranking'].glob('*.mp4'))


def test_vision_screen_requires_boolean_decisions(monkeypatch):
    class Provider:
        async def analyze_images(self,*args):
            return '{"suitable_raw":"true","already_ranked":"false","compilation":false}'
    async def provider(*args,**kwargs): return Provider(),'test'
    monkeypatch.setattr(AIRouter,'get_active_provider',provider)
    assert asyncio.run(AIRouter.screen_ranking_source(Path('unused.jpg'),{},{})) is None


def test_source_search_prioritizes_individual_events_and_excludes_rankings(monkeypatch):
    from urllib.parse import urlparse,parse_qs
    from unittest.mock import Mock
    captured=[]
    def get(url,**kwargs):
        captured.append(url)
        return Mock(text='<script>var ytInitialData = {};</script>')
    monkeypatch.setattr('app.sources.discovery.requests.get',get)
    assert SourceDiscovery.generate_search_queries('Parkour fails')[0]=='Parkour fail'
    SourceDiscovery.search_public_shorts('Parkour fail')
    query=parse_qs(urlparse(captured[0]).query)['search_query'][0]
    assert all(term in query for term in ('-ranking','-compilation','-countdown'))
