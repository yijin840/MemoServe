"""
kb_agent.py — 知识库子Agent (CustomerServiceAgent)
=====================================================
整合：文档检索 + mem0 记忆 + AI 生成

职责：
- 处理 API 文档、FAQ 等静态知识类问题
- 从 RAG 知识库检索相关内容
- 结合用户历史记忆提供个性化回复
- 自动记录重要信息到记忆库

处理流程：
1. 检索用户记忆（了解上下文）
2. 检索 RAG 知识库（获取文档片段）
3. 知识库硬约束：无命中 → 直接拒答
4. 构建增强 Prompt
5. 调用 AI 生成回答
6. 保存重要记忆 + 提炼经验

对外类名：CustomerServiceAgent
别名：KnowledgeAgent
"""

import re
import json
import time
import logging

logger = logging.getLogger(__name__)

from src.agent import answer as search_docs
from src.agent import learn, get_all_patterns
from src.mem0_manager import recall as mem_recall, remember as mem_remember

# ══════════════════════════════════════════
# 常量
# ══════════════════════════════════════════

RAG_TOP_K = 10
RAG_SCORE_THRESHOLD = 3.0
MEM0_TOP_K = 2
MAX_RAG_CHARS = 3000  # 约 1000-1500 tokens
REJECT_ANSWER = "抱歉，您的问题不在我的知识范围内，暂时无法回答。如需进一步帮助，请联系人工客服。"


def normalize_answer(text: str) -> str:
    """统一回答格式：压缩多余空行，清理首尾空白"""
    if not text:
        return text
    # 1. 先标准化换行符
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # 2. 列表项前的多余空行去掉（\n\n- item → \n- item）
    text = re.sub(r'\n\n(?=\s*[-*•]\s|\s*\d+\.\s)', '\n', text)
    # 3. 连续3个及以上换行压缩成2个（保留段落分隔）
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 4. 去掉每行末尾空格
    text = '\n'.join(line.rstrip() for line in text.split('\n'))
    return text.strip()

# ══════════════════════════════════════════
# System Prompt
# ══════════════════════════════════════════

SYSTEM_PROMPT_TEMPLATE = """
## 核心约束

1. **如果【相关知识库内容】不为空**，请基于内容详细回答
2. **如果【相关知识库内容】为空**，请告知用户暂无相关信息，并引导其提问 API 相关问题
3. **禁止编造、推测或补充知识库以外的信息**
4. **时间戳单位说明（必须遵守）**：
   - HMAC 签名认证中的 timestamp 参数：**毫秒级** UNIX 时间戳（如 1585310160226）
   - 查询接口的历史时间参数（former_time、latter_time 等）：**秒级** UNIX 时间戳
   - 回答涉及时间戳的问题时，必须先判断场景，明确告知用户单位
5. **不确定时主动说明，避免误导用户**

## 输出格式规范（必须遵守）

- **先一句话总结**，再展开细节
- **参数列表必须用 Markdown 表格**呈现，禁止用纯文本罗列
- **列表项之间不留空行**（`\n- item1\n- item2`，不要`\n- item1\n\n- item2`）
- **使用标准 Markdown 标题层级**：`#` 主标题、`##` 副标题，不要用加粗代替标题
- **描述简洁**，不要重复啰嗦
- **关键信息前置**：URL、Method 等核心信息放在列表最前面
{memory_section}
"""

MEMORY_SECTION = """
【用户历史记忆】
{memory_content}
"""

RAG_CONTEXT = """
【相关知识库内容】
{rag_content}
"""

RAG_EMPTY = """
【相关知识库内容】
（知识库中未找到与用户问题相关的内容）
"""

# ══════════════════════════════════════════
# 主Agent: 意图分类
# ══════════════════════════════════════════

def classify_intent(question: str) -> str:
    """关键词规则意图分类。返回 greeting | knowledge | business | unknown"""
    text = question.lower().strip()

    # 1. 寒暄
    greeting = ["你好", "您好", "hello", "hi", "嗨", "哈喽", "谢谢", "感谢", "再见", "拜拜", "bye", "ok", "好的"]
    if text in greeting or (len(text) <= 6 and any(text.startswith(g) for g in ["你好", "hi", "hello"])):
        return "greeting"

    # 2. 业务无关
    reject_kw = ["天气", "比特币", "股票", "唱歌", "讲笑话", "预测", "price", "weather", "bitcoin", "stock", "joke", "song"]
    if any(k in text for k in reject_kw):
        return "unknown"

    # 3. 业务操作
    biz_kw = ["我要充值", "我要开卡", "查余额", "帮我冻结", "帮我解冻",
              "recharge", "open card", "freeze", "unfreeze"]
    if any(k in text for k in biz_kw):
        return "business"

    # 4. 默认：知识问答
    return "knowledge"


