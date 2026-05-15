from __future__ import annotations

"""
知识库 Agent（多 Agent 架构中的子 Agent）
整合：mem0 记忆 + ChromaDB RAG + Qwen 大模型

职责：
- 处理政策咨询、FAQ、使用说明等静态知识类问题
- 从 RAG 知识库检索相关内容
- 结合用户历史记忆提供个性化回复

处理流程：
1. 检索用户记忆（了解用户背景偏好）
2. 检索 RAG 知识库（获取相关文档）
3. 构建增强 Prompt
4. 调用 Qwen 生成回答
5. 异步更新用户记忆

对外类名：CustomerServiceAgent（保持向下兼容）
别名：KnowledgeAgent
"""
import json
import logging
from typing import Optional, AsyncIterator
from openai import OpenAI, AsyncOpenAI

from config import (
    DASHSCOPE_API_KEY,
    QWEN_MODEL,
    QWEN_BASE_URL,
    RAG_TOP_K,
    RAG_SCORE_THRESHOLD,
    MEM0_TOP_K,
)
from rag_knowledge_base import KnowledgeBase
from memory_manager import MemoryManager
from answer_cache import AnswerCache

logger = logging.getLogger(__name__)

# ====================== System Prompt ======================

# Markdown 输出格式说明（Web 界面使用）
MARKDOWN_FORMAT_INSTRUCTION = (
    "- **输出格式**：用 Markdown 格式输出回答，合理使用以下元素：\n"
    "  - 标题（##、###）用于分节\n"
    "  - **加粗** 用于强调重点\n"
    "  - 列表（- 或 1.）用于多条信息\n"
    "  - `代码` 或代码块用于技术内容\n"
    "  - > 引用 用于提示或注意事项\n"
    "  - 表格用于对比信息"
)

# 纯文本输出格式说明（Telegram 等不支持 Markdown 的渠道）
PLAINTEXT_FORMAT_INSTRUCTION = (
    "- **输出格式**：用纯文本输出回答，不使用任何 Markdown 或特殊格式符号。\n"
    "  - 用数字编号（1.、2.）或短横线（-）列出多条信息\n"
    "  - 重要内容用【】括起来强调\n"
    "  - 不使用 #、**、`、> 等 Markdown 符号"
)

SYSTEM_PROMPT_TEMPLATE = """你是一位专业、友善的智能客服助手，**严格基于知识库内容回答问题**。

## 核心约束（必须遵守）

1. **只回答知识库中有的内容**：你的回答必须完全基于下方【相关知识库内容】中的信息。
2. **知识库中没有的内容 → 一律回复不支持**：如果【相关知识库内容】为空，或者用户的问题在知识库中找不到相关依据，你必须直接回复：
   > 抱歉，您的问题不在我的知识范围内，暂时无法回答。如需进一步帮助，请联系人工客服。
3. **禁止编造、推测或补充知识库以外的信息**：宁可拒绝回答，也绝不能凭空编造数据、规则或流程。

## 回答原则
- 简洁清晰，优先使用知识库中的原文或关键数据
- 如有用户历史记忆，结合记忆提供个性化回复
- 回答结构化：先给结论，再分步骤说明，最后补充注意事项
- 不确定时主动说明，避免误导用户
- 语气亲切专业，使用中文回答
{format_instruction}

{memory_section}

**记忆管理（重要）**：
当你从对话中了解到用户的重要信息时，必须调用 `save_memory` 工具保存。
以下信息**必须记录**：
- API对接：API Key/Secret、接口调用问题、Webhook配置、错误码
- KYC认证：KYC级别、提交材料、审核状态、活体认证进度
- 充值相关：钱包地址、充值金额、资金池状态、到账问题
- 开卡问题：卡类型、申请参数、卡号、激活状态、限额咨询
- 用户身份：机构名称、联系人、手机号、邮箱

以下信息**不要保存**：
- 闲聊、寒暄（如"你好"、"谢谢"、"今天天气不错"）
- 与API对接/KYC/充值/开卡无关的个人信息
- 重复确认已记录过的内容

每次对话最多调用 2 次 `save_memory`。
"""

