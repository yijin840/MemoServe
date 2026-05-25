"""
web_crawler.py — 搜索导入爬虫
=============================
输入关键词 → DuckDuckGo 搜索 → 爬取前 N 条结果正文 → 汇成 Markdown 文件
"""

import re
import httpx
from datetime import date
from bs4 import BeautifulSoup
from pathlib import Path
from doc_loader import upload_doc

SEARCH_URL = "https://lite.duckduckgo.com/lite/"
MAX_RESULTS = 5
TIMEOUT = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def _search(keyword: str) -> list[str]:
    """DuckDuckGo Lite 搜索，返回结果 URL 列表"""
    urls = []
    try:
        resp = httpx.post(SEARCH_URL, data={"q": keyword}, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select("a.result-link"):
            href = link.get("href", "")
            if href.startswith("http") and "duckduckgo" not in href:
                urls.append(href)
                if len(urls) >= MAX_RESULTS:
                    break
    except Exception as e:
        print(f"[Crawler] 搜索失败: {e}")
    return urls


def _fetch_text(url: str) -> str:
    """爬取单个页面的正文文本"""
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 40]
        return "\n\n".join(lines[:80])  # 限制每页最多 80 段
    except Exception as e:
        print(f"[Crawler] 爬取 {url[:60]} 失败: {e}")
        return ""


def crawl_and_import(keyword: str) -> dict:
    """搜索 + 爬取 + 导入"""
    urls = _search(keyword)
    if not urls:
        return {"error": f"未找到与「{keyword}」相关的搜索结果"}

    today = date.today().isoformat()
    safe_kw = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', keyword)[:30]
    name = f"{safe_kw}_{today}"

    parts = [f"# {keyword} — 搜索导入\n\n> 日期：{today}  |  来源：自动抓取\n"]
    for i, url in enumerate(urls, 1):
        print(f"[Crawler] ({i}/{len(urls)}) {url[:80]}...")
        content = _fetch_text(url)
        if content:
            parts.append(f"## 来源 {i}\n\n{url}\n\n{content}\n")

    if len(parts) <= 1:
        return {"error": "所有搜索结果均无法提取有效内容"}

    full_md = "\n---\n\n".join(parts)
    return upload_doc(name, full_md)
