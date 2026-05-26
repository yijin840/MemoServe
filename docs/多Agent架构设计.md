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
| **greeting** | 你好、您好、谢谢、再见、拜拜、hello、hi、嗨、哈喽、bye、ok、好的 | hello、hi、bye、ok | `server.py` 直接返回硬编码 `"您好，有什么可以帮您？"`，不调 AI | `kb_agent.py:88` |
| **unknown** | 天气、比特币、股票、唱歌、讲笑话、预测 | weather、bitcoin、stock、joke、song | `server.py` 直接返回硬编码拒答引导语 | `kb_agent.py:98` |
| **business** | 我要充值、我要开卡、查余额、帮我冻结、帮我解冻 | recharge、open card、freeze | 路由到 `biz_agent.py`，当前返回占位"功能开发中" | `kb_agent.py:104` `server.py:320` |
| **knowledge** | 默认（以上均不匹配） | 默认 | 路由到 `kb_agent.py` CustomerServiceAgent，走完整 RAG+mem0+LLM 流程 | `kb_agent.py:110` |

> 注意：greeting 和 unknown 不走知识库 Agent，不调 LLM，不浪费 token。

### 2.3 函数签名与代码位置

```python
# 文件: src/agents/kb_agent.py (第 85-110 行)
# 调用位置: server.py (第 303 行)
def classify_intent(question: str) -> str:
    """返回: "greeting" | "unknown" | "business" | "knowledge" """
```

### 2.4 server.py 路由逻辑

```python
# server.py 第 306-338 行
intent = classify_intent(req.question)

if intent == "greeting":
    return AskResponse(answer="您好，有什么可以帮您？", method="greeting")

if intent == "unknown":
    return AskResponse(answer="不好意思，这个问题不在...", method="none")

if intent == "business":
    return await biz_agent.handle(req.question)

# knowledge（默认）
result = await kb_agent.chat(question=req.question, user_id=user_id,
                              session_id=req.session_id, call_ai=call_ai_api)
```

---

## 三、知识库Agent — RAG + mem0 + LLM

### 3.1 设计思路

将问答逻辑抽取为独立模块 `src/agents/kb_agent.py` 中的 `CustomerServiceAgent` 类。核心流程六步走，每步独立可测试。

### 3.2 完整流程

```
用户问题
  │
  ├─ Step 1: mem0 记忆检索 (mem0_manager.py)
  │    跨会话召回用户画像和历史对话
  │
  ├─ Step 2: RAG 文档检索 (src/agent.py + src/rag_store.py)
  │    关键词增强 → TF-IDF/CromaDB检索 → KYC硬规则提权 → 标题去重
  │
  ├─ Step 3: 多主题反问检测 (src/agent.py)
  │    一级主题 ≥ 2 个？ → 反问用户确认（不调 AI）
  │
  ├─ Step 4: 上下文构建 (kb_agent.py)
  │    MAX_RAG_CHARS=3000 预算控制 → 组装 system_prompt + RAG + mem0 + 对话历史
  │
  ├─ Step 5: LLM 生成 (server.py call_ai_api)
  │    7 种提供商兼容 → 返回回答
  │
  └─ Step 6: 学习归档 (obsidian_writer.py + mem0_manager.py)
       写入日志 + 提炼经验模式 + mem0 记忆存储
```

### 3.3 关键实现细节

**RAG 检索（`src/agent.py`）**
- 英文术语（如 KYC、API）权重加倍，直接追加到搜索词
- TF-IDF 默认检索 top_k=10，ChromaDB 向量检索可选
- KYC 相关问题硬规则插入核心接口块（`/api/v1/customers/accounts`）
- 同一标题只保留最高分（去重）

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

**语言自适应（`kb_agent.py`）**
- 检测用户提问是否含中文 → 中文提问用中文 system prompt，英文提问用全英文 prompt
- 用户消息注入语言指令（如 `"answer in English only"`）
- DeepSeek 模型有中文知识库强倾向，此为已知限制

### 3.4 与旧版本的关键差异

