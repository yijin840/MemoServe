# mem0-demo 客服系统 – 完整审查报告

> 生成时间：2026-03-09  
> 审查范围：`router_agent.py` / `knowledge_agent.py` / `rag_knowledge_base.py` / `memory_manager.py` / `answer_cache.py` / `telegram_bot.py` / `config.py` / `main.py`

---

## 一、架构总览

```
User → FastAPI(/chat) → IntentClassifier(router_agent.py)
                               │
                   ┌────────┴────────┐
                greeting          faq / crypto_topup / technical_concept
                   │                  │
             预设响应          KnowledgeAgent(knowledge_agent.py)
                                  │
                ┌─────────────────┼─────────────────┐
             RAG(ChromaDB)   Memory(mem0)      Tool(save_memory)
                │                │                  │
             rag_knowledge_base  memory_manager  写入用户记忆
```

**组件清单：**
| 文件 | 职责 | 状态 |
|---|---|---|
| `router_agent.py` | 意图分类 + 路由 | ✅ 已修复 |
| `knowledge_agent.py` | 核心 Agent，调 LLM 生成回答 | ⚠️ 缓存禁用 |
| `rag_knowledge_base.py` | ChromaDB 向量检索 | ✅ 已修复 |
| `memory_manager.py` | mem0 + 对话存档 | ✅ 正常 |
| `answer_cache.py` | 双模式问答缓存 | ⚠️ 已被禁用 |
| `telegram_bot.py` | Telegram 接入 | ⚠️ 群映射不持久化 |
| `config.py` | 全局配置 | ⚠️ 缺少部分校验 |
| `main.py` | FastAPI 入口 + 生命周期 | ✅ 正常 |

---

## 二、已修复的问题（本次 + 后续跟进）

### ✅ 修复 1：`router_agent.py` – 意图分类器缺少意图

**问题：** 只定义了 `faq` / `greeting` / `unknown` 三个意图，导致：
- 稳定币充值类问题（USDT/USDC）被误判
- API 抽象概念（幂等性、签名）被拒答

**修复：** 
- `INTENT_SYSTEM_PROMPT` 新增 `crypto_topup` 和 `technical_concept` 两个意图说明
- `classify()` 方法中的 `valid_intents` 集合已同步更新

---

### ✅ 修复 2：`rag_knowledge_base.py` – `TextSplitter` 死循环风险

**问题：** `split()` 方法中 `start = end - self.overlap`，若 `overlap >= chunk_size`，`start` 不前进，造成死循环。

**修复：** `__init__()` 中增加校验：
```python
if self.overlap >= self.chunk_size:
    raise ValueError(...)
```

---

## 三、已修复的问题（后续跟进）

### ✅ 修复 3：`knowledge_agent.py` – 回答缓存已启用

**修复日期：** 2026-05-15  
**改动：** 取消注释并恢复了 Step 0（读缓存）和 Step 8（写缓存）代码块。

---

### ✅ 修复 4：`telegram_bot.py` – `GROUP_USER_MAP` 已持久化

**修复日期：** 2026-05-15  
**改动：** 新增 JSON 文件读写（`data/telegram_group_map.json`），重启不丢失。

---

### ✅ 修复 5：`knowledge_agent.py` – 流式模式已保存记忆

**修复日期：** 2026-05-15  
**改动：** `stream_chat()` 流式结束后，自动归档对话记录到记忆库（过滤闲聊逻辑同 `chat()`）。

---

### ✅ 修复 6：`telegram_bot.py` – Markdown → Telegram HTML 转换改进

**修复日期：** 2026-05-15  
**改动：**
- 表格：正确识别表格块，对齐列宽输出，表头加分隔线
- 多级引用：支持 `>` / `>>` / `>>>` 层级，嵌套显示 `  |` 前缀

---

### ✅ 修复 7：`config.py` – 增加配置校验

**修复日期：** 2026-05-15  
**改动：**
- `check_startup()`：增加 `RAG_SCORE_THRESHOLD` 和 `MEM0_SCORE_THRESHOLD` 范围校验
- `answer_cache.py`：`CACHE_TTL` 低于 60s 自动修正；`CACHE_SEMANTIC_THRESHOLD` 越界自动修正

---

### 🟢 低优先级 / 优化建议

---

### 🟢 低优先级 / 优化建议

#### 建议 1：增加意图后处理

当前 `crypto_topup` 和 `technical_concept` 只是分类，实际仍走 `KnowledgeAgent`。可以考虑：
- `crypto_topup` → 专有知识库子集（只搜充值相关文档）
- `technical_concept` → 直接 LLM 解释，不走 RAG

#### 建议 2：ChromaDB 集合分离

当前知识库和对话存档在同一个 ChromaDB 实例，但不同 collection。可以考虑：
- 知识库用远程 Chroma（可扩展）
- 对话存档用本地 Chroma（快速）

#### 建议 3：增加 Prometheus / OpenTelemetry 指标

当前只有日志，建议增加：
- 每轮对话耗时（RAG / LLM / 总耗时）
- 意图分类分布
- 缓存命中率（启用缓存后）

---

## 四、测试覆盖情况

| 测试集 | 用例数 | 通过率 | 备注 |
|---|---|---|---|
| GLOSSARY (G-1~G-29) | 29 | 93.1% | 2 个因意图分类问题失败（已修复意图定义） |
| 意图分类 | 未系统测试 | — | 建议补充 |
| 多轮对话 | 未系统测试 | — | 需验证 history 注入是否正确 |
| Telegram E2E | 未测试 | — | 需实际 Bot 验证 |

---

## 五、总结

| 类别 | 数量 |
|---|---|
| ✅ 已修复 | 6 |
| 🟢 优化建议（未修） | 3 |

**全部 🔴高优先级 和 🟡中优先级 问题已在 2026-05-15 修复完成。**
