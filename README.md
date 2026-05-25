# mem0-demo 智能客服系统

> 基于 RAG + mem0 记忆的多 Agent 智能客服系统，支持 7 种 AI 提供商、20 个 API 接口、115 块知识库。

**线上地址：** [yijin840/mem0-demo](https://github.com/yijin840/mem0-demo) · **版本：** v4.0.0

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
  │                     business    →  biz_agent（占位）
  │                     knowledge   →  知识库 Agent
  │
  ▼
知识库 Agent (kb_agent.py)
  │
  ├── 1. 记忆检索 (mem0)    ──→  跨会话用户画像 + 对话历史
  ├── 2. RAG 检索           ──→  TF-IDF 关键词 + Chroma 向量检索
  ├── 3. 质量校验           ──→  置信度评估 + 知识库硬约束
  ├── 4. LLM 生成           ──→  7 种 AI 提供商兼容
  └── 5. 学习归档           ──→  自动提炼经验模式
```

### 四层协同

| 层次 | 模块 | 核心能力 |
|------|------|----------|
| 文档层 | `doc_loader.py` | 自动下载切块（115块）、文件上传、URL导入、搜索爬虫 |
| 检索层 | `rag_store.py` + `agent.py` | TF-IDF关键词检索、中文停用词过滤、KYC智能提权、标题去重 |
| 回答层 | `kb_agent.py` + `server.py` | RAG上下文组装、7种AI生成、mem0记忆注入、多轮对话历史 |
| 学习层 | `obsidian_writer.py` + `mem0_manager.py` | 每日日志、经验模式提炼、跨会话持久化记忆、30天自动清理 |

### 意图分类规则

| 类别 | 判定词 | 处理 | 实现 |
|------|--------|------|------|
| greeting | 你好、您好、hello、hi、谢谢、再见 | 返回"您好，有什么可以帮您？" | `kb_agent.py:85` |
| unknown | 天气、比特币、股票、预测 | 返回拒答引导语 | `kb_agent.py:98` |
| business | 我要充值、我要开卡、查余额 | 返回"功能开发中" | `biz_agent.py` |
| knowledge | 默认 | RAG 检索 → 多主题检测 → LLM 生成 | `kb_agent.py:148` |

### 多主题反问（P1-04）

当用户模糊提问（如只输入「KYC」），检索命中多个不相干的一级主题时，系统不直接回答，而是列出选项反问用户确认。

**实现原理：**

```
search_docs("KYC")
  │
  ▼
去重后的 Top 8 命中块
  │  title_path = "Card issuing API 文档 > KYC > 提交用户 KYC 数据"
  │  title_path = "KYC — 搜索导入 > 来源 3"
  ▼
提取一级主题（按 " > " 分割取第一段）
  │  "Card issuing API 文档"
  │  "KYC — 搜索导入"
  ▼
统计不同主题数 ≥ 2？
  │  是 → 返回 clarify，反问用户
  │  否 → 继续走 RAG + LLM 生成
```

**关键代码**（`src/agent.py`）：
```python
# 提取每个命中块的一级主题
topics = []
for h in hits[:8]:
    title = h['chunk'].get('title_path', '')
    top = title.split(' > ')[0]       # 取第一段
    if top and top not in topics:
        topics.append(top)

# ≥2 个不同一级主题 → 反问
if len(topics) >= 2:
    return {"clarify": True, "clarify_topics": topics}
```

`src/agents/kb_agent.py` 收到 `clarify=True` 后**不调 AI，零延迟**直接返回反问语。

**示例：**
- 问「KYC」→ "您想了解 KYC 概念说明，还是 Card issuing API 的 KYC 接口？"

### 数据流（完整）

```
server.py /api/ask
  │
  ├─ classify_intent(question)
  │     │
  │     ├─ "greeting"    →  直接返回问候
  │     ├─ "unknown"     →  直接返回拒答引导
  │     ├─ "business"    →  biz_agent.handle()
  │     │
  │     └─ "knowledge"   →  kb_agent.chat()
  │                           │
  │                           ├─ search_docs()    ←  rag_store.py + agent.py
  │                           ├─ mem_recall()     ←  mem0_manager.py
  │                           ├─ call_ai_api()    ←  server.py → LLM
  │                           ├─ learn()          ←  obsidian_writer.py
  │                           └─ mem_remember()   ←  mem0_manager.py
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
| **知识库Agent** | knowledge 意图 | RAG检索10个文档块 → mem0记忆注入 → LLM生成 → 学习归档 | ✅ |
| **业务Agent** | business 意图 | 对接真实 API（充值/开卡/冻结等），当前占位返回"开发中" | 🚧 |

---

## 功能特性

**问答能力**
- 自动检索文档并生成中文/英文回答
- 回答包含 API 地址、方法、完整参数表、错误码、示例代码
- 知识库外的问题直接礼貌拒答，不编造内容
- 模糊问题主动追问确认场景（如问「KYC」会反问是想了解概念还是查API接口）
- 同一会话内支持 6 轮对话上下文关联
- 每条回答标注置信度和匹配的文档片段

**AI 提供商**
- 支持 7 种：OpenAI / Anthropic / 硅基流动 / DeepSeek / 通义千问 / 智谱 GLM / 自定义
- 配置仅需三项：API URL + 模型名 + API Key
- 前端一键测试连接
- API Key 安全脱敏显示

**知识库管理**
- 三种导入方式：上传文件、粘贴 URL（GitHub 自动适配）、搜索爬虫
- 来源可查、可删、可一键恢复默认
- 爬虫无需第三方 API Key

**mem0 记忆系统**
- 跨会话持久化用户画像和对话历史
- 语义搜索、自动提取关键信息
- 本地 fastembed，无需外部 API

**前端体验**
- 暗色主题界面，侧栏 + 聊天区布局
- Markdown 富文本渲染（表格斑马纹、代码块等宽字体、标题层级）
- AI 设置面板（7 种提供商卡片、参数配置、连接测试）
- 文档管理面板（上传/URL/搜索 + 文件浏览/删除）
- 快捷问题入口
- 用户列表 + 记忆详情查看

---

## 项目结构

```
mem0-demo/
├── server.py                # FastAPI 入口（路由 + AI 配置管理）
├── src/
│   ├── agent.py             # 文档检索协调层（关键词增强、KYC提权、去重）
│   ├── doc_loader.py        # Markdown 文档加载器（按 H2/H3 切块）
│   ├── rag_store.py         # RAG 检索层（ChromaDB 向量 / TF-IDF 降级）
│   ├── mem0_manager.py      # mem0 记忆管理（本地 fastembed）
│   ├── simple_memory.py     # 本地记忆备选方案（sentence-transformers）
│   ├── obsidian_writer.py   # 问答日志 + 经验模式写入
│   ├── web_crawler.py       # 搜索导入爬虫（DuckDuckGo + BS4）
│   ├── dspy_rag_optimizer.py # DSPy 优化模块（可选）
│   └── agents/
│       ├── __init__.py
│       ├── kb_agent.py      # 知识库 Agent（意图分类 + RAG检索 + LLM生成）
│       └── biz_agent.py     # 业务 Agent（占位，待对接真实 API）
├── static/
│   └── index.html           # 前端单页应用（侧栏+聊天+AI设置面板）
├── docs/                    # 项目文档（详见下方导航）
│   ├── 多Agent架构设计.md
│   ├── 真实测试用例集.md
│   ├── 问题清单.md
│   ├── 项目总结.md
│   └── 部署文档.md
├── tests/
│   └── regression_test.py   # 回归测试（18 个用例）
├── data/
│   └── knowledge/           # 知识库文档（热加载，gitignored）
├── requirements.txt         # Python 依赖
├── api_config.json          # AI 配置（gitignored，含 API Key）
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
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-v4-flash",
  "enabled": true
}
```

支持的 provider：`openai` / `anthropic` / `siliconflow` / `deepseek` / `qwen` / `zhipu` / `custom`

### 启动

```bash
python3 server.py
# 访问 http://localhost:8000
```

首次启动自动下载文档、初始化 RAG 向量库。

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
| `base_url` | str | 自定义 API 地址 |
| `model` | str | 模型名称 |
| `temperature` | float | 0.0-2.0，默认 0.7 |
| `max_tokens` | int | 最大输出 token，默认 2048 |
| `enabled` | bool | 是否启用 AI |

### .gitignore 保护

以下文件不会被提交到 Git：
```
api_config.json    # AI Key
memory_store.json  # 会话数据
.env / .env_back   # 环境变量
.workbuddy/        # 工作数据
data/              # 知识库
chroma_db/ mem0_db/  # 向量库
logs/              # 运行日志
```

---

## API 接口

### 核心接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ask` | 问答（请求体：`{"question":"...", "user_id":"..."}`） |
| GET | `/api/health` | 健康检查（文档块数、AI状态） |

### 配置接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取当前 AI 配置（Key 脱敏） |
| POST | `/api/config` | 更新 AI 配置 |
| POST | `/api/config/test` | 测试 AI 连接（`{"api_key":"...", "base_url":"..."}`） |

### 文档管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/docs/chunks` | 查看文档块列表 |
| POST | `/api/docs/refresh` | 重新从 GitHub 拉取文档 |
| GET | `/api/docs/sources` | 查看文档来源 |
| POST | `/api/docs/import` | 导入 URL 文档 |
| POST | `/api/docs/upload` | 上传文件 |
| POST | `/api/docs/crawl` | 搜索关键词并导入 |
| DELETE | `/api/docs/remove?name=...` | 删除文档 |
| DELETE | `/api/docs/reset` | 恢复默认文档 |

### 回答格式（`/api/ask` 响应）

```json
{
  "answer": "根据知识库内容，KYC 认证需要提交以下材料...",
  "source": "ai",
  "doc_hits": 10,
  "confidence": 0.8,
  "chunks_used": [
    {"title_path": "Card issuing API > KYC > 提交用户KYC数据", "score": 99.0, "content": "..."}
  ],
  "method": "keyword"
}
```

---

## 前端使用

- 打开 `http://localhost:8000`
- 左侧栏：用户选择 / 知识库浏览 / 用户记忆
- 主区域：聊天对话 + 快捷问题入口
- 顶部 ⚙️ 按钮 → 知识库管理（文档列表 / 手动录入 / 搜索导入 / 文件管理）
- 顶部 ⚙️ AI 设置：提供商选择 → 填 API Key → 测试连接 → 保存

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
| 单轮 | TC-01 ~ TC-10 | 寒暄、KYC材料、业务外话题、对比、401错误、记忆召回、重复提问、多问题、混合意图、订单查询 |
| 多轮 | M01 ~ M08 | 8 轮连续对话：API对接咨询 → 认证 → 401排查 → Python示例 → 时间戳单位 → 机构确认 → KYC示例 → 失败重试 |

**当前通过率：18/18**

---

## 文档导航

详细文档见 `docs/` 目录：

| 文档 | 说明 |
|------|------|
| [🏗️ 多Agent架构设计](docs/多Agent架构设计.md) | 三层 Agent 架构（主路由 → 知识库 Agent → 业务 Agent）、意图分类设计、数据流、改动清单 |
| [🧪 真实测试用例集](docs/真实测试用例集.md) | 18 个真实场景测试用例（单轮10 + 多轮8），含预期行为、实际回答、通过判定 |
| [🐛 问题清单与修复方案](docs/问题清单.md) | 已知问题按 P0/P1/P2 排列，含根因分析、修复方案、验证方法 |
| [📋 项目总结](docs/项目总结.md) | 项目定位、架构、功能清单、运行数据、质量保障 |
| [🚀 部署文档](docs/部署文档.md) | 环境要求、安装依赖、配置、systemd/pm2/Nginx、故障排查 |

---

## 版本历史

**v4.0.0** (2026-05-25)

- 多 Agent 架构（意图分类 + 子 Agent 路由）
- mem0 跨会话记忆系统
- Markdown 完整渲染（H4、表格、代码块）
- 知识库 115 块，经验模式 61 条
- AI 设置面板（7 种提供商 + 连接测试）
- 文档动态管理（上传/URL/搜索导入）
- 前端响应式布局
- 多主题反问（P1-04）：检索跨主题时先反问确认，不浪费 AI 调用
- `info_provide` 意图移除（修复 echo 问题）
- 英文提问语言自适应
- 工程化目录结构 `src/` + `tests/`

---

## License

MIT
