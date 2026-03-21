import os
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# 1. 定义大模型审题的结构化输出格式
class IntentCheck(BaseModel):
    is_clear: bool = Field(description="用户需求是否包含足够的技术细节（如编程语言、核心功能）来进行精确搜索？")
    clarification_question: str = Field(description="如果需求模糊，生成一句友好的反问（如：您是想找前端还是后端的库？）；如果清晰，留空。")

# 2. 定义意图分析节点
def analyze_intent(state,config) -> dict:
    """评估用户意图，若模糊则提前拦截并生成反问"""
    user_query = getattr(state, "user_query", "")
    
    # 召唤模型 (使用你配置好的环境变量)
 
    llm = ChatOpenAI(
        
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model=os.getenv("ENRICH_LLM_MODEL", "deepseek/deepseek-v3.2"),
        temperature=0.2,
        max_retries=2
    )

    structured_llm = llm.with_structured_output(IntentCheck)
    prompt = f"""
    你是一个专业的 GitHub 开源项目推荐专家。请评估以下用户请求是否足够清晰。
    一个清晰的请求必须包含：编程语言、核心功能或特定应用场景。

    【评估标准与示例】

    示例 1（极度模糊：缺乏所有要素）
    用户请求："推荐几个好用的开源库"
    内部判断：is_clear=False
    反问策略："开源生态非常庞大，请问您习惯使用哪种编程语言（如 Python、C++）？主要想解决什么领域的问题（如后端开发、数据分析）？"

    示例 2（部分模糊：有语言，无场景）
    用户请求："有没有什么值得学习的 C++ 项目推荐？"
    内部判断：is_clear=False
    反问策略："C++ 的应用方向很多，请问您是想看网络编程、音视频处理，还是底层基础框架类的项目？"

    示例 3（部分模糊：有场景，无限制）
    用户请求："最近在搞大模型，推荐点工具"
    内部判断：is_clear=False
    反问策略："大模型工具链很长，您是需要训练微调框架（如 LLaMA-Factory），还是推理部署工具（如 vLLM），或者是应用层的 Agent 编排框架？"

    示例 4（意图清晰：可直接执行搜索）
    用户请求："找一个用 Rust 写的，并且支持高并发的微服务网关项目。"
    内部判断：is_clear=True
    反问策略：(留空)

    【当前用户请求】
    用户请求：{user_query}
    """
    
    
    result = structured_llm.invoke(prompt)
    
    if not result.is_clear:
        # 🚨 拦截！返回 False，并把反问用户的话写入 final_results
        return {
            "is_query_clear": False, 
            "final_results": result.clarification_question
        }
    else:
        # ✅ 放行！
        return {"is_query_clear": True}