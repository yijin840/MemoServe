# mem0-demo 生产环境部署方案

> 基于当前代码现状，面向生产环境需要补充的配置和改造
> 目标：不乱答、知识库可热加载、记忆持久化、用户身份可靠识别

---

## 一、保证不乱回答问题

### 1.1 提高 RAG 阈值（必须）

`.env` 配置：

```bash
# 生产环境建议 0.6~0.7（当前 0.4 太低）
RAG_SCORE_THRESHOLD=0.65

# mem0 记忆检索阈值也适当提高
MEM0_SCORE_THRESHOLD=0.4
```

### 1.2 答案真实性校验层（新增）

在 `knowledge_agent.py` 的 `chat()` 方法中，LLM 返回答案后增加校验：

```python
# 在获得 answer 之后、返回之前，加入：

        # ★ 生产加固：答案真实性校验
        if update_memory and rag_results:
            ground_score = _calc_grounding_score(answer, rag_results)
            if ground_score < 0.5:
                logger.warning(
                    f"[生产告警] 答案可能未基于知识库 | "
                    f"ground_score={ground_score:.2f} | query='{user_input[:40]}'"
                )
                # 低置信度：转人工或返回保守答案
                # answer = "您的问题需要人工确认，请稍候联系人工客服。"

def _calc_grounding_score(answer: str, rag_results: list[dict]) -> float:
    """简单计算答案与 RAG 内容的关联度（0~1）"""
    rag_text = " ".join([r["text"] for r in rag_results[:3]])
    if not rag_text or not answer:
        return 0.0
    # 检查答案是否包含 RAG 内容中的关键片段（至少 10 字重叠）
    for r in rag_results[:3]:
        if len(r["text"]) > 20:
            snippet = r["text"][:50]
            if snippet in answer:
                return 1.0
    return 0.3
```

### 1.3 拒答日志 + 人工介入接口（新增）

```python
# config.py 新增
ENABLE_HUMAN_HANDOFF = os.getenv("ENABLE_HUMAN_HANDOFF", "false").lower() == "true"
HANDOFF_THRESHOLD = float(os.getenv("HANDOFF_THRESHOLD", "0.5"))  # 置信度低于此值转人工
```

当 RAG 最高分 < `HANDOFF_THRESHOLD` 时，返回：
> "您的问题已转交人工客服，请稍候..."

并在 `/memory/handoff` 接口记录待处理队列。

---

## 二、动态加载知识库（当前已支持，需补充管理接口）

### 2.1 现状

✅ `/knowledge/add` — 热加载文本  
✅ `/knowledge/file` — 热加载文件（.md/.txt/.pdf/.docx）  
✅ ChromaDB 持久化，写入后立即可检索  

**无需改造即可动态加载。**

### 2.2 建议补充：知识库版本管理

```python
# 新增 /knowledge/version 接口
# 每次 add_file 时记录版本，支持回滚

@ app.post("/knowledge/version/rollback", tags=["知识库"])
async def rollback_knowledge(version_id: str):
    """回滚到指定版本（从备份 ChromaDB 恢复）"""
    pass
```

### 2.3 建议补充：知识库刷新接口

```python

@ app.post("/knowledge/reload", tags=["知识库"])
async def reload_knowledge():
    """重新加载整个知识库（从原始文件重新向量化）"""
    pass
```

---

## 三、持续的记忆（当前基本可用，需修复一个 bug）

### 3.1 现状

| 功能 | 状态 | 说明 |
|---|---|---|
| mem0 用户画像 | ✅ 持久化 | ChromaDB 存储，重启不丢失 |
| 对话存档 | ✅ 持久化 | `conv_archive` collection |
| `GROUP_USER_MAP` | ❌ 仅内存 | **需修复**，见下文 |
| 流式对话记忆 | ❌ 不保存 | `stream_chat()` 无记忆写入 |

### 3.2 修复：`GROUP_USER_MAP` 持久化

`telegram_bot.py` 第 31 行，改为 JSON 文件读写：

```python
import json, os

GROUP_MAP_FILE = os.path.join(os.path.dirname(__file__), "data", "telegram_group_map.json")

def _load_group_map():
    if os.path.exists(GROUP_MAP_FILE):
        with open(GROUP_MAP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def _save_group_map():
    os.makedirs(os.path.dirname(GROUP_MAP_FILE), exist_ok=True)
    with open(GROUP_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(GROUP_USER_MAP, f, ensure_ascii=False, indent=2)

# 启动时加载
GROUP_USER_MAP: dict[str, str] = _load_group_map()
```

