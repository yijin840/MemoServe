# PayCrypto API 智能客服系统

基于 RAG（检索增强生成）的 API 文档智能问答系统。支持多 Agent 架构：意图分类 → 知识库 Agent / 业务 Agent。内置文档检索（TF-IDF 关键词 + 可选 Chroma 向量检索）、会话记忆（simple_memory / mem0）、自主学习（经验模式提炼）等功能。

**适用场景**
- API 文档智能问答（技术支持机器人）
- 企业内部知识库问答
- 需要结合文档检索 + LLM 的客服场景

**技术栈**
- 后端：Python 3.9+ / FastAPI / Uvicorn
- 前端：原生 HTML + CSS + Vanilla JS（无框架依赖）
- LLM：OpenAI 兼容接口（支持 OpenAI / DeepSeek / 智谱等）
- 向量检索：ChromaDB + sentence-transformers（可选）
- 部署：单机运行，无需 Docker

---

## 1. 系统架构

```
                    ┌─────────────────────┐
                    │   前端 (浏览器)      │
                    │  static/index.html  │
                    └─────────┬───────────┘
                              │ HTTP / JSON
                              ▼
                    ┌─────────────────────┐
                    │   FastAPI 路由层     │
                    │  server.py /api/ask │
                    └─────────┬───────────┘
                              │
                  ┌───────────┴────────────┐
                  │  意图分类 (classify_intent)  │
                  └───────────┬────────────┘
               ┌───────┬───────┬──────────┬────────┐
               ▼       ▼       ▼          ▼        ▼
            greeting  info   unknown   business  knowledge
                                   │          │
                             ┌─────┴─────┐  ┌───┴──────────┐
                             │ biz_agent  │  │   kb_agent    │
                             │ (占位实现)  │  │CustomerServiceAgent│
                             └───────────┘  └────┬───────────┘
                                                 │
                              ┌──────────────────┼──────────────┐
                              ▼                  ▼              ▼
                         RAG 检索        记忆检索        LLM 调用
                        (rag_store)      (simple_      (call_ai_api)
                                            memory)
```

**核心模块**

| 文件 | 说明 |
|------|------|
| `server.py` | FastAPI 应用入口，路由定义，AI API 配置管理 |
| `agent.py` | 文档检索协调层，DSPy 优化模块（可选） |
| `agents/kb_agent.py` | 知识库 Agent（CustomerServiceAgent） |
| `agents/biz_agent.py` | 业务 Agent（占位，待对接真实 API） |
| `doc_loader.py` | Markdown 文档加载器，按标题切块 |
| `rag_store.py` | RAG 检索层（TF-IDF 降级 / Chroma 向量检索） |
| `obsidian_writer.py` | 问答日志、经验模式写入 Obsidian vault |
| `simple_memory.py` | 本地轻量记忆管理（sentence-transformers） |
| `mem0_manager.py` | mem0 记忆管理（可选，需 API 兼容） |
| `web_crawler.py` | 搜索导入爬虫（DuckDuckGo + BS4） |
| `regression_test.py` | 回归测试脚本 |
| `static/index.html` | 前端单页应用（聊天界面） |

---

## 2. 功能特性

- [x] 多 Agent 意图分类（greeting / info_provide / knowledge / business / unknown）
- [x] RAG 文档检索（TF-IDF 关键词 + Chroma 向量检索可选）
- [x] 支持多种 LLM 提供商（OpenAI / DeepSeek / 智谱 / 自定义兼容接口）
- [x] 会话记忆（按 user_id 隔离，支持 simple_memory 或 mem0）
- [x] 自主学习（自动提炼经验模式到 patterns.json）
- [x] 文档动态导入（URL 下载 / 本地上传 / 搜索导入）
- [x] 前端 Markdown 渲染（标题 / 加粗 / 代码块 / 表格 / 引用）
- [x] 多轮对话支持（按 session_id 隔离历史）
- [x] 回归测试脚本
- [x] 日志自动清理（超过 30 天自动删除）

---

## 3. 项目结构

```
dspy/
├── server.py              # FastAPI 入口
├── agent.py               # 检索协调 + DSPy 优化
├── agents/
│   ├── __init__.py
│   ├── kb_agent.py       # 知识库 Agent
│   └── biz_agent.py      # 业务 Agent
├── doc_loader.py         # 文档加载 + 切块
├── rag_store.py          # RAG 检索层
├── obsidian_writer.py    # Obsidian 日志写入
├── simple_memory.py      # 本地记忆管理
├── mem0_manager.py       # mem0 管理器（可选）
├── web_crawler.py        # 搜索导入爬虫
├── regression_test.py    # 回归测试
├── requirements.txt      # Python 依赖
├── api_config.json       # AI 配置（不提交，见 .gitignore）
├── memory_store.json     # 本地记忆存储（不提交）
├── docs_cache/           # 文档缓存（不提交）
├── chroma_db/            # Chroma 向量库（不提交）
├── mem0_db/              # mem0 数据库（不提交）
├── obsidian_vault/       # Obsidian vault（不提交）
└── static/
    └── index.html        # 前端页面
```

