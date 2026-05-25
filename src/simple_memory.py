"""
simple_memory.py — 轻量本地记忆管理（替代 mem0）

功能：
- 使用 sentence-transformers 本地模型做语义向量化
- 用 JSON 文件持久化存储，无需 Qdrant/mem0
- 支持 remember / recall / get_user_history

依赖：sentence-transformers（已安装），无其他外部服务

user_id 隔离说明：
- 记忆按 user_id 隔离存储（store[user_id]），不同用户不会混淆
- 当前阶段：user_id 来自请求中的 session_id 或前端传入（见 server.py）
- 生产阶段：user_id 应从 JWT/Session 中解析，不应由前端随意传入
- 隔离粒度：每个 user_id 独立存储，最多保留 100 条记忆
"""

import json
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer

MEMORY_FILE = Path(__file__).parent / "memory_store.json"
_model = None


def _get_model():
    """懒加载 sentence-transformers 模型（全局单例）"""
    global _model
    if _model is None:
        print("[simple_memory] 加载本地嵌入模型 paraphrase-MiniLM-L3-v2 ...")
        _model = SentenceTransformer("paraphrase-MiniLM-L3-v2")
        print("[simple_memory] 模型加载完成")
    return _model


def _load_store() -> dict:
    """加载记忆存储文件"""
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_store(store: dict):
    """保存记忆存储到文件"""
    MEMORY_FILE.write_text(
        json.dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _embed(text: str) -> list[float]:
    """将文本转为向量"""
    model = _get_model()
    vec = model.encode(text, convert_to_numpy=True)
    return vec.tolist()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度"""
    a = np.array(a)
    b = np.array(b)
    if a.ndim == 1:
        a = a.reshape(1, -1)
    if b.ndim == 1:
        b = b.reshape(1, -1)
    from numpy.linalg import norm
    sim = (a @ b.T) / (norm(a) * norm(b, axis=1, keepdims=True) + 1e-9)
    return float(sim[0][0])


# ========== 对外接口（与 mem0_manager 兼容）==========

def remember(user_id: str, question: str, answer: str, confidence: float):
    """存储一次问答到本地记忆"""
    if confidence < 0.5:
        return
    store = _load_store()
    if user_id not in store:
        store[user_id] = []
    entry = {
        "question": question,
        "answer": answer,
        "confidence": confidence,
        "embedding": _embed(question + " " + answer),
    }
    store[user_id].append(entry)
    # 最多保留 100 条 per user
    store[user_id] = store[user_id][-100:]
    _save_store(store)


def recall(query: str, user_id: str = "default", top_k: int = 3) -> list[str]:
    """语义搜索历史记忆，返回相关回答文本列表"""
    store = _load_store()
    entries = store.get(user_id, [])
    if not entries:
        return []
    q_emb = _embed(query)
    scored = []
    for e in entries:
        emb = e.get("embedding", [])
        if not emb:
            continue
        sim = _cosine_similarity(q_emb, emb)
        scored.append((sim, e.get("answer", "")))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for sim, text in scored[:top_k] if sim > 0.3]


def get_user_history(user_id: str, limit: int = 20) -> list[dict]:
    """获取用户全部记忆（按时间倒序）"""
    store = _load_store()
    entries = store.get(user_id, [])
    return list(reversed(entries[-limit:]))
