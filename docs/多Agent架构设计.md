# 多Agent架构设计文档

> 日期：2026-05-26 | 版本：v4.0.0 | 状态：✅ 已实现

---

## 一、架构概览

系统采用三层 Agent 协同架构，职责分明、可扩展：

```
用户提问
    │
    ▼
┌─────────────────────────┐
│   主Agent (路由层)        │  ← src/agents/kb_agent.py  classify_intent()
│   关键词规则分类，零延迟    │     server.py 第 303-322 行调用
└────────┬────────────────┘
         │
   ┌─────┼──────────┬──────────┐
   ▼     ▼          ▼          ▼
greeting unknown  business  knowledge
 (硬编码) (硬编码)     │          │
                     ▼          ▼
               ┌──────────┐ ┌──────────────┐
               │业务Agent  │ │ 知识库Agent   │  ← src/agents/
               │biz_agent │ │ Customer-    │
               │(占位)    │ │ ServiceAgent │
               └──────────┘ └──────┬───────┘
                                   │
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
                  RAG检索      mem0记忆      LLM生成
               (src/agent.py (mem0_manager (call_ai_api
                + rag_store)   .py)         → 7种提供商)
```

| Agent | 触发条件 | 职责 | 状态 |
|-------|----------|------|------|
| **主Agent** | 所有请求 | 意图分类 + 路由分发。纯关键词规则，不调 LLM，零延迟、100% 可控 | ✅ |
| **知识库Agent** | knowledge 意图（默认） | RAG检索→多主题检测→mem0记忆注入→LLM生成→学习归档 | ✅ |
| **业务Agent** | business 意图 | 对接真实 API（充值/开卡/冻结等），当前占位返回"功能开发中" | 🚧 |

---

## 二、主Agent — 意图分类

### 2.1 设计思路

不调 LLM，纯关键词规则。分类数少、规则明确、零延迟、100% 可控。

### 2.2 分类规则（完整五类）

| 意图 | 判定词（中文） | 判定词（英文） | 处理方式 | 代码位置 |
|------|-------------|-------------|---------|---------|
| **greeting** | 你好、您好、谢谢、再见、拜拜、hello、hi、嗨、哈喽、bye、ok、好的 | hello、hi、bye、ok | `server.py` 直接返回硬编码 `"您好，有什么可以帮您？"`，不调 AI | `kb_agent.py:106` |
| **unknown** | 天气、比特币、股票、唱歌、讲笑话、预测 | weather、bitcoin、stock、joke、song | `server.py` 直接返回硬编码拒答引导语 | `kb_agent.py:98` |
| **business** | 充值、开卡、查余额、冻结、解冻、我要充值、我要开卡、帮我冻结、帮我解冻 | recharge、open card、freeze、unfreeze | **P2-01：临时路由到 `kb_agent.chat()`**（biz_agent 开发完成后切回） | `kb_agent.py:121` `server.py:327` |
| **knowledge** | 默认（以上均不匹配） | 默认 | 路由到 `kb_agent.py` CustomerServiceAgent，走完整 RAG+mem0+LLM 流程 | `kb_agent.py:110` |

> 注意：greeting 和 unknown 不走知识库 Agent，不调 LLM，不浪费 token。

### 2.3 函数签名与代码位置

```python
# 文件: src/agents/kb_agent.py (第 106-128 行)
# 调用位置: server.py (第 303 行)
def classify_intent(question: str) -> str:
    """返回: "greeting" | "unknown" | "business" | "knowledge" """
```

### 2.4 server.py 路由逻辑

```python
# server.py 第 310-346 行
intent = classify_intent(req.question)

if intent == "greeting":
    return AskResponse(answer="您好，有什么可以帮您？", method="greeting")

if intent == "unknown":
    return AskResponse(answer="不好意思，这个问题不在...", method="none")

if intent == "business":
    # P2-01: 临时路由到知识库 Agent（biz_agent 完成后切换回来）
    user_id = req.user_id or req.session_id or "default"
    result = await _get_agent().chat(
        question=req.question, user_id=user_id,
        session_id=req.session_id, call_ai=call_ai_api,
    )
    return AskResponse(**result)

# knowledge（默认）
user_id = req.user_id or req.session_id or "default"
result = await _get_agent().chat(
    question=req.question, user_id=user_id,
    session_id=req.session_id, call_ai=call_ai_api,
)
return AskResponse(**result)
```

