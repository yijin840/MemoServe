from __future__ import annotations

"""
mem0ai 记忆管理模块
- 使用 mem0ai 为每个用户维护长期记忆
- 基于 ChromaDB 存储向量记忆
- 使用 Qwen 作为 LLM 提取记忆要点
- 对话存档使用独立的 ChromaDB collection（绕过 LLM 提取，直接存储原文）
"""
import logging
import time
import uuid
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
from mem0 import Memory

from config import (
    DASHSCOPE_API_KEY,
    QWEN_MODEL,
    QWEN_BASE_URL,
    QWEN_EMBEDDING_MODEL,
    CHROMA_PERSIST_PATH,
    CHROMA_MEMORY_COLLECTION,
    MEM0_TOP_K,
    MEM0_SCORE_THRESHOLD,
    CUSTOM_INSTRUCTIONS,
    MEMORY_STRATEGY,
)

# 对话存档专用 collection 名称
CONV_COLLECTION = "conv_archive"

logger = logging.getLogger(__name__)

# ====================== Custom Instructions 预设策略 ======================

# 策略1：客服场景（默认）
CUSTOMER_SERVICE_INSTRUCTIONS = """
你是一位专业的金融卡平台客服记忆提取助手。请从对话中提取与客户服务相关的重要信息。

【必须提取】
- API对接：API Key/Secret、接口调用问题、Webhook配置、错误码
- KYC认证：KYC级别（无/标准/加强）、提交材料、审核状态、活体认证进度
- 充值相关：钱包地址、充值金额、资金池状态、到账问题
- 开卡问题：卡类型（Native/UQ/JDB）、申请参数、卡号、激活状态、限额咨询
- 用户身份：机构名称、联系人姓名、手机号、邮箱

【忽略内容】
- 闲聊、寒暄（如"你好"、"谢谢"、"今天天气不错"）
- 重复确认已记录的信息
- 与API对接/KYC/充值/开卡无关的个人信息

【输出格式】
必须返回严格的 JSON 格式，禁止其他文字：
{"facts": ["要点1", "要点2", ...]}
如果没有需要记忆的信息，返回空数组：
{"facts": []}

【语言要求】
使用中文记录所有事实，与用户语言保持一致。
"""

# 策略2：通用场景（宽松）
GENERAL_INSTRUCTIONS = """
请从对话中提取对理解用户长期偏好和需求有帮助的信息。

【优先提取】
- 用户偏好：喜欢的风格、品牌、功能
- 重要事件：人生阶段、重大决策
- 身份信息：职业、地区、家庭情况
- 特殊需求：健康状况、语言偏好、无障碍需求

【可忽略】
- 一次性问题（如"北京天气如何"）
- 明显随口说的内容
- 技术调试对话

【输出格式】
{"facts": ["要点1", "要点2", ...]}
"""

# 策略3：严格模式（最少记忆）
STRICT_INSTRUCTIONS = """
仅提取绝对必要且需要长期记住的信息。

【提取标准】只有满足以下任一条件才记录：
1. 涉及金钱交易或法律承诺
2. 用户明确要求记住的重要承诺
3. 多次重复出现的偏好或约束

【输出格式】
{"facts": []}  <!-- 默认返回空，除非非常确定 -->
"""


def _get_strategy_instructions(strategy: str) -> str:
    """根据策略名称返回对应的指令文本"""
    strategies = {
        "customer_service": CUSTOMER_SERVICE_INSTRUCTIONS,
        "general": GENERAL_INSTRUCTIONS,
        "strict": STRICT_INSTRUCTIONS,
    }
    return strategies.get(strategy.lower(), CUSTOMER_SERVICE_INSTRUCTIONS)


