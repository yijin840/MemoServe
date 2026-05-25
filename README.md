# mem0-demo 智能客服系统

基于 [mem0](https://github.com/mem0ai/mem0) 记忆管理的智能客服系统 Demo，支持多意图路由、RAG 知识库检索、Telegram Bot 接入。

## 项目结构

```
.
├── main.py                  # FastAPI 入口 + 生命周期管理
├── config.py                # 全局配置（环境变量加载）
├── router_agent.py          # 意图分类器（IntentClassifier）
├── knowledge_agent.py       # 知识库 Agent（CustomerServiceAgent）
├── rag_knowledge_base.py   # ChromaDB 向量检索
├── memory_manager.py        # mem0 记忆管理
├── answer_cache.py          # 问答缓存（双模式：精确 + 语义）
├── telegram_bot.py         # Telegram Bot 接入
├── prod_grounding_check.py # 生产环境答案真实性校验
├── static/
│   └── index.html          # Web 聊天界面
├── data/
│   └── knowledge/          # 知识库文档（热加载）
└── docs/                   # 项目文档
    ├── 多Agent架构设计.md   # 多 Agent 架构设计文档
    ├── 真实测试用例集.md     # 真实场景测试用例集
    ├── 问题清单.md           # 已知问题清单与修复方案
    ├── 项目总结.md           # 项目总结
    └── 部署文档.md           # 部署文档
```

## 功能特性

- **多意图路由**：自动分类用户问题（greeting / faq / crypto_topup / technical_concept / unknown）
- **RAG 知识库**：基于 ChromaDB 的向量检索，支持热加载文档
- **mem0 记忆管理**：持久化用户画像和对话历史
- **问答缓存**：精确匹配 + 语义相似度双模式缓存，降低延迟和成本
- **流式对话**：SSE（Server-Sent Events）流式返回
- **Telegram Bot**：支持多群映射，每个群独立 user_id
- **答案真实性校验**：生产环境可选启用 grounding 校验

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
```

关键配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DASHSCOPE_API_KEY` | DashScope API Key（阿里云） | 必填 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | 可选 |
| `RAG_SCORE_THRESHOLD` | RAG 检索阈值 | 0.4 |
| `MEM0_SCORE_THRESHOLD` | mem0 记忆检索阈值 | 0.3 |
| `CACHE_TTL` | 缓存 TTL（秒） | 3600 |
| `CACHE_SEMANTIC_THRESHOLD` | 语义缓存阈值 | 0.92 |

### 3. 启动服务

```bash
python main.py
```

访问 http://localhost:8000 查看 Web 界面。

### 4. 启动 Telegram Bot（可选）

```bash
# 在 .env 中配置 TELEGRAM_BOT_TOKEN 后
python telegram_bot.py
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat` | 普通对话 |
| GET | `/chat/stream` | SSE 流式对话 |
| POST | `/knowledge/add` | 上传文本到知识库 |
| POST | `/knowledge/search-and-add` | 搜索并导入知识库 |
| POST | `/knowledge/file` | 上传文件到知识库 |
| GET | `/knowledge/list` | 查看知识库来源列表 |
| DELETE | `/knowledge/` | 删除知识库来源 |
| GET | `/memory/{user_id}` | 查看用户记忆 |
| DELETE | `/memory/{user_id}` | 清除用户记忆 |
| GET | `/health` | 健康检查 |

## 架构说明

```
User → FastAPI(/chat) → IntentClassifier(router_agent.py)
                               │
                   ┌─────────┴─────────┐
                greeting          faq / crypto_topup / technical_concept
                   │                  │
             预设响应          KnowledgeAgent(knowledge_agent.py)
                                  │
                ┌─────────────────┼─────────────────┐
             RAG(ChromaDB)   Memory(mem0)      Tool(save_memory)
```

**意图分类**（router_agent.py）：
- `greeting`：问候语，返回预设响应
- `faq`：常见问题，走知识库检索
- `crypto_topup`：稳定币充值类问题，走知识库检索
- `technical_concept`：API 抽象概念（幂等性、签名等），走知识库检索
- `unknown`：未知意图，返回兜底响应

**知识库检索**（rag_knowledge_base.py）：
- 使用 ChromaDB 做向量检索
- 支持 `add_texts()` 热加载
- `similarity_search()` 返回相似度分数

**记忆管理**（memory_manager.py）：
- 使用 mem0 管理用户记忆
- `add_memory()` 添加记忆
- `search_memory()` 检索相关记忆
- 对话记录自动归档到 `conv_archive` collection

## 文档导航

项目 `docs/` 目录包含以下文档，点击查看详情：

### [🏗️ 多Agent架构设计](docs/多Agent架构设计.md)

多 Agent 架构设计文档，包括：
- 架构概览（主Agent路由层 + 知识库Agent + 业务Agent）
- 意图分类设计（关键词规则、分类函数签名、三层 Agent 职责）
- 知识库 Agent 核心流程（RAG 检索 + AI 回答、检索后处理）
- 业务 Agent 预留设计（后续对接真实 API）
- 数据流图、改动清单、已知问题列表

### [🧪 真实测试用例集](docs/真实测试用例集.md)

基于真实场景的测试用例，包括：
- 第一部分：单轮基础测试（TC-01 ~ TC-10），覆盖寒暄、KYC 材料咨询、业务外话题、虚拟卡 vs 实体卡对比等
- 第二部分：多轮连续对话测试（8 轮），模拟开发者从初次咨询到排查 API 401 错误的完整流程
- 每例包含：用户问题、客服实际回答、测试要点、通过/失败判定
- 测试结论汇总与和原系统对比

### [🐛 问题清单与修复方案](docs/问题清单.md)

已知问题清单，按优先级（P0/P1/P2）排列，每个问题包含：
- 现象描述
- 根因分析
- 具体修复方案（含代码改动位置和示例）
- 验证方法
- 覆盖问题：RAG 硬约束误杀、上下文爆炸、检索无 token 预算、时间戳单位矛盾、mem0 不工作、意图分类准确率、多主题匹配未反问、biz_agent 空壳等

### [📋 项目总结](docs/项目总结.md)

项目阶段性总结，包括：
- 已完成功能清单
- 技术架构概述
- 已知问题概览
- 后续迭代方向

### [🚀 部署文档](docs/部署文档.md)

部署相关文档，包括：
- 一键部署脚本使用说明
- Docker / Docker Compose 部署
- systemd 服务配置
- 环境变量配置说明


## 生产部署注意事项

1. **提高 RAG 阈值**：生产环境建议 `RAG_SCORE_THRESHOLD=0.65`（默认 0.4 太低）
2. **启用 JWT 认证**：`/chat` 接口的 `user_id` 当前由前端随意传递，生产环境必须启用 JWT 认证
3. **启用答案缓存**：取消 `knowledge_agent.py` 222-228 行和 381-389 行的注释
4. **GROUP_USER_MAP 持久化**：`telegram_bot.py` 的群映射当前仅内存存储，重启后丢失，需改为 JSON 文件读写
5. **流式对话记忆**：`stream_chat()` 方法当前不保存记忆，需补充

详细生产部署方案见 [部署文档](docs/部署文档.md)。

## License

MIT
