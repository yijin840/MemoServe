from __future__ import annotations

"""
FastAPI 后端接口
提供：
- POST /chat           - 普通对话
- GET  /chat/stream    - SSE 流式对话
- POST /knowledge/add  - 上传文本到知识库
- POST /knowledge/search-and-add - 搜索关键词并导入知识库
- POST /knowledge/file - 上传文件到知识库
- GET  /knowledge/list - 查看知识库来源列表
- DELETE /knowledge/?source=xxx - 删除知识库来源
- GET  /memory/{user_id}     - 查看用户记忆
- DELETE /memory/{user_id}   - 清除用户记忆
- GET /health         - 健康检查
- Telegram Bot（后台轮询，每个群对应一个 user_id）
"""
import logging
import os
import signal
import tempfile
import threading
from contextlib import asynccontextmanager
from typing import Optional, Any
import httpx
from bs4 import BeautifulSoup

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel, Field

from config import APP_HOST, APP_PORT, DEBUG, TELEGRAM_BOT_TOKEN, check_startup
from rag_knowledge_base import KnowledgeBase
from memory_manager import MemoryManager
from answer_cache import AnswerCache
from knowledge_agent import CustomerServiceAgent as KnowledgeAgent
from router_agent import IntentClassifier

# ====================== 日志配置 ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 全局单例
kb: Optional[KnowledgeBase] = None
mm: Optional[MemoryManager] = None
cache: Optional[AnswerCache] = None
agent: Optional[IntentClassifier] = None  # 意图分类器（统一入口）
knowledge_agent: Optional[KnowledgeAgent] = None
_telegram_app = None  # Telegram bot Application 实例

# 知识库文件目录
KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "knowledge")
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global kb, mm, cache, agent, knowledge_agent, _telegram_app

    # 启动校验
    _warnings = check_startup()
    if _warnings:
        for w in _warnings:
            logger.warning(f"[启动校验] {w}")

    logger.info("初始化知识库...")
    kb = KnowledgeBase()
    logger.info("初始化记忆管理器...")
    mm = MemoryManager()
    logger.info("初始化问答缓存...")
    cache = AnswerCache()
    logger.info("初始化知识库 Agent...")
    knowledge_agent = KnowledgeAgent(kb, mm, cache=cache)
    logger.info("初始化意图分类器...")
    agent = IntentClassifier()
    logger.info("✅ 服务启动完成（IntentClassifier + KnowledgeAgent）")

    # 启动 Telegram Bot（如果配置了 Token）
    _telegram_app = None
    if TELEGRAM_BOT_TOKEN:
        try:
            from telegram_bot import build_bot_app
            _telegram_app = build_bot_app(agent)
            _telegram_app.bot_data["knowledge_agent"] = knowledge_agent
            # 在后台线程启动 polling（不阻塞 FastAPI）
            import subprocess, sys
            def _start_bot():
                logger.info("[Telegram] Bot 开始轮询...")
                subprocess.Popen(
                    [sys.executable, "-m", "telegram_bot"],
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    env={**os.environ, "TELEGRAM_AS_PROCESS": "1"},
                )
                logger.info("[Telegram] Bot 进程已启动")
            threading.Thread(target=_start_bot, daemon=True, name="tg-bot").start()
        except Exception as e:
            logger.warning(f"[Telegram] Bot 启动失败（已跳过）：{e}")
    else:
        logger.info("[Telegram] 未配置 TELEGRAM_BOT_TOKEN，跳过 Bot 启动")

    yield

    # 关闭 Telegram Bot
    if _telegram_app:
        try:
            await _telegram_app.shutdown()
            logger.info("[Telegram] Bot 已关闭")
        except Exception as e:
            logger.warning(f"[Telegram] Bot 关闭异常：{e}")
    logger.info("服务关闭")

# ====================== 初始化组件 ======================
app = FastAPI(
    title="智能客服 API",
    description="基于 mem0ai + Qwen + ChromaDB 的智能客服系统",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====================== 数据模型 ======================

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="用户输入")
    user_id: str = Field(default="default_user", description="用户 ID")
    history: list[dict] = Field(default=[], description="历史对话 [{role, content}]")