在 `cmd_setid` 和 `cmd_resetid` 中调用 `_save_group_map()`。

### 3.3 修复：流式对话记忆

`knowledge_agent.py` 的 `stream_chat()` 方法末尾，补充：

```python
        # 流式对话也存档（如果答案足够长）
        if len(full_answer) >= 20:
            conv_text = (
                f"【{time.strftime('%Y-%m-%d %H:%M')}】\n"
                f"用户：{user_input}\n"
                f"助手：{full_answer[:300]}{'...' if len(full_answer) > 300 else ''}"
            )
            try:
                self.mm.add_memory_direct(
                    content=conv_text,
                    user_id=user_id,
                    category="对话记录",
                )
            except Exception as e:
                logger.warning(f"[流式存档失败] {e}")
```

---

## 四、每个用户做识别（必须新增认证）

### 4.1 当前最大安全隐患

**`/chat` 接口的 `user_id` 是前端随意传的，没有任何认证。**
用户 A 可以传 `user_id=user_B` 查看用户 B 的记忆和对话记录。

### 4.2 方案：JWT Token 认证

```python
# ========== 新增 auth.py ==========
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from datetime import datetime, timedelta

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-in-production!")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 小时

security = HTTPBearer()

def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(cred: HTTPAuthorizationCredentials = Depends(security)) -> str:
    try:
        payload = jwt.decode(cred.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise JWTError()
        return user_id
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败")

# ========== main.py 改造 ==========
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    # user_id 不再由前端传，而从 JWT token 解析
    history: list[dict] = []

@app.post("/chat", response_model=ChatResponse, tags=["对话"])
async def chat(req: ChatRequest, user_id: str = Depends(get_current_user)):
    # user_id 现在从 JWT token 解析，前端无法伪造
    ...

@app.post("/auth/login", tags=["认证"])
async def login(user_id: str = Form(...), password: str = Form(...)):
    """简单登录（生产建议接入 OAuth2 / 企业微信 / LDAP）"""
    # 这里简化为密码校验，实际应接入企业认证系统
    if not _verify_user(user_id, password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user_id)
    return {"access_token": token, "token_type": "bearer"}
```

### 4.3 Telegram 端用户识别（已可靠）

Telegram 端已经用 `telegram_{user.id}` 作为 user_id，这个是 Telegram 平台保证的，无法伪造，无需额外改造。

---

## 五、生产环境配置清单（.env）

```bash
# ========== 必须修改 ==========
DASHSCOPE_API_KEY=sk-your-real-key-here

# RAG 阈值调高（生产建议 0.6~0.7）
RAG_SCORE_THRESHOLD=0.65
MEM0_SCORE_THRESHOLD=0.4

# JWT 密钥（必须修改！）
JWT_SECRET_KEY=your-random-secret-key-here

# ========== 建议启用 ==========
# 启用回答缓存（大幅降低延迟和成本）
# 取消 knowledge_agent.py 222-228 行和 381-389 行的注释

# 缓存 TTL（秒）
CACHE_TTL=3600

# ========== 可选 ==========
# 人工介入开关
ENABLE_HUMAN_HANDOFF=false
HANDOFF_THRESHOLD=0.5

# 日志级别（生产建议 WARNING）
LOG_LEVEL=INFO
```

---

## 六、生产部署优先级总结

| 优先级 | 项目 | 工作量 |
|---|---|---|
| 🔴 P0 | JWT 认证（`user_id` 安全） | 新增 auth.py + 改 main.py |
| 🔴 P0 | 提高 `RAG_SCORE_THRESHOLD` 到 0.65 | 改 .env |
| 🟡 P1 | 启用 answer_cache | 取消 knowledge_agent.py 注释 |
| 🟡 P1 | `GROUP_USER_MAP` 持久化 | 改 telegram_bot.py |
| 🟡 P1 | 流式对话记忆保存 | 改 knowledge_agent.py |
| 🟢 P2 | 答案真实性校验 | 新增 grounding 逻辑 |
| 🟢 P2 | 人工介入接口 | 新增 /memory/handoff 接口 |
| 🟢 P2 | 知识库版本管理 | 新增接口 |
