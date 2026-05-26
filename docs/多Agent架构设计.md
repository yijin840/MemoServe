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

1. **零额外成本**：检测逻辑在检索阶段完成，不额外调 AI，毫秒级完成
2. **关键词规则，不调 LLM**：和 `classify_intent()` 同样思路——分类数少（2-3 个一级主题）、规则明确（title_path 第一段），不需要让 AI 来判断"是不是同一个主题"
3. **反问即回答**：检测到多主题 ≠ 报错，而是生成一次反问，这次反问本身就是对用户的有效回应
4. **不破坏现有流程**：反问结果和正常回答使用相同的 `AskResponse` 结构，前端无需任何改动

### 6.3 方案设计

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

### 6.4 实现细节

**步骤 1：检索 + 去重后，提取一级主题（`src/agent.py` 第 175-197 行）**

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

**步骤 2：`kb_agent.py` 处理反问（第 178-197 行）**

```python
doc_result = search_docs(question)

# 多主题反问：检索结果跨多个一级主题 → 先反问
if doc_result.get("clarify"):
    topics = doc_result.get("clarify_topics", [])
    options = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(topics))
    return {
        "answer": f"您的问题涉及多个方面，请问您想了解哪个？\n\n{options}\n\n请告诉我序号或直接描述您的需求。",
        "source": "clarify",
        "doc_hits": doc_result["doc_hits"],
        "confidence": 0.99,
        "chunks_used": [],
        "method": "clarify",
    }
```

**关键设计点**：

1. **不调 AI**：反问是服务端硬编码文本，零延迟、零 token 消耗
2. **置信度 0.99**：反问不是"不确定"，而是"确定需要用户选择"
3. **source="clarify"**：前端可以据此做特殊渲染（如选项卡片样式）
4. **chunks_used 为空**：反问不需要展示来源（选项本身就是来源）
5. **返回后终止流程**：不再进入步骤 4-6（上下文构建、AI 调用、学习归档）

### 6.5 效果对比

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
系统：您的问题涉及多个方面，请问您想了解哪个？
       1. KYC — 搜索导入
       2. Card issuing API 文档
      请告诉我序号或直接描述您的需求。
      （零 AI 调用，用户可以选择精确方向）
```

### 6.6 上下文预算控制

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
