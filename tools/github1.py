# tools/github.py
import re
import os
import base64
import logging
import asyncio
from pathlib import Path
import httpx
from tools.mcp_adapter import mcp_adapter  # Import our MCP adapter
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
logger = logging.getLogger(__name__)

# In-memory cache to store file content for given URLs
FILE_CONTENT_CACHE = {}

#定义一个协程函数，用于获取指定 GitHub 仓库的 README 内容
async def fetch_readme_content(repo_full_name: str, headers: dict, client: httpx.AsyncClient) -> str:
    readme_url = f"https://api.github.com/repos/{repo_full_name}/readme"
    try:
        response = await mcp_adapter.fetch(readme_url, headers=headers, client=client)
        if response.status_code == 200:
            readme_data = response.json()
            content = readme_data.get('content', '')
            if content:
                return base64.b64decode(content).decode('utf-8')
    except Exception as e:
        logger.error(f"Error fetching README for {repo_full_name}: {e}")
    return ""
#定义一个协程函数，用于获取指定文件的内容，并使用缓存来避免重复请求
async def fetch_file_content(download_url: str, client: httpx.AsyncClient) -> str:
    if download_url in FILE_CONTENT_CACHE:
        return FILE_CONTENT_CACHE[download_url]
    try:
        response = await mcp_adapter.fetch(download_url, client=client)
        if response.status_code == 200:
            text = response.text
            FILE_CONTENT_CACHE[download_url] = text
            return text
    except Exception as e:
        logger.error(f"Error fetching file from {download_url}: {e}")
    return ""
#定义一个协程函数，用于获取指定目录下所有 Markdown 文件的内容
async def fetch_directory_markdown(repo_full_name: str, path: str, headers: dict, client: httpx.AsyncClient) -> str:
    md_content = ""
    url = f"https://api.github.com/repos/{repo_full_name}/contents/{path}"
    try:
        response = await mcp_adapter.fetch(url, headers=headers, client=client)
        if response.status_code == 200:
            items = response.json()
            tasks = []
            for item in items:
                if item["type"] == "file" and item["name"].lower().endswith(".md"):
                    tasks.append(fetch_file_content(item["download_url"], client))
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)#并发运行所有任务
                for item, content in zip(items, results):
                    if item["type"] == "file" and item["name"].lower().endswith(".md") and not isinstance(content, Exception):
                        md_content += f"\n\n# {item['name']}\n" + content
    except Exception as e:
        logger.error(f"Error fetching directory markdown for {repo_full_name}/{path}: {e}")
    return md_content
def clean_markdown_noise(text: str) -> str:
    """规则去噪：清洗 GitHub README 中的无意义噪声"""
    if not text:
        return ""
    # 1. 移除标准的 Markdown 徽章和图片
    text = re.sub(r'\[!\[.*?\]\(.*?\)\]\(.*?\)', '', text)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    
    # 2. 移除 HTML 注释
    text = re.sub(r'', '', text, flags=re.DOTALL)
    
    # 🌟 3. 终极杀招：无差别移除所有 HTML 标签 (如 <a>, </a>, <em>, <img>, <div> 等)
    text = re.sub(r'<[^>]+>', '', text)
    
    # 🌟 4. 清除因为删掉标签后，遗留下来的空 Markdown 链接，比如 [](https://...)
    text = re.sub(r'\[\s*\]\([^\)]+\)', '', text)
    
    # 5. 将连续的空行（3个及以上）压缩为2个空行，保持段落清爽
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # 6. 将连续的多个空格压缩为一个空格
    text = re.sub(r'^[ \t]+$', '', text, flags=re.MULTILINE)
    # 第二步：把连续多个换行符（超过2个），全部压缩成标准的2个（即保留正常的段落间距）
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

async def enrich_documentation_with_llm(cleaned_text: str, repo_name: str) -> str:
    """异步调用轻量级 LLM 对文档进行总结和打标"""
    if len(cleaned_text) < 50:
        return "[AI Summary: No sufficient documentation provided.]\n[Tags: N/A]"

    # 截取前 5000 个字符进行分析，节省 Token
    truncated_text = cleaned_text[:5000]

    # 初始化大模型 (建议用 deepseek-chat 等快速便宜的模型)
    llm = ChatOpenAI(
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model=os.getenv("ENRICH_LLM_MODEL", "deepseek/deepseek-chat"),
        temperature=0.1,
        max_tokens=150,
        max_retries=2
    )

    system_prompt = (
        "You are an expert developer. Read the following GitHub repository README excerpt "
        "and extract the core functionality and technical tags. "
        "Output ONLY in the following exact format without any markdown code blocks:\n"
        "[AI Summary: <3-4 sentences summarizing what this repo does>]\n"
        "[Tags: <3-5 comma-separated technical tags>]"
    )

    try:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Repo: {repo_name}\n\nReadme Excerpt:\n{truncated_text}")
        ]
        # 🌟 核心：使用 ainvoke 进行异步调用，完美融入你的协程机制！
        response = await llm.ainvoke(messages)
        return response.content.strip()
    except Exception as e:
        logger.error(f"LLM Enrichment 失败 ({repo_name}): {e}")
        return "[AI Summary: Enrichment failed.]\n[Tags: N/A]"

