# mem0-demo 智能客服系统

> 基于 RAG + mem0 记忆的多 Agent 智能客服系统，支持 7 种 AI 提供商、20 个 API 接口、动态知识库加载。

**线上地址：** [yijin840/mem0-demo](https://github.com/yijin840/m0-demo) · **版本：** v4.0.0

---

## 目录

- [系统架构](#系统架构)
- [功能特性](#功能特性)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [API 接口](#api-接口)
- [前端使用](#前端使用)
- [测试](#测试)
- [文档导航](#文档导航)
- [版本历史](#版本历史)
- [License](#license)

---

## 系统架构

### 整体流程

```
用户提问
  │
  ▼
server.py  /api/ask
  │
  ▼
classify_intent()  ──→  greeting    →  硬编码友好问候
  │                     unknown     →  硬编码拒答引导
  │                     business    →  kb_agent.chat()（P2-01 临时路由）
  │                     knowledge   →  知识库 Agent
  │
  ▼
知识库 Agent (kb_agent.py)
  │
  ├── 0. 短业务追问检测  ──→  充值/开卡/查余额/冻结/解冻（≤5字）→ 直接追问返回
  ├── 1. 分类检索        ──→  分类推断 → ChromaDB 向量/TF-IDF 降级 + category 过滤
  ├── 2. 多主题检测      ──→  一级主题 ≥2 个 → LLM 生成反问问句
  ├── 3. 记忆检索 (mem0)  ──→  跨会话用户画像 + 对话历史
  ├── 4. 质量校验        ──→  置信度评估（无硬约束拒答）
  ├── 5. LLM 生成        ──→  7 种 AI 提供商兼容 + reasoning 模型兼容
  └── 6. 学习归档        ──→  自动提炼经验模式
```

### 四层协同

| 层次 | 模块 | 核心能力 |
|------|------|----------|
| 文档层 | `doc_loader.py` | 自动下载切块、文件上传、URL 导入（GitHub 自动转 raw）、搜索爬虫、category 分类字段 |
| 检索层 | `rag_store.py` + `agent.py` | ChromaDB 语义检索 + TF-IDF 降级、category 预过滤、英文短术语权重加倍、KYC 硬规则提权、标题去重、多主题检测 |
| 回答层 | `kb_agent.py` + `server.py` | 短业务追问、RAG 上下文组装、7种AI生成 + reasoning 模型兼容、mem0 记忆注入、多轮对话历史（6轮/12条）、语言自适应（中/英） |
| 学习层 | `obsidian_writer.py` + `mem0_manager.py` | 每日日志、经验模式提炼、mem0 跨会话持久化、30天自动清理 |

### 意图分类规则

| 类别 | 判定词 | 处理 | 实现 |
|------|--------|------|------|
| greeting | 你好、您好、hello、hi、谢谢、再见 | 返回"您好，有什么可以帮您？" | `kb_agent.py:85` |
| unknown | 天气、比特币、股票、预测 | 返回拒答引导语 | `kb_agent.py:98` |
| business | 充值、开卡、查余额、冻结、解冻、我要充值、我要开卡、帮我冻结、帮我解冻、recharge、open card、freeze、unfreeze | **临时路由到 kb_agent.chat()**（P2-01） | `server.py:327` |
| knowledge | 默认 | 短业务追问 → 分类检索 → 多主题检测 → LLM 生成 | `kb_agent.py:198` |

> **注意**：`business` 意图当前临时路由到知识库 Agent（biz_agent 开发完成后切回）。充值/开卡/查余额/冻结/解冻等短词（≤5字）会直接触发追问，不检索。

### 短业务追问（新增）

当用户输入极短的业务关键词（≤5字），系统不盲目检索，而是直接追问用户具体想了解什么：

```python
# kb_agent.py chat() 开头
biz_kw_short = ["充值", "开卡", "查余额", "冻结", "解冻"]
if any(kw in question for kw in biz_kw_short) and len(question) <= 5:
    return {"answer": "您想了解关于「充值」的哪些信息？例如：\n- 如何操作\n- 需要什么条件\n- 多久到账\n\n您具体想了解哪个？",
            "method": "clarify", ...}
```

**与多主题反问的区别**：
- 短业务追问：**不调 LLM**，零延迟，直接返回预设追问模板
- 多主题反问：调 LLM 基于 chunks 内容生成自然问句

### 多主题反问（P1-04）

当用户模糊提问（如只输入「KYC」），检索命中多个不相干的一级主题时，系统不直接回答，而是列出选项反问用户确认。

**实现原理：**

```
search_docs("KYC")
  │
  ▼
去重后的 Top 8 命中块
  │  title_path = "Card issuing API 文档 > KYC > 提交用户 KYC 数据"
  │  title_path = "Native MM API Documentation > KYC > 企业认证"
  ▼
提取一级主题（按 " > " 分割取第一段）
  │  "Card issuing API 文档"
  │  "Native MM API Documentation"
  ▼
统计不同主题数 ≥ 2？
  │  是 → 返回 clarify，调 LLM 生成反问问句
  │  否 → 继续走 RAG + LLM 生成
```

**关键代码**（`src/agent.py`）：

```python
# 提取每个命中块的一级主题
topics = []
for h in hits[:8]:
    title = h['chunk'].get('title_path', '')
    top = title.split(' > ')[0]
    if top and top not in topics:
        topics.append(top)

# ≥2 个不同一级主题 → 反问
if len(topics) >= 2:
    return {"clarify": True, "clarify_topics": topics, "clarify_labels": [...]}
```

`src/agents/kb_agent.py` 收到 `clarify=True` 后**调 LLM 生成自然问句**（非模板），零幻觉。

**示例：**
- 问「KYC」→ LLM 生成："您想了解 KYC 的哪些内容？例如：1. KYC 提交接口和参数 2. KYC 审核状态和查询 3. KYC 材料要求和格式"

### RAG 上下文预算控制

无论原始文档多大，传入 LLM 的上下文始终受三层限制，不会撑爆 token 预算：

| 层级 | 位置 | 限制 | 说明 |
|------|------|------|------|
| 文档切块 | `doc_loader.py` | 每块 ≤ 1200 字符 | 按标题切分 + 超长块按段落二次切分 |
| 检索截断 | `src/agent.py:205` | 每块内容 ≤ 1000 字符 | `content[:1000]` 硬截断 |
| 上下文预算 | `src/agents/kb_agent.py:42` | 总上下文 ≤ 3000 字符 | `MAX_RAG_CHARS = 3000`（约 1000-1500 tokens） |

```
200MB 文档 → 切块(≤1200字/块) → Top10检索 → 每块截1000字 → 总预算3000字 → LLM
```

无检索结果时 RAG 为空但仍调 LLM（让其自行告知无相关信息），不强行拒答。

### 分类检索（新增）

每个 chunk 自动提取 `category` 字段（从 `title_path` 一级路径提取），检索时用于预过滤：

**分类推断**（`src/agent.py` `_infer_category()`）：

```python
_cat_kw = {
    "Card issuing API 文档": ["KYC", "开卡", "充值", "冻结", "解冻", "信用卡", ...],
    "Native MM API Documentation": ["deposit", "exchange", "asset", "MM", "充值卡", ...],
}
```

**ChromaDB 过滤**（`src/rag_store.py`）：
```python
if category:
    results = collection.query(
        query_embeddings=[q_emb],
        n_results=top_k,
        where={"category": category}  # 精确匹配
    )
```

**TF-IDF 降级**：category 不匹配时返回 `[]`，不强行列退到全量检索。

### 数据流（完整）

```
server.py /api/ask
  │
  ├─ classify_intent(question)
  │     │
  │     ├─ "greeting"    →  直接返回问候
  │     ├─ "unknown"     →  直接返回拒答引导
  │     ├─ "business"    →  kb_agent.chat()（P2-01 临时路由）
  │     │
  │     └─ "knowledge"   →  kb_agent.chat()
  │                           │
  │                           ├─ 0. 短业务追问检测
  │                           │        充值/开卡/查余额/冻结/解冻（≤5字）→ 直接追问返回
  │                           │
  │                           ├─ 1. search_docs()    ←  rag_store.py + agent.py
  │                           │        ├─ 分类推断（KY C→Card issuing, deposit→Native MM）
  │                           │        ├─ 英文短术语增强（KYC/API 追加到搜索词）
  │                           │        ├─ ChromaDB 语义检索 / TF-IDF 降级
  │                           │        ├─ KYC 硬规则提权
  │                           │        ├─ 标题去重
  │                           │        └─ 多主题检测（clarify_topics ≥ 2 且非具体提问）
  │                           │
  │                           ├─ 2. 多主题反问（LLM 生成问句）
  │                           │        clarify==True → 调 LLM 生成 2-3 个中文问句
  │                           │        LLM 输出 "." 表示无相关内容 → 放过，继续正常流程
  │                           │
  │                           ├─ 3. mem_recall()     ←  mem0_manager.py
  │                           │
  │                           ├─ 4. 时间戳增强检索（可选）
  │                           │        问题含"时间戳" → 同时检索 HMAC毫秒 + 查询接口秒
  │                           │
  │                           ├─ 5. RAG 上下文组装
  │                           │        MAX_RAG_CHARS=3000 预算控制
  │                           │        system_prompt + RAG + mem0 + 对话历史（最近6轮/12条）
  │                           │
  │                           ├─ 6. call_ai_api()    ←  server.py → LLM
  │                           │        ← 7种提供商 + reasoning 模型兼容
  │                           │
  │                           └─ 7. learn() + mem_remember()   ←  obsidian_writer.py
  │                                                    ←  mem0_manager.py
  │
  └─ 返回 AskResponse
```

### 多 Agent 架构

系统采用三层 Agent 协同架构，职责分明、可扩展：

```
用户提问
    │
    ▼
┌─────────────────────┐
│   主Agent (路由层)    │  ← server.py + classify_intent()
│   关键词规则分类      │     不调 LLM，零延迟，100% 可控
└──────┬──────────────┘
       │
   ┌───┼───────────┬──────────┐
   ▼   ▼           ▼          ▼
greeting unknown  business  knowledge
 (直返)  (直返)      │          │
                    ▼          ▼
              ┌──────────┐ ┌──────────────┐
              │业务Agent  │ │ 知识库Agent   │  ← agents/
              │biz_agent │ │ kb_agent     │
              │(占位实现) │ │ Customer-    │
              └──────────┘ │ ServiceAgent │
                           └──────┬───────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
                 RAG检索      mem0记忆       LLM生成
              (rag_store)  (mem0_manager) (call_ai_api)
```

| Agent | 触发条件 | 职责 | 状态 |
|-------|----------|------|------|
| **主Agent** | 所有请求 | 意图分类 + 路由分发，关键词规则（不调LLM） | ✅ |
| **知识库Agent** | knowledge 意图 + business 意图（临时） | 短业务追问 → 分类检索 → 多主题反问 → RAG+mem0+LLM 生成 → 学习归档 | ✅ |
| **业务Agent** | （预留）business 意图 | 对接真实 API（充值/开卡/冻结等），当前占位 | 🚧 开发中 |

---

## 功能特性

**问答能力**
- 自动检索文档并生成回答（Markdown 格式，含表格/代码块）
- 回答包含 API 地址、方法、完整参数表、错误码、示例代码
- 知识库外的问题直接礼貌拒答，不编造
- 短业务追问：充值/开卡/查余额/冻结/解冻等短词（≤5字）直接追问，不盲目检索
- 多主题反问：模糊提问（如「KYC」）由 LLM 基于知识库 chunks 生成自然问句
- 上下文预算控制：无论文档多大，传入 LLM 最多 3000 字符
- 同一会话内支持 6 轮对话上下文关联（session 隔离，12条消息上限）
- 英文提问自适应语言检测（system prompt + user message 双指令）
- 时间戳问题增强检索：同时检索 HMAC（毫秒）和查询接口（秒）两类文档

**AI 提供商**
- 支持 7 种：OpenAI / Anthropic / 硅基流动 / DeepSeek / 通义千问 / 智谱 GLM / 自定义
- 配置仅需三项：API URL + 模型名 + API Key
- 前端一键测试连接
- API Key 安全脱敏显示
- **reasoning 模型兼容**：glm-5 等模型 `content` 为空时取 `reasoning_content`

**知识库管理**
- 三种导入方式：上传文件、粘贴 URL（GitHub 自动转 raw 链接）、搜索爬虫
- 来源可查、可删、可一键恢复默认
- 爬虫无需第三方 API Key
- 每个 chunk 自动提取 `category` 字段（一级标题），用于检索预过滤

**mem0 记忆系统**
- 跨会话持久化用户画像和对话历史
- 语义搜索、自动提取关键信息
- 本地 fastembed，无需外部 API

**前端体验**
- 暗色主题界面，侧栏 + 聊天区布局
- Markdown 富文本渲染（表格斑马纹、代码块等宽字体、标题层级）
- AI 设置面板（7 种提供商 + 连接测试 + Key 脱敏显示）
- 文档管理面板（上传/URL/搜索导入 + 来源浏览/删除/一键恢复默认）
- Vault 文件浏览（qa_logs / summaries / patterns 目录）
- 系统状态面板（health 接口实时展示文档块数、经验数、RAG 状态）
- 快捷问题入口
- 用户列表 + 记忆详情查看

---

## 项目结构

```
mem0-demo/
├── server.py                # FastAPI 入口（20 个 API）
├── src/
│   ├── agent.py             # 检索协调 + 分类推断 + 多主题反问 + 关键词增强
│   ├── doc_loader.py        # 文档加载切块（含 category 字段）
│   ├── rag_store.py         # RAG 检索层（ChromaDB 语义 + TF-IDF 降级 + category 过滤）
│   ├── mem0_manager.py      # mem0 记忆管理（fastembed 本地向量化）
│   ├── simple_memory.py     # 本地记忆备选方案（sentence-transformers）
│   ├── obsidian_writer.py   # 问答日志 + 经验模式写入
│   ├── web_crawler.py       # 搜索导入爬虫（DuckDuckGo + BS4）
│   ├── dspy_rag_optimizer.py # DSPy 优化模块（预留）
│   └── agents/
│       ├── __init__.py
│       ├── kb_agent.py      # 知识库 Agent（短业务追问 + RAG + mem0 + LLM）
│       └── biz_agent.py     # 业务 Agent（占位，待对接真实 API）
├── static/
│   └── index.html           # 前端单页应用（侧栏+聊天+AI设置面板）
├── tests/                   # 回归测试
├── docs/                    # 项目文档（详见下方导航）
│   ├── 多Agent架构设计.md
│   ├── 真实测试用例集.md
│   ├── 问题清单.md
│   ├── 项目总结.md
│   └── 部署文档.md
├── docs_cache/                # 知识库缓存（chunks.json + sources.json）
├── chroma_db/                 # ChromaDB 向量库持久化目录
├── chroma_viewer.py           # ChromaDB 查看脚本（Python CLI）
├── api_config.json          # AI API 配置（gitignored，含 API Key）
├── memory_store.json        # 对话记忆数据（gitignored）
├── requirements.txt         # Python 依赖
├── .env                   # 环境变量（可选，gitignored）
└── README.md
```

---

## 快速开始

### 环境要求

- Python 3.9+
- pip

### 安装

```bash
git clone git@github.com:yijin840/mem0-demo.git
cd mem0-demo
pip install -r requirements.txt
```

### 配置

通过前端 ⚙️ **AI 设置** 配置，或直接编辑 `api_config.json`：

```json
{
  "provider": "custom",
  "api_key": "YOUR_API_KEY",
  "base_url": "https://your-endpoint.com/v1",
  "model": "model-name",
  "enabled": true
}
```

> **注意**：`base_url` **必须以 `/v1` 结尾**，否则会返回非 JSON 响应导致解析错误。

支持的 provider：`openai` / `anthropic` / `siliconflow` / `deepseek` / `qwen` / `zhipu` / `custom`

### 启动

```bash
python3 server.py
# 访问 http://localhost:8000
```

首次启动自动下载文档、初始化 RAG 向量库（ChromaDB）。

### 知识库导入

```bash
# 自动从 GitHub 拉取（已在 server.py 启动时执行）
python3 -B -c "from src.doc_loader import refresh_docs; refresh_docs()"
```

也可通过前端「文档管理」面板上传、粘贴 URL、或搜索关键词导入。

---

## 配置说明

### api_config.json

| 字段 | 类型 | 说明 |
|------|------|------|
| `provider` | str | 提供商：openai/anthropic/siliconflow/deepseek/qwen/zhipu/custom |
| `api_key` | str | API 密钥（**勿提交到 git**） |
| `base_url` | str | 自定义 API 地址（**必须以 `/v1` 结尾**） |
| `model` | str | 模型名称 |
| `temperature` | float | 0.0-2.0，默认 0.7 |
| `max_tokens` | int | 最大输出 token，默认 2048 |
| `enabled` | bool | 是否启用 AI |

### 环境变量（可选）

```bash
# 缓存目录（默认 ./docs_cache）
export DOCS_CACHE_DIR="/opt/mem0-demo/docs_cache"

# ChromaDB 路径（默认 ./chroma_db）
export CHROMA_PATH="/opt/mem0-demo/chroma_db"

# 额外文档源（JSON 数组）
export EXTRA_DOCS_JSON='[{"name":"自定义文档","url":"https://example.com/doc.md","local":null}]'

# HuggingFace 镜像（国内加速）
export HF_ENDPOINT=https://hf-mirror.com
```

> **注意**：API Key 仍优先读取 `api_config.json`。`python-dotenv` 已集成，也可在 `.env` 文件中配置上述变量。

### .gitignore 保护

以下文件不会被提交到 Git：

```
api_config.json    # AI Key
memory_store.json  # 会话数据
.env / .env_back   # 环境变量
.workbuddy/        # 工作数据
docs_cache/         # 知识库缓存
chroma_db/         # ChromaDB 向量库
logs/              # 运行日志
```

---

## API 接口

### 核心接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ask` | 问答（请求体：`{"question":"...", "user_id":"...", "session_id":"..."}`） |
| GET | `/api/health` | 健康检查（文档块数、AI状态、RAG状态、经验数） |

### 配置接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取当前 AI 配置（Key 脱敏） |
| POST | `/api/config` | 更新 AI 配置 |
| POST | `/api/config/test` | 测试 AI 连接（`{"api_key":"...", "base_url":"..."}`） |

### 文档管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/docs/chunks` | 查看文档块列表（含 category 字段） |
| POST | `/api/docs/refresh` | 重新从远程拉取文档 |
| GET | `/api/docs/sources` | 查看文档来源 |
| POST | `/api/docs/import` | 导入 URL 文档 |
| POST | `/api/docs/upload` | 上传文件 |
| POST | `/api/docs/crawl` | 搜索关键词并导入 |
| DELETE | `/api/docs/remove?name=...` | 删除文档 |
| DELETE | `/api/docs/reset` | 恢复默认文档 |

### 经验库接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/patterns` | 查看经验模式列表 |
| POST | `/api/patterns/refresh` | 重新提炼经验模式 |
| DELETE | `/api/patterns` | 清空经验模式 |

### Vault 文件浏览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/vault/files` | 浏览 Vault 目录结构 |
| GET | `/api/vault/file?path=...` | 读取 Vault 文件内容 |

### 回答格式（`/api/ask` 响应）

```json
{
  "answer": "根据知识库内容，KYC 认证需要提交以下材料...",
  "source": "ai",
  "doc_hits": 10,
  "confidence": 0.8,
  "chunks_used": [
    {"title_path": "Card issuing API > KYC > 提交用户KYC数据", "score": 99.0, "content": "...", "category": "Card issuing API 文档"}
  ],
  "method": "semantic"
}
```

`method` 可能值：`greeting` / `unknown` / `clarify` / `keyword` / `semantic` / `llm` / `none`

---

## 前端使用

- 打开 `http://localhost:8000`
- 左侧栏：用户选择 / 知识库浏览 / 用户记忆 / Vault 文件浏览 / 系统状态
- 主区域：聊天对话 + 快捷问题入口
- 顶部 ⚙️ 按钮 → AI 设置：提供商选择 → 填 API Key → 测试连接 → 保存
- 顶部 📂 按钮 → 知识库管理（文档列表 / 手动录入 / 搜索导入 / 文件管理 / 恢复默认）

---

## 测试

### 回归测试

```bash
# 先启动服务
python3 -B server.py &

# 运行测试
python3 -B tests/regression_test.py
```

### 测试覆盖

| 类别 | 用例 | 说明 |
|------|------|------|
| 单轮 | TC-01 ~ TC-12 | 问候、KYC材料、业务外话题、对比、401错误、记忆召回、重复提问、多问题、混合意图、订单查询、短业务词（充值） |
| 多轮 | M01 ~ M08 | 8 轮连续对话：API对接咨询 → 认证 → 401排查 → Python示例 → 时间戳单位 → 机构确认 → KYC示例 → 失败重试 |

**当前通过率：单轮 10/10（含设计行为） + 多轮 7/8（M05 已加修复，待重测）**

---

## 文档导航

详细文档见 `docs/` 目录：

| 文档 | 说明 |
|------|------|
| [🏗️ 多Agent架构设计](docs/多Agent架构设计.md) | 三层 Agent 架构（主路由 → 知识库 Agent → 业务 Agent）、意图分类设计、数据流、改动清单、已修复问题 |
| [🧪 真实测试用例集](docs/真实测试用例集.md) | 真实场景测试用例（单轮12 + 多轮8），含预期行为、实际回答、通过判定、与原系统对比 |
| [🐛 问题清单与修复方案](docs/问题清单.md) | 已知问题按 P0/P1/P2 排列，含根因分析、修复方案、验证方法、已修复问题列表 |
| [📋 项目总结](docs/项目总结.md) | 项目定位、架构、功能清单、运行数据、质量保障、技术栈 |
| [🚀 部署文档](docs/部署文档.md) | 环境要求、安装依赖、配置、systemd/pm2/Nginx、故障排查、环境变量 |
| [🗄️ 向量数据库设计文档](docs/向量数据库设计文档.md) | ChromaDB 设计、实现细节、分类检索、操作指南、维护与故障排查 |

---

## 版本历史

**v4.0.0** (2026-05-26)

- 多 Agent 架构（意图分类 + 子 Agent 路由）
- mem0 跨会话记忆系统（本地 fastembed）
- Markdown 完整渲染（H4、表格、代码块）
- 动态知识库加载（远程文档源自动下载切块）
- AI 设置面板（7 种提供商 + 连接测试 + Key 脱敏）
- 文档动态管理（上传/URL/搜索导入 + category 分类字段）
- 前端响应式布局 + Vault 文件浏览 + 系统状态面板
- 多主题反问（P1-04）：检索跨主题时先反问确认，LLM 生成自然问句
- 短业务追问（新增）：充值/开卡/查余额/冻结/解冻（≤5字）直接追问，不检索
- 分类检索（新增）：chunk 加 `category` 字段，ChromaDB `where` 过滤 + TF-IDF 预过滤
- reasoning 模型兼容（新增）：glm-5 等 `content` 为空时取 `reasoning_content`
- 时间戳增强检索（新增）：HMAC 毫秒 + 查询接口秒 同时检索合并
- 英文短术语增强（新增）：KYC/API 等 ≤4 字母术语直接追加到搜索词
- 工程化目录结构 `src/` + `tests/`
- `info_provide` 意图移除（修复 echo 问题）
- 英文提问语言自适应
- P2-01 修复：business 意图临时路由到 `kb_agent.chat()`
- BUG-05 修复：API `base_url` 缺 `/v1` 导致 JSON 解析错误
- BUG-06 修复：短业务词（充值/开卡）误分类为 knowledge
- BUG-07 修复：reasoning 模型 `content` 为空时正常运行

---

## License

MIT
