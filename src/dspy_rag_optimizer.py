"""
dspy_rag_optimizer.py
=====================
DSPy 优化模块 — 将项目升级为真正的 DSPy 优化流水线。

核心功能：
1. dspy.Example 训练集 — 把 patterns.json 转为 DSPy 训练样本
2. BootstrapFewShot 优化器 — 自动对齐业务话术
3. dspy.ChainOfThought — 多步推理链
4. dspy.Retrieve — 原生 RAG 集成
5. 自定义 Metric — 文本相似度评估

基于 DSPy 客服问答实战文档实现
"""

import os
import re
import json
from typing import List, Optional, Tuple, Any
from pathlib import Path

# DSPy 可选导入
DSPY_AVAILABLE = False
dspy = None

try:
    import dspy as _dspy
    from dspy.evaluate import Evaluate
    from dspy.functional import TypedPredictor
    from dspy.datasets import DataLoader
    DSPY_AVAILABLE = True
    dspy = _dspy
except ImportError:
    dspy = None

# ─────────────────────────────────────────────
# DSPy 占位符（DSPy 不可用时）
# ─────────────────────────────────────────────

class DummyExample:
    """DSPy 不可用时的 Example 占位符"""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

class DummyModule:
    """DSPy 不可用时的 Module 占位符"""
    def __init__(self, *args, **kwargs):
        pass
    def forward(self, *args, **kwargs):
        return {}

class DummyPrediction:
    """DSPy 不可用时的 Prediction 占位符"""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ─────────────────────────────────────────────
# 1. dspy.Example 训练集
# ─────────────────────────────────────────────

def load_patterns_as_examples() -> List[Any]:
    """
    把 patterns.json 中的经验模式转为 dspy.Example 训练集。

    每个 Example 包含：
    - question: 用户问题
    - answer: 标准回答
    - doc_context: 参考文档片段（可选）
    """
    from obsidian_writer import get_all_patterns

    patterns = get_all_patterns()
    examples = []

    Example = dspy.Example if DSPY_AVAILABLE else DummyExample

    for p in patterns:
        ex = Example(
            question=p.get("canonical_question", ""),
            answer=p.get("best_answer", ""),
            category=p.get("category", "通用"),
            aliases=p.get("aliases", []),
            hit_count=p.get("hit_count", 1),
        )
        examples.append(ex)

    print(f"[DSPy Optimizer] 从 patterns.json 加载 {len(examples)} 个训练样本")
    return examples


def load_qa_logs_as_examples() -> List[Any]:
    """
    从 qa_logs 中提取问答对作为额外训练样本。
    """
    from obsidian_writer import VAULT_ROOT, QA_LOGS_DIR

    examples = []
    log_files = list(QA_LOGS_DIR.glob("*.md")) if QA_LOGS_DIR.exists() else []

    Example = dspy.Example if DSPY_AVAILABLE else DummyExample

    for log_file in log_files:
        try:
            content = log_file.read_text(encoding="utf-8")
            # 解析 Obsidian callout 格式
            entries = re.findall(
                r'> \*\*用户提问：\*\* (.+?)\n>\s*>\s*\*\*客服回答：\*\*\n> (.+?)\n>',
                content,
                re.DOTALL
            )
            for q, a in entries:
                examples.append(Example(
                    question=q.strip(),
                    answer=a.strip(),
                    source="qa_log"
                ))
        except Exception as e:
            print(f"[DSPy Optimizer] 解析日志失败 {log_file}: {e}")

    print(f"[DSPy Optimizer] 从 qa_logs 加载 {len(examples)} 个训练样本")
    return examples


def get_training_set() -> List[Any]:
    """获取合并后的完整训练集"""
    pattern_examples = load_patterns_as_examples()
    qa_examples = load_qa_logs_as_examples()

    # 去重（按 question 哈希）
    seen = set()
    combined = []
    for ex in pattern_examples + qa_examples:
        q_text = getattr(ex, 'question', '') or ''
        q_hash = hash(q_text.lower().strip())
        if q_hash not in seen:
            seen.add(q_hash)
            combined.append(ex)

    print(f"[DSPy Optimizer] 训练集总计 {len(combined)} 个样本")
    return combined


