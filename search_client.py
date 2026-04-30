"""Web search client with multiple backends: Tavily, SerpAPI, DuckDuckGo fallback."""

import re
from html import unescape
from urllib.parse import unquote, urlparse, parse_qs

import aiohttp
from config import settings


class SearchClient:
    """Web search with multiple fallback backends."""
    
    def __init__(self):
        self.tavily_key = settings.TAVILY_API_KEY
        self.serpapi_key = settings.SERPAPI_KEY
    
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Search the web using available backends in priority order."""
        # Try Tavily first
        if self.tavily_key:
            results = await self._search_tavily(query, max_results)
            if results:
                return results
        
        # Try SerpAPI
        if self.serpapi_key:
            results = await self._search_serpapi(query, max_results)
            if results:
                return results
        
        # Fallback to DuckDuckGo (no API key needed)
        return await self._search_duckduckgo(query, max_results)
    
    async def _search_tavily(self, query: str, max_results: int) -> list[dict] | None:
        """Search using Tavily API."""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "api_key": self.tavily_key,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": max_results,
                    "include_answer": True,
                }
                async with session.post(
                    "https://api.tavily.com/search",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    results = []
                    for r in data.get("results", []):
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("content", ""),
                        })
                    return results
        except Exception:
            return None
    
    async def _search_serpapi(self, query: str, max_results: int) -> list[dict] | None:
        """Search using SerpAPI (Google results)."""
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "q": query,
                    "api_key": self.serpapi_key,
                    "engine": "google",
                    "num": max_results,
                }
                async with session.get(
                    "https://serpapi.com/search",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    results = []
                    for r in data.get("organic_results", []):
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("link", ""),
                            "snippet": r.get("snippet", ""),
                        })
                    return results
        except Exception:
            return None
    
    async def _search_duckduckgo(self, query: str, max_results: int) -> list[dict]:
        """Search using DuckDuckGo HTML (no API key). Fallback only."""
        try:
            async with aiohttp.ClientSession() as session:
                data = {"q": query, "kl": "es-es"}
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                }
                async with session.post(
                    "https://html.duckduckgo.com/html/",
                    data=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()
            return self._parse_ddg_html(html, max_results)
        except Exception:
            return []

    @staticmethod
    def _parse_ddg_html(html: str, max_results: int) -> list[dict]:
        """Extract results from DuckDuckGo HTML response."""
        result_pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
            r'.*?<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
            re.DOTALL,
        )
        tag_re = re.compile(r"<[^>]+>")
        results: list[dict] = []
        for match in result_pattern.finditer(html):
            raw_url = unescape(match.group("url"))
            if raw_url.startswith("//duckduckgo.com/l/"):
                parsed = urlparse("https:" + raw_url)
                qs = parse_qs(parsed.query)
                if "uddg" in qs:
                    raw_url = unquote(qs["uddg"][0])
            title = unescape(tag_re.sub("", match.group("title"))).strip()
            snippet = unescape(tag_re.sub("", match.group("snippet"))).strip()
            results.append({"title": title, "url": raw_url, "snippet": snippet})
            if len(results) >= max_results:
                break
        return results
    
    def format_results(self, results: list[dict]) -> str:
        """Format search results for inclusion in Claude prompt."""
        if not results:
            return "No se encontraron resultados de busqueda web."
        lines = ["Resultados de busqueda web:"]
        for i, r in enumerate(results[:5], 1):
            lines.append(f"{i}. {r['title']}: {r['snippet'][:200]}... ({r['url']})")
        return "\n".join(lines)


# Singleton instance
search_client = SearchClient()
