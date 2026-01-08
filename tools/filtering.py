# tools/filtering.py
import logging
#淘汰那些星数和深度相关性分数都很低的仓库，淘汰掉那些不符合用户指定硬件要求的仓库。
logger = logging.getLogger(__name__)

def threshold_filtering(state, config):
    """
    1) Filters out repos with too few stars AND too-low cross-encoder scores.
    2) If the user specified hardware constraints (state.hardware_spec),
       narrows down to state.hardware_filtered (populated in dependency_analysis).
    """
    # Import config schema lazily to avoid circular dependency
    from agent import AgentConfiguration
    agent_config = AgentConfiguration.from_runnable_config(config)

    # 1) Basic star + cross-encoder cutoff
    filtered = []
    for repo in state.reranked_candidates:
        stars = repo.get("stars", 0)
        ce_score = repo.get("cross_encoder_score", 0.0)
        # drop only if BOTH the star count AND cross-encoder score are too low
        if stars < agent_config.min_stars and ce_score < agent_config.cross_encoder_threshold:#这个过滤机制很好
            #不会淘汰 那些星数很少，但与用户查询 高度相关 的新项目（相关性分数高）。不会淘汰 那些相关性分数较低，但 非常流行 的老项目（星数高）。
            continue
        filtered.append(repo)

    # if nothing passes, keep all reranked candidates
    if not filtered:
        filtered = list(state.reranked_candidates)

    # 2) Apply hardware filter if specified by user
    if getattr(state, "hardware_spec", None):#getattr(object, name, default=None),object: 对象；name: 属性名称字符串；default: 可选，属性不存在时返回的默认值。
        hw_filtered = getattr(state, "hardware_filtered", None)
        if hw_filtered:
            filtered = hw_filtered
        else:
            logger.info(
                "Hardware spec provided but no hardware_filtered list found; "
                "skipping hardware filter."
            )

    state.filtered_candidates = filtered
    logger.info(
        f"Filtering complete: {len(filtered)} candidates remain "
        f"(after thresholds{' + hardware filter' if state.hardware_spec else ''})."
    )
    return {"filtered_candidates": filtered}
