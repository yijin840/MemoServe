from __future__ import annotations

"""
IntentClassifier（意图分类器）
- 接收用户输入，通过 LLM 识别一个或多个意图
- 支持意图：faq, greeting, unknown (future: business)
- greeting/unknown 由分类器直接返回响应，无需调用 Agent
- faq 路由到 KnowledgeAgent
- 设计为可扩展：添加新意图只需修改分类 prompt + 路由映射
"""
import logging
import random
from typing import Optional, AsyncIterator

from openai import OpenAI

from .config import DASHSCOPE_API_KEY, QWEN_MODEL, QWEN_BASE_URL

logger = logging.getLogger(__name__)


# ====================== 意图分类 Prompt ======================

INTENT_SYSTEM_PROMPT = """你是一个意图分类器。分析用户消息，判断包含哪些意图。

支持的意图：
- faq：与知识库内容相关的问题（API对接、KYC认证、充值、开卡、卡片使用等）
- greeting：寒暄、打招呼、感谢等社交性消息（"你好"、"谢谢"、"再见"等）
- unknown：无法识别的内容（与业务无关的闲聊、超出知识范围的话题）
- crypto_topup：稳定币充值相关问题（USDT、USDC 充值流程、地址、到账等）
- technical_concept：API/技术概念解释（幂等性、签名机制、webhook 等抽象概念）

规则：
- 返回一个或多个意图，用英文逗号分隔（无空格），如 faq 或 greeting,faq 或 unknown
- 如果用户一句话既有寒暄又有实质问题，返回 greeting,faq
- 不要解释，不要加标点或换行，只返回意图列表"""


# ====================== 预设响应 ======================

GREETING_RESPONSES = [
    "您好！请问有什么可以帮您？",
    "您好，很高兴为您服务！",
    "您好！我是智能客服助手，有任何问题都可以问我。",
]

UNKNOWN_RESPONSE = (
    "抱歉，您的问题不在我的服务范围内。"
    "我主要提供API对接、KYC认证、充值、开卡等方面的帮助。"
    "如需其他支持，请联系人工客服。"
)

# 纯寒暄词快速路径（跳过 LLM 调用）
_SALUTATIONS = frozenset({
    "你好", "您好", "hello", "hi", "嗨", "哈喽",
    "早上好", "下午好", "晚上好", "早安", "晚安",
})


# ====================== IntentClassifier ======================

