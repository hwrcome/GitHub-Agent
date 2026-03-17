import os
import json
import logging
import re
from typing import Any, Dict

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

# 导入我们刚刚写的中间件
from tools.skill_manager import MarkdownSkillMiddleware

logger = logging.getLogger(__name__)

def _build_llm(config: Any) -> ChatOpenAI:
    # ... (保持原样不变) ...
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("REPORT_LLM_MODEL", "deepseek/deepseek-r1")
    temperature = float(os.getenv("REPORT_LLM_TEMPERATURE", "0.1"))
    max_tokens = int(os.getenv("REPORT_LLM_MAX_TOKENS", "2048"))
    timeout = int(os.getenv("REPORT_LLM_TIMEOUT", "180"))

    if not api_key:
        logger.warning("未检测到 API Key，模型调用可能会失败。")

    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=timeout,
        max_retries=3,
        default_headers={
            "HTTP-Referer": "https://github.com/hanwenrui/github-agent",
            "X-Title": "GitHub Agent"
        }
    )

def _slim_repo(repo: Dict[str, Any]) -> Dict[str, Any]:
    # ... (保持原样不变) ...
    allowed = [
        "title", "full_name", "link", "stars", "semantic_similarity", 
        "cross_encoder_score", "activity_score", "commit_frequency", 
        "pr_count", "latest_commit_days", "open_issues_count", 
        "code_quality_score", "code_quality_issues", "python_files", 
        "final_score", "combined_doc"
    ]
    out = {k: repo.get(k) for k in allowed if k in repo}
    if "combined_doc" in out and isinstance(out["combined_doc"], str):
        out["combined_doc"] = out["combined_doc"][:600]
    return out


def report_generation(state, config) -> dict:
    """
    基于智能决策的终端响应节点：模型根据用户需求自行决定是否调用 Skill
    """
    llm = _build_llm(config)

    top_n = 3
    if isinstance(state, dict):
        ranked = state.get("final_ranked", [])
        user_query = state.get("user_query", "")
    else:
        ranked = getattr(state, "final_ranked", []) or []
        user_query = getattr(state, "user_query", "")

    top_repos = [_slim_repo(r) for r in ranked[:top_n]]
    payload = {
        "top_n": top_n,
        "repositories": top_repos,
    }
    input_json = json.dumps(payload, ensure_ascii=False, indent=2)

    # 🌟 核心修改 1：重写 System Prompt，赋予大模型行为准则和自由度
    system_prompt = (
        "You are an intelligent GitHub open-source project recommendation expert.\n"
        "The underlying data pipeline has already filtered and ranked the Top repositories for the user.\n"
        "Please carefully read the user's original query to understand their true intent.\n\n"
        "【Action Guidelines】:\n"
        "1. If the user explicitly asks for a 'report', 'detailed analysis', or 'explanation', you SHOULD use the `load_skill` tool to read the appropriate skill manual (e.g., repo-recommendation-advisor) and output a strictly formatted Markdown report.\n"
        "2. If the user only wants a 'simple list', 'ranking', or 'top repos', you CAN directly output the names, links, and scores WITHOUT loading any skill manual to save time.\n"
        "3. If the candidate data is empty, directly inform the user that no suitable repositories were found.\n\n"
        "Decide your best response strategy based on what the user actually asked for."
    )

    agent = create_agent(
        llm,
        system_prompt=system_prompt,
        middleware=[MarkdownSkillMiddleware()],
    )

    logger.info(f"🚀 正在启动智能响应节点，候选仓库数量: {len(top_repos)}")
    
    # 🌟 核心修改 2：把用户的原话顶在最前面，不再强迫它调用工具
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": f"The user's original query is: '{user_query}'\n\nCandidate repositories data (JSON):\n{input_json}\n\nPlease decide the best way to respond based on the Action Guidelines."
                }
            ]
        },
        config
    )

    # 提取并清理最终生成的文本
    final_message = result["messages"][-1]
    raw_content = getattr(final_message, "content", str(final_message)).strip()
    
    # 把原始输出打印到控制台，方便我们抓虫
    logger.info(f"🤖 大模型原始输出长度: {len(raw_content)} 字符")
    
    # 5. 清理 DeepSeek 的 <think> 标签
    md = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()

    # 🌟 防御性编程：如果清完 <think> 之后没东西了，说明它把答案写在思考里了！
    # 我们就退回使用原始文本，并把 <think> 标签稍微美化一下
    if not md and raw_content:
        logger.warning("清理 <think> 后内容为空！模型可能把报告写在了思考过程中。启动回退机制...")
        md = raw_content.replace("<think>", "> **🧠 模型的思考过程:**\n> ").replace("</think>", "\n\n---\n")

    if not md:
        logger.warning("智能体真的返回了空内容！")
        md = "## Executive summary\n\nNo report content generated.\n"

    return {"final_results": md}
    # final_message = result["messages"][-1]
    # md = getattr(final_message, "content", str(final_message)).strip()
    # md = re.sub(r"<think>.*?</think>", "", md, flags=re.DOTALL).strip()

    # if not md:
    #     logger.warning("智能体返回了空内容！")
    #     md = "## Executive summary\n\nNo report content generated.\n"

    # return {"final_results": md}