# ─────────────────────────────────────────────
# 2. 自定义 Metric 函数
# ─────────────────────────────────────────────

def answer_quality_metric(example: Any, prediction: Any,
                          trace: Optional[object] = None) -> float:
    """
    评估回答质量的 Metric 函数。

    基于以下维度打分：
    1. 答案是否为空
    2. 答案长度是否合理（>10 字符）
    3. 答案与标准回答的相似度
    4. 是否包含文档关键词

    返回 0.0 ~ 1.0 的分数
    """
    pred_answer = getattr(prediction, 'answer', '') or ""
    gold_answer = getattr(example, 'answer', '') or ""
    question = getattr(example, 'question', '') or ""

    # 基础检查
    if not pred_answer or len(pred_answer.strip()) < 5:
        return 0.0

    score = 0.0
    total = 0.0

    # 1. 长度合理性 (0.2)
    total += 0.2
    if 10 < len(pred_answer) < 2000:  # 合理长度
        score += 0.2

    # 2. 文本相似度 (0.5)
    total += 0.5
    similarity = _text_similarity(pred_answer, gold_answer)
    score += similarity * 0.5

    # 3. 关键词覆盖 (0.3)
    total += 0.3
    if gold_answer:
        keywords = set(re.findall(r'\b\w{3,}\b', gold_answer.lower()))
        pred_words = set(re.findall(r'\b\w{3,}\b', pred_answer.lower()))
        overlap = len(keywords & pred_words) / max(len(keywords), 1)
        score += overlap * 0.3
    else:
        score += 0.15  # 没有标准答案时给基础分

    return score / max(total, 1.0)


def _text_similarity(text1: str, text2: str) -> float:
    """
    计算两个文本的相似度（Jaccard + 长度惩罚）
    """
    if not text1 or not text2:
        return 0.0

    # Tokenize
    tokens1 = set(re.findall(r'\b\w{2,}\b', text1.lower()))
    tokens2 = set(re.findall(r'\b\w{2,}\b', text2.lower()))

    if not tokens1 or not tokens2:
        return 0.0

    # Jaccard 相似度
    intersection = len(tokens1 & tokens2)
    union = len(tokens1 | tokens2)
    jaccard = intersection / union if union > 0 else 0.0

    # 长度惩罚：答案过长或过短都降低分数
    len_ratio = min(len(text1), len(text2)) / max(len(text1), len(text2), 1)

    return 0.7 * jaccard + 0.3 * len_ratio


def rag_quality_metric(example: Any, prediction: Any,
                       trace: Optional[object] = None) -> float:
    """
    评估 RAG 质量的 Metric。
    检查检索到的文档是否真正回答了问题。
    """
    retrieved_context = getattr(prediction, 'context', []) or []
    question = getattr(example, 'question', '') or ""

    if not retrieved_context:
        return 0.3  # 无检索结果，低分

    # 检查检索内容与问题的关键词重叠度
    q_keywords = set(re.findall(r'\b\w{2,}\b', question.lower()))
    context_text = " ".join([str(c) for c in retrieved_context])
    c_keywords = set(re.findall(r'\b\w{2,}\b', context_text.lower()))

    overlap = len(q_keywords & c_keywords) / max(len(q_keywords), 1)
    return min(overlap * 1.5, 1.0)  # 放宽阈值


# ─────────────────────────────────────────────
# 3. DSPy Signature 定义
# ─────────────────────────────────────────────