class IntentClassifier:
    """轻量级意图分类器 + 简单意图处理"""

    def __init__(self):
        self.llm = OpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url=QWEN_BASE_URL,
        )

    # ------------------------------------------------------------------
    # 意图分类
    # ------------------------------------------------------------------

    def classify(self, user_input: str) -> list[str]:
        """
        LLM-based intent classification.
        Returns list of intents, e.g. ["faq"] or ["greeting", "faq"] or ["unknown"]
        """
        stripped = user_input.strip()

        # 快速路径：纯寒暄词，跳过 LLM 调用
        if stripped.lower() in _SALUTATIONS:
            return ["greeting"]

        try:
            resp = self.llm.chat.completions.create(
                model=QWEN_MODEL,
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_input},
                ],
                temperature=0,
                max_tokens=30,
            )
            raw = resp.choices[0].message.content.strip().lower()
            parts = [p.strip() for p in raw.split(",")]
            valid_intents = {"faq", "greeting", "unknown", "crypto_topup", "technical_concept"}  # future: add "business"
            result = [p for p in parts if p in valid_intents]
            if result:
                logger.info(f"[IntentClassifier] 分类结果: {result} | input='{user_input[:40]}'")
                return result
        except Exception as e:
            logger.warning(f"[IntentClassifier] LLM 分类失败，默认 faq：{e}")

        # 默认 faq（宁可让 KnowledgeAgent 判断，也不漏掉有效问题）
        return ["faq"]

    # ------------------------------------------------------------------
    # 简单意图处理（无需调用 Agent）
    # ------------------------------------------------------------------

    def handle_greeting(self) -> dict:
        """返回预设寒暄响应"""
        return {
            "answer": random.choice(GREETING_RESPONSES),
            "rag_sources": [],
            "memories_used": [],
            "model": "intent_classifier",
            "intents": ["greeting"],
        }

    def handle_unknown(self) -> dict:
        """返回拒绝响应"""
        return {
            "answer": UNKNOWN_RESPONSE,
            "rag_sources": [],
            "memories_used": [],
            "model": "intent_classifier",
            "intents": ["unknown"],
        }

    # ------------------------------------------------------------------
    # 主入口（同步）
    # ------------------------------------------------------------------

    def chat(
        self,
        user_input: str,
        user_id: str,
        conversation_history=None,
        update_memory=True,
        knowledge_agent=None,
    ) -> dict:
        """
        分类 → 路由 → 返回结果

        流程：
        1. classify() 获取意图列表
        2. 纯 greeting → 直接返回寒暄
        3. 纯 unknown → 直接返回拒绝
        4. 含 faq → 委托 knowledge_agent 处理
        5. 多意图（如 greeting+faq）→ 合并返回
        """
        intents = self.classify(user_input)

        # 纯寒暄
        if intents == ["greeting"]:
            return self.handle_greeting()

        # 纯未知 → 不再直接拒绝，先走 RAG 搜索（让 KnowledgeAgent 判断知识库是否有相关内容）
        if intents == ["unknown"]:
            if knowledge_agent:
                agent_result = knowledge_agent.chat(
                    user_input=user_input,
                    user_id=user_id,
                    conversation_history=conversation_history,
                    update_memory=update_memory,
                )
                agent_result["intents"] = intents
                return agent_result
            return self.handle_unknown()

        # 多意图拆分
        greeting_response = None
        agent_intents = []
        for intent in intents:
            if intent == "greeting":
                greeting_response = self.handle_greeting()["answer"]
            elif intent == "unknown":
                pass  # 多意图中忽略 unknown
            else:
                agent_intents.append(intent)

        # 没有 agent 可处理的意图
        if not agent_intents:
            return self.handle_greeting() if greeting_response else self.handle_unknown()

        # 委托给 knowledge_agent
        if knowledge_agent:
            agent_result = knowledge_agent.chat(
                user_input=user_input,
                user_id=user_id,
                conversation_history=conversation_history,
                update_memory=update_memory,
            )
            agent_result["intents"] = intents

            # 前置寒暄
            if greeting_response:
                agent_result["answer"] = greeting_response + "\n\n" + agent_result["answer"]

            return agent_result

        # fallback（不应该走到这里）
        return self.handle_unknown()

    # ------------------------------------------------------------------
    # 主入口（流式）
    # ------------------------------------------------------------------

    async def stream_chat(
        self,
        user_input: str,
        user_id: str,
        conversation_history=None,
        knowledge_agent=None,
    ) -> AsyncIterator[str]:
        """
        流式版本：greeting/unknown 模拟打字效果，faq 透传 knowledge_agent 流
        """
        intents = self.classify(user_input)

        # 纯寒暄 → 模拟流式输出
        if intents == ["greeting"]:
            resp = self.handle_greeting()["answer"]
            for i in range(0, len(resp), 5):
                yield resp[i:i + 5]
            return

        # 纯未知 → 同样走 knowledge_agent 的 RAG 搜索
        if intents == ["unknown"]:
            if knowledge_agent:
                async for chunk in knowledge_agent.stream_chat(
                    user_input=user_input,
                    user_id=user_id,
                    conversation_history=conversation_history,
                ):
                    yield chunk
                return
            resp = self.handle_unknown()["answer"]
            for i in range(0, len(resp), 5):
                yield resp[i:i + 5]
            return

        # 多意图拆分
        greeting_response = None
        agent_intents = []
        for intent in intents:
            if intent == "greeting":
                greeting_response = self.handle_greeting()["answer"]
            elif intent not in ("unknown",):
                agent_intents.append(intent)

        # 先流式输出寒暄
        if greeting_response:
            for i in range(0, len(greeting_response), 5):
                yield greeting_response[i:i + 5]
            yield "\n\n"

        # 流式输出 faq 内容
        if knowledge_agent and agent_intents:
            async for chunk in knowledge_agent.stream_chat(
                user_input=user_input,
                user_id=user_id,
                conversation_history=conversation_history,
            ):
                yield chunk
