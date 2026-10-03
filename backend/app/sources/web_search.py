"""Find public individual video pages across sites through public search results."""
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs
import xml.etree.ElementTree as ET
import requests

PLATFORMS = {
    "youtube": "youtube.com",
    "reddit": "reddit.com",
    "dailymotion": "dailymotion.com",
    "tiktok": "tiktok.com",
    "instagram": "instagram.com",
    "vimeo": "vimeo.com",
}


def video_page(url, platform):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    domain = PLATFORMS[platform]
    if parsed.scheme not in {"http", "https"} or not (host == domain or host.endswith("." + domain)):
        return False
    path = parsed.path
    return {
        "youtube": path.startswith(("/watch", "/shorts/")),
        "reddit": "/comments/" in path,
        "dailymotion": path.startswith("/video/"),
        "tiktok": "/video/" in path,
        "instagram": path.startswith(("/reel/", "/reels/", "/p/")),
        "vimeo": path.strip("/").isdigit(),
    }[platform]


class ResultParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results, self.current = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "result__a" in attrs.get("class", "").split():
            url = attrs.get("href", "")
            redirect = parse_qs(urlparse(url).query).get("uddg")
            self.current = {"url": redirect[0] if redirect else url, "title": ""}

    def handle_data(self, text):
        if self.current is not None:
            self.current["title"] += text

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.results.append(self.current)
            self.current = None


class WebVideoSearch:
    @staticmethod
    def search_dailymotion(query):
        response = requests.get("https://api.dailymotion.com/videos", params={
            "search": query, "limit": 30, "fields": "id,title,url,duration,views_total,created_time,owner.screenname",
            "sort": "relevance"}, timeout=20)
        response.raise_for_status()
        return [{"url": item["url"], "title": item["title"], "id": item.get("id"), "duration": item.get("duration"),
                 "view_count": item.get("views_total"), "timestamp": item.get("created_time"), "creator": item.get("owner.screenname")}
                for item in response.json().get("list", []) if 1 <= (item.get("duration") or 0) <= 300]

    @staticmethod
    def search(query, platform):
        if platform == "dailymotion":
            return WebVideoSearch.search_dailymotion(query)
        terms = f"{query} site:{PLATFORMS[platform]} -ranking -compilation -countdown"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ClipRank/1.0)"}
        errors = []
        try:
            response = requests.get("https://html.duckduckgo.com/html/", params={"q": terms}, headers=headers, timeout=20)
            response.raise_for_status()
            parser = ResultParser()
            parser.feed(response.text)
            entries = [entry for entry in parser.results if video_page(entry["url"], platform)]
            if entries:
                return entries
        except (requests.RequestException, ValueError) as exc:
            errors.append(type(exc).__name__)
        try:
            response = requests.get("https://www.bing.com/search", params={"q": terms, "format": "rss"}, headers=headers, timeout=20)
            response.raise_for_status()
            tree = ET.fromstring(response.content)
            entries = [{"url": item.findtext("link", ""), "title": item.findtext("title", "")} for item in tree.findall("./channel/item")]
            entries = [entry for entry in entries if video_page(entry["url"], platform)]
            if entries:
                return entries
            errors.append("no indexed video pages")
        except (requests.RequestException, ET.ParseError) as exc:
            errors.append(type(exc).__name__)
        raise ValueError(f"{platform.title()} web search is unavailable ({', '.join(errors)}). Supply public source links instead.")
