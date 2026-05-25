# Long-term Memory

## mem0-demo 项目
- **路径**：`/Users/native/Documents/GitHub/mem0-demo/`
- **项目类型**：智能客服系统（mem0ai + Qwen + ChromaDB + RAG）
- **架构**：多 Agent 架构（Orchestrator → KnowledgeAgent / MerchantAgent）
- **技术栈**：mem0ai 0.1.29、qwen-plus（通义千问）、text-embedding-v3、ChromaDB 1.5.8、FastAPI、httpx
- **Python 版本**：需要 3.10+，使用 `/Users/native/.workbuddy/binaries/python/versions/3.11.9/bin/python3`
- **启动方式**：`/Users/native/.workbuddy/binaries/python/versions/3.11.9/bin/python3 main.py`（默认端口 8000）
- **核心文件**：main.py / router_agent.py / knowledge_agent.py / merchant_agent.py / rag_knowledge_base.py / memory_manager.py / config.py
- **前端**：static/index.html（含快捷问题、知识库上传、记忆面板、用户列表）
- **知识库**：启动时自动异步写入6条演示知识（退款、配送、会员、售后、支付、FAQ）
- **商户接口**：通过 MERCHANT_API_BASE_URL 配置，未连接时自动返回 Mock 数据；已集成接口：/api/v1/institution/card/type（查卡类型，52条）、/api/v1/institution/balance（查余额，含 RUSD/USDT/USDT-TRC20）
- **DashScope API Key**：通过 `.env` 配置（DASHSCOPE_API_KEY），需填入真实 Key
