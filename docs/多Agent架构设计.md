# 多Agent架构设计文档

> 日期：2026-05-25 | 版本：v5.0.0

---

## 一、架构概览

```
用户提问
    │
    ▼
┌─────────────────────┐
│   主Agent (路由层)    │  ← server.py 新增意图分类函数
│   关键词规则分类      │
└──────┬──────────────┘
       │
   ┌───┼───────────┐
   ▼   ▼           ▼
 知识库  无关       业务
 Agent  拒答       Agent
        (引导语)   (预留)
```

三层 Agent：

| Agent | 触发条件 | 职责 | 实现状态 |
|-------|----------|------|----------|
| 主Agent | 所有请求 | 意图分类 + 路由 | 新增 |
| 知识库Agent | 命中业务关键词 | RAG检索 + AI回答 | 已有，重构为类 |
| 业务Agent | 命中操作关键词 | 调用真实API / 查询系统 | 预留占位 |

---

## 二、主Agent — 意图分类

### 2.1 设计思路

不调 LLM，纯关键词规则。分类数少、规则明确、零延迟。

### 2.2 分类规则

| 类别   | 判定词（中文）                 | 判定词（英文）                   | 处理         |
| ---- | ----------------------- | ------------------------- | ---------- |
| 业务操作 | 我要充值、我要开卡、查余额、帮我冻结、帮我解冻 | recharge、open card、freeze | → 业务Agent  |
| 业务无关 | 天气、比特币、股票、唱歌、讲笑话        | weather、bitcoin、stock     | → 直接拒答     |
| 知识问答 | 其余所有（默认）                | 其余所有（默认）                  | → 知识库Agent |

说明：「你好」「谢谢」等寒暄归入知识问答（默认分类），由 AI 自行应对，不作特殊处理。

### 2.3 函数签名

```
classify_intent(question: str) -> str
  返回: "knowledge" | "business" | "unknown"
```

### 2.4 位置

`server.py` 内新增函数，在 `/api/ask` 路由入口处调用。

---

## 三、知识库Agent — RAG + AI

### 3.1 设计思路

将现有问答逻辑抽取为独立模块 `agents/kb_agent.py`，保持功能不变，结构更清晰。

### 3.2 核心流程

```
用户问题
  → 关键词增强（英文术语权重加倍）
  → TF-IDF 文档检索（top_k=10，停用词过滤）
  → KYC 硬规则提权
  → 标题去重
  → system_prompt + RAG上下文 + mem0记忆 → AI生成回答
  → 记录日志 + 提炼经验
```

### 3.3 与现状的差异

| 项目 | 现状 | 新设计 |
|------|------|--------|
| 硬约束拒答 | RAG 分数 < 3.0 直接拒答 | 移除。无检索结果也调 AI，AI 自己决定如何回应 |
| 位置 | agent.py 内联 | agents/kb_agent.py 独立模块 |
| 寒暄处理 | 被拒答 | AI 正常回复问候 |

### 3.4 文件结构

```
agents/
├── __init__.py
├── kb_agent.py      # 知识库Agent（从 agent.py 重构）
└── biz_agent.py     # 业务Agent（预留占位）
```

### 3.5 检索后处理（当前行为）

**当前实现**：检索到 top 10 个 chunks 后，全部组装成 `RAG_CONTEXT` 传给 AI，由 AI 自行决定如何回答。

**局限**：当用户问题匹配到多个不同主题时（如"KYC"同时匹配到提交材料、查询状态、失败重试），AI 可能全部回答或混淆。

**期望行为**（待确认）：检索到多个不同主题时，先列出选项反问用户，而非直接传给 AI 生成综合回答。

**讨论记录**（2026-05-25）：
- yijin：问"KYC"时如果有多个匹配，是不是应该先问用户想了解哪方面？
- 当前未实现反问逻辑，需确认是否要做。

---

## 四、业务Agent — 系统操作

### 4.1 设计思路

预留模块，后续对接真实的 API 进行充值、开卡、冻结等系统操作。

### 4.2 当前占位实现

```python
def handle_business(question: str) -> dict:
    return {
        "answer": "抱歉，该功能正在开发中。如需充值或开卡，请登录管理后台操作。",
        "source": "biz_agent"
    }
```

### 4.3 后续规划

- 充值操作：调用 `POST /api/v1/mm/customer/asset/deposit`
- 开卡操作：调用 `POST /api/v1/debit-cards`
- 账号查询：调用 `GET /api/v1/customers/accounts/kyc`
- 冻结/解冻：调用 `PUT /api/v1/debit-cards/freeze`

---

## 五、数据流

```
server.py (/api/ask)
  │
  ├─ classify_intent(question)
  │     │
  │     ├─ "knowledge" → kb_agent.answer(question, session_id)
  │     │                   │
  │     │                   ├─ search_docs()  ← rag_store.py
  │     │                   ├─ mem_recall()   ← mem0_manager.py
  │     │                   ├─ call_ai_api()  ← LLM
  │     │                   └─ learn()        ← obsidian_writer.py
  │     │
  │     ├─ "business" → biz_agent.handle(question)
  │     │                   └─ 占位：返回"功能开发中"
  │     │
  │     └─ "unknown"  → 直接拒答（引导语）
  │
  └─ 返回 AskResponse
```

---

## 六、改动清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `agents/__init__.py` | 新增 | 包初始化 |
| `agents/kb_agent.py` | 新增 | 从 agent.py 提取 RAG+AI 逻辑 |
| `agents/biz_agent.py` | 新增 | 业务Agent占位 |
| `server.py` | 修改 | 新增 classify_intent()，重构 /api/ask 路由到 Agent |
| `agent.py` | 修改 | 精简为检索函数，Agent 逻辑移到 kb_agent.py |
| `rag_store.py` | 不变 | 检索引擎 |
| `doc_loader.py` | 不变 | 文档管理 |
| `obsidian_writer.py` | 不变 | 日志与经验 |
| `mem0_manager.py` | 不变 | 语义记忆 |

---

## 七、修复的问题

| 问题 | 修复方式 |
|------|----------|
| BUG-01 寒暄被拦截 | 移除 RAG 分数硬约束，"你好"正常调 AI，AI 自回问候 |
| BUG-02 时间戳矛盾 | 后续在 system_prompt 补充单位说明 |
| BUG-03 mem0 未生效 | 后续换本地 embedding 模型 |

---

## 八、扩展性

- 新增Agent：只需在 `agents/` 目录新建文件，加一条路由规则
- 业务Agent对接：`agents/biz_agent.py` 替换占位代码即可
- 意图分类升级：后续可换 LLM 分类，不影响 Agent 接口