class ChatResponse(BaseModel):
    answer: str
    rag_sources: list[dict] = []
    memories_used: list[str] = []
    model: str = ""
    intents: Optional[list[str]] = None  # 识别到的意图列表


class AddTextRequest(BaseModel):
    text: str = Field(..., min_length=1, description="知识内容")
    source: str = Field(default="手动录入", description="来源标识")
    category: str = Field(default="通用", description="分类")


class SearchAndAddRequest(BaseModel):
    """搜索并导入知识库请求"""
    keyword: str = Field(..., min_length=1, max_length=200, description="搜索关键词")
    max_results: int = Field(default=3, ge=1, le=10, description="最多处理结果数")
    category: str = Field(default="通用", description="知识分类")


def _check_search_relevance(keyword: str, title: str, content: str) -> bool:
    """
    检查搜索结果与关键词的相关性。
    简单的关键词匹配：提取关键词中的核心词汇（2+字符），
    要求标题或内容中至少包含一个核心词。
    """
    import re

    # 提取核心词汇（中英文，长度>=2）
    # 中文按字切，英文按词切
    kw = keyword.lower()
    # 中文提取连续中文字符（长度>=2，CJK统一表意文字范围）
    chinese_words = re.findall('[\u4e00-\u9fff]{2,}', kw)
    # 英文/数字提取连续单词（长度>=2）
    english_words = re.findall(r'[a-z0-9]{2,}', kw)
    core_words = chinese_words + english_words

    if not core_words:
        # 关键词太短，无法提取核心词，直接放行
        return True

    text = (title + " " + content).lower()
    matched = [w for w in core_words if w in text]
    # 要求至少命中一个核心词
    return len(matched) > 0


async def search_and_fetch_content(keyword: str, max_results: int = 3) -> list[dict]:
    """
    搜索关键词并抓取内容，自动过滤不相关结果
    返回 [{"title": ..., "content": ..., "url": ...}, ...]
    """
    raw_results = []

    try:
        # 1. 尝试 DuckDuckGo Instant Answer API
        async with httpx.AsyncClient(timeout=10.0) as client:
            ddg_resp = await client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": keyword,
                    "format": "json",
                    "no_html": "1",
                    "skip_disambig": "1",
                },
            )
            ddg_data = ddg_resp.json()

            # 如果有 AbstractText（即时答案），直接使用
            if ddg_data.get("AbstractText"):
                raw_results.append({
                    "title": ddg_data.get("Heading", keyword),
                    "content": ddg_data["AbstractText"],
                    "url": ddg_data.get("AbstractURL", ""),
                    "source_type": "instant_answer",
                })

            # 如果有 RelatedTopics，也加入
            for topic in (ddg_data.get("RelatedTopics") or [])[:max_results * 2]:
                if isinstance(topic, dict) and topic.get("Text"):
                    raw_results.append({
                        "title": topic.get("Text", "")[:100],
                        "content": topic["Text"],
                        "url": (topic.get("FirstURL") or ""),
                        "source_type": "related_topic",
                    })

            # 如果即时答案不够，用 HTML 搜索补充
            if len(raw_results) < max_results:
                # 使用 DuckDuckGo HTML 搜索获取更多结果
                html_resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": keyword},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                soup = BeautifulSoup(html_resp.text, "html.parser")

                # 提取搜索结果链接
                for result_div in soup.select(".result__body")[:max_results * 2]:
                    title_elem = result_div.select_one(".result__a")
                    snippet_elem = result_div.select_one(".result__snippet")

                    if title_elem:
                        url = title_elem.get("href", "")
                        title = title_elem.get_text(strip=True)
                        snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

                        if url and title:
                            raw_results.append({
                                "title": title,
                                "content": f"{title}\n\n{snippet}",
                                "url": url,
                                "source_type": "web_search",
                            })

    except Exception as e:
        logger.error(f"搜索失败: {e}")

    # 过滤不相关结果
    results = []
    for item in raw_results:
        title = item.get("title", "")
        content = item.get("content", "")
        if _check_search_relevance(keyword, title, content):
            results.append(item)
        else:
            logger.warning(f"[搜索过滤] 丢弃不相关结果：{title[:60]}... (keyword={keyword})")

    return results[:max_results]