# ══════════════════════════════════════════
# 知识库Agent
# ══════════════════════════════════════════

class CustomerServiceAgent:
    """智能客服 Agent"""

    def __init__(self):
        self._chat_history: dict[str, list[dict]] = {}  # session_id → [{role, content}]

    def _build_system_prompt(self, memory_text: str, question: str = "") -> str:
        """构建带记忆的 System Prompt"""
        if memory_text:
            mem = MEMORY_SECTION.format(memory_content=memory_text)
        else:
            mem = ""
        
        # 动态语言指令
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in question)
        if has_chinese:
            lang_prefix = "你是专业、友善的智能客服助手，严格基于知识库内容回答问题。"
        else:
            lang_prefix = "You are a professional, friendly customer support assistant. You MUST answer in English only. Translate all Chinese knowledge base content to English. Never output Chinese."
        
        prompt = lang_prefix + "\n\n" + SYSTEM_PROMPT_TEMPLATE.format(memory_section=mem)
        return prompt

    def _build_user_message(self, question: str, rag_text: str) -> str:
        """构建带 RAG 的用户消息，语言跟随用户问题"""
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in question)
        prefix = "用户问题：" if has_chinese else "User question (answer in English only): "
        lang_note = "" if has_chinese else "[IMPORTANT: You must answer in English. Translate any Chinese knowledge base content to English in your response.]\n\n"
        if rag_text:
            return f"{lang_note}{RAG_CONTEXT.format(rag_content=rag_text)}\n\n{prefix}{question}"
        return f"{lang_note}{RAG_EMPTY}\n\n{prefix}{question}"

    def _should_archive(self, question: str, answer: str) -> bool:
        """判断是否值得存档（过滤纯闲聊）"""
        SMALL_TALK = {"你好", "您好", "hello", "hi", "嗨", "谢谢", "感谢", "再见", "拜拜", "bye", "好的", "ok", "嗯", "哦"}
        q = question.strip()
        if len(q) < 5 or q.lower() in SMALL_TALK:
            return False
        if len(answer.strip()) < 20:
            return False
        return True

    async def chat(
        self,
        question: str,
        user_id: str,
        session_id: str = None,
        call_ai=None,
        conversation_history: list[dict] = None,
    ) -> dict:
        """
        单轮对话：检索记忆 + RAG + AI 生成 + 记忆保存

        Args:
            user_id:     用户唯一标识（用于记忆隔离，生产环境来自 JWT/登录态）
            session_id:   会话标识（用于对话历史隔离，可与 user_id 不同）
        """
        t0 = time.time()
        # session_id 兜底到 user_id（兼容旧调用）
        if not session_id:
            session_id = user_id
        conversation_history = conversation_history or []

        # Step 1+2: 检索记忆和 RAG
        t1 = time.time()
        doc_result = search_docs(question)
        
        # ★ 多主题反问：把相关 chunks 丢给 LLM，让它自己生成问句
        if doc_result.get("clarify") and call_ai:
            chunks = doc_result.get("chunks_used", [])
            chunk_texts = []
            for c in chunks[:5]:
                title = c.get("title_path", "").split(" > ")[-1]
                content = c.get("content", "")[:500]
                chunk_texts.append(f"【{title}】\n{content}")
            
            prompt = (
                f'用户问了"{question}"。以下是知识库中可能相关的文档内容：\n\n'
                + "\n\n".join(chunk_texts)
                + f'\n\n请根据文档内容生成2-3个简短的中文问句，帮助用户确认想了解什么。'
                  f'只列出问句，一行一个，不加编号。'
                  f'即使文档与问题不完全匹配，也基于已有内容生成最可能的问句。'
            )
            try:
                answer, _ = await call_ai(messages=[{"role": "user", "content": prompt}])
                # 格式化：去编号、去横线，统一用 Markdown 列表
                raw_lines = [q.strip("- 1234567890. *") for q in answer.strip().split("\n") if q.strip()]
                # 去重、去空，确保每条末尾有问号
                questions = []
                for q in raw_lines:
                    if q not in questions:
                        if not q.endswith(("？", "?")):
                            q += "？"
                        questions.append(q)
                clarify_msg = "关于" + question + "，您是想了解以下哪方面？\n\n" + "\n".join(f"- {q}" for q in questions) + "\n\n您具体想了解哪个？"
                clarify_msg = normalize_answer(clarify_msg)
                return {
                    "answer": clarify_msg,
                    "source": "ai",
                    "doc_hits": doc_result["doc_hits"],
                    "confidence": 0.99,
                    "chunks_used": chunks,
                    "method": "clarify",
                }
            except Exception:
                pass  # LLM 调用失败，继续走正常 RAG 流程
        
        try:
            memories = mem_recall(question, user_id, MEM0_TOP_K)
        except Exception:
            memories = []
        t2 = time.time()

        memory_text = "\n".join(memories) if memories else ""
        chunks = doc_result.get("chunks_used", [])

        # ★ 时间戳问题增强检索
        if "时间戳" in question or "timestamp" in question.lower():
            # 强制同时检索 HMAC 块和查询接口块
            result_hmac = search_docs("HMAC 签名 时间戳 毫秒")
            result_query = search_docs("查询接口 时间戳 秒 former_time latter_time")
            # 合并结果，去重（兼容不同 chunk 结构的 id 字段名）
            def _chunk_key(c):
                return c.get("id") or c.get("chunk_id") or c.get("title_path", "")
            seen = {_chunk_key(c) for c in chunks}
            for c in result_hmac.get("chunks_used", []) + result_query.get("chunks_used", []):
                if _chunk_key(c) not in seen:
                    chunks.append(c)
                    seen.add(_chunk_key(c))


        # 构建 RAG 上下文（带预算控制）
        rag_text_parts = []
        total_len = 0
        used = 0
        for c in chunks:
            part = f"【{c['title_path']} （匹配度: {c['score']:.2f}）】\n{c['content']}"
            if total_len + len(part) > MAX_RAG_CHARS and rag_text_parts:
                break
            rag_text_parts.append(part)
            total_len += len(part)
            used += 1
        
        rag_text = "\n\n".join(rag_text_parts)
        if chunks and not rag_text_parts:
            # 第一个 chunk 就超长，强制加入（截断）
            first_part = f"【{chunks[0]['title_path']} （匹配度: {chunks[0]['score']:.2f}】\n{chunks[0]['content']}"
            rag_text = first_part[:MAX_RAG_CHARS]

        if not chunks:
            rag_text = ""
        
        logger.info(f"[RAG] 上下文长度: {len(rag_text)} 字符，使用 {used}/{len(chunks)} 个块")
        
        # 完全无检索结果时，仍调 AI（让 AI 自行判断）
        if not chunks:
            logger.info(f"[知识约束] 无检索结果，转 AI 自行判断")
        
        t3 = time.time()

        # Step 3: 构建 Prompt
        system_prompt = self._build_system_prompt(memory_text, question)
        user_message = self._build_user_message(question, rag_text)

        messages = [{"role": "system", "content": system_prompt}]
        for msg in conversation_history[-6:]:
            if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})

        t4 = time.time()

        # Step 4: 调用 AI（直接传 messages，保留 system/user 角色）
        answer, latency = await call_ai(messages=messages) if call_ai else ("AI未配置", 0)
        answer = normalize_answer(answer)
        t5 = time.time()

        # Step 5: 学习 + 存档
        learn(question, answer, doc_result.get("confidence", 0.8))
        archived = False
        if self._should_archive(question, answer):
            try:
                mem_remember(user_id, question, answer, doc_result.get("confidence", 0.8))
                archived = True
            except Exception:
                pass

        # 更新对话历史（按 session_id 隔离，不同会话互不干扰）
        if session_id not in self._chat_history:
            self._chat_history[session_id] = []
        self._chat_history[session_id].append({"role": "user", "content": question})
        self._chat_history[session_id].append({"role": "assistant", "content": answer})
        if len(self._chat_history[session_id]) > 12:
            self._chat_history[session_id] = self._chat_history[session_id][-12:]

        logger.info(
            f"[性能] 检索={(t2-t1)*1000:.0f}ms "
            f"构建={(t4-t3)*1000:.0f}ms "
            f"LLM={(t5-t4)*1000:.0f}ms "
            f"总计={(t5-t0)*1000:.0f}ms"
        )

        return {
            "answer": answer,
            "source": "ai",
            "doc_hits": doc_result["doc_hits"],
            "confidence": doc_result["confidence"],
            "chunks_used": chunks,
            "method": doc_result["method"],
        }


# 别名
KnowledgeAgent = CustomerServiceAgent
