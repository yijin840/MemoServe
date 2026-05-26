"""
rag_store.py  (v2 — Doc-QA 版)
================================
基于文档块的向量检索层。

策略：
  - 有 chromadb + sentence-transformers → 语义搜索
  - 没有 → TF-IDF 风格关键词打分（纯标准库，零依赖）
  - 两种模式对外 API 完全一致
"""

import os
import re
import math
from pathlib import Path
from collections import Counter

CHROMA_PATH = Path(os.getenv("CHROMA_PATH", "./chroma_db"))

# ── 向量检索（可选） ─────────────────────────────────────
CHROMA_OK  = False
_collection = None
_embedder  = None

def _init_chroma():
    global CHROMA_OK, _collection, _embedder
    try:
        import chromadb
        from sentence_transformers import SentenceTransformer
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        _collection = client.get_or_create_collection(
            name="doc_qa_chunks",
            metadata={"hnsw:space": "cosine"},
        )
        _embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        CHROMA_OK = True
        print("✅ ChromaDB + SentenceTransformer 向量检索就绪")
    except ImportError:
        print("⚠️  向量检索依赖未安装，使用 TF-IDF 关键词降级")
    except Exception as e:
        print(f"⚠️  ChromaDB 初始化失败: {e}")

_init_chroma()


# ── TF-IDF 关键词检索（降级）────────────────────────────

CHINESE_STOPWORDS = set("你我了的是吗呢好在有什么这不和就也说要去")

def _tokenize(text: str) -> list[str]:
    """简单 CJK + 英文 token 化，过滤中文停用词"""
    # 英文单词
    tokens = re.findall(r'[a-zA-Z0-9_\-\.]+', text.lower())
    # CJK 字符（过滤停用字，只留实词）
    tokens += [c for c in re.findall(r'[\u4e00-\u9fff]', text) if c not in CHINESE_STOPWORDS]
    return tokens


def _tfidf_score(query_tokens: list[str], doc_tokens: list[str],
                 doc_freq: dict, N: int) -> float:
    """TF-IDF 打分 — 用词频计数，避免长文档被长度惩罚"""
    tf = Counter(doc_tokens)
    score = 0.0
    for t in set(query_tokens):
        count = tf.get(t, 0)
        df = doc_freq.get(t, 0)
        idf = math.log((N + 1) / (df + 1)) + 1
        score += count * idf
    return score


def keyword_search(query: str, chunks: list[dict], top_k: int = 5,
                   threshold: float = 0.0) -> list[dict]:
    """
    关键词打分检索文档块。
    返回 [{"chunk": ..., "score": float}, ...]
    """
    if not chunks:
        return []

    # 预分词
    q_tokens = _tokenize(query)
    doc_token_lists = [_tokenize(c["title_path"] + " " + c["content"]) for c in chunks]

    # 计算 DF
    N = len(chunks)
    doc_freq: dict[str, int] = {}
    for tl in doc_token_lists:
        for t in set(tl):
            doc_freq[t] = doc_freq.get(t, 0) + 1

    scored = []
    for chunk, tokens in zip(chunks, doc_token_lists):
        s = _tfidf_score(q_tokens, tokens, doc_freq, N)
        if s > threshold:
            scored.append({"chunk": chunk, "score": round(s, 4)})

    scored.sort(key=lambda x: -x["score"])
    return scored[:top_k]


# ── 向量检索 ────────────────────────────────────────────

def sync_chunks_to_vector(chunks: list[dict]):
    """把所有文档块同步进 ChromaDB（启动时调用）"""
    if not CHROMA_OK or _collection is None:
        return
    batch_size = 64
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]
        ids   = [c["id"] for c in batch]
        texts = [c["title_path"] + "\n" + c["content"][:400] for c in batch]
        metas = [{"source": c["source"], "title_path": c["title_path"],
                  "url": c.get("url", ""), "content": c["content"][:800],
                  "category": c.get("category", "")} for c in batch]
        embeds = _embedder.encode(texts).tolist()
        _collection.upsert(ids=ids, embeddings=embeds, documents=texts, metadatas=metas)
    print(f"[RAG] 同步 {len(chunks)} 个文档块到向量库")


def semantic_search(query: str, top_k: int = 5, threshold: float = 0.3,
                     category: str = "") -> list[dict]:
    """
    语义检索，返回 [{"chunk": {...}, "score": float}, ...]
    支持 category 过滤（ChromaDB where 查询）
    """
    if not CHROMA_OK or _collection is None or _collection.count() == 0:
        return []
    try:
        q_embed = _embedder.encode(query).tolist()
        n = min(top_k * 2, max(1, _collection.count()))
        where_filter = {"category": category} if category else None
        res = _collection.query(
            query_embeddings=[q_embed],
            n_results=n,
            where=where_filter,
            include=["metadatas", "distances"],
        )
        hits = []
        for meta, dist in zip(res["metadatas"][0], res["distances"][0]):
            score = 1.0 - dist
            if score >= threshold:
                    hits.append({
                        "chunk": {
                            "id": "",
                            "title_path": meta["title_path"],
                            "content": meta["content"],
                            "source": meta["source"],
                            "url": meta.get("url", ""),
                            "category": meta.get("category", ""),
                        },
                        "score": round(score, 3),
                    })
        return hits[:top_k]
    except Exception as e:
        print(f"[RAG] 语义检索失败: {e}")
        return []


# ── 统一检索入口 ─────────────────────────────────────────

def search_docs(query: str, chunks: list[dict], top_k: int = 5,
                category: str = "") -> list[dict]:
    """
    优先语义检索，降级关键词。
    支持 category 过滤（ChromaDB where 查询 + TF-IDF 预过滤）
    返回 [{"chunk": {...}, "score": float, "method": "semantic"|"keyword"}, ...]
    """
    if CHROMA_OK:
        hits = semantic_search(query, top_k=top_k, threshold=0.3, category=category)
        if hits:
            return [dict(h, method="semantic") for h in hits]

    # TF-IDF 降级：先按 category 预过滤 chunks
    if category:
        filtered = [c for c in chunks if c.get("category", "") == category]
        if filtered:
            chunks = filtered
        # category 不匹配任何 chunk 时不降级到全量，返回空
        else:
            return []

    hits = keyword_search(query, chunks, top_k=top_k, threshold=0.05)
    return [dict(h, method="keyword") for h in hits]