---

## 三、知识库Agent — RAG + mem0 + LLM

### 3.1 设计思路

将问答逻辑抽取为独立模块 `src/agents/kb_agent.py` 中的 `CustomerServiceAgent` 类。核心流程六步走，每步独立可测试。

### 3.2 完整流程

```
用户问题
  │
  ├─ Step 0: 短业务追问检测 (kb_agent.py)
  │    充值/开卡/查余额/冻结/解冻（≤5字）→ 直接追问，跳过全部检索
  │
  ├─ Step 1: RAG 文档检索 (src/agent.py + src/rag_store.py)
  │    分类推断 → 英文短术语增强 → ChromaDB语义检索/TF-IDF降级
  │    → KYC硬规则提权 → 标题去重 → 多主题检测
  │
  ├─ Step 2: 多主题反问 (kb_agent.py)
  │    clarify==True 且 chunk 质量足够 → LLM 生成自然问句反问
  │
  ├─ Step 3: mem0 记忆检索 (mem0_manager.py)
  │    跨会话召回用户画像和历史对话
  │
  ├─ Step 4: 时间戳增强检索 (kb_agent.py)
  │    问题含"时间戳" → 同时检索 HMAC（毫秒）+ 查询接口（秒）并合并
  │
  ├─ Step 5: 上下文构建 (kb_agent.py)
  │    MAX_RAG_CHARS=3000 预算控制 → 组装 system_prompt + RAG + mem0 + 对话历史
  │
  ├─ Step 6: LLM 生成 (server.py call_ai_api)
  │    7 种提供商兼容 + reasoning 模型兼容 → 返回回答
  │
  └─ Step 7: 学习归档 (obsidian_writer.py + mem0_manager.py)
       写入日志 + 提炼经验模式 + mem0 记忆存储
```

### 3.3 关键实现细节

**RAG 检索（`src/agent.py`）**
- **分类推断**：根据关键词（KYC/开卡/充值/deposit/exchange 等）自动推断 `category`，传给 `search_docs()` 做预过滤
- **英文短术语增强**：KYC、API 等 ≤4 字母术语直接追加到搜索词，提升 TF-IDF 命中率
- **ChromaDB 语义检索优先**：有 chromadb 时先走向量检索（cosine 距离），降级 TF-IDF
- **category 预过滤**：ChromaDB `where` 过滤 + TF-IDF 先按 category 筛 chunks
- **KYC 硬规则提权**：KYC 相关问题强制插入核心接口块（`/api/v1/customers/accounts`）
- **同一标题只保留最高分（去重）**

**上下文预算控制（`src/agents/kb_agent.py`）**
```python
MAX_RAG_CHARS = 3000  # 约 1500 tokens

# 逐块拼接，超过预算就停止
for c in chunks:
    if total_len + len(part) > MAX_RAG_CHARS:
        break
    rag_text_parts.append(part)
# 首个 chunk 就超长？截断到 3000 字符强制加入
if not rag_text_parts:
    rag_text = first_part[:MAX_RAG_CHARS]
```

**短业务查询直接追问（`kb_agent.py` 第 198-214 行）**
- 命中充值/开卡/查余额/冻结/解冻 且问题长度 ≤5 字 → 直接返回追问
- 不触发 RAG 检索，不浪费 token，提升用户体验
- 追问示例：「您想了解关于「充值」的哪些信息？例如：- 如何操作 - 需要什么条件 - 多久到账」

**语言自适应（`kb_agent.py`）**
- 检测用户提问是否含中文 → 中文提问用中文 system prompt，英文提问用全英文 prompt
- user message 中也注入 `[IMPORTANT: You must answer in English...]` 双保险
- DeepSeek 模型有中文知识库强倾向，此为已知限制

