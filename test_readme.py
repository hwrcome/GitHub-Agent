import os
import asyncio
import httpx
import logging
from dotenv import load_dotenv

# 导入你刚刚修改过的 github 工具库中的核心函数
from tools.github2 import fetch_repo_documentation

# 设置日志格式以方便看报错和进度
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 加载环境变量（确保 .env 中有 GITHUB_API_KEY 和 OPENROUTER_API_KEY）
load_dotenv()

async def run_isolated_test():
    """单独测试 GitHub README 拉取、去噪与 LLM 提纯链路"""
    
    # 选一个 README 非常长、而且带有很多构建徽章的著名仓库作为小白鼠
    test_repo = "tiangolo/fastapi"
    
    github_token = os.getenv("GITHUB_API_KEY")
    if not github_token:
        logger.warning("未找到 GITHUB_API_KEY，可能会触发 GitHub API 频率限制！")

    headers = {
        "Authorization": f"token {github_token}" if github_token else "",
        "Accept": "application/vnd.github.v3+json"
    }

    print(f"🚀 开始测试拉取并提纯仓库: {test_repo} ...")
    print("⏳ 正在请求 GitHub API 并调用 LLM 进行提纯，请稍候...")

    # 初始化异步 HTTP 客户端
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            # 🌟 直接调用你刚刚修改完的文档拉取函数！
            result_doc = await fetch_repo_documentation(test_repo, headers, client)
            
            print("\n" + "=".center(60, "="))
            print("🎉 测试成功！以下是生成的 combined_doc 头部预览：")
            print("=".center(60, "=") + "\n")
            
            # 我们只打印前 1000 个字符，足够看到 AI 总结和清洗后的正文了
            print(result_doc[:1000])
            
            print("\n" + "=".center(60, "="))
            print(f"📄 总字符数: {len(result_doc)}")
            
            # 简单验证一下是否包含我们期望的 AI 标签
            if "[AI Summary:" in result_doc and "[Tags:" in result_doc:
                print("✅ 验证通过：成功检测到大模型生成的元数据标签！")
            else:
                print("⚠️ 警告：未检测到大模型生成的标签，请检查 LLM 调用是否报错。")

        except Exception as e:
            logger.error(f"❌ 测试过程中发生异常: {e}")

if __name__ == "__main__":
    # 启动异步事件循环运行测试
    asyncio.run(run_isolated_test())