@app.post("/knowledge/search-and-add", tags=["知识库"])
async def search_and_add_knowledge(req: SearchAndAddRequest):
    """
    搜索关键词并导入知识库
    1. 使用 DuckDuckGo 搜索关键词
    2. 抓取搜索结果内容
    3. 整理后导入知识库
    """
    if not kb:
        raise HTTPException(status_code=503, detail="服务初始化中")

    try:
        # 搜索并抓取内容
        search_results = await search_and_fetch_content(req.keyword, req.max_results)

        if not search_results:
            raise HTTPException(status_code=404, detail=f"未找到关键词「{req.keyword}」的相关内容")

        # 整理并导入知识库
        added = []
        for item in search_results:
            content = item["content"]
            if not content or len(content.strip()) < 50:
                continue

            # 限制单条内容长度（避免超大文本）
            if len(content) > 5000:
                content = content[:5000] + "...(内容过长已截断)"

            title = (item.get("title") or req.keyword)[:30]
            source = f"搜索:{title}"

            ids = kb.add_text(
                content,
                metadata={
                    "source": source,
                    "category": req.category,
                    "keyword": req.keyword,
                    "url": item.get("url", ""),
                    "title": item.get("title", ""),
                    "source_type": item.get("source_type", "unknown"),
                },
            )
            added.append({
                "title": item["title"],
                "url": item.get("url", ""),
                "chunk_count": len(ids),
                "ids": ids,
            })

        return {
            "success": True,
            "keyword": req.keyword,
            "search_results_count": len(search_results),
            "imported_count": len(added),
            "imported": added,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"搜索并导入失败: {e}")
        raise HTTPException(status_code=500, detail=f"搜索并导入失败：{str(e)}")

# ====================== 知识库文件管理接口 ======================

def _list_knowledge_files(directory: str = None, relative_path: str = "") -> list[dict]:
    """递归列出知识库目录下的所有文件（返回统一结构）"""
    if directory is None:
        directory = KNOWLEDGE_DIR
    result = []
    try:
        for entry in sorted(os.listdir(directory)):
            full_path = os.path.join(directory, entry)
            rel_path = os.path.join(relative_path, entry) if relative_path else entry
            if os.path.isdir(full_path):
                result.append({
                    "name": entry,
                    "path": rel_path,
                    "type": "dir",
                    "size": 0,
                    "modified": os.path.getmtime(full_path),
                    "children": _list_knowledge_files(full_path, rel_path),
                })
            else:
                ext = os.path.splitext(entry)[1].lower()
                result.append({
                    "name": entry,
                    "path": rel_path,
                    "type": "file",
                    "size": os.path.getsize(full_path),
                    "modified": os.path.getmtime(full_path),
                    "ext": ext,
                })
    except Exception as e:
        logger.error(f"列出文件失败 {directory}: {e}")
    return result


@app.get("/knowledge/files", tags=["知识库文件管理"])
async def list_knowledge_files():
    """列出知识库文件目录（data/knowledge/）下的所有文件"""
    files = _list_knowledge_files()
    return {"files": files, "total": sum(1 for f in files if f["type"] == "file")}


@app.get("/knowledge/files/{file_path:path}/content", tags=["知识库文件管理"])
async def get_knowledge_file_content(file_path: str):
    """预览知识库文件内容（仅支持文本文件）"""
    # 安全校验：防止路径穿越
    full_path = os.path.normpath(os.path.join(KNOWLEDGE_DIR, file_path))
    if not full_path.startswith(os.path.normpath(KNOWLEDGE_DIR)):
        raise HTTPException(status_code=400, detail="非法文件路径")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    ext = os.path.splitext(full_path)[1].lower()
    text_exts = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log"}

    if ext not in text_exts:
        return {"success": True, "path": file_path, "type": ext.lstrip("."), "content": None, "message": "非文本文件，无法预览"}

    try:
        with open(full_path, encoding="utf-8") as f:
            content = f.read()
        return {"success": True, "path": file_path, "type": "text", "content": content[:10000]}
    except UnicodeDecodeError:
        with open(full_path, encoding="gbk", errors="ignore") as f:
            content = f.read()
        return {"success": True, "path": file_path, "type": "text", "content": content[:10000]}


