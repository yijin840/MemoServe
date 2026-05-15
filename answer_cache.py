from __future__ import annotations

"""
问答缓存模块
- 二级缓存：精确匹配（哈希）+ 语义匹配（向量余弦相似度）
- 基于 ChromaDB 存储缓存向量，复用现有 embedding 基础设施
- 支持 TTL 过期、按 user_id 隔离
- 缓存命中时跳过 RAG 检索和 LLM 调用，大幅降低延迟和成本
"""
import hashlib
import json
import logging
import time
from typing import Optional

from config import (
    DASHSCOPE_API_KEY,
    QWEN_BASE_URL,
    QWEN_EMBEDDING_MODEL,
    CHROMA_PERSIST_PATH,
)
from openai import OpenAI
import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)

# ====================== 缓存配置 ======================

CACHE_COLLECTION = "answer_cache"

# 缓存过期时间（秒），默认 1 小时
CACHE_TTL = int(__import__("os").getenv("CACHE_TTL", "3600"))

# 语义匹配阈值（余弦相似度），0.0~1.0，越高越严格
# 0.92 = 非常相似才命中，避免误缓存
CACHE_SEMANTIC_THRESHOLD = float(__import__("os").getenv("CACHE_SEMANTIC_THRESHOLD", "0.92"))

# 缓存命中统计
_cache_stats = {
    "hits_exact": 0,
    "hits_semantic": 0,
    "misses": 0,
    "total_queries": 0,
}


