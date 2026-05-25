"""
server.py  (v3 — 完整 AI API 配置版)
=====================================

支持多种 AI API 提供商：
- OpenAI (GPT-4o, GPT-4o-mini, etc.)
- Anthropic (Claude-3.5, Claude-3, etc.)
- 硅基流动 (SiliconFlow)
- DeepSeek
- 通义千问 (Qwen)
- 智谱 GLM
- 自定义 OpenAI 兼容 API
"""

import os
import json
from pathlib import Path
from typing import Optional, Literal
from fastapi import FastAPI, HTTPException, UploadFile, File as FastAPIFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

from agent import generate_summary, startup as agent_startup, DSPY_AVAILABLE
from doc_loader import get_chunks, refresh_docs, import_doc, remove_doc, reset_docs, upload_doc, get_all_sources
from obsidian_writer import get_all_patterns, delete_pattern, VAULT_ROOT
from rag_store import CHROMA_OK
from web_crawler import crawl_and_import

app = FastAPI(title="API Doc QA Bot", version="3.0.0")

# 知识库Agent 单例
_kb_agent = None


def _get_agent():
    global _kb_agent
    if _kb_agent is None:
        from agents.kb_agent import CustomerServiceAgent
        _kb_agent = CustomerServiceAgent()
    return _kb_agent

# ─────────────────────────────────────────────
# AI API 配置管理
# ─────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "api_config.json"

# 支持的 API 提供商
PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        "base_url": "https://api.openai.com/v1",
        "needs_org": False,
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "default_model": "claude-3-5-sonnet-20240620",
        "models": ["claude-3-5-sonnet-20240620", "claude-3-opus-20240229",
                   "claude-3-sonnet-20240229", "claude-3-haiku-20240307"],
        "base_url": "https://api.anthropic.com/v1",
        "needs_org": False,
    },
    "siliconflow": {
        "name": "硅基流动 (SiliconFlow)",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
        "models": ["Qwen/Qwen2.5-7B-Instruct", "deepseek-ai/DeepSeek-V2.5",
                   "THUDM/glm-4-9b-chat", "Yi/Yi-1.5-9B-Chat"],
        "base_url": "https://api.siliconflow.cn/v1",
        "needs_org": False,
    },
    "deepseek": {
        "name": "DeepSeek",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-coder"],
        "base_url": "https://api.deepseek.com/v1",
        "needs_org": False,
    },
    "qwen": {
        "name": "通义千问 (Qwen)",
        "default_model": "qwen-plus",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max", "qwen-long"],
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "needs_org": True,
    },
    "zhipu": {
        "name": "智谱 GLM",
        "default_model": "glm-4-flash",
        "models": ["glm-4-flash", "glm-4", "glm-4-plus", "glm-4v"],
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "needs_org": False,
    },
    "custom": {
        "name": "自定义 (OpenAI 兼容)",
        "default_model": "gpt-3.5-turbo",
        "models": [],
        "base_url": "",
        "needs_org": False,
    },
}

# 默认配置
DEFAULT_CONFIG = {
    "provider": "openai",
    "api_key": "",
    "base_url": "",
    "model": "gpt-4o-mini",
    "temperature": 0.7,
    "max_tokens": 2048,
    "top_p": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "enabled": False,  # 是否启用 AI 增强
    "system_prompt": "你是专业的 API 技术支持助手。回答简洁精确，基于文档内容回答。",
}


