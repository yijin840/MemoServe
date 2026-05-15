"""
Telegram Bot 集成
- 私聊：user_id = telegram_{user_id}，随时响应
- 群聊：user_id = telegram_group_{chat_id}（默认），或通过 /setid 绑定自定义 ID
- 群聊必须 @bot 或回复 bot 消息才触发（静默忽略其他消息）
- 支持 /setid、/resetid、/whoami 命令
"""
import sys

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Union

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from knowledge_agent import CustomerServiceAgent
from router_agent import IntentClassifier
from config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

# 群 ID → user_id 映射（持久化到 JSON 文件，重启不丢失）
GROUP_MAP_FILE = Path(__file__).resolve().parent / "data" / "telegram_group_map.json"

def _load_group_map() -> dict[str, str]:
    """从 JSON 文件加载群 ID → user_id 映射"""
    if GROUP_MAP_FILE.exists():
        try:
            with open(GROUP_MAP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"[Telegram] 已从 {GROUP_MAP_FILE} 加载 {len(data)} 条群映射")
            return data
        except Exception as e:
            logger.warning(f"[Telegram] 读取群映射文件失败：{e}，使用空映射")
    return {}

def _save_group_map():
    """将当前 GROUP_USER_MAP 持久化到 JSON 文件"""
    try:
        GROUP_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GROUP_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(GROUP_USER_MAP, f, ensure_ascii=False, indent=2)
        logger.info(f"[Telegram] 群映射已保存到 {GROUP_MAP_FILE}")
    except Exception as e:
        logger.error(f"[Telegram] 保存群映射失败：{e}")

GROUP_USER_MAP: dict[str, str] = _load_group_map()


# ====================== Markdown → Telegram HTML 转换 ======================

def markdown_to_telegram_html(text: str) -> str:
    """
    将 Markdown 格式转换为 Telegram HTML 格式（ParseMode.HTMLV2 兼容）。
    支持：**粗体**、*斜体*、`代码`、```代码块```、> 引用、## 标题、列表、链接、表格。
    """
    # 保护代码块（```...```）和行内代码（`...`）中的内容不被转换
    code_blocks = []
    code_inline = []

    def _save_block(m):
        code_blocks.append(m.group(0))
        return f"\x00CODEBLOCK{len(code_blocks)-1}\x00"

    def _save_inline(m):
        code_inline.append(m.group(1))
        return f"\x00CODEINLINE{len(code_inline)-1}\x00"

    # 保存代码块
    text = re.sub(r"```[\s\S]*?```", _save_block, text)
    # 保存行内代码
    text = re.sub(r"`([^`]+)`", _save_inline, text)

    # 标题：## Title 或 # Title → <b>Title</b>
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 粗体：**text** → <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # 斜体：*text* → <i>text</i>（此时 * 已不在代码块中）
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)

    # 删除线：~~text~~ → <s>text</s>
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # 行内代码（已提取，重新插入为 <code>...</code>）
    for i, code in enumerate(code_inline):
        code_escaped = _escape_html(code)
        code_inline[i] = f"<code>{code_escaped}</code>"
    text = re.sub(r"\x00CODEINLINE(\d+)\x00", lambda m: code_inline[int(m.group(1))], text)

    # 代码块 → <pre><code>...</code></pre>
    for i, block in enumerate(code_blocks):
        inner = block.strip("`").strip()
        inner_escaped = _escape_html(inner)
        code_blocks[i] = f"<pre><code>{inner_escaped}</code></pre>"
    text = re.sub(r"\x00CODEBLOCK(\d+)\x00", lambda m: code_blocks[int(m.group(1))], text)

    # 链接：[text](url) → <a href="url">text</a>
    text = re.sub(r"\[([^\]]+)\]\(([^\)]+)\)", r'<a href="\2">\1</a>', text)

    # 引用块：> text → <i>text</i>（简单处理，逐行）
    text = re.sub(r"^>\s?(.*)$", r"<i>\1</i>", text, flags=re.MULTILINE)

    # 表格：简单转换（Telegram HTML 不支持表格，转换为纯文本对齐格式）
    # 删除表格分隔行（|---|---|）
    text = re.sub(r"^\|?[\s\-|]+\|?$", "", text, flags=re.MULTILINE)
    # 简单表格行：| col1 | col2 | → col1   col2
    text = re.sub(r"\|([^\|]+)\|", r"\1   ", text)

    # 转义 Telegram HTML 需要的特殊字符（在标签外的内容中）
    # 注意：标签内的内容已经处理好，这里只处理普通文本中的 < > &
    # 实际上 convert 过程中已经用标签包裹，这里再做一次全局转义
    text = _escape_html_except_tags(text)

    # 清理多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _escape_html(s: str) -> str:
    """转义 HTML 特殊字符"""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_html_except_tags(s: str) -> str:
    """
    转义 HTML 特殊字符，但保护已知的 Telegram HTML 标签内容。
    简单策略：对 & < > 做转义，但跳过标签边界。
    """
    # 先保护所有 Telegram 合法标签
    tags = [
        r"<b>", r"</b>", r"<i>", r"</i>", r"<u>", r"</u>",
        r"<s>", r"</s>", r"<code>", r"</code>", r"<pre>", r"</pre>",
    ]
    placeholders = {}
    idx = 0
    for tag in tags:
        placeholder = f"\x00TAG{idx}\x00"
        if tag in s:
            placeholders[placeholder] = tag
            s = s.replace(tag, placeholder)
            idx += 1

    # 转义特殊字符
    s = _escape_html(s)

    # 还原标签
    for placeholder, tag in placeholders.items():
        s = s.replace(placeholder, tag)

    # 处理 <a href="...">...</a>
    s = re.sub(
        r"&lt;a href=&quot;(.*?)&quot;&gt;(.*?)&lt;/a&gt;",
        r'<a href="\1">\2</a>',
        s,
    )

    return s


