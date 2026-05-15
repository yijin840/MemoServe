# MemoServe — 多 Agent 智能客服系统

> 基于 **mem0ai + 通义千问 (Qwen) + ChromaDB + RAG** 的多 Agent 智能客服系统  
> 支持 Web 对话、Telegram Bot 接入、知识库动态加载、跨会话用户记忆

[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [配置参数详解](#配置参数详解)
- [服务管理（run.sh）](#服务管理runsh)
- [项目结构](#项目结构)
- [架构说明](#架构说明)
- [API 文档](#api-文档)
- [知识库管理](#知识库管理)
- [Telegram Bot 配置](#telegram-bot-配置)
- [生产部署指南](#生产部署指南)
- [已知问题与待优化项](#已知问题与待优化项)
- [故障排查](#故障排查)
- [相关文档](#相关文档)

---

## 功能特性

- **多 Agent 架构** — Router Agent 意图识别，自动路由到知识库 Agent 或商户 Agent
- **RAG 知识库问答** — ChromaDB 向量检索 + Qwen Embedding，支持动态加载知识库
- **跨会话用户记忆** — mem0ai 持久化用户画像和对话历史，重启不丢失
- **双模式回答缓存** — 精确匹配 + 语义相似度缓存，降低延迟和成本
- **多渠道接入** — Web 界面（FastAPI）+ Telegram Bot 同时支持
- **流式 SSE 输出** — 支持 `/chat/stream` 实时流式返回答案

---

## 快速开始

### 环境要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Python | ≥ 3.9 | 推荐 3.9+ |
| pip | 最新版 | 安装 Python 依赖 |
| DashScope API Key | — | 阿里云通义千问 API Key（[获取地址](https://dashscope.aliyun.com/)） |
| Telegram Bot Token | 可选 | 如需 Telegram Bot 接入，向 [@BotFather](https://t.me/BotFather) 申请 |

### 1. 克隆项目

```bash
git clone git@github.com:yijin840/MemoServe.git
cd MemoServe
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，**必须填写** `DASHSCOPE_API_KEY`：

```env
# 必填：阿里云 DashScope API Key
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# 可选：Telegram Bot Token（不需要 Bot 接入可留空）
TELEGRAM_BOT_TOKEN=

# 服务端口（默认 8000）
APP_PORT=8000
```

> ⚠️ 完整参数说明见 [配置参数详解](#配置参数详解) 章节。

### 4. 启动服务

推荐使用 `run.sh` 管理服务：

```bash
# 启动所有服务（main + bot）
./run.sh start

# 查看运行状态
./run.sh status

# 查看实时日志
./run.sh logs        # 所有日志
./run.sh logs main   # 只看 Web 服务日志
./run.sh logs bot    # 只看 Bot 日志

# 停止所有服务（优雅停止，先 SIGTERM，15s 后 SIGKILL）
./run.sh stop

# 重启所有服务
./run.sh restart
```

也可以直接运行（仅启动 Web 服务，不含 Bot）：

```bash
python main.py
# 或
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. 访问服务

| 地址 | 说明 |
|------|------|
| http://localhost:8000 | Web 聊天界面 |
| http://localhost:8000/docs | Swagger API 文档（在线调试） |
| http://localhost:8000/redoc | ReDoc API 文档 |

---

## 配置参数详解

所有参数在 `.env` 文件中配置，以下按功能分组说明。

### 一、Qwen / DashScope 配置

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `DASHSCOPE_API_KEY` | **必填，无默认值** | 阿里云 DashScope API Key。[获取地址](https://dashscope.aliyun.com/) |
| `QWEN_MODEL` | `qwen-plus` | 对话使用的 Qwen 模型。`qwen-plus` / `qwen-turbo` / `qwen-max` 等，见 [DashScope 模型列表](https://help.aliyun.com/zh/model-studio/) |
| `QWEN_EMBEDDING_MODEL` | `text-embedding-v3` | Embedding 模型，用于 RAG 向量化。`text-embedding-v3` 是推荐值 |
| `ROUTER_MODEL` | `qwen-plus` | Router Agent 意图分类使用的模型，可用更轻量的模型（如 `qwen-turbo`）降低成本 |

### 二、ChromaDB 配置

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `CHROMA_PERSIST_PATH` | `./data/chroma_db` | ChromaDB 数据持久化目录，重启不丢失 |
| `CHROMA_COLLECTION_NAME` | `customer_service_kb` | 知识库向量数据的 Collection 名称 |
| `CHROMA_MEMORY_COLLECTION` | `mem0_memory` | mem0 用户记忆的 Collection 名称 |

### 三、RAG 配置

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `RAG_TOP_K` | `5` | 每次检索返回 Top-K 个最相关文档片段 |
| `RAG_CHUNK_SIZE` | `500` | 知识库文本分块大小（字符数），需大于 `RAG_CHUNK_OVERLAP` |
| `RAG_CHUNK_OVERLAP` | `50` | 文本分块重叠字符数，保证上下文连贯 |
| `RAG_SCORE_THRESHOLD` | `0.4` | RAG 检索最低相关度阈值（0~1）。**生产环境建议 0.6~0.7**，低于此值的检索结果不会被使用，避免乱答 |

### 四、mem0 记忆配置

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `MEM0_TOP_K` | `5` | 每次对话检索的历史记忆条数 |
| `MEM0_SCORE_THRESHOLD` | `0.3` | 记忆检索最低相关度阈值（0~1），生产建议 0.4 |
| `MEM0_CUSTOM_INSTRUCTIONS` | 空 | 自定义 mem0 记忆提取指令，控制从对话中提取什么信息 |
| `MEM0_MEMORY_STRATEGY` | `customer_service` | 记忆提取策略：`customer_service`（客服场景）/ `general`（通用）/ `strict`（严格模式） |

### 五、应用配置

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `APP_PORT` | `8000` | FastAPI 服务监听端口 |
| `APP_HOST` | `0.0.0.0` | 监听地址，`0.0.0.0` 允许外部访问 |
| `DEBUG` | `true` | 调试模式。`true` 时开启详细日志；生产环境设为 `false` |

### 六、Telegram Bot 配置

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `TELEGRAM_BOT_TOKEN` | 空 | Telegram Bot Token。向 [@BotFather](https://t.me/BotFather) 申请，不需要 Bot 可留空（Bot 不会启动） |

### 七、回答缓存配置（当前被注释，需手动启用）

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `ENABLE_ANSWER_CACHE` | `true`（代码中硬编码） | 是否启用回答缓存。启用后相同/相似问题直接返回缓存答案 |
| `CACHE_TTL` | `3600` | 缓存过期时间（秒），默认 1 小时 |

> ⚠️ 当前缓存代码被注释（`knowledge_agent.py` 第 222-228 行、381-389 行），启用需取消注释。见 [已知问题](#已知问题与待优化项)。

### 八、生产安全配置（生产部署必改）

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `JWT_SECRET_KEY` | `change-me-in-production!` | JWT 签名密钥，**生产必须修改**为一个随机长字符串 |
| `CORS_ORIGINS` | `*` | CORS 允许的域名列表，生产环境应改为具体域名，如 `https://yourdomain.com` |

---

## 服务管理（run.sh）

项目根目录提供 `run.sh` 脚本，支持优雅启动、停止、重启和日志查看。

### 子命令说明

```bash
./run.sh start     # 启动所有服务（main + bot）
./run.sh stop      # 优雅停止所有服务
./run.sh restart   # 重启所有服务（先 stop，再 start）
./run.sh status    # 查看所有服务运行状态
./run.sh logs      # 实时查看所有日志（tail -f）
./run.sh logs main # 只看 Web 服务日志
./run.sh logs bot  # 只看 Bot 日志
```

### 优雅停止说明

1. 发送 `SIGTERM` 信号，等待进程优雅退出（处理完当前请求）
2. 最多等待 15 秒
3. 若仍未退出，发送 `SIGKILL` 强制终止

### PID 和日志位置

| 类型 | 路径 |
|------|------|
| main PID | `.pids/main.pid` |
| bot PID | `.pids/bot.pid` |
| main 日志 | `logs/main.log` |
| bot 日志 | `logs/bot.log` |

> `.pids/` 和 `logs/` 目录会在首次运行时自动创建，建议加入 `.gitignore`。

---

## 项目结构

```
MemoServe/
├── main.py                  # FastAPI 入口，注册路由和生命周期
├── router_agent.py          # Router Agent：意图识别 + 多 Agent 路由
├── knowledge_agent.py       # 知识库 Agent：RAG 检索 + LLM 生成 + 记忆
├── rag_knowledge_base.py   # RAG 知识库：ChromaDB + Qwen Embedding
├── memory_manager.py       # mem0ai 记忆管理：用户画像 + 对话存档
├── answer_cache.py         # 双模式回答缓存（精确 + 语义）
├── telegram_bot.py        # Telegram Bot 接入
├── config.py               # 全局配置，从 .env 加载
├── prod_grounding_check.py # 生产加固：答案真实性校验
├── requirements.txt       # Python 依赖清单
├── run.sh                  # 服务管理脚本（start/stop/restart/status/logs）
├── .env.example            # 环境变量模板
├── static/
│   └── index.html          # Web 聊天前端
├── data/
│   ├── chroma_db/          # ChromaDB 持久化数据（自动创建）
│   └── knowledge/           # 知识库原始文件（.md/.txt/.pdf/.docx）
└── logs/                   # 运行日志（自动创建，已加入 .gitignore）
```

---

## 架构说明

### 系统架构图

```
用户输入
   │
   ▼
┌─────────────────────────────────────┐
│         Router Agent                 │  ← 统一入口，意图识别
│  · 关键词快速匹配（0ms，免 LLM）    │
│  · LLM 兜底路由                    │
│  · 支持多意图串行组合              │
└────────────┬────────────────────────┘
             │
    ┌────────┴────────┐
    ▼                 ▼
KnowledgeAgent    (可扩展其他 Agent)
(知识库 Agent)
    │
    ▼
┌─────────────────────────────────────┐
│  RAG 检索 → LLM 生成 → 记忆保存    │
│  + 回答缓存（启用时）               │
└─────────────────────────────────────┘
```

### 意图分类说明

Router Agent 支持以下意图：

| 意图 | 说明 | 处理方式 |
|------|------|----------|
| `faq` | 常见问题 | 走 KnowledgeAgent（RAG + 记忆） |
| `greeting` | 问候语 | 直接返回预设响应，不调 LLM |
| `crypto_topup` | 稳定币充值 | 走 KnowledgeAgent，可扩展专有知识库 |
| `technical_concept` | 技术概念解释 | 走 KnowledgeAgent，可扩展直连 LLM |
| `unknown` | 无法识别 | 返回兜底回答，建议转人工 |

### 多意图串行处理

当用户一句话包含多个意图时，Router Agent 会串行调用多个 Agent，结果自动合并：

```
用户：「查一下我的订单，顺便问退货政策」
         ↓
  Router 识别 → ["merchant", "knowledge"]
         ↓
  [1] MerchantAgent.chat()  →  "您的订单已发货"
  [2] KnowledgeAgent.chat() →  "退货政策：7天无理由..."
         ↓
  合并答案 → "您的订单已发货\n\n退货政策：7天无理由..."
```

---

## API 文档

服务启动后访问 http://localhost:8000/docs 可在线调试所有接口。

### 对话接口

#### POST `/chat` — 普通对话

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "如何退款？",
    "user_id": "user_001",
    "history": []
  }'
```

**响应示例：**

```json
{
  "answer": "7天无理由退款，请联系客服处理。",
  "rag_sources": [
    {
      "text": "退款政策：7天无理由退款...",
      "source": "退款政策.txt",
      "score": 0.82
    }
  ],
  "memories_used": [
    {
      "text": "用户偏好：希望快速退款",
      "score": 0.75
    }
  ],
  "model": "qwen-plus",
  "routed_to": ["knowledge"]
}
```

#### GET `/chat/stream` — 流式对话（SSE）

```bash
curl "http://localhost:8000/chat/stream?message=如何退款&user_id=user_001"
```

返回 `text/event-stream` 格式的 SSE 流，逐 token 返回答案。

### 知识库管理接口

```bash
# 添加文本到知识库
curl -X POST http://localhost:8000/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{"text": "退款政策：7天无理由退款。", "source": "退款政策.txt"}'

# 上传文件（.txt / .md / .pdf / .docx）
curl -X POST http://localhost:8000/knowledge/file \
  -F "file=@/path/to/document.pdf"

# 查看知识库中的所有来源
curl http://localhost:8000/knowledge/list

# 删除指定来源的所有文档
curl -X DELETE http://localhost:8000/knowledge/退款政策.txt
```

### 记忆管理接口

```bash
# 查看用户的记忆
curl http://localhost:8000/memory/user_001

# 清除用户的所有记忆
curl -X DELETE http://localhost:8000/memory/user_001
```

---

## 知识库管理

### 支持的文件格式

| 格式 | 说明 |
|------|------|
| `.txt` / `.md` | 纯文本 / Markdown |
| `.pdf` | PDF 文档（自动提取文字） |
| `.docx` | Word 文档（自动提取文字） |

### 知识库文件放置

将知识库文件放入 `data/knowledge/` 目录，然后通过 API 或启动时自动加载：

```bash
# 放置文件
cp your_document.md data/knowledge/

# 通过 API 热加载（推荐，无需重启）
curl -X POST http://localhost:8000/knowledge/file -F "file=@your_document.md"
```

### RAG 分块参数调优

| 场景 | 建议 `RAG_CHUNK_SIZE` | 建议 `RAG_CHUNK_OVERLAP` |
|------|------------------------|----------------------------|
| 问答对（FAQ） | `300` | `30` |
| 长文档（政策条款） | `800` | `100` |
| 技术文档（API 文档） | `500` | `50` |

> ⚠️ 必须保证 `RAG_CHUNK_OVERLAP < RAG_CHUNK_SIZE`，否则会死循环。

---

## Telegram Bot 配置

### 1. 申请 Bot Token

1. 在 Telegram 中打开 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot`，按提示设置用户名
3. 获得 Bot Token（形如 `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`）
4. 将 Token 填入 `.env` 的 `TELEGRAM_BOT_TOKEN`

### 2. 启动 Bot

```bash
./run.sh start
```

启动后：
- **私聊**：直接用 Telegram 给 Bot 发消息即可
- **群聊**：将 Bot 加入群，消息中 @bot 或回复 bot 消息触发

### 3. 群聊用户绑定

群聊中默认用 `telegram_group_{chat_id}` 作为 user_id。若要让群聊使用固定 user_id（以复用记忆）：

```
# 在群聊中发送：
/setid my_company_user

# 重置为默认：
/resetid

# 查看当前绑定：
/whoami
```

> ⚠️ `GROUP_USER_MAP` 当前仅内存存储，重启后丢失。见 [已知问题](#已知问题与待优化项)。

---

## 生产部署指南

### 生产 .env 配置（参考）

```env
# ========== 必须修改 ==========
DASHSCOPE_API_KEY=sk-your-real-key-here

# RAG 阈值调高，防止乱答
RAG_SCORE_THRESHOLD=0.65
MEM0_SCORE_THRESHOLD=0.4

# JWT 密钥（必须修改为一个随机长字符串！）
JWT_SECRET_KEY=your-random-secret-key-here-change-this

# ========== 安全 ==========
# CORS：改为具体域名，不要用 *
CORS_ORIGINS=https://yourdomain.com

# 关闭调试模式
DEBUG=false

# ========== 性能优化 ==========
# 启用回答缓存（需先取消 knowledge_agent.py 中的注释）
# CACHE_TTL=3600

# ========== 可选 ==========
# 人工介入开关（低置信度时转人工）
# ENABLE_HUMAN_HANDOFF=false
# HANDOFF_THRESHOLD=0.5
```

### 生产部署检查清单

- [ ] `DASHSCOPE_API_KEY` 已填写真实 Key
- [ ] `RAG_SCORE_THRESHOLD` 已调高至 `0.65` 以上
- [ ] `JWT_SECRET_KEY` 已修改为随机字符串
- [ ] `DEBUG` 已设为 `false`
- [ ] `CORS_ORIGINS` 已改为具体域名（非 `*`）
- [ ] 启用回答缓存（取消 `knowledge_agent.py` 相关注释）
- [ ] 配置进程管理器（systemd / supervisor）自动拉起服务
- [ ] 配置 Nginx 反向代理 + HTTPS
- [ ] 配置日志轮转（logrotate）

### systemd 服务配置（参考）

创建 `/etc/systemd/system/memoserve.service`：

```ini
[Unit]
Description=MemoServe Customer Service
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/MemoServe
ExecStart=/path/to/MemoServe/run.sh start
ExecStop=/path/to/MemoServe/run.sh stop
Restart=on-failure
RestartSec=10
Environment="PATH=/usr/bin:/usr/local/bin"

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable memoserve
sudo systemctl start memoserve
sudo systemctl status memoserve
```

---

## 已知问题与待优化项

### 🔴 高优先级

#### 问题 1：回答缓存被禁用

**位置：** `knowledge_agent.py` 第 222-228 行（读缓存）、第 381-389 行（写缓存）

**影响：** 每个问题都走完整 RAG 检索 + LLM 调用，延迟高、Token 成本高。

**修复：** 取消相关代码注释，重新启用缓存。

#### 问题 2：`GROUP_USER_MAP` 不持久化

**位置：** `telegram_bot.py` 第 31 行

```python
GROUP_USER_MAP: dict[str, str] = {}  # ⚠️ 仅内存，重启丢失
```

**影响：** Bot 重启后所有群的 `/setid` 绑定丢失。

**修复：** 改为 JSON 文件读写（见 `PROD_DEPLOY_PLAN.md` 第四节）。

#### 问题 3：流式模式不保存记忆

**位置：** `knowledge_agent.py` `stream_chat()` 方法

**影响：** 使用 SSE 流式对话的用户，其对话不会被记录到 mem0 记忆中。

**修复：** 在 `stream_chat()` 末尾补充记忆保存逻辑（见 `PROD_DEPLOY_PLAN.md` 第三节）。

### 🟡 中优先级

#### 问题 4：Markdown → Telegram HTML 转换不完整

`markdown_to_telegram_html()` 对表格、嵌套格式、多级引用的转换不完整。

**建议：** 复杂格式知识库输出时先转为纯文本，或限制知识库输出格式。

#### 问题 5：`config.py` 缺少阈值合理性校验

`RAG_SCORE_THRESHOLD`、`MEM0_SCORE_THRESHOLD` 等值若为非法值（如 >1 或 <0），启动时不会报错。

**建议：** 在 `check_startup()` 中增加范围校验。

### 🟢 优化建议

- **意图后处理：** `crypto_topup` 和 `technical_concept` 可路由到专有知识库子集，减少无关检索
- **ChromaDB 分离：** 知识库和对话存档可使用不同的 ChromaDB 实例
- **监控指标：** 增加 Prometheus / OpenTelemetry 指标（对话耗时、意图分布、缓存命中率）

---

## 故障排查

### 启动失败

**现象：** `./run.sh start` 后 `./run.sh status` 显示未运行

**排查步骤：**

```bash
# 查看启动日志
cat logs/main.log
cat logs/bot.log

# 常见原因：
# 1. DASHSCOPE_API_KEY 未配置 → 填写 .env
# 2. 端口被占用 → 修改 APP_PORT 或 kill 占用进程
# 3. ChromaDB 目录无写入权限 → chmod 755 data/chroma_db
```

### RAG 检索无结果 / 相关度低

```bash
# 检查知识库是否有数据
curl http://localhost:8000/knowledge/list

# 检查 RAG_SCORE_THRESHOLD 是否过高
# 在 .env 中调低 RAG_SCORE_THRESHOLD 后重启
```

### Bot 不响应群聊消息

- 确认在群聊中 @bot 或回复了 bot 消息
- 确认 `TELEGRAM_BOT_TOKEN` 配置正确
- 查看 `logs/bot.log` 报错信息

### 内存占用过高

ChromaDB 默认将所有向量加载到内存。若知识库很大：

```env
# 在 .env 中配置 ChromaDB 使用磁盘 + 内存混合模式
# （需要升级 ChromaDB 版本，或使用远程 ChromaDB）
```

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [PROD_DEPLOY_PLAN.md](PROD_DEPLOY_PLAN.md) | 生产部署完整方案（防止乱答、动态加载、记忆持久化、用户认证） |
| [SYSTEM_REVIEW.md](SYSTEM_REVIEW.md) | 系统完整审查报告（架构、已修复问题、剩余问题清单） |
| [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md) | Telegram Bot 详细配置指南 |
| [TEST_CASES.md](TEST_CASES.md) | 测试用例报告 |

---

## License

MIT License
