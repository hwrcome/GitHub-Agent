# tools/cross_encoder_reranking.py
import numpy as np
import logging
from sentence_transformers import CrossEncoder
# 注意这里用的是最新版的导入路径
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

def cross_encoder_reranking(state, config):
    from agent import AgentConfiguration
    agent_config = AgentConfiguration.from_runnable_config(config)
    
    cross_encoder = CrossEncoder(agent_config.cross_encoder_model_name)
    
    # 因为 ColBERT 已经很准了，为了极致的速度，我们只取前 30 个进行深度精排
    candidates_for_rerank = state.semantic_ranked[:30]
    logger.info(f"🚀 开始使用 Cross-Encoder 对 Top {len(candidates_for_rerank)} 个仓库进行深度精排...")

    # 初始化智能切块器
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1500,  # 稍微给大一点的块，让 Cross-Encoder 看得更连贯
        chunk_overlap=150,
        separators=["\n\n", "\n", " ", ""]
    )

    def cross_encoder_rerank_func(query, candidates, top_n):
        all_pairs = []
        chunk_tracking = []

        # 1. 智能切块与打平
        for idx, candidate in enumerate(candidates):
            doc = candidate.get("combined_doc", "")
            if not doc.strip():
                continue
                
            chunks = text_splitter.split_text(doc)
            
            # 🌟 满足你的好奇心：专门打印第一个仓库的切块情况供你“验货”
            if idx == 0:
                repo_name = candidate.get('full_name', 'Unknown')
                logger.info("\n" + "="*50)
                logger.info(f"👀 [切块观察站] 仓库 '{repo_name}' 总共被切成了 {len(chunks)} 块！")
                logger.info(f"👀 我们来抽查它的前 2 块：")
                for i in range(min(2, len(chunks))):
                    # 为了不刷屏，每块只打印前 200 个字符
                    preview_text = chunks[i][:200].replace('\n', ' ') + "..."
                    logger.info(f"   🧱 [第 {i+1} 块] (长度 {len(chunks[i])}): {preview_text}")
                logger.info("="*50 + "\n")

            for chunk in chunks:
                all_pairs.append([query, chunk])
                chunk_tracking.append(idx)

        # 2. 批量推理
        if all_pairs:
            logger.info(f"🧠 将 {len(all_pairs)} 个文本块一次性送入大模型进行 Batch 推理...")
            raw_scores = cross_encoder.predict(all_pairs, show_progress_bar=False)
        else:
            raw_scores = []

        # 3. 分数聚合回各自的候选仓库
        candidate_scores = {i: [] for i in range(len(candidates))}
        for score, cand_idx in zip(raw_scores, chunk_tracking):
            candidate_scores[cand_idx].append(score)

        for idx, candidate in enumerate(candidates):
            scores = candidate_scores[idx]
            if scores:
                max_score = np.max(scores)
                avg_score = np.mean(scores)
                # 长文本经典聚合：一半看最高光时刻，一半看整体平均水平
                candidate["cross_encoder_score"] = float(0.5 * max_score + 0.5 * avg_score)
            else:
                candidate["cross_encoder_score"] = 0.0

        # 4. 后处理：平移并归一化 (Min-Max Scaling)
        all_final_scores = [c["cross_encoder_score"] for c in candidates]
        if all_final_scores:
            min_score = min(all_final_scores)
            max_score = max(all_final_scores)
            
            for c in candidates:
                if max_score > min_score:
                    c["cross_encoder_score"] = (c["cross_encoder_score"] - min_score) / (max_score - min_score)
                else:
                    c["cross_encoder_score"] = 0.5

        # 按照精排分数降序，返回 Top N
        return sorted(candidates, key=lambda x: x["cross_encoder_score"], reverse=True)[:top_n]

    state.reranked_candidates = cross_encoder_rerank_func(
        state.user_query,
        candidates_for_rerank,
        int(agent_config.cross_encoder_top_n)
    )
    
    logger.info(f"✅ 精排完成！保留了 {len(state.reranked_candidates)} 个最优仓库。")
    return {"reranked_candidates": state.reranked_candidates}