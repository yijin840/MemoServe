#!/usr/bin/env python3
"""
回归测试脚本 - 基于 真实测试用例集-2026-05-25.md
自动调用 /api/ask 接口，记录实际返回结果
"""
import json
import time
import requests

BASE_URL = "http://localhost:8000"

# 单轮测试用例（简化版，只测核心场景）
SINGLE_TURN = [
    {"id": "TC-01", "question": "你好", "expect_intent": "greeting"},
    {"id": "TC-02", "question": "KYC认证需要提交哪些材料？", "expect_intent": "knowledge"},
    {"id": "TC-03", "question": "帮我预测一下比特币明天的价格", "expect_intent": "unknown"},
    {"id": "TC-05", "question": "我的机构名称是TechPay Ltd，我们在对接API时遇到了401错误", "expect_intent": "business"},
    {"id": "TC-09", "question": "你好，请问开卡费用是多少？", "expect_intent": "knowledge"},
]

# 多轮对话测试场景
MULTI_TURN = [
    {"session_id": "scene-A", "round": 1, "question": "你好，我想咨询API对接的问题"},
    {"session_id": "scene-A", "round": 2, "question": "你们的API支持哪些认证方式"},
    {"session_id": "scene-A", "round": 3, "question": "我用的是HMAC-SHA256签名，但一直返回401"},
    {"session_id": "scene-A", "round": 5, "question": "时间戳是用秒还是毫秒"},
]


def call_api(question: str, session_id: str = "default") -> dict:
    """调用 /api/ask 接口"""
    try:
        resp = requests.post(
            f"{BASE_URL}/api/ask",
            json={"question": question, "session_id": session_id},
            timeout=30
        )
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def run_single_turn():
    """运行单轮测试"""
    results = []
    for case in SINGLE_TURN:
        print(f"\n{'='*60}")
        print(f"【{case['id']}】问题：{case['question']}")
        result = call_api(case["question"], session_id=f"test-{case['id']}")
        
        answer = result.get("answer", "")
        intent = result.get("intent", "")
        rag_hits = result.get("rag_hits", 0)
        
        print(f"意图：{intent}")
        print(f"RAG命中：{rag_hits}")
        print(f"回答：{answer[:200]}...")
        
        results.append({
            "id": case["id"],
            "question": case["question"],
            "intent": intent,
            "rag_hits": rag_hits,
            "answer": answer,
            "expect_intent": case["expect_intent"]
        })
        time.sleep(1)  # 避免速率限制
    
    return results


def run_multi_turn():
    """运行多轮对话测试"""
    results = []
    for case in MULTI_TURN:
        sid = case["session_id"]
        print(f"\n{'='*60}")
        print(f"【场景A-第{case['round']}轮】问题：{case['question']}")
        result = call_api(case["question"], session_id=sid)
        
        answer = result.get("answer", "")
        intent = result.get("intent", "")
        
        print(f"意图：{intent}")
        print(f"回答：{answer[:200]}...")
        
        results.append({
            "session_id": sid,
            "round": case["round"],
            "question": case["question"],
            "intent": intent,
            "answer": answer
        })
        time.sleep(1)
    
    return results


def main():
    print("开始回归测试...")
    print(f"目标服务：{BASE_URL}")
    
    # 检查服务是否可用
    try:
        resp = requests.get(f"{BASE_URL}/docs", timeout=5)
        if resp.status_code != 200:
            print(f"❌ 服务不可用（状态码：{resp.status_code}）")
            return
    except Exception as e:
        print(f"❌ 无法连接到服务：{e}")
        return
    
    print("✅ 服务可用，开始测试...\n")
    
    # 运行单轮测试
    print("="*60)
    print("第一部分：单轮基础测试")
    print("="*60)
    single_results = run_single_turn()
    
    # 运行多轮测试
    print("\n" + "="*60)
    print("第二部分：多轮连续对话测试（场景A）")
    print("="*60)
    multi_results = run_multi_turn()
    
    # 保存结果
    output = {
        "test_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "single_turn": single_results,
        "multi_turn": multi_results
    }
    
    output_file = "/tmp/regression_test_result.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n测试结果已保存到：{output_file}")
    print("请手动查看结果，并整理到 Obsidian 文档中。")


if __name__ == "__main__":
    main()
