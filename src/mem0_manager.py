"""
mem0_manager.py — 基于 mem0 的智能记忆管理
============================================
替代 patterns.json 关键词匹配 + 内存会话历史，
提供语义搜索、跨会话持久化、用户画像。

配置：自动读取同目录下的 api_config.json，复用 AI API 配置。
embedder 使用 fastembed（本地，无需 API）。
"""

import json
import warnings
from pathlib import Path
from typing import Optional
from mem0 import Memory

MEM0_DB = Path("./mem0_db")
CONFIG_FILE = Path(__file__).parent / "api_config.json"
_instance = None


def _load_api_config() -> dict:
    """读取 api_config.json"""
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_memory() -> Optional[Memory]:
    """获取 mem0 实例（单例）"""
    global _instance
    if _instance is not None:
        return _instance

    api_config = _load_api_config()
    api_key = api_config.get("api_key", "")
    base_url = api_config.get("base_url", "")
    model = api_config.get("model", "deepseek-chat")

    # 构建 mem0 配置
    config = {
        "llm": {
            "provider": "openai",
            "config": {
                "api_key": api_key,
                "openai_base_url": base_url,
                "model": model,
            }
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {"path": str(MEM0_DB)}
        },
        "embedder": {
            "provider": "huggingface",
            "config": {"model": "paraphrase-MiniLM-L3-v2"}
        },
    }

    try:
        _instance = Memory.from_config(config)
        print(f"[mem0] 初始化成功（Qdrant: {MEM0_DB}，LLM: {model}）")
    except Exception as e:
        print(f"[mem0] 初始化失败: {e}")
        _instance = None

    return _instance


def remember(user_id: str, question: str, answer: str, confidence: float):
    """存储一次问答到记忆（confidence >= 0.5 才保存）"""
    if confidence < 0.5:
        return
    try:
        m = get_memory()
        if m is None:
            return
        m.add(
            messages=[
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer}
            ],
            user_id=user_id,
            metadata={"confidence": confidence}
        )
    except Exception as e:
        print(f"[mem0] 存储失败: {e}")


def recall(query: str, user_id: str = "default", top_k: int = 3) -> list[str]:
    """语义搜索历史记忆，返回相关记忆文本列表"""
    try:
        m = get_memory()
        if m is None:
            return []
        results = m.search(query, user_id=user_id, limit=top_k)
        return [r.get("memory", "") for r in results if r.get("memory")]
    except Exception as e:
        print(f"[mem0] 搜索失败: {e}")
        return []


def get_user_history(user_id: str, limit: int = 20) -> list[dict]:
    """获取用户全部记忆"""
    try:
        m = get_memory()
        if m is None:
            return []
        return m.get_all(user_id=user_id, limit=limit)
    except Exception as e:
        print(f"[mem0] 获取历史失败: {e}")
        return []