MEMORY_SECTION_TEMPLATE = """
【用户历史记忆】
{memory_content}
"""

RAG_CONTEXT_TEMPLATE = """
【相关知识库内容】
{rag_content}
"""

RAG_EMPTY_CONTEXT = """
【相关知识库内容】
（知识库中未找到与用户问题相关的内容）
"""


# ====================== 工具定义 ======================
SAVE_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "save_memory",
        "description": "保存一条重要信息到用户记忆库。"
                       "当用户透露API对接、KYC认证、充值相关、开卡问题、身份信息等值得记住的内容时调用。"
                       "不要在闲聊或无关对话时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的内容，用简洁的中文描述，不超过50字",
                },
                "category": {
                    "type": "string",
                    "description": "记忆分类",
                    "enum": ["API对接", "KYC认证", "充值相关", "开卡问题", "对话记录", "通用"],
                },
            },
            "required": ["content"],
        },
    },
}


class CustomerServiceAgent:
    """
    智能客服 Agent
    """

    def __init__(self, knowledge_base: KnowledgeBase, memory_manager: MemoryManager, cache: AnswerCache = None):
        self.kb = knowledge_base
        self.mm = memory_manager
        self.cache = cache

        # 同步客户端（普通调用）
        self.llm = OpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url=QWEN_BASE_URL,
        )
        # 异步客户端（流式调用）
        self.async_llm = AsyncOpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url=QWEN_BASE_URL,
        )

    def _build_system_prompt(self, user_memory_context: str) -> str:
        """构建带记忆的 System Prompt（始终输出 Markdown 格式）"""
        if user_memory_context:
            memory_section = MEMORY_SECTION_TEMPLATE.format(memory_content=user_memory_context)
        else:
            memory_section = ""

        return SYSTEM_PROMPT_TEMPLATE.format(
            format_instruction=MARKDOWN_FORMAT_INSTRUCTION,
            memory_section=memory_section,
        )

    def _build_user_message(self, user_input: str, rag_context: str) -> str:
        """构建带 RAG 的用户消息"""
        if rag_context:
            return f"{RAG_CONTEXT_TEMPLATE.format(rag_content=rag_context)}\n\n用户问题：{user_input}"
        return f"{RAG_EMPTY_CONTEXT}\n\n用户问题：{user_input}"

    def _should_archive(self, user_input: str, answer: str) -> bool:
        """
        判断本轮对话是否值得存档。
        过滤标准：
        - 用户输入少于 5 个字（纯打招呼）
        - 输入属于常见闲聊关键词
        - LLM 回复极短（< 20 字，通常是确认语）
        """
        SMALL_TALK_KEYWORDS = {
            "你好", "您好", "hello", "hi", "嗨", "哈喽",
            "谢谢", "感谢", "多谢", "thx", "thanks",
            "再见", "拜拜", "bye", "好的", "ok", "好",
            "嗯", "哦", "啊", "没事", "没问题", "不用了",
        }
        inp = user_input.strip()
        # 长度过短
        if len(inp) < 5:
            return False
        # 命中闲聊词（完整匹配）
        if inp.lower() in SMALL_TALK_KEYWORDS:
            return False
        # LLM 回复过短，说明没有实质内容
        if len(answer.strip()) < 20:
            return False
        return True

    def chat(
        self,
        user_input: str,
        user_id: str,
        conversation_history: Optional[list[dict]] = None,
        update_memory: bool = True,
    ) -> dict:
        """
        单轮对话（同步）
        - 当 update_memory=True 时，LLM 自主调用 save_memory 工具保存重要信息
        """
        import time as _t
        _overall_start = _t.time()
        conversation_history = conversation_history or []

        # ★ Step 0: 查询问答缓存（命中直接返回，跳过 RAG + LLM）
        # ⚠️ 缓存已临时禁用（用于无缓存测试），如需启用请取消下方注释
        # if self.cache:
        #     cached = self.cache.get(user_input, user_id)
        #     if cached:
        #         logger.info(f"[缓存] 命中，跳过 RAG + LLM | query='{user_input[:40]}'")
        #         return cached
        logger.debug(f"[缓存已禁用] 跳过缓存查询 | query='{user_input[:40]}'")

        # Step 1+2: 并行检索记忆和 RAG
        _t1 = _t.time()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(self.mm.search_memory, user_input, user_id)
            f2 = ex.submit(self.kb.search, user_input, RAG_TOP_K)
            memories_used = f1.result()
            rag_results = f2.result()
        _t2 = _t.time()

        _t3 = _t.time()
        memory_context = self.mm.format_memory_context_from_list(memories_used)
        rag_context = self.kb.format_context_from_results(rag_results)

        # ★ 知识库硬约束：RAG 没有结果或最高分太低 → 直接返回"不支持"，不调用 LLM
        _REJECT_ANSWER = "抱歉，您的问题不在我的知识范围内，暂时无法回答。如需进一步帮助，请联系人工客服。"
        if not rag_results:
            logger.info(f"[知识约束] RAG 无结果，直接拒绝 | query='{user_input[:40]}'")
            return {
                "answer": _REJECT_ANSWER,
                "rag_sources": [],
                "memories_used": [m.get("memory", "") for m in memories_used],
                "memory_archived": False,
                "model": QWEN_MODEL,
            }
        if rag_results[0]["score"] < RAG_SCORE_THRESHOLD:
            logger.info(f"[知识约束] RAG 最高分={rag_results[0]['score']}，低于 {RAG_SCORE_THRESHOLD}，直接拒绝 | query='{user_input[:40]}'")
            return {
                "answer": _REJECT_ANSWER,
                "rag_sources": [],
                "memories_used": [m.get("memory", "") for m in memories_used],
                "memory_archived": False,
                "model": QWEN_MODEL,
            }

        # Step 3: 构建 Prompt
        system_prompt = self._build_system_prompt(memory_context)
        user_message = self._build_user_message(user_input, rag_context)
        _t4 = _t.time()

        # Step 4: 构建消息列表（含历史）
        messages = [{"role": "system", "content": system_prompt}]
        for msg in conversation_history[-6:]:
            if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})

        # Step 5: 调用 Qwen（带工具）
        tools = [SAVE_MEMORY_TOOL] if update_memory else None
        response = self.llm.chat.completions.create(
            model=QWEN_MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto" if tools else None,
            temperature=0.7,
            max_tokens=2000,
        )
        _t5 = _t.time()

        message = response.choices[0].message

        # Step 6: 处理工具调用
        if message.tool_calls and update_memory:
            for tool_call in message.tool_calls:
                if tool_call.function.name == "save_memory":
                    try:
                        args = json.loads(tool_call.function.arguments)
                        self.mm.add_memory_direct(
                            content=args["content"],
                            user_id=user_id,
                            category=args.get("category", "通用"),
                        )
                        logger.info(
                            f"[工具记忆] 已保存：{args['content'][:50]}... 分类：{args.get('category', '通用')}"
                        )
                    except Exception as e:
                        logger.warning(f"[工具记忆失败] {e}")

            # 将工具调用结果返回给 LLM，获取最终回复
            messages.append(message.model_dump())
            for tool_call in message.tool_calls:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "记忆已保存",
                })

            final_response = self.llm.chat.completions.create(
                model=QWEN_MODEL,
                messages=messages,
                temperature=0.7,
                max_tokens=2000,
            )
            _t6 = _t.time()
            answer = final_response.choices[0].message.content
        else:
            _t6 = _t5  # 无tool call，跳过第二次LLM
            answer = message.content

        # Step 7: 存档本轮对话（仅保存有实质内容的对话，过滤纯闲聊）
        archived = False
        if update_memory and self._should_archive(user_input, answer):
            import time as _time
            ts = _time.strftime("%Y-%m-%d %H:%M", _time.localtime())
            conv_text = (
                f"【{ts}】\n"
                f"用户：{user_input}\n"
                f"助手：{answer[:300]}"
                + ("..." if len(answer) > 300 else "")
            )
            try:
                self.mm.add_memory_direct(
                    content=conv_text,
                    user_id=user_id,
                    category="对话记录",
                )
                archived = True
                logger.info(f"[对话存档] 已保存用户 {user_id} 的对话")
            except Exception as e:
                logger.warning(f"[对话存档失败] {e}")
        else:
            logger.debug(f"[对话存档] 跳过（闲聊或内容过短）：{user_input[:30]}")
        _t7 = _t.time()

        # 性能汇总日志
        logger.info(
            f"[性能] query='{user_input[:30]}' | "
            f"检索={(_t2-_t1)*1000:.0f}ms | "
            f"构建Prompt={(_t4-_t3)*1000:.0f}ms | "
            f"LLM1={(_t5-_t4)*1000:.0f}ms | "
            f"LLM2(tool)={ (_t6-_t5)*1000:.0f}ms | "
            f"存档={(_t7-_t6)*1000:.0f}ms | "
            f"总计={(_t7-_overall_start)*1000:.0f}ms"
        )

        result = {
            "answer": answer,
            "rag_sources": [
                {
                    "text": r["text"][:150] + "...",
                    "score": r["score"],
                    "source": r["metadata"].get("source", "知识库"),
                }
                for r in rag_results
            ],
            "memories_used": [m.get("memory", "") for m in memories_used],
            "memory_archived": archived,
            "model": QWEN_MODEL,
        }

        # ★ Step 8: 写入问答缓存（仅对知识库命中的正常回答缓存）
        # ⚠️ 缓存已临时禁用（用于无缓存测试），如需启用请取消下方注释
        # if self.cache and rag_results and rag_results[0]["score"] >= RAG_SCORE_THRESHOLD:
        #     self.cache.set(
        #         query=user_input,
        #         answer=answer,
        #         rag_sources=result["rag_sources"],
        #         user_id=user_id,
        #     )

        return result

    async def stream_chat(
        self,
        user_input: str,
        user_id: str,
        conversation_history: Optional[list[dict]] = None,
    ) -> AsyncIterator[str]:
        """
        流式对话（异步，SSE 使用）
        - 流式模式暂不支持工具调用，记忆由 /chat 接口的 LLM 自主保存
        """
        import time as _t
        conversation_history = conversation_history or []

        # 并行检索记忆 & RAG（避免重复调用）
        _st1 = _t.time()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(self.mm.search_memory, user_input, user_id)
            f2 = ex.submit(self.kb.search, user_input, RAG_TOP_K)
            memories_used = f1.result()
            rag_results = f2.result()
        _st2 = _t.time()
        logger.info(f"[流式性能] 检索耗时={(_st2-_st1)*1000:.0f}ms")

        memory_context = self.mm.format_memory_context_from_list(memories_used)
        rag_context = self.kb.format_context_from_results(rag_results)

        system_prompt = self._build_system_prompt(memory_context)
        user_message = self._build_user_message(user_input, rag_context)

        messages = [{"role": "system", "content": system_prompt}]
        for msg in conversation_history[-6:]:
            if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})

        # 流式调用
        full_answer = ""
        async with self.async_llm.chat.completions.stream(
            model=QWEN_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
        ) as stream:
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    full_answer += delta
                    yield delta


# 别名：多 Agent 架构中作为子 Agent 使用
KnowledgeAgent = CustomerServiceAgent