**时间戳问题增强检索（`kb_agent.py` 第 280-292 行）**
- 问题含"时间戳"或"timestamp"时，同时检索两组关键词并合并去重
- `search_docs("HMAC 签名 时间戳 毫秒")` + `search_docs("查询接口 时间戳 秒 former_time latter_time")`
- 解决文档中认证用毫秒、查询接口用秒导致的回答矛盾问题

### 3.4 与旧版本的关键差异

| 项目 | 旧版本 (v3) | 当前版本 (v4) |
|------|-----------|-------------|
| 硬约束拒答 | RAG 分数 < 3.0 直接拒答 | 已移除，无检索结果也调 AI |
| 寒暄处理 | 被拒答 | classify_intent 识别为 greeting，服务端硬编码返回 |
| 短业务追问 | 无 | 充值/开卡等 ≤5 字直接追问，不检索 |
| 分类检索 | 无 | chunk 加 category 字段，ChromaDB where + TF-IDF 预过滤 |
| 模块位置 | agent.py 内联 | 独立 src/agents/kb_agent.py (CustomerServiceAgent 类) |
| 记忆系统 | mem0（API 调用失败） | mem0_manager（本地 fastembed，已修复） |
| 多主题处理 | 全量传给 AI | 先反问确认（P1-04 已实现，含 clarify_labels） |
| 上下文控制 | 无 | MAX_RAG_CHARS=3000 预算控制 |
| 英文术语增强 | 无 | KYC/API 等短术语直接追加搜索词 |
| reasoning 兼容 | 无 | content 为空时取 reasoning_content |
| 时间戳增强 | 无 | HMAC毫秒 + 查询秒 同时检索合并 |

---

## 四、业务Agent — 系统操作

### 4.1 设计思路

预留模块，后续对接真实的 API 进行充值、开卡、冻结等系统操作。

### 4.2 当前占位实现（`src/agents/biz_agent.py`）

```python
async def handle(question: str, session_id: str = "", **kwargs) -> dict:
    """业务 Agent：对接真实 API（充值/开卡/冻结等），当前占位"""
    return {
        "answer": "该功能正在开发中。如需充值或开卡，请登录管理后台操作。",
        "source": "biz_agent",
        "doc_hits": 0,
        "confidence": 1.0,
        "chunks_used": [],
        "method": "biz_agent",
    }
```

### 4.3 后续规划

- 充值操作：`POST /api/v1/mm/customer/asset/deposit`
- 开卡操作：`POST /api/v1/debit-cards`
- 账号查询：`GET /api/v1/customers/accounts/kyc`
- 冻结/解冻：`PUT /api/v1/debit-cards/freeze`

---

## 五、完整数据流

```
server.py  POST /api/ask
  │
  ├─ classify_intent(req.question)
  │     │
  │     ├─ "greeting"   →  AskResponse(answer="您好，有什么可以帮您？", method="greeting")
  │     ├─ "unknown"    →  AskResponse(answer="不好意思，这个问题不在...", method="none")
  │     ├─ "business"   →  await kb_agent.chat()（P2-01：临时路由到知识库Agent）
  │     │
  │     └─ "knowledge"  →  await kb_agent.chat(
  │                            question=req.question,
  │                            user_id=req.user_id or req.session_id or "default",
  │                            session_id=req.session_id,
  │                            call_ai=call_ai_api)
  │                           │
  │                           ├─ 0. 短业务追问检测
  │                           │        充值/开卡/查余额/冻结/解冻（≤5字）→ 直接追问返回
  │                           │
  │                           ├─ 1. search_docs(question)
  │                           │        ← src/agent.py (answer函数)
  │                           │        ├─ 分类推断（KYC→Card issuing, deposit→Native MM）
  │                           │        ├─ 英文短术语增强（KYC/API 追加到搜索词）
  │                           │        ├─ ChromaDB 语义检索 / TF-IDF 降级
  │                           │        ├─ KYC 硬规则提权
  │                           │        ├─ 标题去重
  │                           │        └─ 多主题检测（clarify_topics ≥ 2 且非具体提问）
  │                           │        ← src/rag_store.py (ChromaDB + TF-IDF + category过滤)
  │                           │
  │                           ├─ 2. 多主题反问（LLM 生成问句）
  │                           │        clarify==True → 调 LLM 生成 2-3 个中文问句
  │                           │        LLM 输出 "." 表示无相关内容 → 放过，继续正常流程
  │                           │
  │                           ├─ 3. mem_recall(question, user_id, top_k=2)
  │                           │        ← src/mem0_manager.py
  │                           │
  │                           ├─ 4. 时间戳增强检索（可选）
  │                           │        问题含"时间戳" → 同时检索 HMAC毫秒 + 查询接口秒
  │                           │
  │                           ├─ 5. RAG 上下文组装
  │                           │        MAX_RAG_CHARS=3000 预算控制
  │                           │        system_prompt + RAG + mem0 + 对话历史（最近6轮/12条）
  │                           │
  │                           ├─ 6. call_ai_api(messages)
  │                           │        ← server.py → LLM (7种提供商 + reasoning兼容)
  │                           │
  │                           └─ 7. learn(question, answer, confidence)
  │                                    + mem_remember(user_id, ...)
  │                                    ← src/obsidian_writer.py
  │                                    ← src/mem0_manager.py
  │
  └─ 返回 AskResponse
```

