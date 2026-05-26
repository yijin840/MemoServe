"""
doc_loader.py
=============
从本地 Markdown 文件 或 GitHub Raw URL 加载 API 文档，
切分成语义块（chunk），供 RAG 检索使用。

核心逻辑：
  - 按 Markdown 标题（##/###）切块，保留层级上下文
  - 每块记录：id / title_path / content / source / url
  - 支持本地文件 + HTTP(S) URL，自动缓存到 docs_cache/
  - 支持 max_chars 参数，超长 chunk 自动按段落二次切分
"""

import os
import re
import json
import hashlib
import urllib.request
from pathlib import Path
from typing import Optional, List

CACHE_DIR = Path(os.getenv("DOCS_CACHE_DIR", "./docs_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─── 默认文档源（可在 .env 中覆盖） ─────────────────────
DEFAULT_DOCS = [
    {
        "name": "PayCrypto API 完整文档",
        "url": "https://raw.githubusercontent.com/pay-crypto001/api-docs/main/paycrypto-api-cn.md",
        "local": None,
    },
    {
        "name": "Native MM API",
        "url": "https://raw.githubusercontent.com/pay-crypto001/api-docs/main/native-mm-api-en.md",
        "local": None,
    },
]


# ──────────────────────────────────────────────────────────────────
# 1. 加载原始 Markdown
# ──────────────────────────────────────────────────────────────────

def _url_to_cache_path(url: str) -> Path:
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{h}.md"


def fetch_markdown(source: dict, force_refresh: bool = False) -> str:
    """
    优先读本地文件；没有则从 URL 下载，缓存到本地。
    source: {"name": ..., "url": ..., "local": ...}
    """
    # 本地文件优先
    if source.get("local") and Path(source["local"]).exists():
        return Path(source["local"]).read_text(encoding="utf-8")

    url = source.get("url", "")
    if not url:
        return ""

    cache_path = _url_to_cache_path(url)
    if cache_path.exists() and not force_refresh:
        return cache_path.read_text(encoding="utf-8")

    # 下载
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DocQABot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")
        cache_path.write_text(text, encoding="utf-8")
        print(f"[DocLoader] 下载完成: {source['name']} → {cache_path.name}")
        return text
    except Exception as e:
        print(f"[DocLoader] 下载失败 {url}: {e}")
        return ""


# ──────────────────────────────────────────────────────────────────
# 2. 切分 Markdown → 语义块
# ──────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """去除多余空行，段落间保留一个 \n\n"""
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def split_markdown(text: str, source_name: str, source_url: str = "", max_chars: int = 1200) -> List[dict]:
    """
    按 Markdown 标题切块。
    每块不超过 max_chars 字符，超过时按段落（\n\n）二次切分。
    """
    lines = text.splitlines()
    chunks = []
    current_path = ["", "", ""]  # [h1, h2, h3]
    current_lines: List[str] = []
    chunk_idx = 0

    def flush(path: List[str], body_lines: List[str]):
        nonlocal chunk_idx
        body = _clean("\n".join(body_lines))
        if len(body.strip()) < 30:
            return

        # ★ 超长 chunk 按段落切分
        if len(body) > max_chars:
            paras = body.split('\n\n')
            current_seg = []
            current_len = 0
            for p in paras:
                p = p.strip()
                if not p:
                    continue
                if current_len + len(p) > max_chars and current_seg:
                    seg_body = _clean("\n\n".join(current_seg))
                    if len(seg_body.strip()) > 30:
                        title_path = " > ".join(pp for pp in path if pp)
                        if not title_path:
                            title_path = source_name
                        cid = f"{source_name.replace(' ', '_')}_{chunk_idx:04d}"
                        cat = title_path.split(" > ")[0]
                        chunks.append({
                            "id": cid,
                            "title_path": title_path,
                            "category": cat,
                            "content": seg_body,
                            "source": source_name,
                            "url": source_url,
                        })
                        chunk_idx += 1
                    current_seg = [p]
                    current_len = len(p)
                else:
                    current_seg.append(p)
                    current_len += len(p)
            if current_seg:
                seg_body = _clean("\n\n".join(current_seg))
                if len(seg_body.strip()) > 30:
                    title_path = " > ".join(pp for pp in path if pp)
                    if not title_path:
                        title_path = source_name
                    cid = f"{source_name.replace(' ', '_')}_{chunk_idx:04d}"
                    cat = title_path.split(" > ")[0]
                    chunks.append({
                        "id": cid,
                        "title_path": title_path,
                        "category": cat,
                        "content": seg_body,
                        "source": source_name,
                        "url": source_url,
                    })
                    chunk_idx += 1
            return  # 超长 chunk 处理完直接返回

        # 正常大小
        title_path = " > ".join(p for p in path if p)
        if not title_path:
            title_path = source_name
        cid = f"{source_name.replace(' ', '_')}_{chunk_idx:04d}"
        cat = title_path.split(" > ")[0]
        chunks.append({
            "id": cid,
            "title_path": title_path,
            "category": cat,
            "content": body,
            "source": source_name,
            "url": source_url,
        })
        chunk_idx += 1

    heading_re = re.compile(r'^(#{1,4})\s+(.*)')

    for line in lines:
        m = heading_re.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if level <= 3:
                # 保存上一块
                if current_lines:
                    flush(current_path[:], current_lines)
                    current_lines = []
                # 更新层级路径
                if level == 1:
                    current_path = [title, "", ""]
                elif level == 2:
                    current_path = [current_path[0], title, ""]
                else:  # level == 3
                    current_path = [current_path[0], current_path[1], title]
            # h4+ 直接追加到当前块
            current_lines.append(line)
        else:
            current_lines.append(line)

    if current_lines:
        flush(current_path[:], current_lines)

    # 如果切块太少（<5），说明文档标题层级浅，按段落二次切分大块
    if len(chunks) < 5:
        new_chunks = []
        for c in chunks:
            paras = re.split(r'\n\n+', c["content"])
            # 每 3 段合并成一小块
            for i in range(0, len(paras), 3):
                seg = "\n\n".join(p for p in paras[i:i+3] if len(p.strip()) > 20)
                if len(seg.strip()) > 40:
                    cid = f"{source_name.replace(' ', '_')}_{chunk_idx:04d}"
                    chunk_idx += 1
                    new_chunks.append({
                        "id": cid,
                        "title_path": c["title_path"],
                        "category": c["title_path"].split(" > ")[0],
                        "content": seg,
                        "source": source_name,
                        "url": source_url,
                    })
        if new_chunks:
            return new_chunks

    return chunks


# ──────────────────────────────────────────────────────────────────
# 3. 全量加载（启动时调用）
# ──────────────────────────────────────────────────────────────────

_CHUNKS_CACHE: list[dict] = []
_CHUNKS_JSON  = CACHE_DIR / "chunks.json"


def load_all_docs(force_refresh: bool = False) -> List[dict]:
    """
    加载所有文档源，返回全部切块列表。
    结果缓存到 docs_cache/chunks.json，下次快速读取。
    """
    global _CHUNKS_CACHE

    if _CHUNKS_CACHE and not force_refresh:
        return _CHUNKS_CACHE

    if _CHUNKS_JSON.exists() and not force_refresh:
        try:
            _CHUNKS_CACHE = json.loads(_CHUNKS_JSON.read_text(encoding="utf-8"))
            print(f"[DocLoader] 从缓存加载 {len(_CHUNKS_CACHE)} 个文档块")
            return _CHUNKS_CACHE
        except Exception:
            pass

    # 读取额外文档源（环境变量 EXTRA_DOCS_JSON 指定 JSON 数组）
    sources = list(DEFAULT_DOCS)
    extra = os.getenv("EXTRA_DOCS_JSON", "")
    if extra:
        try:
            sources += json.loads(extra)
        except Exception:
            pass

    all_chunks = []
    for src in sources:
        md = fetch_markdown(src, force_refresh=force_refresh)
        if md:
            chunks = split_markdown(md, src["name"], src.get("url", ""))
            all_chunks.extend(chunks)
            print(f"[DocLoader] {src['name']}: {len(chunks)} 块")

    _CHUNKS_CACHE = all_chunks
    _CHUNKS_JSON.write_text(
        json.dumps(all_chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DocLoader] 总计 {len(all_chunks)} 个文档块已缓存")
    return all_chunks


def get_chunks() -> List[dict]:
    """对外接口"""
    return _CHUNKS_CACHE or load_all_docs()


def refresh_docs() -> int:
    """强制刷新文档（用于 API 触发）"""
    global _CHUNKS_CACHE
    _CHUNKS_CACHE = []
    chunks = load_all_docs(force_refresh=True)
    return len(chunks)


# ──────────────────────────────────────────────────────────────────
# 4. 动态导入文档
# ──────────────────────────────────────────────────────────────────

SOURCES_FILE = CACHE_DIR / "sources.json"


def _load_sources() -> list[dict]:
    """加载自定义文档源列表"""
    if SOURCES_FILE.exists():
        try:
            return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_sources(sources: list[dict]):
    SOURCES_FILE.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")


def get_all_sources() -> list[dict]:
    """返回所有文档源（默认 + 自定义）"""
    custom = _load_sources()
    all_sources = []
    # 默认源去重
    seen_names = set()
    for s in DEFAULT_DOCS + custom:
        if s["name"] not in seen_names:
            all_sources.append(s)
            seen_names.add(s["name"])
    return all_sources


def _normalize_url(url: str) -> str:
    """自动将 GitHub blob 链接转为 raw 链接"""
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/blob/(.+)', url)
    if m:
        return f'https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}'
    return url


def import_doc(name: str, url: str) -> dict:
    """导入新文档：下载 + 切块 + 持久化"""
    global _CHUNKS_CACHE
    url = _normalize_url(url)
    source = {"name": name, "url": url, "local": None}

    md = fetch_markdown(source)
    if not md:
        return {"error": f"下载失败: {url}"}

    new_chunks = split_markdown(md, name, url)
    if not new_chunks:
        return {"error": "文档内容过短，无法切块"}

    # 持久化到自定义源列表
    sources = _load_sources()
    sources.append({"name": name, "url": url, "local": None})
    _save_sources(sources)

    # 更新缓存
    _CHUNKS_CACHE = _CHUNKS_CACHE or load_all_docs()
    _CHUNKS_CACHE.extend(new_chunks)
    _CHUNKS_JSON.write_text(json.dumps(_CHUNKS_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"name": name, "chunks": len(new_chunks), "total_chunks": len(_CHUNKS_CACHE)}


def remove_doc(name: str) -> dict:
    """删除自定义文档源及其所有切块"""
    global _CHUNKS_CACHE
    sources = _load_sources()
    new_sources = [s for s in sources if s["name"] != name]
    if len(new_sources) == len(sources):
        return {"error": "文档源不存在或不可删除（默认文档）"}
    _save_sources(new_sources)
    _CHUNKS_CACHE = [c for c in (_CHUNKS_CACHE or get_chunks()) if c["source"] != name]
    _CHUNKS_JSON.write_text(json.dumps(_CHUNKS_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"name": name, "remaining_chunks": len(_CHUNKS_CACHE)}


def reset_docs() -> int:
    """清空所有自定义文档，恢复为默认文档源"""
    global _CHUNKS_CACHE
    _save_sources([])
    if SOURCES_FILE.exists():
        SOURCES_FILE.unlink()
    _CHUNKS_CACHE = []
    if _CHUNKS_JSON.exists():
        _CHUNKS_JSON.unlink()
    chunks = load_all_docs(force_refresh=True)
    return len(chunks)


def upload_doc(name: str, content: str) -> dict:
    """上传本地文件内容，切块入库"""
    global _CHUNKS_CACHE

    new_chunks = split_markdown(content, name, "")
    if not new_chunks:
        return {"error": "文件内容过短，无法切块"}

    safe_name = re.sub(r'[^\w\-]', '_', name)
    cache_path = CACHE_DIR / f"{safe_name}.md"
    cache_path.write_text(content, encoding="utf-8")

    sources = _load_sources()
    sources.append({"name": name, "url": "", "local": str(cache_path)})
    _save_sources(sources)

    _CHUNKS_CACHE = _CHUNKS_CACHE or get_chunks()
    _CHUNKS_CACHE.extend(new_chunks)
    _CHUNKS_JSON.write_text(json.dumps(_CHUNKS_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"name": name, "chunks": len(new_chunks), "total_chunks": len(_CHUNKS_CACHE)}