# import os
# import json
# import logging
# import re
# from typing import Any, Dict

# from langchain_openai import ChatOpenAI
# from langchain.agents import create_agent

# # 导入我们刚刚写的中间件
# from tools.skill_manager import MarkdownSkillMiddleware

# logger = logging.getLogger(__name__)

# def _build_llm(config: Any) -> ChatOpenAI:
#     base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
#     api_key = os.getenv("OPENROUTER_API_KEY")
#     model = os.getenv("REPORT_LLM_MODEL", "deepseek/deepseek-r1")
#     temperature = float(os.getenv("REPORT_LLM_TEMPERATURE", "0.1"))
#     max_tokens = int(os.getenv("REPORT_LLM_MAX_TOKENS", "2048"))
#     timeout = int(os.getenv("REPORT_LLM_TIMEOUT", "180"))

#     if not api_key:
#         logger.warning("未检测到 API Key，模型调用可能会失败。")

#     return ChatOpenAI(
#         base_url=base_url,
#         api_key=api_key,
#         model=model,
#         temperature=temperature,
#         max_tokens=max_tokens,
#         request_timeout=timeout,
#         max_retries=3,
#         default_headers={
#             "HTTP-Referer": "https://github.com/hanwenrui/github-agent",
#             "X-Title": "GitHub Agent"
#         }
#     )

# def _slim_repo(repo: Dict[str, Any]) -> Dict[str, Any]:
#     """保留模型真正需要的字段，防止输入过载"""
#     allowed = [
#         "title", "full_name", "link", "stars", "semantic_similarity", 
#         "cross_encoder_score", "activity_score", "commit_frequency", 
#         "pr_count", "latest_commit_days", "open_issues_count", 
#         "code_quality_score", "code_quality_issues", "python_files", 
#         "final_score", "combined_doc"
#     ]
#     out = {k: repo.get(k) for k in allowed if k in repo}
#     if "combined_doc" in out and isinstance(out["combined_doc"], str):
#         out["combined_doc"] = out["combined_doc"][:600]
#     return out


# def report_generation(state, config) -> dict:
#     """
#     基于 Tool Calling + Middleware 架构的报告生成节点
#     """
#     llm = _build_llm(config)

#     # 1. 兼容性读取状态数据
#     top_n = 3
#     if isinstance(state, dict):
#         ranked = state.get("final_ranked", [])
#         user_query = state.get("user_query", "")
#     else:
#         ranked = getattr(state, "final_ranked", []) or []
#         user_query = getattr(state, "user_query", "")

#     top_repos = [_slim_repo(r) for r in ranked[:top_n]]
#     payload = {
#         "user_query": user_query,
#         "top_n": top_n,
#         "repositories": top_repos,
#     }
#     input_json = json.dumps(payload, ensure_ascii=False, indent=2)

#     # 2. 🌟 核心：使用官方 create_agent 组装智能体
#     agent = create_agent(
#         llm,
#         system_prompt=(
#             "You are an expert technical analyst and recommender. "
#             "Your task is to write a final Markdown recommendation report based on the provided JSON data."
#         ),
#         middleware=[MarkdownSkillMiddleware()],
#     )

#     logger.info(f"🚀 正在启动技能智能体生成报告，候选仓库数量: {len(top_repos)}")
    
#     # 3. 触发智能体，开启 ReAct 循环
#     result = agent.invoke(
#         {
#             "messages": [
#                 {
#                     "role": "user",
#                     "content": f"Please generate the recommendation report based on this data. You MUST load the appropriate skill first.\n\n{input_json}"
#                 }
#             ]
#         },
#         config
#     )

#     # 4. 提取最终生成的报告文本
#     final_message = result["messages"][-1]
#     md = getattr(final_message, "content", str(final_message)).strip()

#     # 5. 清理 DeepSeek 的 <think> 标签，保证 Markdown 纯净
#     md = re.sub(r"<think>.*?</think>", "", md, flags=re.DOTALL).strip()

#     if not md:
#         logger.warning("智能体返回了空内容！")
#         md = "## Executive summary\n\nNo report content generated.\n"

#     return {"final_results": md}