def build_mem0_config(
    custom_instructions: Optional[str] = None,
) -> dict:
    """
    构建 mem0 配置，使用 Qwen + ChromaDB

    :param custom_instructions:
        运行时指定的 custom_instructions，优先级最高；
        其次是环境变量 MEM0_CUSTOM_INSTRUCTIONS；
        最后回退到策略文件（由 MEM0_MEMORY_STRATEGY 指定）。
    """
    # 决定最终使用的指令
    final_instructions = None
    if custom_instructions:
        final_instructions = custom_instructions
    elif CUSTOM_INSTRUCTIONS:
        final_instructions = CUSTOM_INSTRUCTIONS
    else:
        final_instructions = _get_strategy_instructions(MEMORY_STRATEGY)

    config = {
        # LLM：通义千问（OpenAI 兼容模式）
        "llm": {
            "provider": "openai",
            "config": {
                "model": QWEN_MODEL,
                "openai_base_url": QWEN_BASE_URL,
                "api_key": DASHSCOPE_API_KEY,
                "temperature": 0.1,
                "max_tokens": 2000,
            },
        },
        # Embedder：通义千问 text-embedding
        "embedder": {
            "provider": "openai",
            "config": {
                "model": QWEN_EMBEDDING_MODEL,
                "openai_base_url": QWEN_BASE_URL,
                "api_key": DASHSCOPE_API_KEY,
            },
        },
        # 向量存储：ChromaDB 本地持久化
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": CHROMA_MEMORY_COLLECTION,
                "path": CHROMA_PERSIST_PATH,
            },
        },
        # Custom Instructions（控制记忆提取质量）
        "custom_instructions": final_instructions,
        # 版本
        "version": "v1.1",
    }
    logger.info(f"[mem0配置] Custom Instructions 策略：{MEMORY_STRATEGY}（含自定义内容：{bool(custom_instructions)}）")
    return config