def load_config() -> dict:
    """加载配置文件"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> dict:
    """保存配置文件"""
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def get_active_config() -> dict:
    """获取当前激活的配置"""
    config = load_config()
    
    # 如果使用预设提供商，填充 base_url
    if config["provider"] != "custom" and not config.get("base_url"):
        config["base_url"] = PROVIDERS.get(config["provider"], {}).get("base_url", "")
    
    return config


# ─────────────────────────────────────────────
# Pydantic 模型
# ─────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str
    session_id: str = "default"
    # 用户身份标识（预留）
    # 当前阶段：可选，未传时降级为 session_id
    # 生产阶段：应从 JWT/Session 中解析，不应由前端随意传入
    user_id: Optional[str] = None


class AskResponse(BaseModel):
    answer: str
    source: str
    doc_hits: int
    confidence: float
    chunks_used: list
    method: str


class ConfigUpdate(BaseModel):
    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=100, le=32000)
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    enabled: Optional[bool] = None
    system_prompt: Optional[str] = None


class TestRequest(BaseModel):
    provider: str
    api_key: str
    base_url: str = ""
    model: str


class TestResponse(BaseModel):
    success: bool
    message: str
    latency_ms: Optional[float] = None
    model_response: Optional[str] = None


# ─────────────────────────────────────────────
# AI 对话接口
# ─────────────────────────────────────────────

def build_messages(question: str, system_prompt: str = "") -> list:
    """构建对话消息"""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": question})
    return messages


async def call_ai_api(messages: list = None, question: str = "") -> tuple[str, float]:
    """
    调用 AI API
    支持两种调用方式：
      1. messages=list[dict]  → 直接使用（推荐，保留 system/ user 角色）
      2. question=str          → 兼容旧调用，自动构建 messages
    返回 (回答内容, 延迟毫秒数)
    """
    import httpx
    import time

    config = get_active_config()

    if not config.get("api_key"):
        return "❌ 未配置 API Key，请在设置中配置", 0

    headers = {"Authorization": f"Bearer {config['api_key']}"}

    # 构建 messages（优先使用传入的 messages）
    if messages and isinstance(messages, list):
        use_messages = messages
    else:
        use_messages = build_messages(question, config.get("system_prompt", ""))

    # 根据提供商构建请求
    if config["provider"] == "anthropic":
        headers["x-api-key"] = headers.pop("Authorization")
        headers["anthropic-version"] = "2023-06-01"
        # anthropic 不支持多轮 messages，只取最后一条 user message
        user_text = next((m["content"] for m in reversed(use_messages) if m["role"] == "user"), question)
        payload = {
            "model": config["model"],
            "max_tokens": config.get("max_tokens", 2048),
            "messages": [{"role": "user", "content": user_text}],
            "temperature": config.get("temperature", 0.7),
        }
        url = f"{config.get('base_url', PROVIDERS['anthropic']['base_url'])}/messages"
        method = "POST"
    else:
        # OpenAI 兼容格式
        base = config.get("base_url", PROVIDERS.get(config["provider"], {}).get("base_url", ""))
        if not base:
            base = PROVIDERS["openai"]["base_url"]
        url = f"{base}/chat/completions"
        payload = {
            "model": config["model"],
            "messages": use_messages,
            "temperature": config.get("temperature", 0.7),
            "max_tokens": config.get("max_tokens", 2048),
        }
        method = "POST"

    start = time.time()

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.request(method, url, headers=headers, json=payload)
            latency = (time.time() - start) * 1000

            if resp.status_code != 200:
                error_detail = resp.json().get("error", {}).get("message", resp.text)
                return f"❌ API 错误 ({resp.status_code}): {error_detail}", latency

            data = resp.json()

            # 解析响应
            if config["provider"] == "anthropic":
                answer = data["content"][0]["text"]
            else:
                answer = data["choices"][0]["message"]["content"]

            return answer, latency

    except httpx.TimeoutException:
        return "❌ 请求超时，请检查网络连接", 0
    except Exception as e:
        return f"❌ 调用失败: {str(e)}", 0

@app.post("/api/ask", response_model=AskResponse)
async def api_ask(req: AskRequest):
    """多Agent路由：意图分类 → 分发到子Agent

    user_id 提取优先级（为未来生产环境预留）：
      1. req.user_id       → 前端已登录用户（未来从 JWT 解析）
      2. req.session_id    → 当前匿名会话标识
      3. "default"         → 兜底
    """
    config = get_active_config()
    if not config.get("enabled") or not config.get("api_key"):
        return AskResponse(answer="未配置 AI", source="error", doc_hits=0, confidence=0, chunks_used=[], method="none")

    from agents.kb_agent import classify_intent
    from agents.biz_agent import handle as biz_handle

    intent = classify_intent(req.question)

    if intent == "greeting":
        return AskResponse(
            answer="您好，有什么可以帮您？",
            source="ai", doc_hits=0, confidence=1.0, chunks_used=[], method="greeting",
        )

    if intent == "info_provide":
        return AskResponse(
            answer=f"收到，{req.question}。请问接下来需要我协助什么？",
            source="confirm", doc_hits=0, confidence=1.0, chunks_used=[], method="confirm",
        )

    if intent == "unknown":
        return AskResponse(
            answer="不好意思，这个问题不在我的服务范围内。您可以试试问 KYC 认证、充提币、发卡、HMAC 签名等问题。",
            source="ai", doc_hits=0, confidence=0, chunks_used=[], method="none",
        )

    if intent == "business":
        result = await biz_handle(req.question)
        return AskResponse(**result)

    # 知识库Agent：传入 user_id（用于记忆隔离）和 session_id（用于对话历史）
    user_id = req.user_id or req.session_id or "default"
    result = await _get_agent().chat(
        question=req.question,
        user_id=user_id,
        session_id=req.session_id,
        call_ai=call_ai_api,
    )
    return AskResponse(**result)


# ─────────────────────────────────────────────
# 配置接口
# ─────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    """获取当前配置"""
    config = get_active_config()
    # 隐藏 API Key 前几位
    if config.get("api_key"):
        key = config["api_key"]
        config["api_key"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "****"
    config["providers"] = PROVIDERS
    return config


@app.post("/api/config")
async def update_config(update: ConfigUpdate):
    """更新配置"""
    config = load_config()
    
    for field, value in update.model_dump(exclude_none=True).items():
        if field == "api_key" and value:
            # 不允许设置为 **** 或空
            if value and not value.startswith("****"):
                config[field] = value
        elif value is not None:
            config[field] = value
    
    # 如果选择了预设提供商，自动填充 base_url
    if update.provider and update.provider != "custom":
        config["base_url"] = PROVIDERS.get(update.provider, {}).get("base_url", "")
        # 自动选择默认模型
        if not update.model:
            config["model"] = PROVIDERS.get(update.provider, {}).get("default_model", "")
    
    save_config(config)
    
    return {"success": True, "message": "配置已保存", "config": get_active_config()}


@app.post("/api/config/test")
async def test_config(req: TestRequest):
    """测试 API 连接"""
    import httpx
    import time
    
    try:
        headers = {"Authorization": f"Bearer {req.api_key}"}
        
        if req.provider == "anthropic":
            headers["x-api-key"] = headers.pop("Authorization")
            headers["anthropic-version"] = "2023-06-01"
            payload = {
                "model": req.model,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Hi"}],
            }
            url = f"{req.base_url}/messages"
        else:
            url = f"{req.base_url}/chat/completions"
            payload = {
                "model": req.model,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 100,
            }
        
        start = time.time()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request("POST", url, headers=headers, json=payload)
            latency = (time.time() - start) * 1000
        
        if resp.status_code == 200:
            return TestResponse(
                success=True,
                message="连接成功!",
                latency_ms=round(latency, 2),
            )
        else:
            error = resp.json().get("error", {}).get("message", resp.text)
            return TestResponse(success=False, message=f"错误: {error}")
            
    except Exception as e:
        return TestResponse(success=False, message=f"连接失败: {str(e)}")


# ─────────────────────────────────────────────
# 文档接口
# ─────────────────────────────────────────────

@app.get("/api/docs/chunks")
async def get_doc_chunks(source: str = "", q: str = ""):
    chunks = get_chunks()
    if source:
        chunks = [c for c in chunks if source.lower() in c["source"].lower()]
    if q:
        chunks = [c for c in chunks
                  if q.lower() in c["title_path"].lower() or q.lower() in c["content"].lower()]
    return {"chunks": chunks[:50], "total": len(chunks)}


@app.post("/api/docs/refresh")
async def refresh_documents():
    count = refresh_docs()
    return {"count": count, "message": f"文档已刷新，共 {count} 个块"}


@app.get("/api/docs/sources")
async def list_sources():
    chunks = get_chunks()
    sources = {}
    for c in chunks:
        s = c["source"]
        sources[s] = sources.get(s, 0) + 1
    return {"sources": [{"name": k, "chunks": v} for k, v in sources.items()]}


class ImportRequest(BaseModel):
    name: str
    url: str


@app.post("/api/docs/import")
async def import_document(req: ImportRequest):
    """导入新文档（URL 下载 → 切块 → 入库）"""
    result = import_doc(req.name, req.url)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/docs/upload")
async def upload_document(file: UploadFile = FastAPIFile(...)):
    """上传本地 Markdown 文件 → 切块 → 入库"""
    name = file.filename.rsplit(".", 1)[0] if file.filename else "uploaded"
    content = (await file.read()).decode("utf-8")
    result = upload_doc(name, content)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class CrawlRequest(BaseModel):
    keyword: str


@app.post("/api/docs/crawl")
async def crawl_document(req: CrawlRequest):
    """搜索导入：关键词 → 爬虫抓取 → 切块入库"""
    result = crawl_and_import(req.keyword)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.delete("/api/docs/remove")
async def remove_document(name: str):
    """删除自定义文档源"""
    result = remove_doc(name)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.delete("/api/docs/reset")
async def reset_documents():
    """清空所有自定义文档，恢复默认"""
    count = reset_docs()
    return {"chunks": count, "message": f"已恢复默认文档，共 {count} 个块"}


# ─────────────────────────────────────────────
# 经验库接口
# ─────────────────────────────────────────────

@app.get("/api/patterns")
async def get_patterns():
    patterns = get_all_patterns()
    return {"patterns": patterns, "total": len(patterns)}


@app.post("/api/summary")
async def trigger_summary():
    summary = generate_summary()
    return {"summary": summary, "success": True}


@app.delete("/api/patterns/{pattern_id}")
async def remove_pattern(pattern_id: str):
    """删除指定经验模式"""
    ok = delete_pattern(pattern_id)
    if not ok:
        raise HTTPException(404, "模式不存在")
    return {"success": True, "deleted": pattern_id}


# ─────────────────────────────────────────────
# Vault 文件浏览
# ─────────────────────────────────────────────

@app.get("/api/vault/files")
async def list_vault_files():
    files = []
    for folder in ["qa_logs", "summaries", "patterns"]:
        d = VAULT_ROOT / folder
        if d.exists():
            for f in sorted(d.iterdir()):
                if f.suffix in (".md", ".json"):
                    files.append({"folder": folder, "name": f.name, "size": f.stat().st_size})
    return {"files": files}


@app.get("/api/vault/read")
async def read_vault_file(folder: str, name: str):
    path = VAULT_ROOT / folder / name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return {"content": path.read_text(encoding="utf-8"), "name": name}


@app.delete("/api/vault/delete")
async def delete_vault_file(folder: str, name: str):
    """删除 Vault 中的文件"""
    path = VAULT_ROOT / folder / name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    try:
        path.unlink()
        return {"success": True, "deleted": f"{folder}/{name}"}
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")


# ─────────────────────────────────────────────
# 系统状态
# ─────────────────────────────────────────────

@app.get("/api/health")
async def health():
    chunks = get_chunks()
    patterns = get_all_patterns()
    config = get_active_config()
    return {
        "status": "ok",
        "dspy_enabled": DSPY_AVAILABLE,
        "rag_enabled": CHROMA_OK,
        "doc_chunks": len(chunks),
        "patterns_count": len(patterns),
        "ai_enabled": config.get("enabled", False),
        "version": "4.0.0",
    }


# ─────────────────────────────────────────────
# 静态前端
# ─────────────────────────────────────────────

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def index():
    f = os.path.join(static_dir, "index.html")
    return FileResponse(f) if os.path.exists(f) else JSONResponse({"msg": "前端未找到"})


# ─────────────────────────────────────────────
# 启动
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    
    # 初始化 Agent
    agent_startup()
    
    print("=" * 50)
    print("🚀 API Doc QA Bot v3.0 启动...")
    print("📡 访问: http://localhost:8000")
    print("=" * 50)
    
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