if DSPY_AVAILABLE:
    class RAGSignature(dspy.Signature):
        """
        RAG 问答 Signature — 核心 DSPy 模块。

        输入：
        - context: 检索到的文档片段
        - question: 用户问题

        输出：
        - answer: 基于文档的精确回答
        - confidence: 回答置信度 (0.0-1.0)
        """
        context = dspy.InputField(desc="从 API 文档中检索到的相关片段，多个片段用 --- 分隔")
        question = dspy.InputField(desc="用户的技术问题")
        answer = dspy.OutputField(desc="基于文档的精确、简洁回答")
        confidence = dspy.OutputField(desc="回答置信度，0.0-1.0")


    class ChainOfThoughtRAGSignature(dspy.Signature):
        """
        带推理链的 RAG Signature — 复杂问题分步推理。
        """
        context = dspy.InputField(desc="检索到的文档片段")
        question = dspy.InputField(desc="用户问题")
        reasoning = dspy.OutputField(desc="推理过程：先理解问题类型，再分析文档，最后组织回答")
        answer = dspy.OutputField(desc="最终回答")
        confidence = dspy.OutputField(desc="置信度 0.0-1.0")


    class RouterSignature(dspy.Signature):
        """
        问题路由器 — 判断问题类型，选择合适的处理策略。
        """
        question = dspy.InputField(desc="用户问题")
        category = dspy.OutputField(desc="问题分类：KYC|Card|Deposit|Balance|Exchange|Webhook|Error|Auth|General")
        strategy = dspy.OutputField(desc="处理策略：exact_match|pattern_lookup|rag_search|llm_generate")

else:
    # 占位符
    RAGSignature = None
    ChainOfThoughtRAGSignature = None
    RouterSignature = None


# ─────────────────────────────────────────────
# 4. BootstrapFewShot 优化器
# ─────────────────────────────────────────────

def create_teleprompter():
    """
    创建 DSPy Teleprompter（自动优化 Prompt）。

    使用 BootstrapFewShot + 质量 Metric 自动对齐业务话术。
    """
    if not DSPY_AVAILABLE:
        print("[DSPy] DSPy 不可用，跳过 Teleprompter 创建")
        return None

    from dspy.teleprompt import BootstrapFewShot, BootstrapFewShotWithRandomSearch

    # 基础版 BootstrapFewShot
    config = dict(
        max_bootstrapped_demos=4,      # 最多生成 4 个 bootstrapped 示例
        max_labeled_demos=4,           # 最多使用 4 个标注示例
        num_threads=6,                # 并行线程数
    )

    # 带随机搜索的增强版（更好的 Prompt 探索）
    teleprompter = BootstrapFewShot(
        metric=answer_quality_metric,
        **config
    )

    return teleprompter


def create_advanced_teleprompter():
    """
    创建高级 Teleprompter — 结合 BootstrapFinetune 和 BootstrapRS。
    """
    if not DSPY_AVAILABLE:
        return None

    from dspy.teleprompt import BootstrapFewShot, BootstrapRS

    # BootstrapRS 自动生成扩展样例
    rs_config = dict(
        num_trials=15,                 # 搜索试验次数
        max_errors=5,                  # 允许的最大错误数
    )

    # BootstrapFewShot 优化
    fewshot_config = dict(
        max_bootstrapped_demos=4,
        max_labeled_demos=4,
    )

    # 返回组合配置
    return {
        "bootstrap": BootstrapFewShot(metric=answer_quality_metric, **fewshot_config),
        "random_search": BootstrapRS(metric=rag_quality_metric, **rs_config),
    }


def optimize_module(module: Any, trainset: List[Any],
                    teleprompter=None) -> Any:
    """
    使用 Teleprompter 优化 DSPy Module。

    Args:
        module: 待优化的 DSPy 模块
        trainset: 训练样本集
        teleprompter: Teleprompter 实例

    Returns:
        优化后的模块
    """
    if not DSPY_AVAILABLE:
        print("[DSPy] DSPy 不可用，跳过模块优化")
        return module

    if teleprompter is None:
        teleprompter = create_teleprompter()

    if len(trainset) < 2:
        print("[DSPy Optimizer] 训练集样本不足，跳过优化")
        return module

    print(f"[DSPy Optimizer] 开始优化模块，使用 {len(trainset)} 个训练样本...")
    optimized = teleprompter.compile(module, trainset=trainset)
    print("[DSPy Optimizer] 优化完成!")

    return optimized


# ─────────────────────────────────────────────
# 5. DSPy RAG 模块
# ─────────────────────────────────────────────

class DSPyRAG:
    """
    基于 DSPy 原生 Retrieve 的 RAG 模块。

    替代手写 TF-IDF，使用 DSPy 的 dspy.Retrieve 组件。
    支持多步推理（ChainOfThought）。
    """
    pass  # 占位符，实际定义在下面