class AnswerCache:
    """
    问答缓存
    - 精确匹配：对用户输入做归一化 + MD5 哈希，O(1) 查找
    - 语义匹配：embedding 向量余弦相似度，支持相似问题复用缓存
    """

    def __init__(self):
        persist_path = CHROMA_PERSIST_PATH
        self._chroma = chromadb.PersistentClient(
            path=persist_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._col = self._chroma.get_or_create_collection(
            name=CACHE_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        # 精确匹配索引：{normalized_hash: cache_record}
        self._exact_index: dict[str, dict] = {}
        # embedding 客户端
        self._embed_client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=QWEN_BASE_URL)
        self._embed_model = QWEN_EMBEDDING_MODEL

        # 启动时从 ChromaDB 加载缓存到内存
        self._load_from_chroma()
        logger.info(
            f"[缓存] 已初始化，TTL={CACHE_TTL}s，语义阈值={CACHE_SEMANTIC_THRESHOLD}，"
            f"加载 {len(self._exact_index)} 条缓存"
        )

    @staticmethod
    def _normalize(text: str) -> str:
        """归一化文本：去首尾空格、转小写、合并连续空格"""
        return " ".join(text.strip().lower().split())

    @staticmethod
    def _hash(text: str) -> str:
        """MD5 哈希"""
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def _load_from_chroma(self):
        """启动时从 ChromaDB 加载缓存"""
        try:
            now = time.time()
            all_data = self._col.get(include=["documents", "metadatas"])
            if not all_data or not all_data.get("ids"):
                return
            expired_ids = []
            for doc, mid, meta in zip(
                all_data["documents"],
                all_data["ids"],
                all_data["metadatas"],
            ):
                if not meta:
                    continue
                created_at = meta.get("created_at", 0)
                if now - created_at > CACHE_TTL:
                    expired_ids.append(mid)
                    continue
                normalized_q = meta.get("normalized_query", "")
                if normalized_q:
                    # rag_sources 在 ChromaDB 中以 JSON 字符串存储
                    raw_sources = meta.get("rag_sources", "[]")
                    rag_sources = json.loads(raw_sources) if isinstance(raw_sources, str) else raw_sources
                    self._exact_index[normalized_q] = {
                        "id": mid,
                        "question": doc,
                        "answer": meta.get("answer", ""),
                        "rag_sources": rag_sources,
                        "user_id": meta.get("user_id", ""),
                        "created_at": created_at,
                    }
            # 清理过期记录
            if expired_ids:
                self._col.delete(ids=expired_ids)
                logger.info(f"[缓存] 启动时清理 {len(expired_ids)} 条过期记录")
        except Exception as e:
            logger.warning(f"[缓存] 加载 ChromaDB 缓存失败：{e}")

    def _embed(self, text: str) -> list[float]:
        """获取文本 embedding"""
        try:
            resp = self._embed_client.embeddings.create(
                model=self._embed_model, input=text
            )
            return resp.data[0].embedding
        except Exception as e:
            logger.warning(f"[缓存] Embedding 失败：{e}")
            return []

    def get(self, query: str, user_id: str) -> Optional[dict]:
        """
        查询缓存
        :return: 缓存命中的记录 {"answer", "rag_sources", "cache_type"} 或 None
        """
        _cache_stats["total_queries"] += 1
        normalized = self._normalize(query)
        query_hash = self._hash(normalized)
        now = time.time()

        # 1. 精确匹配（校验 user_id 隔离）
        record = self._exact_index.get(query_hash)
        if record and (now - record["created_at"]) < CACHE_TTL:
            # 安全检查：缓存记录必须属于当前用户
            if record.get("user_id") and record["user_id"] != user_id:
                logger.debug(f"[缓存] 精确匹配但 user_id 不一致 | cached_uid='{record.get('user_id')}' | query_uid='{user_id}'")
            else:
                _cache_stats["hits_exact"] += 1
                logger.info(f"[缓存] ✅ 精确命中 | query='{query[:40]}'")
                return {
                    "answer": record["answer"],
                    "rag_sources": record.get("rag_sources", []),
                    "cache_type": "exact",
                    "memories_used": [],
                    "memory_archived": False,
                    "model": "cached",
                }

        # 2. 语义匹配
        if self._col.count() > 0:
            query_emb = self._embed(query)
            if query_emb:
                results = self._col.query(
                    query_embeddings=[query_emb],
                    n_results=3,
                    include=["documents", "distances", "metadatas"],
                )
                for doc, dist, meta in zip(
                    results["documents"][0],
                    results["distances"][0],
                    results["metadatas"][0],
                ):
                    if not meta:
                        continue
                    # 检查过期
                    created_at = meta.get("created_at", 0)
                    if now - created_at > CACHE_TTL:
                        continue
                    # 检查 user_id（支持按用户隔离）
                    cached_uid = meta.get("user_id", "")
                    if cached_uid and cached_uid != user_id:
                        continue
                    # 检查相似度阈值
                    similarity = 1.0 - dist
                    if similarity >= CACHE_SEMANTIC_THRESHOLD:
                        _cache_stats["hits_semantic"] += 1
                        # rag_sources 在 ChromaDB 中以 JSON 字符串存储
                        raw_sources = meta.get("rag_sources", "[]")
                        rag_sources = json.loads(raw_sources) if isinstance(raw_sources, str) else raw_sources
                        logger.info(
                            f"[缓存] ✅ 语义命中（相似度={similarity:.2%}）| "
                            f"query='{query[:40]}' → cached='{doc[:40]}'"
                        )
                        return {
                            "answer": meta.get("answer", ""),
                            "rag_sources": rag_sources,
                            "cache_type": "semantic",
                            "memories_used": [],
                            "memory_archived": False,
                            "model": "cached",
                        }

        # 未命中
        _cache_stats["misses"] += 1
        return None

    def set(self, query: str, answer: str, rag_sources: list[dict], user_id: str):
        """
        写入缓存
        """
        normalized = self._normalize(query)
        query_hash = self._hash(normalized)
        now = time.time()

        # 更新内存索引
        cache_id = f"cache_{query_hash}"
        self._exact_index[query_hash] = {
            "id": cache_id,
            "question": query,
            "answer": answer,
            "rag_sources": rag_sources,
            "user_id": user_id,
            "created_at": now,
        }

        # 写入 ChromaDB（向量存储，供语义匹配使用）
        query_emb = self._embed(query)
        if query_emb:
            metadata = {
                "normalized_query": normalized,
                "answer": answer,
                "rag_sources": json.dumps(rag_sources, ensure_ascii=False),
                "user_id": user_id,
                "created_at": now,
            }
            try:
                self._col.upsert(
                    ids=[cache_id],
                    documents=[query],
                    embeddings=[query_emb],
                    metadatas=[metadata],
                )
            except Exception as e:
                logger.warning(f"[缓存] 写入 ChromaDB 失败：{e}")

        logger.info(f"[缓存] 📝 已缓存 | query='{query[:40]}'")

    def invalidate(self, user_id: Optional[str] = None):
        """
        清除缓存
        :param user_id: 指定用户，None 则清除全部
        """
        if user_id:
            # 清除特定用户的缓存
            expired_ids = []
            for h, rec in list(self._exact_index.items()):
                if rec.get("user_id") == user_id:
                    expired_ids.append(rec["id"])
                    del self._exact_index[h]
            if expired_ids:
                self._col.delete(ids=expired_ids)
            logger.info(f"[缓存] 已清除用户 {user_id} 的 {len(expired_ids)} 条缓存")
        else:
            # 清除全部
            try:
                self._chroma.delete_collection(CACHE_COLLECTION)
                self._col = self._chroma.get_or_create_collection(
                    name=CACHE_COLLECTION,
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception:
                pass
            self._exact_index.clear()
            # 重置统计
            global _cache_stats
            _cache_stats = {
                "hits_exact": 0,
                "hits_semantic": 0,
                "misses": 0,
                "total_queries": 0,
            }
            logger.info("[缓存] 已清除全部缓存")

    def cleanup_expired(self):
        """清理过期缓存"""
        now = time.time()
        expired_ids = []
        for h, rec in list(self._exact_index.items()):
            if now - rec["created_at"] > CACHE_TTL:
                expired_ids.append(rec["id"])
                del self._exact_index[h]
        if expired_ids:
            self._col.delete(ids=expired_ids)
            logger.info(f"[缓存] 清理 {len(expired_ids)} 条过期缓存")

    @property
    def stats(self) -> dict:
        """缓存统计信息"""
        total = _cache_stats["total_queries"]
        hits = _cache_stats["hits_exact"] + _cache_stats["hits_semantic"]
        return {
            "total_queries": total,
            "hits_exact": _cache_stats["hits_exact"],
            "hits_semantic": _cache_stats["hits_semantic"],
            "misses": _cache_stats["misses"],
            "hit_rate": f"{(hits / total * 100):.1f}%" if total > 0 else "0.0%",
            "cached_entries": len(self._exact_index),
            "ttl_seconds": CACHE_TTL,
            "semantic_threshold": CACHE_SEMANTIC_THRESHOLD,
        }