---

## 六、多主题反问（P1-04 · 已实现 ✅）

### 6.1 问题描述

用户提问时往往不精确。典型场景：

```
用户输入：「KYC」
```

系统检索到 10 个最相关的文档块，它们分布在两个完全不同的领域：

| 块 | title_path | 主题 |
|----|-----------|------|
| 块1 | `KYC — 搜索导入 > 来源 3` | KYC 概念说明（什么是KYC、为什么重要） |
| 块2 | `KYC — 搜索导入 > 来源 5` | KYC 概念说明 |
| 块3 | `Card issuing API 文档 > KYC > 提交用户 KYC 数据` | API 接口（怎么提交KYC） |
| 块4 | `Card issuing API 文档 > 错误码 > KYC失败错误码` | API 接口（KYC失败处理） |

**旧方案的问题**：将 10 个块全量传给 AI，AI 可能同时解释"KYC 是什么"和"怎么调 API"，用户既看不懂也找不到想要的。浪费了一次 AI 调用，还提供了不相关的信息。

### 6.2 设计原则

1. **LLM 生成反问内容**：检测到多主题后，将相关 chunks 交给 LLM，让它基于知识库内容生成自然问句（零硬编码模板）
2. **具体提问跳过反问**：含动作词（提交/需要/怎么/如何）时直接回答，不反问
3. **格式统一**：反问内容用 Markdown 列表格式，与正常回答格式一致
4. **不破坏现有流程**：反问结果和正常回答使用相同的 `AskResponse` 结构，前端无需任何改动

### 6.3 Agent 分工与协作流程

**参与模块**：知识库 Agent（`src/agents/kb_agent.py`）的 `CustomerServiceAgent.chat()` 方法 + 检索层（`src/agent.py`）的 `answer()` 函数。不涉及业务 Agent 和主 Agent（意图分类在本功能之前已完成）。

**协作时序**：

```
用户「KYC」
  │
  │  [1] server.py 意图分类 → "knowledge"
  │      主Agent完成路由，后续全由知识库Agent处理
  ▼
kb_agent.chat()
  │
  ├─ [2] search_docs(question)
  │      调用 src/agent.py 的 answer() 函数
  │      answer() 内部执行：
  │        ├─ get_chunks()         ← src/doc_loader.py (加载115块)
  │        ├─ search_docs()        ← src/rag_store.py (TF-IDF + ChromaDB 检索)
  │        ├─ KYC 硬规则提权       ← 检测 KYC 关键词，强制插入核心接口块
  │        ├─ 标题去重             ← 同标题只保留最高分
  │        └─ ★ 多主题检测         ← 提取 title_path 一级主题，≥2 则标记 clarify
  │      返回 { "clarify": True, "clarify_topics": [...] }
  │
  ├─ [3] 判断 clarify == True？
  │      ├─ 是 → 将 chunks 交给 LLM 生成自然问句
  │      │      prompt: "用户问了{question}。以下是知识库内容...请根据文档内容生成2-3个简短的中文问句"
  │      │      格式化：去编号、去横线、Markdown 列表、补问号
  │      │      返回 AskResponse(answer=反问, method="clarify")
  │      │
  │      └─ 否 → 继续正常流程
  │              [4] 预算控制 + 上下文构建
  │              [5] call_ai_api() → LLM 生成回答     ← LLM 只在这步参与
  │              [6] learn() + mem_remember()
  │
  └─ 返回 AskResponse
```

