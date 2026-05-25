# SKILL.md — API Doc QA Bot

## 概述

**API Doc QA Bot** 是一个文档驱动的自学习问答系统，基于 DSPy 框架优化。

核心能力：
1. **文档即知识源** — 从 GitHub Markdown / 本地文件自动加载 API 文档
2. **DSPy RAG 精准检索** — DSPy 原生 Retrieve 组件 + TF-IDF 关键词检索（零依赖降级）
3. **自动学习迭代** — 每次问答后自动写 Obsidian 日志 + 提炼经验模式
4. **BootstrapFewShot 优化** — 基于训练样本自动优化 Prompt 和回答质量
5. **DSPy 链式推理** — 支持 ChainOfThought 多步推理处理复杂问题

---

## 目录结构

```
.
├── doc_loader.py           # 文档加载 & Markdown 切块
├── rag_store.py            # 向量检索层（语义/关键词双模式）
├── agent.py                # 核心问答逻辑 + DSPy 集成
├── obsidian_writer.py      # Obsidian 日志 & 经验模式管理
├── dspy_rag_optimizer.py   # DSPy 优化器（新增）
├── server.py               # FastAPI 后端
├── static/
│   └── index.html         # 问答前端界面
├── obsidian_vault/
│   ├── qa_logs/           # 每日问答日志（YYYY-MM-DD.md）
│   ├── patterns/          # 经验模式库（patterns.json + patterns.md）
│   └── summaries/         # 周期总结
├── docs_cache/             # 文档原始 Markdown 及切块缓存
├── .env                    # 环境变量配置
└── requirements.txt        # 依赖清单
```

---

## 快速启动

### 1. 最小安装（无 LLM，无向量库）

```bash
pip install fastapi uvicorn python-dotenv
python server.py
# 浏览器访问 http://localhost:8000
```

> 系统自动下载文档，用关键词检索 + 文档模板回答，完全可用。

### 2. 启用语义检索（推荐）

```bash
pip install chromadb sentence-transformers
python server.py
```

首次运行会下载 ~90MB 的多语言 Embedding 模型（`paraphrase-multilingual-MiniLM-L12-v2`）。

### 3. 启用 DSPy 优化（最佳效果）

创建 `.env`：

```dotenv
# OpenAI
OPENAI_API_KEY=sk-xxx
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini

# 或 Anthropic
# ANTHROPIC_API_KEY=sk-ant-xxx
# LLM_PROVIDER=anthropic
# LLM_MODEL=claude-3-haiku-20240307
```

启动时自动：
- 加载 `patterns.json` 中的经验模式转为 `dspy.Example` 训练集
- 执行 `BootstrapFewShot` 优化
- 后续问答使用优化后的模型

---

## DSPy 优化体系

### 架构图

```
用户提问
    ↓
┌─────────────────────────────────────────────────────┐
│  Layer 0: DSPy 路由器 — 问题分类 + 策略选择        │
│         KYC/Card/Deposit/Balance/Exchange/Webhook  │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  Layer 1: 经验库 → dspy.Example 训练集             │
│         patterns.json → BootstrapFewShot            │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  Layer 2: DSPy RAG — dspy.Retrieve 原生组件        │
│         语义检索 → ChromaDB → TF-IDF 降级          │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  Layer 3: DSPy 生成器 — BootstrapFewShot 优化后   │
│         ChainOfThought 多步推理（可选）              │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  Layer 4: 自动学习 — 日志 + 经验提炼               │
│         置信度 ≥ 0.65 → upsert_pattern              │
└─────────────────────────────────────────────────────┘
```

### DSPy 组件

| 组件 | 说明 |
|------|------|
| `dspy.Example` | 训练样本，patterns.json 转训练集 |
| `dspy.Signature` | 输入输出规范定义 |
| `dspy.Predict` | 基础预测器 |
| `dspy.ChainOfThought` | 链式推理，处理复杂问题 |
| `dspy.Retrieve` | 原生 RAG 检索 |
| `BootstrapFewShot` | 自动优化 Prompt |
| `answer_quality_metric` | 自定义评估函数 |

### 手动重新优化

```python
from agent import retrain_dspy, get_module_info

# 查看模块状态
info = get_module_info()
print(info)

# 重新优化（当 patterns.json 有足够样本时）
result = retrain_dspy()
print(result)
```

### 自定义 Metric

