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

## 二、已修复的问题（本次）

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

## 三、剩余问题清单

### 🔴 高优先级

#### 问题 1：`knowledge_agent.py` – 回答缓存被禁用

**位置：** 第 222-228 行（读缓存），第 381-389 行（写缓存）

**影响：** 每个问题都走 RAG 检索 + LLM 调用，延迟高、成本高。

**建议：** 取消注释，重新启用缓存：
```python
# 取消注释以下代码块
if self.cache:
    cached = self.cache.get(user_input, user_id)
    if cached:
        return cached
```

---

#### 问题 2：`telegram_bot.py` – `GROUP_USER_MAP` 不持久化

**位置：** 第 31 行

```python
GROUP_USER_MAP: dict[str, str] = {}  # ⚠️ 仅内存，重启丢失
```

**影响：** Bot 重启后所有群的 `/setid` 绑定丢失，需要重新设置。

**建议：** 改为从 JSON 文件读写（见完整报告附件）。

---

#### 问题 3：`knowledge_agent.py` – 流式模式不保存记忆

**位置：** `stream_chat()` 方法

**问题：** 流式模式没有调用 `save_memory` 工具，也没有对话存档。只有非流式（`/chat`）才完整保存记忆。

**影响：** 用 SSE 流式对话的用户，其对话不会被记录到记忆中。

---

### 🟡 中优先级

#### 问题 4：`telegram_bot.py` – Markdown → Telegram HTML 转换不完整

**已知局限：**
- 表格：只做了简单 `|` 字符删除，没有真正转为对齐文本
- 嵌套格式：`**加粗` 在列表项中可能无法正确识别
- 多级引用：只处理单级 `>` 

**建议：** 对于复杂 Markdown，可先转纯文本（去掉所有格式），或限制知识库输出格式。

---

#### 问题 5：`config.py` – 缺少配置校验

**缺失校验：**
- `RAG_SCORE_THRESHOLD` 是否在合理范围（0~1）
- `CACHE_TTL` 是否为正数
- `MEM0_SCORE_THRESHOLD` 是否合理

**建议：** 在 `check_startup()` 中增加这些校验。

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
| ✅ 本次已修复 | 2 |
| 🔴 高优先级待修复 | 3 |
| 🟡 中优先级 | 2 |
| 🟢 优化建议 | 3 |

**下一步建议：**
1. **启用 `answer_cache`**（高收益，延迟大幅下降）
2. **持久化 `GROUP_USER_MAP`**（改 JSON 文件读写）
3. **为 `stream_chat` 补充记忆保存逻辑**
4. 补充意图分类和多轮对话的系统测试