---

## 4. 安装部署

### 4.1 环境要求
- Python 3.9+
- pip / venv
- （可选）Docker 20.10+ / Docker Compose 2.0+

### 4.2 快速部署（推荐）

使用部署脚本（自动检查环境、安装依赖、生成配置、启动服务）：

```bash
cd /path/to/dspy
chmod +x deploy.sh

./deploy.sh           # 交互式部署
./deploy.sh --dev     # 开发模式（热重载）
./deploy.sh --prod    # 生产模式（多 worker）
./deploy.sh --check   # 仅检查环境
```

### 4.3 手动部署

```bash
# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 生成配置文件
cp .env.example .env
vim .env                 # 填入真实的 API Key

# 启动服务（开发模式）
uvicorn server:app --host 0.0.0.0 --port 8000 --reload

# 启动服务（生产模式）
uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4
```

### 4.4 Docker 部署

使用 Docker Compose（推荐生产环境）：

```bash
# 1. 复制环境变量模板
cp .env.example .env
vim .env                 # 填入 API Key

# 2. 构建并启动
docker-compose up -d

# 3. 查看日志
docker-compose logs -f

# 4. 停止服务
docker-compose down
```

单独使用 Docker：

```bash
docker build -t paycrypto-cs:latest .
docker run -d -p 8000:8000 --env-file .env paycrypto-cs:latest
```

### 4.5 生产环境部署（systemd）

使用部署脚本配置 systemd 服务：

```bash
./deploy.sh --prod
# 按提示选择配置 systemd 服务
```

手动配置：

```bash
# 1. 创建 systemd 服务文件（见 deploy.sh 中的 setup_systemd 函数）
# 2. 启用并启动服务
sudo systemctl enable paycrypto-cs
sudo systemctl start paycrypto-cs

# 3. 查看服务状态
sudo systemctl status paycrypto-cs

# 4. 查看日志
sudo journalctl -u paycrypto-cs -f
```

### 4.6 配置说明

复制配置模板（如有），或首次启动后通过前端「AI 设置」页面配置：

```json
{
  "provider": "custom",
  "api_key": "YOUR_API_KEY_HERE",
  "base_url": "https://your-api-endpoint/v1",
  "model": "your-model-name",
  "temperature": 0.7,
  "max_tokens": 2048,
  "top_p": 1.0,
  "enabled": true
}
```

支持的 provider：`openai` / `anthropic` / `siliconflow` / `deepseek` / `qwen` / `zhipu` / `custom`

### 4.7 可选：启用向量检索

```bash
pip3 install chromadb sentence-transformers
# 重启服务即可自动启用 Chroma 向量检索
```

---

## 5. 配置说明（api_config.json）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `provider` | str | 提供商：openai / anthropic / siliconflow / deepseek / qwen / zhipu / custom |
| `api_key` | str | API 密钥（勿提交到 git） |
| `base_url` | str | 自定义 API 地址（provider=custom 时必填） |
| `model` | str | 模型名称 |
| `temperature` | float | 温度参数，0.0-2.0，默认 0.7 |
| `max_tokens` | int | 最大输出 token 数，默认 2048 |
| `top_p` | float | nucleus sampling，0.0-1.0，默认 1.0 |
| `frequency_penalty` | float | 频率惩罚，默认 0.0 |
| `presence_penalty` | float | 存在惩罚，默认 0.0 |
| `enabled` | bool | 是否启用 AI，默认 false |
| `system_prompt` | str | 自定义 system prompt（可选） |

---