系统使用 `answer_quality_metric` 评估回答质量：

```python
from dspy_rag_optimizer import answer_quality_metric

# 评估单个回答
score = answer_quality_metric(example, prediction)
# 维度：长度合理性(0.2) + 文本相似度(0.5) + 关键词覆盖(0.3)
```

---

## 接入自己的文档

### 方式 1：修改 doc_loader.py 中的 DEFAULT_DOCS

```python
DEFAULT_DOCS = [
    {
        "name": "My API FAQ",
        "url": "https://raw.githubusercontent.com/your-org/repo/main/FAQ.md",
        "local": None,
    },
    {
        "name": "My API Reference",
        "url": None,
        "local": "/path/to/local/api-reference.md",  # 本地文件
    },
]
```

### 方式 2：环境变量动态追加

```dotenv
EXTRA_DOCS_JSON=[{"name":"My Doc","url":"https://...","local":null}]
```

### 方式 3：API 动态刷新

```bash
curl -X POST http://localhost:8000/api/docs/refresh
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ask` | 问答接口 |
| POST | `/api/ask/ai` | 纯 AI 问答（不依赖文档） |
| POST | `/api/ask/hybrid` | 混合问答：文档检索 + AI 生成 |
| GET  | `/api/config` | 获取当前 AI 配置 |
| POST | `/api/config` | 更新 AI 配置 |
| POST | `/api/config/test` | 测试 AI API 连接 |
| GET  | `/api/health` | 系统状态 |
| GET  | `/api/docs/chunks` | 查看文档块（支持 `?q=` 搜索） |
| GET  | `/api/docs/sources` | 查看文档来源统计 |
| POST | `/api/docs/refresh` | 强制从 GitHub 重新拉取文档 |
| GET  | `/api/patterns` | 获取全部经验模式 |
| POST | `/api/summary` | 触发经验总结生成 |
| GET  | `/api/vault/files` | Obsidian Vault 文件列表 |
| GET  | `/api/vault/read` | 读取指定 Vault 文件内容 |

### 问答接口示例

**请求：**
```json
POST /api/ask
{
  "question": "How to do KYC? What documents are required?"
}
```

**响应：**
```json
{
  "answer": "KYC requires a **passport** only...",
  "source": "dspy",           // "pattern" | "dspy" | "template"
  "doc_hits": 3,
  "confidence": 0.88,
  "chunks_used": [
    {"title_path": "PayCrypto API FAQ > KYC 所需材料", "score": 0.91}
  ],
  "method": "semantic"        // "semantic" | "keyword" | "pattern"
}
```

---

## 自学习机制

```
每次问答
    ↓
写 obsidian_vault/qa_logs/YYYY-MM-DD.md
    ↓
置信度 ≥ 0.65 → upsert_pattern()
    ↙                  ↘
已有相似模式         全新问题
    ↓                    ↓
更新命中次数          新增经验条目
如新答案更长则更新
    ↓
patterns.json 样本增加
    ↓
下次启动时 BootstrapFewShot 重新优化
```

---

## 对接 Bot 集成

### Telegram / Discord / Slack

在你的 Bot handler 中调用：

```python
import httpx

async def on_message(user_question: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "http://localhost:8000/api/ask",
            json={"question": user_question}
        )
        data = r.json()
        return data["answer"]
```

### Webhook 模式

可在 server.py 中增加 `/webhook/telegram` 等路由，直接接收平台推送。

---

## 依赖清单

```
# 核心（必选）
fastapi>=0.110.0
uvicorn>=0.27.0
python-dotenv>=1.0.0
pydantic>=2.0.0

# 语义检索（可选，强烈推荐）
chromadb>=0.5.0
sentence-transformers>=3.0.0

# DSPy（可选，启用优化功能）
dspy-ai>=2.5.0
```

---

## 经验总结

可通过前端「生成经验总结」按钮或调用 `POST /api/summary` 触发，
总结文件写入 `obsidian_vault/summaries/summary_YYYY-WXX.md`。

---

## 故障排除

| 问题 | 解决方案 |
|------|----------|
| "训练样本不足" | 确保 patterns.json 有至少 2 条经验 |
| "DSPy 初始化失败" | 检查 API Key 配置和 DSPy 版本 |
| 回答质量下降 | 积累更多经验后重新启动服务自动优化 |
| 检索结果不准确 | 删除 `docs_cache/` 重新拉取文档 |