| 项目 | 旧版本 (v3) | 当前版本 (v4) |
|------|-----------|-------------|
| 硬约束拒答 | RAG 分数 < 3.0 直接拒答 | 已移除，无检索结果也调 AI |
| 寒暄处理 | 被拒答 | classify_intent 识别为 greeting，服务端硬编码返回 |
| 模块位置 | agent.py 内联 | 独立 src/agents/kb_agent.py (CustomerServiceAgent 类) |
| 记忆系统 | mem0（API 调用失败） | mem0_manager（本地 fastembed，已修复） |
| 多主题处理 | 全量传给 AI | 先反问确认（P1-04 已实现） |
| 上下文控制 | 无 | MAX_RAG_CHARS=3000 预算控制 |

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
  │     ├─ "business"   →  await biz_agent.handle(question)
  │     │
  │     └─ "knowledge"  →  await kb_agent.chat(
  │                            question=req.question,
  │                            user_id=req.user_id,
  │                            session_id=req.session_id,
  │                            call_ai=call_ai_api)
  │                           │
  │                           ├─ 1. mem_recall(question, user_id, top_k=3)
  │                           │        ← src/mem0_manager.py
  │                           │
  │                           ├─ 2. search_docs(question)
  │                           │        ← src/agent.py (answer函数)
  │                           │        ← src/rag_store.py (TF-IDF + ChromaDB)
  │                           │
  │                           ├─ 3. 多主题反问检测
  │                           │        clarify_topics ≥ 2 → 反问返回
  │                           │
  │                           ├─ 4. RAG 上下文组装
  │                           │        MAX_RAG_CHARS=3000 预算控制
  │                           │        system_prompt + RAG + mem0 + 对话历史
  │                           │
  │                           ├─ 5. call_ai_api(messages)
  │                           │        ← server.py → LLM (7种提供商)
  │                           │
  │                           └─ 6. learn(question, answer, confidence)
  │                                    + mem_remember(user_id, ...)
  │                                    ← src/obsidian_writer.py
  │                                    ← src/mem0_manager.py
  │
  └─ 返回 AskResponse
```

---

## 六、多主题反问（P1-04 · 已实现）

### 6.1 问题

用户模糊提问（如只输入「KYC」），检索结果同时命中「KYC概念说明」和「Card issuing API KYC接口」两个不相关主题。旧方案直接传给 AI，AI 可能一股脑全答，体验差。

### 6.2 实现（`src/agent.py` + `src/agents/kb_agent.py`）

检索后去重 → 提取每个命中块的一级主题（`title_path` 按 ` > ` 分割取第一段）→ 统计不同主题数 → ≥2 则反问。

```python
# src/agent.py 第 175-197 行
topics = []
for h in hits[:8]:
    top = h['chunk']['title_path'].split(' > ')[0]
    if top and top not in topics:
        topics.append(top)

if len(topics) >= 2:
    return {"clarify": True, "clarify_topics": topics}
```

`kb_agent.py` 收到 `clarify=True` 后不调 AI，零延迟返回：

```
您的问题涉及多个方面，请问您想了解哪个？
  1. KYC — 搜索导入
  2. Card issuing API 文档
请告诉我序号或直接描述您的需求。
```

### 6.3 上下文预算控制

| 层级 | 限制 | 代码位置 |
|------|------|---------|
| 文档切块 | 每块 ≤ 1200 字符 | `src/doc_loader.py:88` |
| 检索截断 | 每块内容 ≤ 1000 字符 | `src/agent.py:205` |
| 上下文总预算 | ≤ 3000 字符（约 1500 tokens） | `src/agents/kb_agent.py:42` |

---

## 七、改动清单（v3 → v4）

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/agents/__init__.py` | 新增 | 包初始化 |
| `src/agents/kb_agent.py` | 新增 | 从 agent.py 提取 RAG+AI 逻辑，增加 max_chars 控制 |
| `src/agents/biz_agent.py` | 新增 | 业务Agent占位 |
| `server.py` | 修改 | 导入 classify_intent()，重构 /api/ask 路由到 Agent |
| `src/agent.py` | 修改 | 精简为检索函数 + 多主题反问检测 |
| `src/rag_store.py` | 不变 | TF-IDF + ChromaDB 检索引擎 |
| `src/doc_loader.py` | 不变 | Markdown 文档加载切块 |
| `src/obsidian_writer.py` | 不变 | 问答日志与经验模式 |
| `src/mem0_manager.py` | 激活 | mem0 语义记忆（本地 fastembed） |
| `src/simple_memory.py` | 备选 | 降级方案（sentence-transformers） |

---

## 八、已修复的问题

| 问题 | 修复方式 | 状态 |
|------|---------|------|
| BUG-01 寒暄被拒答 | classify_intent 识别为 greeting，server.py 硬编码返回问候 | ✅ |
| BUG-02 时间戳单位矛盾 | system_prompt 第56-58行强制区分毫秒（HMAC认证）vs 秒（查询接口） | ✅ |
| BUG-03 mem0 未生效 | 切回 mem0_manager.py（本地 fastembed），已验证跨对话记忆召回正常 | ✅ |
| P1-04 多主题未反问 | src/agent.py 主题聚类 + kb_agent.py 反问返回 | ✅ |
| BUG-04 上下文无预算 | MAX_RAG_CHARS=3000 + chunk 截断 + 超长强制截断 | ✅ |

---

## 九、扩展性

- **新增Agent**：只需在 `src/agents/` 目录新建文件，加一条路由规则
- **业务Agent对接**：`src/agents/biz_agent.py` 替换占位代码即可
- **意图分类升级**：后续可换 LLM 分类，不影响 Agent 接口
- **记忆系统切换**：mem0_manager ↔ simple_memory 只需改一行 import
