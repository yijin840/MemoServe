"""
全局配置模块
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ====================== 项目根目录 ======================
_PROJECT_ROOT = Path(__file__).resolve().parent

# ====================== Qwen / DashScope ======================
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")
QWEN_EMBEDDING_MODEL = os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v3")

# DashScope OpenAI 兼容接口
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# ====================== 启动校验 ======================
_STARTUP_WARNINGS = []


def check_startup() -> list[str]:
    """
    启动校验，返回警告列表。
    在 main.py 的 lifespan 中调用，有警告时打印日志但不阻止启动。
    """
    warnings = []

    # 1. API Key 校验
    if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY.startswith("sk-your"):
        warnings.append(
            "DASHSCOPE_API_KEY 未配置或使用占位符！"
            "请在 .env 文件中设置有效的 API Key，否则 RAG/Embedding/LLM 功能将无法使用。"
        )

    # 2. ChromaDB 目录可写性
    chroma_path = Path(CHROMA_PERSIST_PATH)
    if not chroma_path.exists():
        try:
            chroma_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            warnings.append(f"ChromaDB 目录不可创建: {chroma_path} ({e})")
    elif not os.access(chroma_path, os.W_OK):
        warnings.append(f"ChromaDB 目录不可写: {chroma_path}")

    # 3. 必要依赖检查
    try:
        import chromadb  # noqa: F401
    except ImportError:
        warnings.append("chromadb 未安装，请运行: pip install chromadb")

    try:
        import openai  # noqa: F401
    except ImportError:
        warnings.append("openai 未安装，请运行: pip install openai")

    try:
        from mem0 import Memory  # noqa: F401
    except ImportError:
        warnings.append("mem0ai 未安装，请运行: pip install mem0ai")

    return warnings


# ====================== ChromaDB ======================
CHROMA_PERSIST_PATH = os.getenv("CHROMA_PERSIST_PATH", str(_PROJECT_ROOT / "data" / "chroma_db"))
CHROMA_COLLECTION_NAME = "customer_service_kb"  # 知识库 collection
CHROMA_MEMORY_COLLECTION = "mem0_memory"         # mem0 记忆 collection

# ====================== RAG ======================
RAG_TOP_K = 5           # 检索 Top-K 片段
RAG_CHUNK_SIZE = 500    # 文本分块大小
RAG_CHUNK_OVERLAP = 50  # 分块重叠
RAG_SCORE_THRESHOLD = float(os.getenv("RAG_SCORE_THRESHOLD", "0.4"))  # 最低相关度阈值

# ====================== mem0 ======================
MEM0_TOP_K = 5           # 每次检索记忆条数
MEM0_SCORE_THRESHOLD = float(os.getenv("MEM0_SCORE_THRESHOLD", "0.3"))

# ====================== mem0 Custom Instructions ======================
# 控制 LLM 如何从对话中提取记忆的指令
# 覆盖 config/init 时设置，影响所有 add() 调用
CUSTOM_INSTRUCTIONS = os.getenv("MEM0_CUSTOM_INSTRUCTIONS", "")

# 预设记忆提取策略（通过 MEM0_MEMORY_STRATEGY 环境变量选择）
# 可选：customer_service | general | strict
MEMORY_STRATEGY = os.getenv("MEM0_MEMORY_STRATEGY", "customer_service")

# ====================== App ======================
APP_PORT = int(os.getenv("APP_PORT", 8000))
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

# ====================== Telegram Bot ======================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
