# mem0-demo 智能客服系统

基于 RAG（检索增强生成）的 API 文档智能问答系统，支持多意图路由、mem0 记忆管理、7 种 AI 提供商。

**线上地址：** [GitHub](https://github.com/yijin840/mem0-demo)

---

## 架构

```
用户提问 → server.py (/api/ask) → classify_intent() → greeting / unknown / business / knowledge
                                                              │
                                    ┌─────────────────────────┤
                                    ▼                         ▼
                              硬编码/引导语              知识库 Agent (kb_agent)
                                                            │
                                    ┌───────────────────────┼──────────────────┐
                                    ▼                       ▼                  ▼
                                RAG检索 (rag_store)    记忆召回 (mem0)     LLM 生成 (call_ai_api)
```

---

## 项目结构

```
mem0-demo/
├── server.py              # FastAPI 入口
├── src/
│   ├── agent.py           # 检索协调 + DSPy 优化
│   ├── doc_loader.py      # 文档加载切块
│   ├── rag_store.py       # RAG 检索（TF-IDF + ChromaDB）
│   ├── mem0_manager.py    # mem0 记忆管理
│   ├── simple_memory.py   # 本地记忆（备选方案）
│   ├── obsidian_writer.py # 日志/经验写入
│   ├── web_crawler.py     # 搜索导入爬虫
│   ├── dspy_rag_optimizer.py
│   └── agents/
│       ├── kb_agent.py    # 知识库 Agent
│       └── biz_agent.py   # 业务 Agent（占位）
├── static/
│   └── index.html         # 前端单页应用
├── docs/                  # 项目文档
│   ├── 多Agent架构设计.md
│   ├── 真实测试用例集.md
│   ├── 问题清单.md
│   ├── 项目总结.md
│   └── 部署文档.md
├── tests/
│   └── regression_test.py # 回归测试
├── data/knowledge/        # 知识库文档（热加载）
├── requirements.txt
└── api_config.json        # AI 配置（gitignored）
```

---

## 快速开始

**环境要求：** Python 3.9+

```bash
pip install -r requirements.txt
python3 server.py
# 访问 http://localhost:8000
```

通过前端左侧栏 ⚙️ **AI 设置** 配置 API Key（支持 7 种提供商：OpenAI / Anthropic / DeepSeek / 通义千问 / 智谱 / 硅基流动 / 自定义）。

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ask` | 核心问答接口 |
| GET | `/api/health` | 健康检查 |
| GET/POST | `/api/config` | AI 配置读写 |
| POST | `/api/config/test` | 测试 AI 连接 |
| POST | `/api/docs/import` | 导入文档URL |
| POST | `/api/docs/upload` | 上传文件 |
| POST | `/api/docs/crawl` | 搜索导入 |
| DELETE | `/api/docs/remove` | 删除文档 |
| DELETE | `/api/docs/reset` | 重置文档 |

---

## 运行数据

| 指标 | 数值 |
|------|------|
| 知识库 | 115 个文档块 |
| 经验模式 | 61 条 |
| AI 提供商 | 7 种 |
| API 接口 | 20 个 |
| 版本 | v4.0.0 |

---

## 测试

```bash
python3 -B tests/regression_test.py
```

覆盖单轮意图分类（10 例）+ 多轮对话（8 轮），共 18 个用例。

---

## 已知问题

- BUG-02：文档中 HMAC 时间戳单位不一致（毫秒 vs 秒），已加 Prompt 约束
- 英文提问：DeepSeek 模型对中文知识库有强中文倾向，英文回答需换模型

---

## License

MIT