@app.delete("/knowledge/files/{file_path:path}", tags=["知识库文件管理"])
async def delete_knowledge_file(file_path: str):
    """删除知识库文件，并同步删除知识库中的对应内容"""
    full_path = os.path.normpath(os.path.join(KNOWLEDGE_DIR, file_path))
    if not full_path.startswith(os.path.normpath(KNOWLEDGE_DIR)):
        raise HTTPException(status_code=400, detail="非法文件路径")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    try:
        # 从知识库中删除对应来源
        source_name = os.path.basename(full_path)
        deleted = kb.delete_by_source(source_name) if kb else 0
        logger.info(f"[文件管理] 删除知识库来源：{source_name}，删除块数：{deleted}")

        # 删除文件
        os.unlink(full_path)
        return {"success": True, "deleted_chunks": deleted, "message": f"文件 {file_path} 已删除"}
    except Exception as e:
        logger.error(f"删除文件失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ====================== 对话接口 ======================

@app.post("/chat", response_model=ChatResponse, tags=["对话"])
async def chat(req: ChatRequest):
    """普通对话接口"""
    if not agent:
        raise HTTPException(status_code=503, detail="服务初始化中，请稍后")
    try:
        result = agent.chat(
            user_input=req.message,
            user_id=req.user_id,
            conversation_history=req.history,
            update_memory=True,
            knowledge_agent=knowledge_agent,
        )
        return result
    except Exception as e:
        logger.error(f"对话失败: {e}")
        raise HTTPException(status_code=500, detail=f"对话失败：{str(e)}")


@app.get("/chat/stream", tags=["对话"])
async def stream_chat(message: str, user_id: str = "default_user"):
    """流式对话（SSE）"""
    if not agent:
        raise HTTPException(status_code=503, detail="服务初始化中")

    async def event_generator():
        try:
            async for chunk in agent.stream_chat(
                user_input=message,
                user_id=user_id,
                knowledge_agent=knowledge_agent,
            ):
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ====================== 知识库接口 ======================

@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge_text(req: AddTextRequest):
    """添加文本到知识库"""
    if not kb:
        raise HTTPException(status_code=503, detail="服务初始化中")
    try:
        ids = kb.add_text(
            req.text,
            metadata={"source": req.source, "category": req.category},
        )
        return {"success": True, "chunk_count": len(ids), "ids": ids}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/knowledge/file", tags=["知识库"])
async def upload_knowledge_file(file: UploadFile = File(...)):
    """上传文件到知识库（支持 .txt/.md/.pdf/.docx），文件保存到 data/knowledge/ 目录"""
    if not kb:
        raise HTTPException(status_code=503, detail="服务初始化中")

    allowed = {".txt", ".md", ".pdf", ".docx"}
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{suffix}")

    # 保存到 KNOWLEDGE_DIR，处理重名
    base_name = file.filename
    save_path = os.path.join(KNOWLEDGE_DIR, base_name)
    counter = 1
    while os.path.exists(save_path):
        name_part, ext_part = os.path.splitext(base_name)
        new_name = f"{name_part}_{counter}{ext_part}"
        save_path = os.path.join(KNOWLEDGE_DIR, new_name)
        counter += 1

    try:
        # 保存文件
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)
        logger.info(f"[文件管理] 文件已保存：{save_path}")

        # 导入知识库
        ids = kb.add_file(save_path)
        return {
            "success": True,
            "filename": os.path.basename(save_path),
            "saved_path": save_path,
            "chunk_count": len(ids),
        }
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "invalid_api_key" in error_msg.lower() or "Incorrect API key" in error_msg:
            raise HTTPException(
                status_code=500,
                detail=(
                    "API Key 无效或已过期！\n\n"
                    "请到阿里云百炼控制台（dashscope.aliyun.com）重新获取 API Key，"
                    "然后编辑项目根目录的 .env 文件替换 DASHSCOPE_API_KEY 的值，"
                    "保存后重启服务即可。"
                ),
            )
        # 如果导入失败，删除已保存的文件
        if os.path.exists(save_path):
            os.unlink(save_path)
        raise HTTPException(status_code=500, detail=error_msg)