**各模块职责**：

| 模块 | 职责 | 是否涉及 LLM | 依赖 |
|------|------|------------|------|
| `src/agent.py` answer() | 检索 + KYC提权 + 去重 + 多主题检测 | ❌ 不涉及 | `src/doc_loader.py`（加载块）`src/rag_store.py`（TF-IDF/ChromaDB） |
| `src/agents/kb_agent.py` chat() | 接收 clarify 标记 → 调 LLM 生成问句 → 格式化返回 | ✅ 反问时调用 | `src/agent.py`（只调 answer() 看返回值） |
| `server.py` call_ai_api() | 多主题反问走不到这步 | ✅ 但反问不会触发 | DeepSeek API / OpenAI 兼容接口 |

**LLM 在反问流程中的作用：生成反问问句。**

检测到多主题后，`kb_agent.py` 将相关 chunks 交给 LLM，prompt 要求生成 2-3 个简短中文问句，再格式化为 Markdown 列表。LLM 调用在 `kb_agent.py` 第 210-240 行，失败时降级走正常 RAG 流程。

**反问的依赖链路**：

```
用户提问
  → server.py (classify_intent)          依赖: src/agents/kb_agent.py
  → kb_agent.chat()                       依赖: src/agent.py, src/mem0_manager.py, server.py(call_ai_api)
  → agent.answer()                        依赖: src/doc_loader.py, src/rag_store.py
  → rag_store.search_docs()              依赖: chromadb (可选), scikit-learn (TF-IDF)
  → 多主题检测 (tags提取)                 依赖: title_path 字段 (来自 doc_loader.py 切块时自动生成)
  → 反问文本组装 (kb_agent.chat())        依赖: 无外部依赖，纯字符串拼接
```

### 6.4 方案设计

**核心思路**：利用文档的 `title_path` 层级结构做主题聚类。

知识库的每个文档块都有一个 `title_path` 字段，用 ` > ` 分隔层级：

```
Card issuing API 文档 > KYC > 提交用户 KYC 数据   ← 一级主题 = "Card issuing API 文档"
KYC — 搜索导入 > 来源 3                          ← 一级主题 = "KYC — 搜索导入"
```

一级主题（按 ` > ` 分割取第一段）反映了文档的最高层分类。如果 Top 检索结果的一级主题数 ≥ 2，说明用户问题同时匹配了不同领域的文档，需要反问。

**为什么放在 `src/agent.py`？**

- 反问检测依赖检索结果（`title_path`），应该在检索完成后、上下文构建前执行
- 放在 `agent.py` 的 `answer()` 函数中，通过 `clarify` 字段传递给 `kb_agent.py`，职责清晰
- 不放在 `server.py` 的路由层——追问属于问答策略，不是意图分类问题

**为什么取前 8 个而不是全部 10 个？**

- 前 8 个已经足够代表性，后 2 个通常是低分噪声
- 减少变量，避免边缘情况（如第 10 个块偶然命中一个生僻主题就触发反问）

### 6.5 实现细节

**步骤 1：检索 + 去重后，提取一级主题（`src/agent.py` 第 170-265 行）**

```python
# 去重后的 hits 列表（同标题只保留最高分）
hits = deduped[:10]

# 提取每个命中块的一级主题
topics = []
for h in hits[:8]:
    title = h['chunk'].get('title_path', '')
    # 按 " > " 分割，取第一段作为一级主题
    top = title.split(' > ')[0].split(' / ')[0].strip()
    if top and top not in topics:
        topics.append(top)
        # 早停：主题数 ≥ 3 时无需继续遍历
        if len(topics) >= 3:
            break

# ≥ 2 个不同一级主题 → 标记为需要反问
if len(topics) >= 2:
    return {
        "clarify": True,
        "clarify_topics": topics,
    }
```

