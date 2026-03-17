import time
import logging
import numpy as np
from sentence_transformers import CrossEncoder
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 配置一下控制台输出格式，方便看测试过程
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def test_isolated_cross_encoder():
    logger.info("🚀 正在加载 Cross-Encoder 模型 (第一次运行会下载权重，请稍等)...")
    # 使用一个经典的轻量级精排模型，跑得快
    cross_encoder = CrossEncoder("/data1/hanwenrui/model/ms")
    
    # 模拟用户的搜索词
    user_query = "A lightweight asynchronous web framework for Python"
    logger.info(f"🔎 模拟用户 Query: '{user_query}'")

    # 模拟 3 个被粗排捞回来的候选仓库
    # 仓库 1：非常对口，而且很长，用来测试切块逻辑
    repo1_doc = "[Tags: Python, Async, Web, API]\n\n" + "# FastAPI\n\n" + "FastAPI is a modern, fast (high-performance), web framework for building APIs with Python.\n\n" * 50
    # 仓库 2：有点对口，但偏向于大而全的同步框架
    repo2_doc = "[Tags: Python, Web, Fullstack]\n\n" + "# Django\n\n" + "Django is a high-level Python Web framework that encourages rapid development and clean, pragmatic design.\n\n" * 10
    # 仓库 3：完全不对口，只是包含 Python 关键字
    repo3_doc = "[Tags: Python, Data Science, Math]\n\n" + "# NumPy\n\n" + "NumPy is the fundamental package for scientific computing with Python."

    candidates = [
        {"full_name": "tiangolo/fastapi", "combined_doc": repo1_doc},
        {"full_name": "django/django", "combined_doc": repo2_doc},
        {"full_name": "numpy/numpy", "combined_doc": repo3_doc},
    ]

    logger.info(f"📦 准备了 {len(candidates)} 个候选仓库进行精排测试...")

    # 🌟 1. 初始化智能切块器 (设置 500 方便演示切块效果)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", " ", ""]
    )

    all_pairs = []
    chunk_tracking = []

    logger.info("✂️ 开始进行 LangChain 智能切块...")
    # 🌟 2. 切块与打平
    for idx, candidate in enumerate(candidates):
        doc = candidate.get("combined_doc", "")
        chunks = text_splitter.split_text(doc)
        logger.info(f"   - {candidate['full_name']} 被切分成了 {len(chunks)} 块")
        for chunk in chunks:
            all_pairs.append([user_query, chunk])
            chunk_tracking.append(idx)

    # 🌟 3. 终极提速：批量推理
    start_time = time.time()
    logger.info(f"🧠 开始将 {len(all_pairs)} 个文本块一次性塞入大模型进行 Batch 推理...")
    
    if all_pairs:
        raw_scores = cross_encoder.predict(all_pairs, show_progress_bar=False)
    else:
        raw_scores = []
        
    cost_time = time.time() - start_time
    logger.info(f"⚡ 推理完成！耗时: {cost_time:.3f} 秒")

    # 🌟 4. 分数聚合回各自的仓库 (Max + Avg)
    candidate_scores = {i: [] for i in range(len(candidates))}
    for score, cand_idx in zip(raw_scores, chunk_tracking):
        candidate_scores[cand_idx].append(score)

    for idx, candidate in enumerate(candidates):
        scores = candidate_scores[idx]
        if scores:
            max_score = np.max(scores)
            avg_score = np.mean(scores)
            candidate["cross_encoder_score"] = float(0.5 * max_score + 0.5 * avg_score)
        else:
            candidate["cross_encoder_score"] = 0.0

    # 🌟 5. 后处理：排序并打印结果
    sorted_candidates = sorted(candidates, key=lambda x: x["cross_encoder_score"], reverse=True)

    print("\n" + "="*50)
    print("🏆 精排最终得分榜单 (分数已根据长文本 Max/Avg 聚合):")
    print("="*50)
    for rank, cand in enumerate(sorted_candidates, 1):
        print(f"Top {rank}: {cand['full_name'].ljust(20)} | 得分: {cand['cross_encoder_score']:.4f}")

if __name__ == "__main__":
    test_isolated_cross_encoder()