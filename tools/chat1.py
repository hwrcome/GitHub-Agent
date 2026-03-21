import os
import re
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

# 1. 实例化模型 (使用你原有的配置)
zhipuai_api_key = "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"
llm = ChatOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=zhipuai_api_key,
    model="deepseek/deepseek-r1",
    temperature=0.3, # 稍微给点温度，便于重试时发散思维
    max_tokens=2048,
    request_timeout=180,
    max_retries=3
)

# 2. 基础的 System Prompt (保留你极其优秀的规则和 Examples)
base_system_prompt = """You are a GitHub search optimization expert.

Your job is to:
1. Read a user's query about tools, research, or tasks.
2. Detect if the query mentions a specific programming language other than Python (for example, JavaScript or JS). If so, record that language as the target language.
3. Think iteratively and generate your internal chain-of-thought enclosed in <think> ... </think> tags.
4. After your internal reasoning, output up to five GitHub-style search tags or library names that maximize repository discovery.
   Use as many tags as necessary based on the query's complexity, but never more than five.
5. If you detected a non-Python target language, append an additional tag at the end in the format target-[language] (e.g., target-javascript).
   If no specific language is mentioned, do not include any target tag.
   
Output Format:
tag1:tag2[:tag3[:tag4[:tag5[:target-language]]]]

Rules:
- Use lowercase and hyphenated keywords (e.g., image-augmentation, chain-of-thought).
- Use terms commonly found in GitHub repo names, topics, or descriptions.
- Avoid generic terms like "python", "ai", "tool", "project".
- Do NOT use full phrases or vague words like "no-code", "framework", or "approach".
- Prefer real tools, popular methods, or dataset names when mentioned.
- If your output does not strictly match the required format, correct it after your internal reasoning.
- Choose high-signal keywords to ensure the search yields the most relevant GitHub repositories.

Excellent Examples:
Input: "No code tool to augment image and annotation"
Output: image-augmentation:albumentations

Input: "Visual reasoning models trained on multi-modal datasets"
Output: multimodal-reasoning:vlm

Input: "I want repos related to instruction-based finetuning for LLaMA 2"
Output: instruction-tuning:llama2

Input: "Deep learning-based object detection with YOLO and transformer architecture"
Output: object-detection:yolov5:transformer

Input: "Find repositories implementing data augmentation pipelines in JavaScript"
Output: data-augmentation:target-javascript

Output must be ONLY the search tags separated by colons. Do not include any extra text, bullet points, or explanations.
"""

def parse_search_tags(response: str) -> dict:
    """提取 <think> 过程和最终的 tags (返回字典方便打日志)"""
    thought_process = ""
    tags = response.strip()
    if "<think>" in response and "</think>" in response:
        start_index = response.index("<think>") + len("<think>")
        end_index = response.index("</think>")
        thought_process = response[start_index:end_index].strip()
        tags = response[end_index + len("</think>"):].strip()
    return {"query": tags, "thought": thought_process}

def valid_tags(tags: str) -> bool:
    pattern = r'^[a-z0-9-]+(?::[a-z0-9-]+){0,5}$'
    return re.match(pattern, tags) is not None

# ==========================================
# 🌟 核心升级：支持传入失败历史的转换函数
# ==========================================
def iterative_convert_to_search_tags(query: str, search_history: list = None, max_iterations: int = 2) -> dict:
    """
    Args:
        query: 用户的原始需求
        search_history: Agent 之前用过但失败的关键词列表
        max_iterations: 格式错误的内部重试次数
    Returns:
        dict: 包含最终 tags 和 思考过程的字典
    """
    history = search_history or []
    
    # 根据是否有失败历史，决定 Human Prompt 的语气
    if not history:
        human_prompt = f"User Query: {query}"
    else:
        human_prompt = f"""
        User Query: {query}
        
        【WARNING】: You previously generated the following tags, but they yielded ZERO good repositories on GitHub:
        {history}
        
        Please THINK carefully about why those tags failed (e.g., too restrictive, rare combination). 
        Then, generate a completely NEW, BROADER, or DIFFERENT set of tags. Do NOT repeat the failed tags!
        """

    prompt = ChatPromptTemplate.from_messages([
        ("system", base_system_prompt),
        ("human", human_prompt)
    ])
    chain = prompt | llm

    print(f"\n[Search Generator] Input Query: {query}")
    if history:
        print(f"   🚨 触发降级重试！规避历史失败词: {history}")

    for iteration in range(max_iterations):
        response = chain.invoke({})
        parsed_result = parse_search_tags(response.content)
        tags_output = parsed_result["query"]
        
        if valid_tags(tags_output):
            print(f"   💡 R1 思考过程: {parsed_result['thought'][:150]}...")
            print(f"   ✅ Valid tags: {tags_output}")
            return parsed_result
        else:
            print("   ⚠️ Invalid format. Requesting refinement...")
            human_prompt += "\nYour last output was invalid. Please strictly follow the format: tag1:tag2."
            prompt = ChatPromptTemplate.from_messages([("system", base_system_prompt), ("human", human_prompt)])
            chain = prompt | llm
            
    # 兜底
    return {"query": tags_output, "thought": "Failed to format properly."}