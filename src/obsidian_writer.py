"""
obsidian_writer.py
==================
负责把每次客服对话、经验总结、问题模式写入 Obsidian vault（Markdown 文件）。

目录结构：
  obsidian_vault/
  ├── qa_logs/          # 每日问答原始记录  YYYY-MM-DD.md
  ├── summaries/        # 周期性经验总结    summary_YYYY-WXX.md
  └── patterns/         # 提炼的问题模式    patterns.md  ← 核心知识库
"""

import os
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

VAULT_ROOT = Path(os.getenv("OBSIDIAN_VAULT", "./obsidian_vault"))
QA_LOGS_DIR  = VAULT_ROOT / "qa_logs"
SUMMARIES_DIR = VAULT_ROOT / "summaries"
PATTERNS_FILE = VAULT_ROOT / "patterns" / "patterns.md"
PATTERNS_JSON = VAULT_ROOT / "patterns" / "patterns.json"   # 机器可读版本

for d in [QA_LOGS_DIR, SUMMARIES_DIR, VAULT_ROOT / "patterns"]:
    d.mkdir(parents=True, exist_ok=True)

LOG_RETENTION_DAYS = 30


def cleanup_old_logs():
    """启动时删除超过 30 天的日志，防止无限膨胀"""
    cutoff = date.today() - timedelta(days=LOG_RETENTION_DAYS)
    deleted = 0
    for f in QA_LOGS_DIR.glob("*.md"):
        try:
            if date.fromisoformat(f.stem) < cutoff:
                f.unlink()
                deleted += 1
        except ValueError:
            pass
    if deleted:
        print(f"[LogCleanup] 删除 {deleted} 个过期日志（保留最近 {LOG_RETENTION_DAYS} 天）")


# ──────────────────────────────────────────────────────────
# 1. 写入单条问答日志
# ──────────────────────────────────────────────────────────

def log_qa(question: str, answer: str, category: str = "未分类",
           confidence: float = 1.0, source: str = "fallback"):
    """
    把一次问答追加写入当日日志文件。
    格式：Obsidian Callout + Frontmatter tags，方便在 Obsidian 里搜索。
    """
    today = date.today().isoformat()
    log_file = QA_LOGS_DIR / f"{today}.md"

    now_str = datetime.now().strftime("%H:%M:%S")
    confidence_bar = "🟩" * int(confidence * 5) + "⬜" * (5 - int(confidence * 5))

    entry = f"""
---

> [!question]+ {now_str} ｜ `{category}`
> **用户提问：** {question}
>
> **客服回答：**
> {answer}
>
> 置信度：{confidence_bar} `{confidence:.0%}`  ｜  来源：`{source}`

"""

    # 如果文件不存在则写 frontmatter 头部
    if not log_file.exists():
        header = f"""---
date: {today}
tags: [客服日志, {today}]
---

# 📋 客服问答日志 — {today}

"""
        log_file.write_text(header + entry, encoding="utf-8")
    else:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(entry)


# ──────────────────────────────────────────────────────────
# 2. 写入 / 更新 patterns.md（核心经验知识库）
# ──────────────────────────────────────────────────────────

def load_patterns() -> list[dict]:
    """加载 patterns.json（机器可读）"""
    if PATTERNS_JSON.exists():
        try:
            return json.loads(PATTERNS_JSON.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_patterns(patterns: list[dict]):
    """同时写 patterns.json（供 RAG 检索）和 patterns.md（供人类阅读）"""
    PATTERNS_JSON.write_text(json.dumps(patterns, ensure_ascii=False, indent=2), encoding="utf-8")

    # 按 category 分组
    by_cat: dict[str, list[dict]] = {}
    for p in patterns:
        cat = p.get("category", "其他")
        by_cat.setdefault(cat, []).append(p)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "---",
        "tags: [客服知识库, 经验总结]",
        f"updated: {now_str}",
        "---",
        "",
        "# 🧠 客服经验知识库（自动更新）",
        "",
        f"> 共 **{len(patterns)}** 条问题模式 ｜ 最后更新：{now_str}",
        "",
    ]

    for cat, items in by_cat.items():
        lines.append(f"## {cat}")
        lines.append("")
        for item in items:
            lines.append(f"### Q：{item['canonical_question']}")
            lines.append("")
            lines.append(f"**标准回答：** {item['best_answer']}")
            lines.append("")
            if item.get("aliases"):
                lines.append("**相似问法：**")
                for a in item["aliases"]:
                    lines.append(f"- {a}")
                lines.append("")
            lines.append(f"*出现次数：{item.get('hit_count', 1)} 次 ｜ 置信度：{item.get('confidence', 1.0):.0%}*")
            lines.append("")
            lines.append("---")
            lines.append("")

    PATTERNS_FILE.write_text("\n".join(lines), encoding="utf-8")