class SimpleDocQA:
    """简单的文档问答模块（DSPy 不可用时的降级）"""
    def __init__(self):
        pass
    def forward(self, doc_context: str, question: str) -> DummyPrediction:
        return DummyPrediction(answer="DSPy 不可用，使用模板回答")


class ChainOfThoughtDocQA:
    """带推理链的问答模块（DSPy 不可用时的降级）"""
    def __init__(self):
        pass
    def forward(self, doc_context: str, question: str) -> DummyPrediction:
        return DummyPrediction(
            answer="DSPy 不可用",
            reasoning="DSPy 库未安装",
            confidence=0.0
        )


class Router:
    """问题路由器模块（DSPy 不可用时的降级）"""
    def __init__(self):
        pass
    def forward(self, question: str) -> dict:
        return {
            "category": "General",
            "strategy": "pattern_lookup",
            "question": question,
        }


class OptimizedDocQA:
    """优化后的文档问答模块（DSPy 不可用时的降级）"""
    def __init__(self, k: int = 4, use_cot: bool = False):
        self.k = k
        self.use_cot = use_cot
    def forward(self, question: str) -> DummyPrediction:
        return DummyPrediction(
            question=question,
            category="General",
            strategy="pattern_lookup",
            answer="DSPy 不可用",
            confidence=0.5,
        )


if DSPY_AVAILABLE:
    class DSPyRAG(dspy.Module):
        """
        基于 DSPy 原生 Retrieve 的 RAG 模块。
        """

        def __init__(self, k: int = 4):
            super().__init__()

            # 检索器
            self.retrieve = dspy.Retrieve(k=k)

            # 生成器（使用 RAG Signature）
            self.generate_answer = dspy.Predict(RAGSignature)

            # 可选：带推理链的版本
            self.generate_answer_cot = dspy.ChainOfThought(RAGSignature)

            # 是否使用 CoT
            self.use_cot = False

        def forward(self, question: str, use_cot: bool = None) -> dspy.Prediction:
            """
            执行 RAG 问答。
            """
            if use_cot is None:
                use_cot = self.use_cot

            # 1. 检索相关文档
            context = self.retrieve(question)

            # 2. 组装上下文
            ctx_text = "\n---\n".join([c.text if hasattr(c, 'text') else str(c) for c in context])

            # 3. 生成回答
            if use_cot:
                pred = self.generate_answer_cot(context=ctx_text, question=question)
            else:
                pred = self.generate_answer(context=ctx_text, question=question)

            return pred


    class SimpleDocQA(dspy.Module):
        """简单的文档问答模块"""

        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict(RAGSignature)

        def forward(self, doc_context: str, question: str) -> dspy.Prediction:
            return self.predict(doc_context=doc_context, question=question)


    class ChainOfThoughtDocQA(dspy.Module):
        """带推理链的问答模块"""

        def __init__(self):
            super().__init__()
            self.predict = dspy.ChainOfThought(ChainOfThoughtRAGSignature)

        def forward(self, doc_context: str, question: str) -> dspy.Prediction:
            return self.predict(doc_context=doc_context, question=question)


    class Router(dspy.Module):
        """问题路由器模块"""

        def __init__(self):
            super().__init__()
            self.classify = dspy.Predict(RouterSignature)

        def forward(self, question: str) -> dict:
            pred = self.classify(question=question)
            return {
                "category": pred.category,
                "strategy": pred.strategy,
                "question": question,
            }


    class OptimizedDocQA(dspy.Module):
        """
        优化后的文档问答模块。
        """

        def __init__(self, k: int = 4, use_cot: bool = False):
            super().__init__()
            self.router = Router()
            self.rag = DSPyRAG(k=k)
            self.rag.use_cot = use_cot
            self.k = k

        def forward(self, question: str) -> dspy.Prediction:
            # 路由分析
            route_info = self.router(question)

            # RAG 生成
            pred = self.rag(question, use_cot=self.rag.use_cot)

            # 合并结果
            result = dspy.Prediction(
                question=question,
                category=route_info["category"],
                strategy=route_info["strategy"],
                answer=pred.answer,
                confidence=getattr(pred, 'confidence', 0.8),
            )

            return result


