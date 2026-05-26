"""
agent.py — 纯 AI 问答 + DSPy 优化（保留）
===========================================
检索文档片段 → 交给 AI 生成中文回答。
DSPy 优化模块保留，待安装依赖后可启用。
"""

import os
import re
from collections import Counter
from datetime import datetime
from typing import Optional, Tuple, List, Any
from dotenv import load_dotenv

load_dotenv()

from src.doc_loader import load_all_docs, get_chunks, refresh_docs
from src.rag_store import search_docs, sync_chunks_to_vector, CHROMA_OK
from src.obsidian_writer import (
    log_qa, upsert_pattern, get_all_patterns,
    search_patterns_by_keyword, write_summary, cleanup_old_logs,
)


# ─────────────────────────────────────────────
# DSPy 模块（保留，待依赖安装后启用）
# ─────────────────────────────────────────────

DSPY_AVAILABLE = False
dspy_predictor = None
optimized_module = None
dspy = None

try:
    import dspy as _dspy
    from dspy_rag_optimizer import (
        OptimizedDocQA, DSPyOptimizer, get_training_set,
        answer_quality_metric, DSPyRAG,
    )
    DSPY_AVAILABLE = True
    dspy = _dspy
except ImportError:
    pass


if DSPY_AVAILABLE:
    class DocQASignature(dspy.Signature):
        doc_context = dspy.InputField(desc="从 API 文档中检索到的相关片段")
        question = dspy.InputField(desc="用户的问题")
        answer = dspy.OutputField(desc="基于文档的精确回答")

    class DocQAWithConfidence(dspy.Signature):
        doc_context = dspy.InputField(desc="检索到的文档片段")
        question = dspy.InputField(desc="用户问题")
        reasoning = dspy.OutputField(desc="推理过程")
        answer = dspy.OutputField(desc="最终回答")
        confidence = dspy.OutputField(desc="置信度 0.0-1.0")

    class SimpleDocQA(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict(DocQASignature)
        def forward(self, doc_context: str, question: str) -> dspy.Prediction:
            return self.predict(doc_context=doc_context, question=question)

    class ChainOfThoughtDocQA(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.ChainOfThought(DocQAWithConfidence)
        def forward(self, doc_context: str, question: str) -> dspy.Prediction:
            return self.predict(doc_context=doc_context, question=question)


def init_dspy(provider="openai", model="gpt-4o-mini", api_key=""):
    global dspy_predictor, DSPY_AVAILABLE, optimized_module, dspy
    if not DSPY_AVAILABLE:
        print("DSPy 未安装，跳过初始化")
        return
    try:
        prefix = "anthropic" if provider == "anthropic" else "openai"
        lm = dspy.LM(f"{prefix}/{model}", api_key=api_key)
        dspy.configure(lm=lm)
        dspy_predictor = SimpleDocQA()
        trainset = get_training_set()
        if len(trainset) >= 2:
            print(f"[DSPy] 开始优化，使用 {len(trainset)} 个训练样本...")
            optimizer = DSPyOptimizer(module=dspy_predictor)
            optimizer.trainset = trainset
            optimized_module = optimizer.optimize()
            print("DSPy 优化完成!")
        else:
            optimized_module = dspy_predictor
        DSPY_AVAILABLE = True
        print(f"DSPy 就绪 ({prefix}/{model})")
    except Exception as e:
        print(f"DSPy 初始化失败: {e}")
        DSPY_AVAILABLE = False


def retrain_dspy() -> dict:
    if not DSPY_AVAILABLE:
        return {"error": "DSPy 不可用"}
    global optimized_module
    try:
        from dspy_rag_optimizer import DSPyOptimizer, get_training_set
        trainset = get_training_set()
        if len(trainset) < 2:
            return {"error": f"训练样本不足 ({len(trainset)})"}
        optimizer = DSPyOptimizer(module=dspy_predictor)
        optimizer.trainset = trainset
        optimized_module = optimizer.optimize()
        return {"success": True, "trained_samples": len(trainset)}
    except Exception as e:
        return {"error": str(e)}


def get_module_info() -> dict:
    return {
        "dspy_available": DSPY_AVAILABLE,
        "predictor_ready": dspy_predictor is not None,
        "optimized": optimized_module is not None,
        "trained_samples": len(get_training_set()) if DSPY_AVAILABLE else 0,
    }


# ─────────────────────────────────────────────
# 文档检索（供 server.py 调用）
# ─────────────────────────────────────────────

def answer(question: str) -> dict:
    """检索文档，返回命中文档片段"""
    chunks = get_chunks()

    # ★ 推断分类，用于向量检索预过滤
    _cat_kw = {
        "Card issuing API 文档": [
            "KY C", "开卡", "充值", "冻结", "解冻", "信用卡",
            "银行卡", "激活", "PIN", "授信", "还款", "账单",
            "card", "issue", "freeze", "unfreeze", "activate",
        ],
        "Native MM API Documentation": [
            "deposit", "exchange", "asset", "MM", "充值卡",
            "兑换", "币对", "native",
        ],
    }
    question_upper = question.upper()
    q_no_space = question_upper.replace(' ', '')
    category = ""
    for cat, kws in _cat_kw.items():
        for kw in kws:
            if kw.upper() in question_upper or kw.upper().replace(' ', '') in q_no_space:
                category = cat
                break
        if category:
            break

    # 关键词增强检索
    import re as _re
    # 短英文术语（如 KYC、API）直接保留，不过滤长度
    en_terms = _re.findall(r'[a-zA-Z]{2,}', question.upper())
    # 去重并保留原大小写
    seen = set()
    unique_terms = []
    for w in _re.findall(r'[a-zA-Z]{2,}', question):
        wu = w.upper()
        if wu not in seen:
            seen.add(wu)
            unique_terms.append(wu)
    # 短术语（≤4字母）直接加入查询；长词做关键词扩展
    short_terms = [w for w in unique_terms if len(w) <= 4]
    long_keywords = ' '.join(unique_terms)
    search_query = question
    if short_terms:
        # KYC、API 等短术语直接追加，提升 TF-IDF 命中率
        search_query = ' '.join(short_terms) + ' ' + question
    elif long_keywords:
        search_query = long_keywords + ' ' + question

    hits = search_docs(search_query, chunks, top_k=10, category=category)

    # KYC 相关问题：强制插入核心 API 块（参数表太大，普通检索排不到）
    q_nospace = question.replace(' ', '')  # 修复 "k y c" 变体
    is_kyc = re.search(r'KYC', q_nospace, re.I)
    has_detail = re.search(r'对接|接口|API|调用|参数|怎么|如何|how|what|步骤|流程|需要|材料|文件|document|require|need', question, re.I)
    if is_kyc and (has_detail or len(question.strip()) <= 6):  # 短问题（如纯"KYC"）也插入
        for c in chunks:
            if '提交用户 KYC 数据' in c.get('title_path','') and 'customers/accounts' in c.get('content','')[:300]:
                hits.insert(0, {"chunk": c, "score": 99.0, "method": "keyword"})
                break

    # 去重（同一标题只保留最高分）
    seen_titles = set()
    deduped = []
    for h in hits:
        title = h['chunk'].get('title_path', '')
        if title not in seen_titles:
            seen_titles.add(title)
            deduped.append(h)
    hits = deduped[:10]

    # ★ 多主题反问：检索到多个不同主题时，列出选项让用户选择
    # 但具体提问（含明确动作词）应跳过反问，直接回答
    is_specific = bool(re.search(
        r'提交|需要|怎么|如何|什么|哪些|步骤|流程|how|what|which|materials|documents|steps|process|submit',
        question, re.I
    ))
    if len(hits) >= 2 and not is_specific:
        # 1. 遍历命中块，按归一化主题聚类，同时记录每个主题下最深 title_path
        topics = []           # 原始一级主题名
        topic_best = {}       # norm → (original_top, deepest_title_path, hit)
        for h in hits[:8]:
            title = h['chunk'].get('title_path', '')
            top = title.split(' > ')[0].strip()
            norm = top.lower().replace(' ', '').replace('—', '-')
            if norm not in topic_best:
                topic_best[norm] = (top, title, h)
                topics.append(top)
            elif len(title) > len(topic_best[norm][1]):
                topic_best[norm] = (top, title, h)
            if len(topics) >= 3:
                break
        
        # 2. 两个以上不同主题 → 生成标签后反问
        if len(topics) >= 2:
            labels = []
            for top in topics[:3]:
                norm = top.lower().replace(' ', '').replace('—', '-')
                deepest = topic_best[norm][1]
                parts = [p.strip() for p in deepest.split(' > ')[1:] if p.strip()]
                noise = {'来源', '来源 1', '来源 2', '来源 3', '来源 4', '来源 5'}
                meaningful = [p for p in parts if p not in noise and not p.isdigit()]
                
                if meaningful:
                    label = " → ".join(meaningful[:2])
                else:
                    # 无深层标题：根据一级主题名+内容推断用户想了解什么
                    content = h['chunk'].get('content', '')[:200].lower()
                    cleaned_topic = top.replace(" — 搜索导入", "").replace(" API 文档", "").replace(" 文档", "").replace(" ", "").upper().strip()
                    if any(w in content[:80] for w in ['什么是', '是什么', '简介', '概述', '概念', '定义', '说明']):
                        label = f"{cleaned_topic}说明"
                    elif "搜索导入" in top:
                        label = f"{cleaned_topic}说明"
                    elif any(w in content[:80] for w in ['步骤', '流程', '方法', '如何', '怎么', '配置', '必须']):
                        label = f"{cleaned_topic}操作"
                    elif any(w in content[:80] for w in ['接口', 'api', 'post', 'get', '参数', '提交']):
                        label = f"{cleaned_topic}接口"
                    else:
                        label = cleaned_topic or top
                # 清理：去后缀，去多余空格，英文大写
                for suffix in [" — 搜索导入", " API 文档", " 文档"]:
                    label = label.replace(suffix, "")
                label = label.replace("  ", " ").strip()
                if label.replace(' ', '').isascii() and label.replace(' ', '').isalpha():
                    label = label.replace(' ', '').upper()
                labels.append(label)
            
            # 每个主题挑一个代表 chunk，供 kb_agent 用 LLM 生成问句
            clarify_chunks = []
            for t in topics[:3]:
                norm = t.lower().replace(' ', '').replace('—', '-')
                if norm in topic_best:
                    _, title, hit = topic_best[norm]
                    clarify_chunks.append({
                        "title_path": title,
                        "score": hit.get("score", 99),
                        "content": hit['chunk'].get('content', '')[:300]
                    })
            return {
                "doc_hits": len(hits),
                "confidence": 0.99,
                "chunks_used": clarify_chunks,
                "method": "clarify",
                "clarify": True,
                "clarify_topics": topics,
                "clarify_labels": labels,
            }

    method = hits[0]["method"] if hits else "none"

    return {
        "doc_hits": len(hits),
        "confidence": 0.80 if hits else 0.30,
        "chunks_used": [
            {"title_path": h["chunk"]["title_path"], "score": h["score"], "content": h["chunk"]["content"][:1000]}
            for h in hits[:11]
        ],
        "method": method,
        "clarify": False,
    }


def _classify(text: str) -> str:
    kw = {
        "kyc": "KYC", "passport": "KYC",
        "card": "Card", "卡": "Card",
        "deposit": "Deposit", "充值": "Deposit", "存款": "Deposit",
        "balance": "Balance", "余额": "Balance",
        "exchange": "Exchange", "兑换": "Exchange",
        "webhook": "Webhook",
        "error": "ErrorCode", "错误码": "ErrorCode",
        "auth": "Auth", "hmac": "Auth", "签名": "Auth",
        "api": "API General",
        "sandbox": "Environment", "测试": "Environment",
    }
    tl = text.lower()
    for k, v in kw.items():
        if k in tl:
            return v
    return "General"


def learn(question: str, answer_text: str, confidence: float):
    category = _classify(question)
    log_qa(question, answer_text, category, confidence, "ai")
    if confidence >= 0.65:
        is_new = upsert_pattern(question, answer_text, category)
        if is_new:
            print(f"[Learn] 新增经验: {question[:40]}...")


def generate_summary() -> str:
    patterns = get_all_patterns()
    if not patterns:
        return "暂无足够的经验数据进行总结。"
    cat_counts = Counter(p.get("category", "其他") for p in patterns)
    hot = sorted(patterns, key=lambda x: -x.get("hit_count", 1))[:5]
    lines = [
        "## 数据概览", "",
        f"- 经验模式总数：**{len(patterns)}** 条",
        f"- 覆盖类别：{', '.join(f'`{k}({v}条)`' for k, v in cat_counts.items())}",
        "", "## 高频问题 Top 5", "",
    ]
    for i, p in enumerate(hot, 1):
        lines.append(f"{i}. **{p['canonical_question']}** （命中 {p.get('hit_count', 1)} 次）\n   > {p['best_answer'][:100]}...")
    lines += ["", "## 优化建议", "",
              "- 高频问题可补充到 API 文档 FAQ 章节",
              "- 低置信问题需要补充更多文档内容或示例"]
    text = "\n".join(lines)
    path = write_summary(text)
    print(f"[Summary] 写入: {path}")
    return text


def startup():
    cleanup_old_logs()
    chunks = load_all_docs()
    print(f"[Startup] 文档加载完成：{len(chunks)} 个块")
    if CHROMA_OK:
        sync_chunks_to_vector(chunks)
    patterns = get_all_patterns()
    if patterns:
        print(f"[Startup] 经验库：{len(patterns)} 条")


startup()