**为什么同时 split ' / '？** 有些文档用 `/` 而非 `>` 做层级分隔，双重分割保底。

**为什么设早停（topics ≥ 3）？** 反问最多列出 3 个选项，遍历到第 3 个就可以停了。

**步骤 2：`kb_agent.py` 处理反问（第 210-240 行）**

```python
doc_result = search_docs(question)

# 多主题反问：把相关 chunks 丢给 LLM，让它自己生成问句
if doc_result.get("clarify") and call_ai:
    chunks = doc_result.get("chunks_used", [])
    chunk_texts = []
    for c in chunks[:5]:
        title = c.get("title_path", "").split(" > ")[-1]
        content = c.get("content", "")[:500]
        chunk_texts.append(f"【{title}】\n{content}")
    
    prompt = (
        f'用户问了"{question}"。以下是知识库中可能相关的文档内容：\n\n'
        + "\n\n".join(chunk_texts)
        + f'\n\n请根据文档内容生成2-3个简短的中文问句，帮助用户确认想了解什么。'
          f'只列出问句，一行一个，不加编号。'
          f'即使文档与问题不完全匹配，也基于已有内容生成最可能的问句。'
    )
    try:
        answer, _ = await call_ai(messages=[{"role": "user", "content": prompt}])
        # 格式化：去编号、去横线，统一用 Markdown 列表
        raw_lines = [q.strip("- 1234567890. *") for q in answer.strip().split("\n") if q.strip()]
        # 去重、去空，确保每条末尾有问号
        questions = []
        for q in raw_lines:
            if q not in questions:
                if not q.endswith(("？", "?")):
                    q += "？"
                questions.append(q)
        clarify_msg = "关于" + question + "，您是想了解以下哪方面？\n\n" + "\n".join(f"- {q}" for q in questions) + "\n\n您具体想了解哪个？"
        clarify_msg = normalize_answer(clarify_msg)
        return {
            "answer": clarify_msg,
            "source": "ai",
            "doc_hits": doc_result["doc_hits"],
            "confidence": 0.99,
            "chunks_used": chunks,
            "method": "clarify",
        }
    except Exception:
        pass  # LLM 调用失败，继续走正常 RAG 流程
```

**关键设计点**：

1. **LLM 生成反问内容**：反问内容由 LLM 基于知识库 chunk 实时生成，零硬编码模板
2. **去重、去空、补问号**：确保问句不重复、末尾有问号
3. **Markdown 列表格式**：反问选项用 `- 问句？` 格式，与正常回答格式一致
4. **normalize_answer()**：统一压缩多余空行，确保格式紧凑
5. **LLM 调用失败时降级**：`except Exception: pass` 继续走正常 RAG 流程，不中断服务
6. **返回后终止流程**：不再进入步骤 4-6（上下文构建、AI 调用、学习归档）

### 6.6 效果对比

**修复前（直接调 AI）**：
```
用户：KYC
系统：KYC 是 Know Your Customer 的缩写...（解释 KYC 概念）
      全球银行每年在 KYC 合规上的投入超过 16 亿美元...
      KYC 接口地址为 POST /api/v1/customers/accounts...（跳到 API 接口）
      （消耗一次 AI 调用，返回了用户可能不想要的混合内容）
```

**修复后（反问）**：
```
用户：KYC
系统：关于KYC，您是想了解以下哪方面？
      - 如何提交用户KYC数据？
      - KYC审核失败后如何重新提交？
      - 什么是Central KYC？
      
      您具体想了解哪个？
      （LLM 基于知识库生成，零硬编码，格式统一Markdown列表）
```

### 6.7 上下文预算控制

检索反问和 AI 生成共享同一套 RAG 上下文预算保护。无论原始文档多大，传入 LLM 的 RAG 上下文始终受三层限制：

