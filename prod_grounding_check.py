"""
生产加固：防乱答增强层
在 knowledge_agent.py 的 chat() 方法中，LLM 返回答案后，
增加一层答案验证，确认答案确实基于 RAG 内容。
"""

def _answer_grounding_check(answer: str, rag_results: list[dict]) -> bool:
    """
    验证答案是否基于 RAG 检索内容。
    返回 True 表示答案可信，False 表示可能幻觉。
    """
    if not rag_results:
        return False

    # 1. 拒答语句直接通过
    if "不在我的知识范围内" in answer or "无法回答" in answer:
        return True

    # 2. 答案过短且没有明确结论，标记为不可信
    if len(answer.strip()) < 20:
        return False

    # 3. 检查答案是否包含 RAG 内容中的关键实体
    #    简单策略：RAG 文本中的数字、代码、专有名词应在答案中出现
    rag_text = " ".join([r["text"] for r in rag_results[:3]])
    # 提取 RAG 中的数字（如 API 错误码、金额等）
    import re
    rag_numbers = set(re.findall(r"\b\d{3,}\b", rag_text))  # 3位以上数字
    answer_numbers = set(re.findall(r"\b\d{3,}\b", answer))
    if rag_numbers and not (rag_numbers & answer_numbers):
        # RAG 里有数字但答案里完全没有，可疑
        logger.warning(f"[ grounding] 答案可能未基于 RAG 内容（数字不匹配）")

    return True  # 目前只做日志告警，不阻断


# ========== 用法（插入 knowledge_agent.py 的 chat() 方法）==========
# 在获得 answer 之后、返回之前，加入：

        # ★ 答案真实性校验（生产加固）
        if update_memory:
            is_grounded = self._answer_grounding_check(answer, rag_results)
            if not is_grounded:
                logger.warning(
                    f"[生产告警] 答案可能未基于知识库 | "
                    f"user_id={user_id} | query='{user_input[:40]}'"
                )
                # 可选：低置信度时转人工
                # answer = "您的问题需要人工确认，请稍候..."
