import asyncio
import sys
import logging
import ast  # <--- ✅ 补上了这个关键导入
from typing import Optional
from contextlib import AsyncExitStack

# 移除原本不需要的 os, subprocess, tempfile, shutil 等，保持 Client 轻量化
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 配置 Logger
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# 配置路径 (保持你原本的设置)
# ---------------------------------------------------------
SERVER_SCRIPT = "/data1/hanwenrui/mcp/server.py"
SERVER_PYTHON = "/data1/hanwenrui/anaconda3/envs/check/bin/python"

# ---------------------------------------------------------
# MCP Client 类定义
# ---------------------------------------------------------
class MCPClient:
    def __init__(self, name, args, command):
        # Initialize session and client objects
        self.name = name
        self.command = command
        self.args = args
        self.session: Optional[ClientSession] = None
        # 因为 mcp 一般都是长时连接，所以用 ExitStack 来管理上下文
        self.exit_stack = AsyncExitStack()
        self.tools = []
        
    async def init(self):
        await self.connect_to_server()
    
    async def close(self):
        try:
            self.session = None
            # 安全关闭 exit_stack，忽略所有异常
            try:
                if hasattr(self, 'exit_stack'):
                    await self.exit_stack.aclose()
            except Exception:
                pass
        except Exception:
            pass  # 忽略所有异常
    
    def get_tools(self):
        return self.tools

    async def call_tool(self, name: str, params: dict):
        # 确保 session 存在再调用
        if not self.session:
            raise RuntimeError("Session not initialized")
        return await self.session.call_tool(name=name, arguments=params)
        
    async def connect_to_server(self):
        """Connect to an MCP server"""
        server_params = StdioServerParameters(
            command=self.command,
            args=self.args
        ) # 告诉 MCP 服务端代码在哪

        # 创建物理连接 (stdio_transport)
        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        
        # 创建协议会话 (session)
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))

        # 初始化握手
        await self.session.initialize()
        
        # 获取工具列表
        response = await self.session.list_tools()
        tools = response.tools
        self.tools = []
    
        # 转换 Tool 对象为字典以便查看
        for tool in tools:
            tool_dict = {
                "name": tool.name if hasattr(tool, 'name') else str(tool),
                "description": tool.description if hasattr(tool, 'description') else "",
                "inputSchema": tool.inputSchema if hasattr(tool, 'inputSchema') else {}
            }
            self.tools.append(tool_dict)
        
        # 打印日志确认连接成功
        # logger.info(f"Connected to server with tools: {[t.name for t in tools]}")
        print(f"\nConnected to server with tools: {[t.name for t in tools]}")

# ---------------------------------------------------------
# 核心业务逻辑函数
# ---------------------------------------------------------

async def call_mcp_tool(repo: dict) -> dict:
    """
    单个仓库的分析逻辑：启动 Client -> 调用 Server -> 解析结果 -> 关闭 Client
    """
    # 1. 获取参数
    clone_url = repo.get("clone_url")
    if not clone_url:
        return repo
    params = {"clone_url": clone_url}

    # 2. 初始化并连接客户端 (CGI模式：每次请求新建一个进程)
    # 注意：args 必须是列表形式 [SERVER_SCRIPT]
    client = MCPClient(
        name="analyze_repo_quality",
        args=[SERVER_SCRIPT], 
        command=SERVER_PYTHON
    )

    try:
        await client.init()
        
        # 3. 调用工具
        mcp_result = await client.call_tool(name="analyze_repo_quality", params=params)
        
        # 4. 解析结果
        if mcp_result and mcp_result.content:
            text_content = ""
            for item in mcp_result.content:
                if item.type == 'text':
                    text_content += item.text
            
            # Server 返回的是字符串形式的字典，需要安全转换
            try:
                # ✅ 这里使用了 ast，所以开头必须 import ast
                data = ast.literal_eval(text_content)
                if isinstance(data, dict):
                    repo.update(data) # 更新分数、问题数等字段
                else:
                    logger.warning(f"Server returned non-dict data for {clone_url}")
            except Exception as e:
                logger.error(f"Failed to parse server output for {clone_url}: {e}")
                repo["code_quality_score"] = 0
        else:
            logger.warning(f"No content returned from MCP for {clone_url}")
            repo["code_quality_score"] = 0

    except Exception as e:
        logger.error(f"MCP execution failed for {clone_url}: {e}")
        repo["code_quality_score"] = 0
        
    finally:
        # ⚠️ 5. 必须关闭连接，防止僵尸进程
        await client.close()

    return repo


async def code_quality_analysis_async(state, config) -> dict:
    """
    并行执行代码质量分析的关键节点
    """
    # 暴力模式：注释掉了这里的判断逻辑，强制运行
    # if not getattr(state, "run_code_analysis", False):
    #     logger.info("Skipping code quality analysis as per decision maker.")
    #     state.quality_candidates = []
    #     return {"quality_candidates": state.quality_candidates}

    tasks = []
    candidates = getattr(state, "filtered_candidates", []) # 安全获取列表

    for repo in candidates:
        if "clone_url" not in repo:
            # 补全 URL
            repo["clone_url"] = f"https://github.com/{repo.get('full_name', '')}.git"
        
        # 加入异步任务队列
        tasks.append(call_mcp_tool(repo))
    
    # 并行执行所有任务
    quality_list = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 过滤掉执行出错的结果
    final_list = [res for res in quality_list if not isinstance(res, Exception)]
    
    state.quality_candidates = final_list
    logger.info(f"Code quality analysis complete. Processed {len(final_list)} repos.")
    return {"quality_candidates": state.quality_candidates}

def code_quality_analysis(state, config):
    """
    Synchronous wrapper
    """
    return asyncio.run(code_quality_analysis_async(state, config))