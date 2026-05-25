"""
biz_agent.py — 业务子Agent（预留）
==================================
后续对接真实 PayCrypto API 进行充值、开卡、冻结等系统操作。
当前为占位实现。
"""


async def handle(question: str, session_id: str = "", **kwargs) -> dict:
    """业务Agent占位"""
    return {
        "answer": "该功能正在开发中。如需充值或开卡，请登录管理后台操作。",
        "source": "biz_agent",
        "doc_hits": 0,
        "confidence": 0,
        "chunks_used": [],
        "method": "none",
    }