class MemoryManager:
    """
    用户记忆管理器
    - 自动从对话中提取关键记忆
    - 按 user_id 隔离记忆
    - 支持记忆检索、更新、删除
    - 支持 Custom Instructions（运行时覆盖/切换策略）
    """

    def __init__(self, custom_instructions: Optional[str] = None):
        """
        初始化记忆管理器

        :param custom_instructions:
            初始化时的自定义指令，会覆盖环境变量 MEM0_CUSTOM_INSTRUCTIONS
            和策略文件（由 MEM0_MEMORY_STRATEGY 指定）。
            mem0.add() 不支持运行时覆盖 custom_instructions，
            如需切换策略请调用 switch_strategy()。
        """
        config = build_mem0_config(custom_instructions=custom_instructions)
        self.memory = Memory.from_config(config)
        # 当前生效的 custom_instructions（供外部查询和日志）
        self._current_instructions = config["custom_instructions"]
        # 对话存档用独立的 ChromaDB client（绕过 LLM 提取，直接存原文）
        self._conv_client = chromadb.PersistentClient(
            path=CHROMA_PERSIST_PATH,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        try:
            self._conv_col = self._conv_client.get_or_create_collection(
                name=CONV_COLLECTION,
                metadata={"description": "对话存档"},
            )
        except Exception:
            self._conv_col = self._conv_client.get_collection(name=CONV_COLLECTION)
        logger.info("mem0 记忆管理器已初始化（Qwen + ChromaDB + Custom Instructions）")

    def add_conversation(
        self,
        messages: list[dict],
        user_id: str,
        metadata: Optional[dict] = None,
    ) -> list[dict]:
        """
        从对话中提取并存储记忆
        :param messages: [{"role": "user"/"assistant", "content": "..."}]
        :param user_id: 用户唯一标识
        :param metadata: 附加元数据（可包含 category 字段）
        :return: 提取到的记忆列表

        注意：mem0 0.1.29 中，add() 不支持运行时覆盖 custom_instructions。
        如需切换策略，请调用 switch_strategy() 后再调用本方法。
        """
        md = metadata.copy() if metadata else {}
        # 统一 metadata 格式，确保 category 字段存在
        if "category" not in md:
            md["category"] = "通用"
        result = self.memory.add(
            messages=messages,
            user_id=user_id,
            metadata=md,
        )
        memories = result.get("results", [])
        logger.info(
            f"[记忆] 用户 {user_id} 新增/更新记忆 {len(memories)} 条，"
            f"分类：{md['category']}，策略：{MEMORY_STRATEGY}"
        )
        return memories

    def add_memory_direct(
        self,
        content: str,
        user_id: str,
        category: str = "通用",
    ) -> list[dict]:
        """
        直接写入一条记忆（供工具调用使用）
        注意：category="对话记录" 时会写入独立的存档 collection，不走 LLM 提取
        """
        if category == "对话记录":
            return self.add_conversation_archive(user_id, content)
        result = self.memory.add(
            messages=[{"role": "user", "content": content}],
            user_id=user_id,
            metadata={"category": category, "source": "tool_call"},
        )
        memories = result.get("results", [])
        logger.info(
            f"[记忆·工具] 用户 {user_id} 写入记忆：{content[:50]}... "
            f"分类：{category}，策略：{MEMORY_STRATEGY}"
        )
        return memories

    def add_conversation_archive(
        self,
        user_id: str,
        conversation_text: str,
    ) -> list[dict]:
        """
        直接将对话存档写入 ChromaDB（绕过 LLM 提取）
        conversation_text 格式：已拼接好的 "【时间】\n用户：...\n助手：..." 字符串
        """
        record_id = str(uuid.uuid4())
        ts = int(time.time())
        # 截取前 500 字做 embedding（超长内容截断）
        embed_text = conversation_text[:500]
        metadata = {
            "user_id": user_id,
            "category": "对话记录",
            "source": "conversation_archive",
            "created_at": ts,
            "timestamp": conversation_text.split("】")[0].replace("【", "") if "】" in conversation_text else "",
        }
        try:
            self._conv_col.add(
                ids=[record_id],
                documents=[conversation_text],
                embeddings=[self._embed(embed_text)],
                metadatas=[metadata],
            )
            logger.info(f"[对话存档] 用户 {user_id} 已保存 (id={record_id})")
            return [{"id": record_id, "memory": conversation_text, "metadata": metadata}]
        except Exception as e:
            logger.error(f"[对话存档] 保存失败：{e}")
            return []

    def _embed(self, text: str) -> list[float]:
        """调用 embedding 模型获取向量"""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=QWEN_BASE_URL)
            resp = client.embeddings.create(model=QWEN_EMBEDDING_MODEL, input=text)
            return resp.data[0].embedding
        except Exception as e:
            logger.warning(f"[Embedding] 失败：{e}，返回零向量")
            return [0.0] * 1536

    def search_memory(self, query: str, user_id: str, top_k: int = MEM0_TOP_K) -> list[dict]:
        """
        根据查询检索用户记忆（同时搜 mem0 用户画像和对话存档）
        - 应用相关度阈值过滤（MEM0_SCORE_THRESHOLD）
        - conv_archive 结果降权（×0.85），优先展示提炼后的用户画像
        - 去重（相同文本只保留 score 最高的一条）
        - 按 score 降序排列，返回前 top_k 条
        :return: [{"id": ..., "memory": ..., "score": ..., "source_type": ...}, ...]
        """
        results = self.memory.search(query=query, user_id=user_id, limit=top_k * 2)
        memories = results.get("results", [])

        # 追加对话存档的向量检索（多取一些，过滤后再裁剪）
        try:
            q_embed = self._embed(query[:500])
            conv_results = self._conv_col.query(
                query_embeddings=[q_embed],
                where={"user_id": user_id},
                n_results=min(top_k * 2, 10),
            )
            for doc, mid, meta, dist in zip(
                conv_results.get("documents", [[]])[0],
                conv_results.get("ids", [[]])[0],
                conv_results.get("metadatas", [[]])[0],
                conv_results.get("distances", [[]])[0],
            ):
                raw_score = 1.0 - (dist or 0)
                # conv_archive 降权，优先展示提炼后的用户画像
                score = round(raw_score * 0.85, 4)
                memories.append({
                    "id": mid,
                    "memory": doc,
                    "score": score,
                    "metadata": meta,
                    "source_type": "conversation_archive",
                })
        except Exception as e:
            logger.warning(f"[对话存档检索] 失败：{e}")

        # 为 mem0 画像结果补充 source_type 字段
        for m in memories:
            if "source_type" not in m:
                m["source_type"] = "user_profile"

        # 阈值过滤
        filtered = [
            m for m in memories
            if (m.get("score") or 0.0) >= MEM0_SCORE_THRESHOLD
        ]

        # 去重：相同 memory 文本保留 score 最高的一条
        seen: dict[str, dict] = {}
        for m in filtered:
            text = m.get("memory", "").strip()
            if not text:
                continue
            score = m.get("score", 0.0) or 0.0
            if text not in seen or score > (seen[text].get("score", 0.0) or 0.0):
                seen[text] = m

        # 按相关度降序排列，取前 top_k
        deduped = sorted(seen.values(), key=lambda x: x.get("score", 0.0) or 0.0, reverse=True)
        deduped = deduped[:top_k]

        logger.info(
            f"[记忆] 用户 {user_id}：原始 {len(memories)} 条 "
            f"→ 阈值过滤后 {len(filtered)} 条 "
            f"→ 去重后 {len(deduped)} 条（阈值={MEM0_SCORE_THRESHOLD}）"
        )
        return deduped

    def get_all_memory(self, user_id: str) -> list[dict]:
        """获取用户所有记忆（mem0 用户画像 + 对话存档）"""
        results = self.memory.get_all(user_id=user_id)
        memories = results.get("results", [])
        # 追加对话存档
        try:
            conv_results = self._conv_col.get(where={"user_id": user_id})
            for doc, mid, meta in zip(
                conv_results.get("documents", []),
                conv_results.get("ids", []),
                conv_results.get("metadatas", []),
            ):
                # 构造与 mem0 格式一致的记忆对象
                memories.append({
                    "id": mid,
                    "memory": doc,
                    "metadata": meta,
                    "created_at": meta.get("created_at"),
                    "updated_at": None,
                })
        except Exception as e:
            logger.warning(f"[对话存档] 读取失败：{e}")
        return memories

    def format_memory_context(self, query: str, user_id: str) -> str:
        """
        检索记忆并格式化为 Prompt 上下文
        - 按 source_type 分类展示，用户画像在前、对话存档在后
        """
        memories = self.search_memory(query, user_id)
        return self.format_memory_context_from_list(memories)

    def format_memory_context_from_list(self, memories: list[dict]) -> str:
        """
        直接格式化已检索好的记忆列表（避免重复检索）
        """
        if not memories:
            return ""

        profile_items = []
        archive_items = []
        for m in memories:
            text = m.get("memory", "")
            score = m.get("score", 0.0) or 0.0
            entry = f"【相关度 {score:.0%}】{text}"
            if m.get("source_type") == "conversation_archive":
                archive_items.append(entry)
            else:
                profile_items.append(entry)

        sections = []
        if profile_items:
            sections.append("【用户画像记忆】\n" + "\n".join(profile_items))
        if archive_items:
            sections.append("【历史对话摘要】\n" + "\n".join(archive_items))
        return "\n\n".join(sections)

    # ====================== Custom Instructions 运行时控制 ======================

    def switch_strategy(self, strategy: str) -> bool:
        """
        运行时切换记忆提取策略（通过重建 Memory 实例实现）

        注意：mem0.add() 本身不支持运行时覆盖 custom_instructions，
        因此本方法通过重建 self.memory 实例来切换策略。切换后，
        所有 add_conversation() / add_memory_direct() 调用将使用新策略。

        :param strategy: "customer_service" | "general" | "strict"
        :return: 是否切换成功
        """
        valid = {"customer_service", "general", "strict"}
        if strategy not in valid:
            logger.warning(f"[策略切换] 无效策略：{strategy}，有效值：{valid}")
            return False
        # 获取策略对应的指令文本，传给 build_mem0_config（避免读环境变量回退）
        strategy_instr = _get_strategy_instructions(strategy)
        config = build_mem0_config(custom_instructions=strategy_instr)
        self.memory = Memory.from_config(config)
        self._current_instructions = strategy_instr
        logger.info(f"[策略切换] 已切换至策略：{strategy}")
        return True

    def set_custom_instructions(self, instructions: str) -> None:
        """
        运行时设置自定义指令（全局生效，重建 mem0 实例）
        :param instructions: 自定义指令全文
        """
        config = build_mem0_config(custom_instructions=instructions)
        self.memory = Memory.from_config(config)
        self._current_instructions = instructions
        logger.info("[自定义指令] 已更新全局 custom_instructions（mem0 实例已重建）")

    def get_current_strategy(self) -> dict:
        """返回当前生效的记忆提取策略信息（基于关键词判断）"""
        if not self._current_instructions:
            return {"strategy": "unknown", "instructions_preview": ""}
        instr = self._current_instructions
        # 关键词判断（不受缩进/换行影响）
        if "金融卡平台客服" in instr or "客服记忆提取" in instr:
            matched = "customer_service"
        elif "长期偏好和需求" in instr or "通用场景" in instr:
            matched = "general"
        elif "绝对必要" in instr and "法律承诺" in instr:
            matched = "strict"
        else:
            matched = "custom"
        return {
            "strategy": matched,
            "instructions_preview": (
                self._current_instructions[:200] + "..."
                if len(self._current_instructions) > 200
                else self._current_instructions
            ),
        }

    def list_all_users(self) -> list[str]:
        """
        列出所有有记忆数据的 user_id（mem0 画像 + 对话存档）
        扫描 conv_archive + mem0 主 collection 的元数据来发现所有用户
        """
        users = set()

        # 1. 从对话存档收集
        try:
            all_data = self._conv_col.get()
            for meta in all_data.get("metadatas", []):
                uid = meta.get("user_id", "") if meta else ""
                if uid:
                    users.add(uid)
        except Exception as e:
            logger.warning(f"[list_all_users] 对话存档扫描失败：{e}")

        # 2. 从 mem0 主 collection 收集（用户画像）
        try:
            # mem0 0.1.29: vector_store.collection 是 ChromaDB collection
            main_col = self.memory.vector_store.collection
            all_data = main_col.get()
            for meta in all_data.get("metadatas", []):
                uid = meta.get("user_id", "") if meta else ""
                if uid:
                    users.add(uid)
        except Exception as e:
            logger.warning(f"[list_all_users] mem0 主库扫描失败：{e}")

        return sorted(users)

    def delete_user_memory(self, user_id: str) -> bool:
        """清除某用户所有记忆（含对话存档）"""
        try:
            self.memory.delete_all(user_id=user_id)
            # 删除对话存档
            try:
                conv_results = self._conv_col.get(where={"user_id": user_id})
                if conv_results and conv_results.get("ids"):
                    self._conv_col.delete(ids=conv_results["ids"])
                    logger.info(f"[对话存档] 已清除用户 {user_id} 的 {len(conv_results['ids'])} 条存档")
            except Exception as e:
                logger.warning(f"[对话存档] 清除失败：{e}")
            logger.info(f"[记忆] 已清除用户 {user_id} 的所有记忆")
            return True
        except Exception as e:
            logger.error(f"[记忆] 清除失败：{e}")
            return False

    def get_memory_by_id(self, memory_id: str) -> Optional[dict]:
        """
        按 ID 获取单条记忆详情
        :return: 记忆字典，含 id/memory/created_at/updated_at/metadata
        """
        try:
            result = self.memory.get(memory_id=memory_id)
            return result
        except Exception as e:
            logger.error(f"[记忆] 获取单条记忆失败：{e}")
            return None

    def get_memory_history(self, memory_id: str) -> list[dict]:
        """
        获取单条记忆的变更历史
        :return: [{"id", "memory_id", "old_memory", "new_memory", "event", "created_at", "updated_at"}, ...]
        """
        try:
            return self.memory.history(memory_id=memory_id) or []
        except Exception as e:
            logger.error(f"[记忆] 获取记忆历史失败：{e}")
            return []

    def update_memory(self, memory_id: str, data: str) -> bool:
        """
        按 ID 更新记忆内容
        :param memory_id: 记忆 ID
        :param data: 新的记忆内容
        """
        try:
            self.memory.update(memory_id=memory_id, data=data)
            return True
        except Exception as e:
            logger.error(f"[记忆] 更新记忆失败：{e}")
            return False

    def delete_memory_by_id(self, memory_id: str) -> bool:
        """按 ID 删除单条记忆（同时支持 mem0 和对话存档）"""
        try:
            self.memory.delete(memory_id=memory_id)
            return True
        except Exception:
            pass
        # 可能是对话存档 ID，尝试从 conv_archive 删除
        try:
            self._conv_col.delete(ids=[memory_id])
            logger.info(f"[对话存档] 已删除记忆 {memory_id}")
            return True
        except Exception as e:
            logger.error(f"[记忆] 删除单条记忆失败：{e}")
            return False