@app.get("/knowledge/list", tags=["知识库"])
async def list_knowledge():
    """列出知识库所有来源"""
    if not kb:
        raise HTTPException(status_code=503, detail="服务初始化中")
    sources = kb.list_sources()
    return {"total_chunks": kb.count(), "sources": sources}


@app.delete("/knowledge/", tags=["知识库"])
async def delete_knowledge(source_name: str = Query(..., description="来源名称")):
    """按来源名称删除知识（使用 query parameter 避免 path 编码问题）"""
    if not kb:
        raise HTTPException(status_code=503, detail="服务初始化中")
    deleted = kb.delete_by_source(source_name)
    return {"success": True, "deleted_chunks": deleted}


@app.get("/knowledge/chunks/", tags=["知识库"])
async def get_knowledge_chunks(source_name: str = Query(..., description="来源名称")):
    """查看指定文档来源的所有文本块内容（使用 query parameter 避免 path 编码问题）"""
    if not kb:
        raise HTTPException(status_code=503, detail="服务初始化中")
    try:
        chunks = kb.get_by_source(source_name)
        return {
            "source": source_name,
            "chunk_count": len(chunks),
            "chunks": [
                {
                    "id": c["id"],
                    "text": c["text"],
                    "heading": c["metadata"].get("heading"),
                    "section_index": c["metadata"].get("section_index", 0),
                    "chunk_index": c["metadata"].get("chunk_index", 0),
                    "total_chunks": c["metadata"].get("total_chunks", 1),
                }
                for c in chunks
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== 记忆接口 ======================

# ====================== Custom Instructions API ======================

@app.get("/memory/strategy", tags=["Custom Instructions"])
async def get_memory_strategy():
    """
    查看当前生效的记忆提取策略
    :return: {"strategy": "customer_service"|"general"|"strict"|"custom", "instructions_preview": "..."}
    """
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    return mm.get_current_strategy()


class StrategyRequest(BaseModel):
    strategy: str = Field(..., description="策略名称：customer_service | general | strict")


class InstructionsRequest(BaseModel):
    instructions: str = Field(..., min_length=10, description="自定义指令全文")


@app.put("/memory/strategy", tags=["Custom Instructions"])
async def switch_memory_strategy(req: StrategyRequest):
    """
    切换记忆提取策略
    - customer_service：客服场景（默认），专注API对接/KYC认证/充值/开卡等业务咨询
    - general：通用场景，宽松提取用户偏好和重要信息
    - strict：严格模式，只记录涉及金钱和法律承诺的内容
    """
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    ok = mm.switch_strategy(req.strategy)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=f"无效策略：{req.strategy}，有效值：customer_service / general / strict",
        )
    return {"success": True, "strategy": req.strategy}


@app.put("/memory/instructions", tags=["Custom Instructions"])
async def set_custom_instructions(req: InstructionsRequest):
    """
    设置完全自定义的记忆提取指令（覆盖所有预设策略）
    传入自定义的指令全文，会在 LLM 提取记忆时被使用
    """
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    mm.set_custom_instructions(req.instructions)
    return {"success": True, "message": "自定义指令已更新，mem0 实例已重建"}


@app.post("/memory/test-instructions", tags=["Custom Instructions"])
async def test_custom_instructions(req: InstructionsRequest):
    """
    测试自定义指令效果
    用指定的指令提取一条测试记忆，返回提取结果（不持久化）
    """
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    # 临时用指定指令构建 mem0 实例（只读，不影响全局状态）
    from memory_manager import build_mem0_config, Memory
    config = build_mem0_config(custom_instructions=req.instructions)
    tmp_memory = Memory.from_config(config)
    test_messages = [
        {"role": "user", "content": "我叫李明，手机号13800138000，上周买了一件红色T恤（订单号#98765）还没收到。"}
    ]
    try:
        result = tmp_memory.add(messages=test_messages, user_id="test_user")
        facts = result.get("results", [])
        return {
            "success": True,
            "instructions_preview": req.instructions[:300] + "..." if len(req.instructions) > 300 else req.instructions,
            "extracted_facts": facts,
            "fact_count": len(facts),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"测试失败：{str(e)}")


# ====================== 记忆 CRUD ======================

@app.get("/memory/{user_id}/{memory_id}/history", tags=["记忆"])
async def get_memory_history(user_id: str, memory_id: str):
    """获取单条记忆的变更历史"""
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    history = mm.get_memory_history(memory_id)
    return {"memory_id": memory_id, "history_count": len(history), "history": history}


@app.get("/memory/{user_id}/{memory_id}", tags=["记忆"])
async def get_memory_detail(user_id: str, memory_id: str):
    """获取单条记忆详情"""
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    memory = mm.get_memory_by_id(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在或已删除")
    return memory


@app.put("/memory/{user_id}/{memory_id}", tags=["记忆"])
async def update_memory_detail(user_id: str, memory_id: str, data: dict):
    """更新单条记忆内容"""
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    content = data.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="记忆内容不能为空")
    ok = mm.update_memory(memory_id, content)
    if not ok:
        raise HTTPException(status_code=500, detail="更新失败")
    return {"success": True, "memory_id": memory_id}


@app.get("/memory/{user_id}", tags=["记忆"])
async def get_user_memory(user_id: str, category: str = None):
    """查看用户所有记忆（可选按分类过滤）"""
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    all_memories = mm.get_all_memory(user_id)
    if category:
        all_memories = [m for m in all_memories
                        if (m.get("metadata") or {}).get("category") == category]
    return {"user_id": user_id, "memory_count": len(all_memories), "memories": all_memories}


@app.delete("/memory/{user_id}/{memory_id}", tags=["记忆"])
async def delete_user_memory_item(user_id: str, memory_id: str):
    """删除用户单条记忆"""
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    ok = mm.delete_memory_by_id(memory_id)
    return {"success": ok, "user_id": user_id, "memory_id": memory_id}


@app.delete("/memory/{user_id}", tags=["记忆"])
async def clear_user_memory(user_id: str):
    """清除用户所有记忆"""
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    success = mm.delete_user_memory(user_id)
    return {"success": success, "user_id": user_id}


# ====================== 用户列表 ======================

@app.get("/users", tags=["用户"])
async def list_users():
    """
    列出所有有记忆数据的 user_id
    - 从对话存档 ChromaDB 中扫描 user_id 元数据
    """
    if not mm:
        raise HTTPException(status_code=503, detail="服务初始化中")
    users = mm.list_all_users()
    return {"user_count": len(users), "users": users}


# ====================== 问答缓存接口 ======================

@app.get("/cache/stats", tags=["缓存"])
async def get_cache_stats():
    """查看缓存命中率统计"""
    if not cache:
        raise HTTPException(status_code=503, detail="服务初始化中")
    return cache.stats


@app.delete("/cache", tags=["缓存"])
async def clear_cache(user_id: Optional[str] = None):
    """
    清除问答缓存
    - 不传 user_id：清除全部缓存
    - 传 user_id：只清除该用户的缓存
    """
    if not cache:
        raise HTTPException(status_code=503, detail="服务初始化中")
    cache.invalidate(user_id=user_id)
    return {"success": True, "cleared_user": user_id or "all"}


# ====================== 健康检查 ======================

@app.get("/health", tags=["系统"])
async def health():
    return {
        "status": "ok",
        "architecture": "single-agent + intent-classifier",
        "knowledge_chunks": kb.count() if kb else 0,
        "services": {
            "knowledge_base": kb is not None,
            "memory_manager": mm is not None,
            "intent_classifier": agent is not None,
            "knowledge_agent": knowledge_agent is not None,
        },
    }


# ====================== 前端页面 ======================

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    """返回前端聊天页面"""
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>前端未找到，请检查 static/index.html</h1>")


# ====================== 启动 ======================

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=APP_HOST,
        port=APP_PORT,
        reload=DEBUG,
        log_level="info",
    )