## 6. API 接口文档

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ask` | 提问 |
| GET | `/api/health` | 健康检查 |
| GET | `/api/config` | 获取 AI 配置 |
| POST | `/api/config` | 更新 AI 配置 |
| POST | `/api/config/test` | 测试 AI 连接 |
| POST | `/api/docs/import` | 导入文档（URL） |
| POST | `/api/docs/upload` | 上传文档（文件） |
| POST | `/api/docs/crawl` | 搜索并导入 |
| DELETE | `/api/docs/remove` | 删除文档 |
| DELETE | `/api/docs/reset` | 重置知识库 |
| GET | `/api/patterns` | 获取经验模式 |
| POST | `/api/summary` | 生成摘要 |
| DELETE | `/api/patterns/{pattern_id}` | 删除经验模式 |
| GET | `/api/vault/files` | 查看日志文件 |
| GET | `/api/vault/read` | 读取日志内容 |
| DELETE | `/api/vault/delete` | 删除日志 |

---

## 7. 前端使用说明

- 打开 http://localhost:8000
- 在输入框输入 API 相关问题，回车发送
- 侧边栏可查看文档管理、AI 设置
- 快捷提问按钮可快速测试常见场景
- 消息气泡下方显示：来源标签、文档命中数、检索方式、置信度

---

## 8. 测试

运行回归测试（需先启动服务）：

```bash
python3 regression_test.py
```

测试覆盖：单轮意图分类、多轮对话上下文记忆、文档检索命中率、API 接口连通性。

---

## 9. 开发说明

**文档切块逻辑**（`doc_loader.py → split_markdown()`）
- 按 Markdown 标题（## / ###）切分
- 每块不超过 max_chars（默认 1200）字符
- 超长块按段落（`\n\n`）二次切分

**RAG 检索逻辑**（`rag_store.py → search_docs()`）
- 优先 Chroma 向量检索（若依赖已安装）
- 降级为 TF-IDF 关键词检索

**意图分类逻辑**（`agents/kb_agent.py → classify_intent()`）
- 关键词规则匹配
- 返回：`greeting` / `info_provide` / `knowledge` / `business` / `unknown`

**会话记忆**（`simple_memory.py`）
- 使用 sentence-transformers 本地模型向量化
- 按 user_id 隔离存储到 memory_store.json
- 可选替换为 mem0（需 API 兼容）

---

## 10. 注意事项 / 已知问题

- `api_config.json` 包含 API Key，已加入 `.gitignore`，请勿手动提交
- `memory_store.json` / `obsidian_vault/` 包含会话数据，已加入 `.gitignore`
- `chroma_db/` / `mem0_db/` 为本地向量库，已加入 `.gitignore`
- 使用第三方兼容 API 时，请注意其是否支持 `top_p` 参数
- mem0 需要 OpenAI 格式嵌入接口，部分第三方 API 不兼容

**已知问题（BUG）**
- BUG-02：文档中 HMAC 时间戳单位描述不一致（毫秒 vs 秒），已加 Prompt 约束
- BUG-03：mem0 与部分第三方 API 不兼容（已降级为 simple_memory）

---

## 11. 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v4.0.0 | 2026-05-25 | 多 Agent 架构重构；修复 Markdown h4 渲染；修复表格 HTML 转义；增加 RAG 预算控制 |
| v3.0.0 | 2026-05 | 接入真实 LLM API；支持多提供商配置；前端 UI 重构 |
| v2.0.0 | 2026-05 | 加入 RAG 检索；加入文档动态导入 |
| v1.0.0 | 2026-04 | 初始版本，基础问答功能 |

---

## 12. 部署脚本详细说明

```bash
./deploy.sh           # 交互式部署（推荐首次使用）
./deploy.sh --dev     # 开发模式快速部署（热重载）
./deploy.sh --prod    # 生产模式部署（多 worker + 可选 systemd）
./deploy.sh --check   # 仅检查系统环境（Python/pip/端口）
```

脚本自动完成步骤：
1. 检查系统环境（Python 版本、pip、端口占用）
2. 创建虚拟环境（venv/）
3. 安装依赖包（requirements.txt）
4. 生成配置文件模板（api_config.json、.env）
5. 创建必要目录（static/、docs_cache/ 等）
6. 启动服务（开发模式热重载 / 生产模式多 worker）
7. （可选）配置 systemd 服务（生产环境开机自启）

**systemd 服务**
- 启动：`sudo systemctl start paycrypto-cs`
- 停止：`sudo systemctl stop paycrypto-cs`
- 状态：`sudo systemctl status paycrypto-cs`
- 日志：`sudo journalctl -u paycrypto-cs -f`

---

## 13. Docker 部署详细说明

**Dockerfile**

```bash
docker build -t paycrypto-cs:latest .
docker run -d -p 8000:8000 --env-file .env paycrypto-cs:latest
docker logs -f <container_id>
```

**docker-compose.yml**

```bash
# 完整启动（含 Nginx）
docker-compose --profile with-nginx up -d

# 仅启动客服系统
docker-compose up -d

# 停止并删除容器
docker-compose down

# 查看日志
docker-compose logs -f

# 重新构建
docker-compose build --no-cache
docker-compose up -d
```

数据持久化（docker-compose）：
- `./data/memory:/app/memory_store.json`
- `./data/chroma:/app/chroma_db`
- `./data/docs_cache:/app/docs_cache`
- `./logs:/app/logs`

---

## License

MIT License
