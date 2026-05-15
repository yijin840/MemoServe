from __future__ import annotations

"""
RAG 知识库模块
- 使用 ChromaDB 作为向量数据库
- 使用 Qwen text-embedding-v3 生成向量
- 支持文档导入（txt, pdf, docx, 纯文本）
- 支持语义检索
"""
import os
import time
import hashlib
import logging
from typing import Optional
from pathlib import Path

import chromadb
from chromadb.config import Settings
from openai import OpenAI

from config import (
    DASHSCOPE_API_KEY,
    QWEN_BASE_URL,
    QWEN_EMBEDDING_MODEL,
    CHROMA_PERSIST_PATH,
    CHROMA_COLLECTION_NAME,
    RAG_TOP_K,
    RAG_CHUNK_SIZE,
    RAG_CHUNK_OVERLAP,
    RAG_SCORE_THRESHOLD,
)

logger = logging.getLogger(__name__)


class QwenEmbedding:
    """通义千问 Embedding 客户端"""

    def __init__(self):
        self.client = OpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url=QWEN_BASE_URL,
        )
        self.model = QWEN_EMBEDDING_MODEL

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成文本向量（每批最多 10 条）"""
        all_embeddings = []
        batch_size = 10
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = self.client.embeddings.create(
                model=self.model,
                input=batch,
            )
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)
        return all_embeddings

    def embed_one(self, text: str) -> list[float]:
        """生成单条文本向量"""
        return self.embed([text])[0]


import re


class TextSplitter:
    """
    智能文本分块器
    - 优先按段落（\n\n）分块
    - 段落过长时按句子边界（。！？.!?）分块
    - 确保每个块内容完整（不切在句子中间）
    - 支持中英文混合文本
    """

    # 句子结束符：英文句点后跟空格+大写/数字/引号，中文句号/问号/感叹号，以及各种换行
    _SENTENCE_PATTERN = re.compile(
        r'[。！？.!?]\s*(?=[\u4e00-\u9fffA-Z0-9"\'（「【])'
        r'|(?<=\n)\s*(?=\S)'
        r'|[:：]\s*(?=\n)'
    )

    def __init__(self, chunk_size: int = RAG_CHUNK_SIZE, overlap: int = RAG_CHUNK_OVERLAP):
        self.chunk_size = chunk_size
        self.overlap = overlap
        if self.overlap >= self.chunk_size:
            raise ValueError(
                f"overlap ({self.overlap}) 必须小于 chunk_size ({self.chunk_size})，"
                "否则会导致死循环。请在 .env 中调整 RAG_CHUNK_SIZE 和 RAG_CHUNK_OVERLAP。"
            )

    def _split_sentences(self, text: str) -> list[str]:
        """将文本拆分为句子列表"""
        parts = self._SENTENCE_PATTERN.split(text)
        # 合并过短的片段
        sentences = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if sentences and len(p) < 10 and p[-1] not in '。！？.!?\n':
                sentences[-1] += p
            else:
                sentences.append(p)
        # 合并连续短句（单行列表项等）
        merged = []
        for s in sentences:
            if merged and len(s) < 30 and (s.startswith('- ') or s.startswith('* ') or s[0].islower()):
                merged[-1] += ' ' + s
            else:
                merged.append(s)
        return [s.strip() for s in merged if s.strip()]

    def split(self, text: str) -> list[str]:
        """
        按语义边界分块
        - 优先按段落拆分，再按句子边界组装
        - 含 overlap 确保上下文连贯
        """
        text = text.strip()
        if not text:
            return []
        if len(text) <= self.chunk_size:
            return [text]

        sentences = self._split_sentences(text)
        if not sentences:
            return [text]

        chunks = []
        current_chunk = []
        current_len = 0

        for sentence in sentences:
            sentence_len = len(sentence)

            # 如果当前块为空且单句就超长，直接切
            if not current_chunk and sentence_len > self.chunk_size:
                # 单句超长，按字符数硬切
                for i in range(0, sentence_len, self.chunk_size):
                    chunks.append(sentence[i:i + self.chunk_size].strip())
                continue

            # 加上这句会超长 → 结算当前块
            if current_len + sentence_len + 1 > self.chunk_size:
                if current_chunk:
                    chunk_text = '\n\n'.join(current_chunk)
                    if chunk_text.strip():
                        chunks.append(chunk_text.strip())

                    # overlap: 保留当前块末尾的句子
                    overlap_sentences = []
                    overlap_len = 0
                    for s in reversed(current_chunk):
                        if overlap_len + len(s) + 1 > self.overlap:
                            break
                        overlap_sentences.insert(0, s)
                        overlap_len += len(s) + 2

                    current_chunk = overlap_sentences
                    current_len = overlap_len

                # 如果新句子依然太大，直接作为新块
                if not current_chunk or sentence_len <= self.chunk_size:
                    current_chunk.append(sentence)
                    current_len += len(sentence) + 2
                else:
                    # 单句超长，硬切
                    for i in range(0, sentence_len, self.chunk_size):
                        chunks.append(sentence[i:i + self.chunk_size].strip())
            else:
                current_chunk.append(sentence)
                current_len += len(sentence) + 2

        # 最后一块
        if current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            if chunk_text.strip():
                chunks.append(chunk_text.strip())

        # 去重（相邻块完全相同的情况）
        unique_chunks = []
        for c in chunks:
            if not unique_chunks or c != unique_chunks[-1]:
                unique_chunks.append(c)

        return unique_chunks


class KnowledgeBase:
    """
    RAG 知识库
    - 文档管理（增删查）
    - 语义检索
    """

    def __init__(self):
        os.makedirs(CHROMA_PERSIST_PATH, exist_ok=True)

        # ChromaDB 本地持久化
        self.chroma_client = chromadb.PersistentClient(
            path=CHROMA_PERSIST_PATH,
            settings=Settings(anonymized_telemetry=False),
        )

        # 使用自定义 Qwen Embedding（不注册到 ChromaDB）
        self.embedding = QwenEmbedding()
        self.splitter = TextSplitter()

        # 获取或创建 collection（不指定 embedding_function，手动传入向量）
        self.collection = self.chroma_client.get_or_create_collection(
            name=CHROMA_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"知识库已加载，当前文档数：{self.collection.count()}")

    # ------------------------------------------------------------------
    # 文档录入
    # ------------------------------------------------------------------

    def add_text(self, text: str, metadata: Optional[dict] = None, doc_id: Optional[str] = None) -> list[str]:
        """
        将文本录入知识库
        :param text: 原始文本
        :param metadata: 额外元数据
        :param doc_id: 文档 ID（默认基于内容 hash）
        :return: 写入的 chunk ID 列表
        """
        chunks = self.splitter.split(text)
        if not chunks:
            return []

        base_id = doc_id or hashlib.md5(text.encode()).hexdigest()[:12]
        ids = [f"{base_id}_chunk{i}" for i in range(len(chunks))]
        metadatas = []
        for i, chunk in enumerate(chunks):
            m = metadata.copy() if metadata else {}
            m.update({"chunk_index": i, "total_chunks": len(chunks), "doc_id": base_id})
            metadatas.append(m)

        # 生成向量
        embeddings = self.embedding.embed(chunks)

        self.collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        logger.info(f"已写入 {len(chunks)} 个文本块，doc_id={base_id}")
        return ids

    def add_file(self, file_path: str) -> list[str]:
        """
        从文件录入知识库（支持 .txt / .md / .pdf / .docx）
        - .md 文件：按标题结构智能分块，保留标题层级信息
        - .txt：按字符数分块
        - .pdf：逐页提取文本后分块
        - .docx：按段落提取后分块
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在：{file_path}")

        suffix = path.suffix.lower()
        base_metadata = {"source": path.name, "file_path": str(path.resolve()), "format": suffix.lstrip(".")}

        # ---- Markdown：按标题分块 ----
        if suffix == ".md":
            raw = path.read_text(encoding="utf-8")
            return self._add_markdown(raw, base_metadata, doc_id=path.stem)

        # ---- 纯文本 ----
        elif suffix == ".txt":
            text = path.read_text(encoding="utf-8")

        # ---- PDF ----
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(file_path)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                raise ImportError("请安装 pypdf：pip install pypdf")

        # ---- Word ----
        elif suffix == ".docx":
            try:
                from docx import Document as DocxDocument
                doc = DocxDocument(file_path)
                text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except ImportError:
                raise ImportError("请安装 python-docx：pip install python-docx")

        else:
            raise ValueError(f"不支持的文件格式：{suffix}，支持 .txt/.md/.pdf/.docx")

        return self.add_text(text, metadata=base_metadata, doc_id=path.stem)

    def _add_markdown(self, raw: str, base_metadata: dict, doc_id: str) -> list[str]:
        """
        Markdown 智能分块：
        1. 先按一、二级标题切分为"节"
        2. 节内若仍过长，再按字符数二次分块
        3. 每块 metadata 含 heading 标题信息
        """
        import re

        # 按 # / ## 标题切分
        heading_pattern = re.compile(r'^(#{1,3})\s+(.+)$', re.MULTILINE)
        sections: list[tuple[str, str]] = []  # [(heading, content)]

        positions = [(m.start(), m.group(1), m.group(2)) for m in heading_pattern.finditer(raw)]

        if not positions:
            # 无标题，整体当纯文本处理
            return self.add_text(raw, metadata=base_metadata, doc_id=doc_id)

        # 提取每个标题下的内容
        for i, (pos, level, heading) in enumerate(positions):
            start = pos
            end = positions[i + 1][0] if i + 1 < len(positions) else len(raw)
            section_text = raw[start:end].strip()
            if section_text:
                sections.append((heading, section_text))

        # 前置内容（第一个标题之前）
        preamble = raw[:positions[0][0]].strip()
        if preamble:
            sections.insert(0, ("（前言）", preamble))

        # 写入各 section
        all_ids: list[str] = []
        for sec_idx, (heading, content) in enumerate(sections):
            chunks = self.splitter.split(content)
            if not chunks:
                continue
            chunk_ids = []
            for chunk_i, chunk in enumerate(chunks):
                cid = f"{doc_id}_sec{sec_idx}_chunk{chunk_i}"
                chunk_ids.append(cid)

            meta_list = []
            for chunk_i, chunk in enumerate(chunks):
                m = base_metadata.copy()
                m.update({
                    "heading": heading,
                    "section_index": sec_idx,
                    "chunk_index": chunk_i,
                    "total_chunks": len(chunks),
                    "doc_id": doc_id,
                })
                meta_list.append(m)

            embeddings = self.embedding.embed(chunks)
            self.collection.upsert(
                ids=chunk_ids,
                documents=chunks,
                embeddings=embeddings,
                metadatas=meta_list,
            )
            all_ids.extend(chunk_ids)

        logger.info(f"[Markdown] {base_metadata['source']} → {len(sections)} 节，{len(all_ids)} 块")
        return all_ids

    # ------------------------------------------------------------------
    # 语义检索
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = RAG_TOP_K) -> list[dict]:
        """
        语义检索，按相关度阈值过滤低质量结果
        :param query: 查询文本
        :param top_k: 返回条数上限
        :return: [{text, score, metadata}, ...]，按 score 降序
        """
        if self.collection.count() == 0:
            return []

        query_embedding = self.embedding.embed_one(query)
        n_results = min(top_k * 2, self.collection.count())  # 多取一倍，过滤后再裁剪
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=["documents", "distances", "metadatas"],
        )

        items = []
        for doc, dist, meta in zip(
            results["documents"][0],
            results["distances"][0],
            results["metadatas"][0],
        ):
            score = round(1.0 - dist, 4)
            if score < RAG_SCORE_THRESHOLD:
                continue
            items.append({
                "text": doc,
                "score": score,
                "metadata": meta,
            })

        # 按相关度降序，取前 top_k
        items.sort(key=lambda x: x["score"], reverse=True)
        filtered = items[:top_k]

        logger.info(
            f"[RAG检索] query='{query[:30]}' → 候选{n_results}条 → "
            f"≥阈值{ RAG_SCORE_THRESHOLD}的有{len(items)}条 → 返回{len(filtered)}条"
        )
        return filtered

    def format_context(self, query: str, top_k: int = RAG_TOP_K) -> str:
        """检索并格式化为 Prompt 上下文"""
        results = self.search(query, top_k=top_k)
        return self.format_context_from_results(results)

    def format_context_from_results(self, results: list[dict]) -> str:
        """直接格式化已检索好的结果（避免重复检索）"""
        if not results:
            return ""
        context_parts = []
        for i, r in enumerate(results, 1):
            source = r["metadata"].get("source", "知识库")
            context_parts.append(f"[参考{i} · {source}]\n{r['text']}")
        return "\n\n".join(context_parts)

    # ------------------------------------------------------------------
    # 管理接口
    # ------------------------------------------------------------------

    def count(self) -> int:
        return self.collection.count()

    def list_sources(self) -> list[str]:
        """列出知识库中所有文档来源"""
        if self.collection.count() == 0:
            return []
        results = self.collection.get(include=["metadatas"])
        sources = set()
        for meta in results["metadatas"]:
            if "source" in meta:
                sources.add(meta["source"])
        return list(sources)

    def delete_by_source(self, source_name: str) -> int:
        """按来源文件名删除文档块"""
        results = self.collection.get(include=["metadatas"])  # ids 默认返回
        ids_to_delete = [
            results["ids"][i]
            for i, meta in enumerate(results["metadatas"])
            if meta.get("source") == source_name
        ]
        if ids_to_delete:
            self.collection.delete(ids=ids_to_delete)
        return len(ids_to_delete)

    def get_by_source(self, source_name: str) -> list[dict]:
        """按来源获取所有文本块及其 metadata"""
        # ChromaDB 1.x get() 默认返回 ids，不需要也支持放在 include 中
        results = self.collection.get(
            include=["documents", "metadatas"],
        )
        chunks = []
        for i, meta in enumerate(results["metadatas"]):
            if meta.get("source") == source_name:
                chunks.append({
                    "id": results["ids"][i],
                    "text": results["documents"][i],
                    "metadata": meta,
                })
        # 按 section_index + chunk_index 排序
        chunks.sort(key=lambda c: (
            c["metadata"].get("section_index", 0),
            c["metadata"].get("chunk_index", 0),
        ))
        return chunks