async def fetch_repo_documentation(repo_full_name: str, headers: dict, client: httpx.AsyncClient) -> str:
    doc_text = ""
    readme_task = asyncio.create_task(fetch_readme_content(repo_full_name, headers, client))
    #立即将获取 README 的耗时网络任务 扔到后台 运行。程序不需要等待。
    root_url = f"https://api.github.com/repos/{repo_full_name}/contents"
    try:
        response = await mcp_adapter.fetch(root_url, headers=headers, client=client)
        if response.status_code == 200:
            items = response.json()
            tasks = []
            for item in items:
                if item["type"] == "file" and item["name"].lower().endswith(".md"):
                    if item["name"].lower() != "readme.md":
                        tasks.append(asyncio.create_task(fetch_file_content(item["download_url"], client)))
                elif item["type"] == "dir" and item["name"].lower() in ["docs", "documentation"]:
                    tasks.append(asyncio.create_task(fetch_directory_markdown(repo_full_name, item["name"], headers, client)))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if not isinstance(res, Exception):
                    doc_text += "\n\n" + res
    except Exception as e:
        logger.error(f"Error fetching repository contents for {repo_full_name}: {e}")
    readme = await readme_task
    if readme:
        doc_text = "# README\n" + readme + doc_text
        
    if not doc_text.strip():
        return "No documentation available."
        
    # 🌟 1. 执行规则清洗
    cleaned_doc = clean_markdown_noise(doc_text)
    
    # 🌟 2. 异步执行 LLM 提纯
    ai_enrichment = await enrich_documentation_with_llm(cleaned_doc, repo_full_name)
    
    # 🌟 3. 将 AI 总结置于最开头，后面跟上清洗后的干净文本
    final_doc = f"{ai_enrichment}\n\n{cleaned_doc}"
    
    return final_doc
    

async def fetch_github_repositories(query: str, max_results: int, per_page: int, headers: dict) -> list:
  #其核心功能是根据给定的查询，从 GitHub API 分页搜索仓库，并对每个搜索到的仓库 并行抓取文档，最终返回一个包含详细信息和文档的仓库列表。  
    url = "https://api.github.com/search/repositories"
    repositories = []
    num_pages = max_results // per_page
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for page in range(1, num_pages + 1):
            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": per_page,
                "page": page
            }#查询参数
            try:
                response = await mcp_adapter.fetch(url, headers=headers, params=params, client=client)
                if response.status_code != 200:
                    logger.error(f"Error {response.status_code}: {response.json().get('message')}")
                    break
                items = response.json().get('items', [])
                if not items:
                    break
                tasks = []
                for repo in items:
                    full_name = repo.get('full_name', '')
                    tasks.append(asyncio.create_task(fetch_repo_documentation(full_name, headers, client)))
                docs = await asyncio.gather(*tasks, return_exceptions=True)
                for repo, doc in zip(items, docs):
                    repo_link = repo['html_url']
                    full_name = repo.get('full_name', '')
                    clone_url = repo.get('clone_url', f"https://github.com/{full_name}.git")
                    star_count = repo.get('stargazers_count', 0)
                    repositories.append({
                        "title": repo.get('name', 'No title available'),
                        "link": repo_link,
                        "clone_url": clone_url,
                        "combined_doc": doc if not isinstance(doc, Exception) else "",
                        "stars": star_count,
                        "full_name": full_name,
                        "open_issues_count": repo.get('open_issues_count', 0)
                    })
            except Exception as e:
                logger.error(f"Error fetching repositories for query {query}: {e}")
                break
    logger.info(f"Fetched {len(repositories)} repositories for query '{query}'.")
    return repositories

async def ingest_github_repos_async(state, config) -> dict:
    #对每个关键词都启动一次 fetch_github_repositories，最后将所有结果去重并返回。
    headers = {
        "Authorization": f"token {os.getenv('GITHUB_API_KEY')}",
        "Accept": "application/vnd.github.v3+json"
    }
    keyword_list = [kw.strip() for kw in state.searchable_query.split(":") if kw.strip()]
    logger.info(f"Searchable keywords (raw): {keyword_list}")
    
    target_language = "python"
    filtered_keywords = []
    for kw in keyword_list:
        if kw.startswith("target-"):
            target_language = kw.split("target-")[-1]
        else:
            filtered_keywords.append(kw)
    keyword_list = filtered_keywords
    logger.info(f"Filtered keywords: {keyword_list} | Target language: {target_language}")
    
    all_repos = []
    from agent import AgentConfiguration
    agent_config = AgentConfiguration.from_runnable_config(config)
    tasks = []
    for keyword in keyword_list:
        query = f"{keyword} language:{target_language}"
        tasks.append(asyncio.create_task(fetch_github_repositories(query, agent_config.max_results, agent_config.per_page, headers)))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if not isinstance(result, Exception):
            all_repos.extend(result)
        else:
            logger.error(f"Error in fetching repositories for a keyword: {result}")
    seen = set()
    unique_repos = []
    for repo in all_repos:
        if repo["full_name"] not in seen:
            seen.add(repo["full_name"])
            unique_repos.append(repo)
    state.repositories = unique_repos
    logger.info(f"Total unique repositories fetched: {len(state.repositories)}")
    return {"repositories": state.repositories}

def ingest_github_repos(state, config):
    return asyncio.run(ingest_github_repos_async(state, config))