| 层级 | 限制 | 目的 | 代码位置 |
|------|------|------|---------|
| **文档切块** | 每块 ≤ 1200 字符 | 防止单个章节过长，确保每次检索粒度均匀 | `src/doc_loader.py:88` |
| **内容截断** | 每块内容 ≤ 1000 字符 | 即使切块有超长残留，最终的 chunk 内容也被硬截断 | `src/agent.py:205` |
| **上下文总预算** | ≤ 3000 字符（≈ 1500 tokens） | 最终传给 LLM 的 RAG 文本总长度上限 | `src/agents/kb_agent.py:42` |

**三层设计的原因**：

- **第一层（切块）** 控制源头——文档切得细，检索才能精准
- **第二层（截断）** 做兜底——即使切块逻辑有漏洞，chunk 输出也被限制
- **第三层（总预算）** 做终裁——无论检索到多少块、每块多大，最终拼装时强制 stop

三层的存在独立于多主题反问。反问发生在步骤 3（检索后），预算控制发生在步骤 4（构建上下文时），如果反问触发则不进入步骤 4。

---

## 七、改动清单（v3 → v4）

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/agents/__init__.py` | 新增 | 包初始化 |
| `src/agents/kb_agent.py` | 新增 | 从 agent.py 提取 RAG+AI 逻辑，增加 max_chars/短业务追问/时间戳增强 |
| `src/agents/biz_agent.py` | 新增 | 业务Agent占位 |
| `server.py` | 修改 | 导入 classify_intent()，重构 /api/ask 路由；P2-01 business 临时路由到 kb_agent；reasoning 模型兼容 |
| `src/agent.py` | 修改 | 检索函数 + 分类推断 + 英文短术语增强 + 多主题反问检测（含 clarify_labels） |
| `src/rag_store.py` | 修改 | TF-IDF + ChromaDB 检索引擎 + category 过滤 + ChromaDB 激活 |
| `src/doc_loader.py` | 修改 | Markdown 文档加载切块 + category 字段自动提取 |
| `src/obsidian_writer.py` | 不变 | 问答日志与经验模式 |
| `src/mem0_manager.py` | 激活 | mem0 语义记忆（本地 fastembed） |
| `src/simple_memory.py` | 备选 | 降级方案（sentence-transformers） |
| `chroma_viewer.py` | 新增 | ChromaDB 查看脚本（Python CLI） |

---

## 八、已修复的问题

| 问题 | 修复方式 | 状态 |
|------|---------|------|
| BUG-01 寒暄被拒答 | classify_intent 识别为 greeting，server.py 硬编码返回问候 | ✅ |
| BUG-02 时间戳单位矛盾 | system_prompt 强制区分毫秒（HMAC认证）vs 秒（查询接口）+ 时间戳增强检索同时召回两类 | ✅ |
| BUG-03 mem0 未生效 | 切回 mem0_manager.py（本地 fastembed），已验证跨对话记忆召回正常 | ✅ |
| P1-04 多主题未反问 | src/agent.py 主题聚类 + clarify_labels 生成 + kb_agent.py 反问返回 | ✅ |
| BUG-04 上下文无预算 | MAX_RAG_CHARS=3000 + chunk 截断 + 超长强制截断 | ✅ |
| BUG-05 API base_url 缺 /v1 | api_config.json base_url 改为 `https://aihubmix.com/v1`，server.py 错误处理加 try/except | ✅ |
| BUG-06 短业务词误分类 | classify_intent 补全短词（充值/开卡/查余额/冻结/解冻）+ kb_agent.py 开头加短业务直接追问 | ✅ |
| BUG-07 reasoning 模型 content 为空 | call_ai_api() content 为空时取 reasoning_content（glm-5 兼容） | ✅ |
| P2-01 biz_agent 空壳 | server.py business 意图临时路由到 kb_agent.chat()（文档里有业务说明） | ✅ |

---

## 九、扩展性

- **新增Agent**：只需在 `src/agents/` 目录新建文件，加一条路由规则
- **业务Agent对接**：`src/agents/biz_agent.py` 替换占位代码即可
- **意图分类升级**：后续可换 LLM 分类，不影响 Agent 接口
- **记忆系统切换**：mem0_manager ↔ simple_memory 只需改一行 import
