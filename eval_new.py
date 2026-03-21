import os
import asyncio
from langsmith import Client
from langsmith.evaluation import evaluate
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

# 引入你真实写好的 LangGraph 实例
from agent3 import graph as github_agent_graph

client = Client()

class GradeResult(BaseModel):
    score: int = Field(description="打分为 0 或 1。")
    reasoning: str = Field(description="详细解释扣分或得分的原因。")

# ==========================================
# 裁判 A：结果与交互评估器 (兼容了反问机制)
# ==========================================
def github_rubric_evaluator(run, example):
    user_query = example.inputs.get("query", "")
    agent_response = run.outputs.get("final_results", "") if run.outputs else "运行失败，无输出"
    is_query_clear = run.outputs.get("is_query_clear", True) if run.outputs else True

    prompt = f"""
    你是一位资深的开源架构师，正在审查 AI 助手的表现。
    
    【用户原始需求】: {user_query}
    【Agent 的最终回复】: {agent_response}
    【内部状态-意图是否清晰】: {is_query_clear}
    
    请严格评估，满足以下条件给 1 分，否则 0 分：
    1. 如果意图不清晰 (is_query_clear=False)，Agent 必须在回复中提出引导性的反问，而不是胡乱推荐。
    2. 如果意图清晰，推荐的仓库必须精准匹配技术栈，并包含活跃度（如 Star 数）分析。
    3. 绝不能出现编造的仓库。

    请以 JSON 格式输出，包含 "score" 和 "reasoning"。
    """
    
    judge_llm = ChatOpenAI(
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model=os.getenv("JUDEGE_LLM_MODEL", "z-ai/glm-5"), # 保持你的模型配置
        temperature=0,
        max_retries=2
    )
    
    # 强制使用 json_mode 防爆雷
    result = judge_llm.with_structured_output(GradeResult, method="json_mode").invoke(prompt)
    return {"key": "final_report_quality", "score": result.score, "comment": result.reasoning}

# ==========================================
# 裁判 B：Agent 动态轨迹健康度 (全新升级)
# ==========================================
def github_agentic_evaluator(run, example):
    if not run.outputs:
        return {"key": "agentic_health", "score": 0, "comment": "系统未产生任何输出"}

    # 提取 Agent 特有的动态行为数据
    is_query_clear = run.outputs.get("is_query_clear", True)
    retry_count = run.outputs.get("retry_count", 0)
    search_history = run.outputs.get("search_history", [])
    repos_fetched = run.outputs.get("repositories_count", 0)
    filtered_count = run.outputs.get("filtered_candidates_count", 0)

    trajectory_log = f"""
    【Agent 运行轨迹分析】
    1. 意图拦截触发: {not is_query_clear}
    2. 触发换词重试次数: {retry_count}
    3. 曾尝试的搜索词路径: {search_history}
    4. 单次拉取 GitHub 仓库最高数: {repos_fetched}
    5. 过滤后存活推荐数: {filtered_count}
    """

    prompt = f"""
    你是一位 Agent 架构审查专家，正在评估一个具备“反思重试”能力的 GitHub 推荐系统的轨迹健康度。
    
    【轨迹数据】: 
    {trajectory_log}
    
    请评估该 Agent 的行为逻辑是否健康。只要满足以下【任意一项】即可给 1 分，否则 0 分：
    1. 成功纠错：如果 retry_count > 0，说明系统在遭遇 0 结果时成功触发了重试机制，表现出了容错韧性。
    2. 成功拦截：如果意图拦截被触发（True），说明成功挡住了无意义的宽泛搜索，节约了算力。
    3. 一次过关且漏斗正常：如果没有重试且没有拦截，那么初始拉取数量必须大于存活推荐数量（存在有效过滤）。
    
    请以 JSON 格式输出，包含 "score" 和 "reasoning"。
    """
    
    judge_llm = ChatOpenAI(
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model=os.getenv("JUDEGE_LLM_MODEL", "z-ai/glm-5"),
        temperature=0,
        max_retries=2
    )
    
    result = judge_llm.with_structured_output(GradeResult).invoke(prompt)
    return {"key": "agentic_health", "score": result.score, "comment": result.reasoning}

# ==========================================
# 执行评估任务的入口
# ==========================================
def predict_github_agent(inputs: dict) -> dict:
    query = inputs["query"]
    
    try:
        result = github_agent_graph.invoke({"user_query": query})
        
        # 完整提取新架构的状态变量
        return {
            "final_results": result.get("final_results", ""),
            "is_query_clear": result.get("is_query_clear", True),
            "retry_count": result.get("retry_count", 0),
            "search_history": result.get("search_history", []),
            "repositories_count": len(result.get("repositories", [])),
            "filtered_candidates_count": len(result.get("filtered_candidates", []))
        }
    except Exception as e:
        return {"final_results": f"图执行崩溃: {str(e)}"}

if __name__ == "__main__":
    dataset_name = "github-test" 
    print(f"开始对数据集 {dataset_name} 进行自动化评估...")
    
    experiment_results = evaluate(
        predict_github_agent,
        data=dataset_name,
        evaluators=[github_rubric_evaluator, github_agentic_evaluator], 
        experiment_prefix="agentic-behavior-eval", 
    )
    
    print("评估完成！请前往 LangSmith 网页端查看详细打分和反馈。")