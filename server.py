from mcp.server.fastmcp import FastMCP
import subprocess
import tempfile
import shutil
import os
import stat
import logging
import sys
#使用FASTMCP框架创建一个名为 CodeQualityAuditor 的服务器，专门负责分析 GitHub 仓库的代码质量。这个服务器将提供一个工具函数 analyze_repo_quality，接受一个 Git 仓库的克隆 URL，执行以下步骤：s
# 初始化 Server
mcp = FastMCP("CodeQualityAuditor")

# 设置日志，方便调试
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("audit_server")

# 辅助函数：处理 Windows 下只读文件删除报错的问题
def remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)

@mcp.tool()
def analyze_repo_quality(clone_url: str) -> str:
    """
    Clones a git repository locally and runs flake8 analysis.
    Returns a JSON-string containing score, issues count, and file count.
    """
    logger.info(f"Starting audit for: {clone_url}")
    
    # 1. 创建临时目录 
    temp_dir = tempfile.mkdtemp()
    repo_name = clone_url.split("/")[-1].replace(".git", "")
    repo_path = os.path.join(temp_dir, repo_name)
    
    result_data = {
        "code_quality_score": 0,
        "code_quality_issues": 0,
        "python_files": 0,
        "details": ""
    }

    try:
        # 2. 执行 Git Clone (🌟 增加超时保护与独立错误拦截)
        try:
            subprocess.run(
                ["git", "-c", "http.sslVerify=false", "clone", "--depth", "1", clone_url, repo_path], 
                check=True, 
                capture_output=True,
                text=True,    # 确保报错输出是字符串，避免 bytes 解码问题
                timeout=60    # 🌟 关键防御：最多等 60 秒，如果卡在输入密码或网络死锁，直接强行掐断！
            )
        except subprocess.TimeoutExpired:
            logger.error(f"⚠️ Git Clone 超时 (60秒): {clone_url}")
            result_data["details"] = "Git clone 超时，仓库可能网络异常。"
            return str(result_data)  # 直接返回0分结果，让 LangGraph 继续流转
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Git Clone 失败: {clone_url} | 报错: {e.stderr}")
            result_data["details"] = f"无法访问该仓库(可能已删除或设为私有): {e.stderr}"
            return str(result_data)  # 直接返回0分结果，让 LangGraph 继续流转
        
        # 3. 统计 Python 文件
        py_files = []
        for root, dirs, files in os.walk(repo_path):
            for file in files:
                if file.endswith(".py"):
                    py_files.append(os.path.join(root, file))
        
        total_files = len(py_files)
        result_data["python_files"] = total_files

        if total_files == 0:
            result_data["details"] = "该仓库没有找到 Python 文件。"
            return str(result_data)

        # 4. 运行 Flake8 (🌟 同样增加超时保护)
        try:
            process = subprocess.run(
                [sys.executable, "-m", "flake8", "--max-line-length=120", repo_path],
                capture_output=True,
                text=True,
                timeout=120  # 🌟 关键防御：给代码检查最多 2 分钟，防止奇葩大文件卡死
            )
            output = process.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.error(f"⚠️ Flake8 检查超时 (120秒): {clone_url}")
            result_data["details"] = "代码库过大或过于复杂，Flake8 检查超时。"
            return str(result_data)

        error_count = len(output.splitlines()) if output else 0
        
        # 5. 计算分数 (完全照搬原本的算法)
        issues_per_file = error_count / total_files
        if issues_per_file <= 2:
            score = 95 + (2 - issues_per_file) * 2.5
        elif issues_per_file <= 5:
            score = 70 + (5 - issues_per_file) * 6.5
        elif issues_per_file <= 10:
            score = 40 + (10 - issues_per_file) * 3
        else:
            score = max(10, 40 - (issues_per_file - 10) * 2)
            
        result_data["code_quality_score"] = int(score)
        result_data["code_quality_issues"] = error_count
        result_data["details"] = output[:500] + "..." if len(output) > 500 else output
        
    except Exception as e:
        logger.error(f"Error analyzing {clone_url}: {e}")
        result_data["details"] = str(e)
    finally:
        # 6. 清理现场
        # 💡 Python 的特性：就算上面的代码执行了 return 提前结束，finally 里的清理代码也必定会被执行！
        try:
            shutil.rmtree(temp_dir, onerror=remove_readonly)
            logger.info(f"成功清理临时目录: {temp_dir}")
        except Exception as e:
            logger.warning(f"清理临时目录失败 {temp_dir}: {e}")
            pass
            
    return str(result_data) 

if __name__ == "__main__":
    mcp.run(transport='stdio')