# ─────────────────────────────────────────────
# 6. 评估工具
# ─────────────────────────────────────────────

def evaluate_module(module: Any, testset: List[Any],
                   metric=None, k: int = 10) -> dict:
    """
    评估优化后的模块。
    """
    if not DSPY_AVAILABLE:
        return {"error": "DSPy 不可用"}

    if metric is None:
        metric = answer_quality_metric

    evaluator = Evaluate(
        devset=testset,
        metric=metric,
        num_threads=k,
        display_progress=True,
        return_scores=True,
    )

    print(f"[DSPy Evaluator] 开始评估，使用 {len(testset)} 个测试样本...")
    results = evaluator(module)

    return {
        "score": results,
        "total": len(testset),
        "pass_rate": results / len(testset) if testset else 0,
    }


# ─────────────────────────────────────────────
# 7. 优化流水线
# ─────────────────────────────────────────────

class DSPyOptimizer:
    """
    DSPy 优化器 — 封装完整的优化流程。
    """

    def __init__(self, module: Any = None):
        self.module = module or OptimizedDocQA()
        self.trained_module = None
        self.trainset = []

    def load_training_data(self) -> int:
        """加载训练数据"""
        self.trainset = get_training_set()
        return len(self.trainset)

    def optimize(self, teleprompter=None) -> Any:
        """执行优化"""
        if not self.trainset:
            self.load_training_data()

        self.trained_module = optimize_module(
            self.module,
            self.trainset,
            teleprompter
        )
        return self.trained_module

    def evaluate(self, testset: List[Any] = None) -> dict:
        """评估模块"""
        module = self.trained_module or self.module
        testset = testset or self.trainset

        if not testset:
            return {"error": "No test data"}

        return evaluate_module(module, testset)

    def save(self, path: str = "./dspy_optimized_model.json"):
        """保存优化后的模块参数"""
        if DSPY_AVAILABLE and self.trained_module:
            import pickle
            with open(path, 'wb') as f:
                pickle.dump(self.trained_module.state_dict(), f)
            print(f"[DSPy Optimizer] 模型已保存到 {path}")

    def load(self, path: str = "./dspy_optimized_model.json"):
        """加载优化后的模块"""
        if DSPY_AVAILABLE:
            import pickle
            try:
                with open(path, 'rb') as f:
                    state = pickle.load(f)
                if self.trained_module:
                    self.trained_module.load_state_dict(state)
                print(f"[DSPy Optimizer] 模型已加载")
            except FileNotFoundError:
                print(f"[DSPy Optimizer] 模型文件不存在: {path}")


# ─────────────────────────────────────────────
# 8. 快捷函数
# ─────────────────────────────────────────────

def quick_optimize(module: Any = None) -> Any:
    """
    一键优化 — 最常用的快捷函数。

    自动加载训练数据，执行 BootstrapFewShot 优化。
    """
    if not DSPY_AVAILABLE:
        print("[DSPy] DSPy 不可用，返回默认模块")
        return module or OptimizedDocQA()

    optimizer = DSPyOptimizer(module)
    optimizer.load_training_data()

    if len(optimizer.trainset) < 2:
        print("[DSPy] 训练数据不足，使用默认模块")
        return module or OptimizedDocQA()

    return optimizer.optimize()


def create_trainset_from_patterns() -> List[Any]:
    """从 patterns.json 创建训练集（兼容旧接口）"""
    return get_training_set()


# 示例用法
if __name__ == "__main__":
    print("=" * 50)
    print("DSPy RAG 优化器")
    print(f"DSPy 可用: {DSPY_AVAILABLE}")
    print("=" * 50)

    # 1. 加载训练数据
    trainset = create_trainset_from_patterns()
    print(f"训练样本数: {len(trainset)}")

    # 2. 创建并优化模块
    if trainset:
        module = OptimizedDocQA()
        optimized = quick_optimize(module)
        print("优化完成!")
    else:
        print("无可用训练数据，请先生成一些问答记录")