def upsert_pattern(question: str, answer: str, category: str = "通用",
                   similarity_threshold: float = 0.85) -> bool:
    """
    将一次成功问答提炼为经验模式。
    - 如果已有相似模式 → 更新 aliases 和命中次数
    - 否则 → 新增模式
    返回 True 表示新增，False 表示更新已有
    """
    patterns = load_patterns()

    # 简单关键词相似度（不依赖向量库）
    def keyword_sim(a: str, b: str) -> float:
        a_words = set(a.lower().replace("？", "").replace("?", "").split())
        b_words = set(b.lower().replace("？", "").replace("?", "").split())
        if not a_words or not b_words:
            return 0.0
        return len(a_words & b_words) / max(len(a_words), len(b_words))

    # 尝试找相似模式
    best_match = None
    best_score = 0.0
    for p in patterns:
        score = keyword_sim(question, p["canonical_question"])
        # 也检查 aliases
        for alias in p.get("aliases", []):
            score = max(score, keyword_sim(question, alias))
        if score > best_score:
            best_score = score
            best_match = p

    if best_match and best_score >= similarity_threshold:
        # 更新已有模式
        best_match["hit_count"] = best_match.get("hit_count", 1) + 1
        if question not in best_match.get("aliases", []) and question != best_match["canonical_question"]:
            best_match.setdefault("aliases", []).append(question)
        # 如果新答案更长/更详细则更新
        if len(answer) > len(best_match["best_answer"]):
            best_match["best_answer"] = answer
        save_patterns(patterns)
        return False
    else:
        # 新增模式
        patterns.append({
            "id": f"P{len(patterns)+1:04d}",
            "canonical_question": question,
            "best_answer": answer,
            "category": category,
            "aliases": [],
            "hit_count": 1,
            "confidence": 1.0,
            "created_at": datetime.now().isoformat(),
        })
        save_patterns(patterns)
        return True


# ──────────────────────────────────────────────────────────
# 3. 周期性经验总结（LLM 调用，写入 summaries/）
# ──────────────────────────────────────────────────────────

def write_summary(summary_text: str, period_label: str = None):
    """把 LLM 生成的经验总结写入 Obsidian summaries 文件夹"""
    if not period_label:
        period_label = datetime.now().strftime("%Y-W%V")
    file = SUMMARIES_DIR / f"summary_{period_label}.md"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    content = f"""---
period: {period_label}
generated: {now_str}
tags: [经验总结, 自动生成]
---

# 📊 经验总结 — {period_label}

> 由 AI 自动分析问答日志生成，人工可补充修改。

{summary_text}

---
*生成时间：{now_str}*
"""
    file.write_text(content, encoding="utf-8")
    return str(file)


# ──────────────────────────────────────────────────────────
# 4. 读取全部 patterns 供 RAG 检索
# ──────────────────────────────────────────────────────────

def get_all_patterns() -> list[dict]:
    return load_patterns()


def delete_pattern(pattern_id: str) -> bool:
    """删除指定 ID 的经验模式。返回 True 表示删除成功。"""
    patterns = load_patterns()
    new_patterns = [p for p in patterns if p.get("id") != pattern_id]
    if len(new_patterns) == len(patterns):
        return False  # 没找到
    save_patterns(new_patterns)
    return True


def search_patterns_by_keyword(query: str, top_k: int = 3) -> list[dict]:
    """关键词检索经验模式（无向量库时的降级方案）"""
    patterns = load_patterns()
    query_words = set(query.lower().split())

    scored = []
    for p in patterns:
        all_text = (p["canonical_question"] + " " + " ".join(p.get("aliases", []))).lower()
        match_words = sum(1 for w in query_words if w in all_text)
        if match_words > 0:
            scored.append((match_words, p))

    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:top_k]]