# ====================== 命令处理 ======================

def _get_user_id(chat_id: int) -> str:
    """群 ID 映射为 user_id，未绑定则直接用群 ID"""
    return GROUP_USER_MAP.get(str(chat_id), f"telegram_group_{chat_id}")


async def cmd_setid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setid <user_id> — 将当前群绑定到指定 user_id
    例：/setid user_001
    """
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("请在群组内使用此命令。")
        return

    if not context.args:
        await update.message.reply_text("用法：/setid <user_id>\n例：/setid user_001")
        return

    user_id = context.args[0].strip()
    GROUP_USER_MAP[str(chat.id)] = user_id
    _save_group_map()
    await update.message.reply_text(
        f"✅ 本群已绑定 user_id：<code>{user_id}</code>",
        parse_mode=ParseMode.HTML,
    )
    logger.info(f"[Telegram] 群 {chat.id} 绑定 user_id={user_id}")


async def cmd_resetid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /resetid — 清除当前群的 user_id 绑定 """
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("请在群组内使用此命令。")
        return
    GROUP_USER_MAP.pop(str(chat.id), None)
    _save_group_map()
    await update.message.reply_text("✅ 本群 user_id 绑定已清除，将使用默认群 ID。")


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /whoami — 查看当前群绑定的 user_id """
    chat = update.effective_chat
    if not chat:
        return
    uid = _get_user_id(chat.id)
    await update.message.reply_text(
        f"当前群 ID：<code>{chat.id}</code>\n绑定 user_id：<code>{uid}</code>",
        parse_mode=ParseMode.HTML,
    )


# ====================== 消息处理 ======================

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理群消息（仅群组）：
    - 必须 @bot 或回复 bot 消息
    - 不同群用不同的 user_id（通过群 ID 映射到独立 memory 空间）
    """
    msg = update.effective_message
    chat = update.effective_chat
    bot = context.bot

    if not msg or not chat:
        return

    # 判断是否 @bot 或被 bot 回复
    mentioned = False
    if msg.entities:
        for e in msg.entities:
            if e.type == "mention":
                mentioned = True
            elif e.type == "text_mention":
                mentioned = True
    if msg.reply_to_message and msg.reply_to_message.from_user.id == bot.id:
        mentioned = True

    if not mentioned:
        return  # 群里没 @bot，静默忽略

    user_text = msg.text or msg.caption
    if not user_text:
        await msg.reply_text("暂不支持非文本消息，请发送文字。")
        return

    # 去掉 @username 部分
    bot_username = (await bot.get_me()).username
    clean_text = user_text.replace(f"@{bot_username}", "").strip()
    if not clean_text:
        return

    # 群 ID 映射为 user_id（每个群独立 memory 空间）
    user_id = _get_user_id(chat.id)

    await _do_reply(msg, user_id, clean_text, bot, context)


