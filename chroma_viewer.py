"""
chroma_viewer.py — 查看 ChromaDB 向量数据库内容
用法：python3 chroma_viewer.py
"""
import chromadb
from sentence_transformers import SentenceTransformer

CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "doc_qa_chunks"

client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_collection(name=COLLECTION_NAME)
embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

total = collection.count()
print(f"📦 集合: {COLLECTION_NAME}")
print(f"📊 总向量数: {total}\n")

# ── 1. 查看所有分类及数量 ──────────────────
all_data = collection.get(include=["metadatas"])
cats = {}
for m in all_data["metadatas"]:
    cat = m.get("category", "(无分类)")
    cats[cat] = cats.get(cat, 0) + 1

print("📂 分类分布:")
for cat, cnt in sorted(cats.items(), key=lambda x: -x[1]):
    print(f"  {cat}: {cnt} 个")
print()

# ── 2. 查看前 10 条记录 ──────────────────────
print("📄 前 10 条记录:")
sample = collection.get(limit=10, include=["metadatas", "documents"])
for i, (meta, doc) in enumerate(zip(sample["metadatas"], sample["documents"])):
    cat = meta.get("category", "?")
    title = meta.get("title_path", "?")[:50]
    print(f"  {i+1}. [{cat}] {title}")
    print(f"      内容预览: {doc[:80]}...")
print()

# ── 3. 语义搜索测试 ──────────────────────────
test_queries = ["充值", "KYC 是什么", "deposit asset"]
print("🔍 语义搜索测试:")
for q in test_queries:
    q_vec = embedder.encode(q).tolist()
    res = collection.query(
        query_embeddings=[q_vec],
        n_results=3,
        include=["metadatas", "distances"],
    )
    print(f"\n  查询: 「{q}」")
    for meta, dist in zip(res["metadatas"][0], res["distances"][0]):
        score = round(1.0 - dist, 3)
        cat = meta.get("category", "?")[:30]
        title = meta.get("title_path", "?")[:40]
        print(f"    score={score}  [{cat}] {title}")
print()
print("✅ 查看完毕")