async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理私聊消息（用户直接对话 bot）：
    - user_id = telegram_{user_id}（与群组隔离）
    - 无需 @mentions，随时响应
    """
    msg = update.effective_message
    user = update.effective_user
    bot = context.bot

    if not msg or not user:
        return

    user_text = msg.text or msg.caption
    if not user_text:
        await msg.reply_text("暂不支持非文本消息，请发送文字。")
        return

    # 私聊用 telegram_{telegram_user_id} 作为 user_id
    user_id = f"telegram_{user.id}"

    await _do_reply(msg, user_id, user_text, bot, context)


async def _do_reply(msg, user_id: str, user_text: str, bot, context: ContextTypes.DEFAULT_TYPE):
    """统一的回复逻辑（群聊/私聊共用）"""
    agent: Optional[Union[CustomerServiceAgent, IntentClassifier]] = context.bot_data.get("agent")
    if not agent:
        await msg.reply_text("⚠️ Agent 未初始化，请联系管理员。")
        return

    # 发"正在输入"状态
    await bot.send_chat_action(chat_id=msg.chat.id, action="typing")

    try:
        ka = context.bot_data.get("knowledge_agent")
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            agent.chat,
            user_text,
            user_id,
            [],       # history（Telegram 暂不传历史，可扩展）
            True,     # update_memory
            ka,       # knowledge_agent
        )
        answer = result.get("answer", "（无回复）")

        # Markdown → Telegram HTML
        html_answer = markdown_to_telegram_html(answer)

        # Telegram 消息长度限制 4096，分段发送
        for i in range(0, len(html_answer), 4000):
            await msg.reply_text(
                html_answer[i:i + 4000],
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.error(f"[Telegram] 回复失败：{e}")
        await msg.reply_text(f"⚠️ 处理失败：{e}")


# ====================== Bot 构建 ======================

def build_bot_app(agent: Union[CustomerServiceAgent, IntentClassifier]):
    """构建 Telegram Application，注入 agent"""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN 未配置，跳过 Bot 启动")
        return None

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # 注入 agent 供 handler 使用
    app.bot_data["agent"] = agent

    # 命令（群/私聊通用）
    app.add_handler(CommandHandler("setid", cmd_setid))
    app.add_handler(CommandHandler("resetid", cmd_resetid))
    app.add_handler(CommandHandler("whoami", cmd_whoami))

    # 私聊（无需 @bot，随时响应）
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.TEXT | filters.CAPTION),
            handle_private_message,
        )
    )

    # 群消息（文本 + caption，必须 @bot 才响应）
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION),
            handle_group_message,
        )
    )

    logger.info("[Telegram] Bot 路由已注册")
    return app


# ====================== 独立进程入口（subprocess 模式）======================

if __name__ == "__main__":
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    _logger = logging.getLogger(__name__)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    _logger.info("初始化 Agent 组件（子进程模式）...")
    from rag_knowledge_base import KnowledgeBase
    from memory_manager import MemoryManager
    from knowledge_agent import CustomerServiceAgent as KnowledgeAgent
    from router_agent import IntentClassifier

    _kb = KnowledgeBase()
    _mm = MemoryManager()
    _knowledge_agent = KnowledgeAgent(_kb, _mm)
    _agent = IntentClassifier()
    _logger.info("IntentClassifier + KnowledgeAgent 初始化完成")

    app = build_bot_app(_agent)
    if app is None:
        _logger.info("未配置 TELEGRAM_BOT_TOKEN，Bot 未启动（可忽略）")
        loop.close()
        sys.exit(0)
    app.bot_data["knowledge_agent"] = _knowledge_agent
    _logger.info("[Telegram] Bot 开始轮询...")
    try:
        app.run_polling(drop_pending_updates=True, close_loop=False)
    finally:
        loop.run_until_complete(app.shutdown())
        loop.close()
        _logger.info("[Telegram] Bot 已退出")
