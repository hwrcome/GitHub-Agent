from mcp.server.fastmcp import FastMCP
import subprocess
import tempfile
import shutil
import os
import stat
import logging
import sys
# 初始化 Server
mcp = FastMCP("CodeQualityAuditor")

# 设置日志，方便调试
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("audit_server")

# 辅助函数：处理 Windows 下只读文件删除报错的问题
def remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)

@mcp.tool()#会提取出函数description
def analyze_repo_quality(clone_url: str) -> str:
    """
    Clones a git repository locally and runs flake8 analysis.
    Returns a JSON-string containing score, issues count, and file count.
    """
    logger.info(f"Starting audit for: {clone_url}")
    
    # 1. 创建临时目录 (搬运原本的逻辑)
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
        # 2. 执行 Git Clone (模拟原代码逻辑)
        # 注意：这里我们直接调用 subprocess，不再依赖 gitpython 库，减少依赖
        # subprocess.run(["git", "clone", "--depth", "1", clone_url, repo_path], check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "http.sslVerify=false", "clone", "--depth", "1", clone_url, repo_path], 
            check=True, 
            capture_output=True
           )
        
        # 3. 统计 Python 文件
        py_files = []
        for root, dirs, files in os.walk(repo_path):
            for file in files:
                if file.endswith(".py"):
                    py_files.append(os.path.join(root, file))
        
        total_files = len(py_files)
        result_data["python_files"] = total_files

        if total_files == 0:
            return str(result_data)

        # 4. 运行 Flake8
        process = subprocess.run(
            [sys.executable, "-m", "flake8", "--max-line-length=120", repo_path],
            capture_output=True,
            text=True
        )
        output = process.stdout.strip()
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
        try:
            shutil.rmtree(temp_dir, onerror=remove_readonly)
        except Exception:
            pass
            
    return str(result_data) # 简单起见返回字符串，最好返回 JSON 结构

if __name__ == "__main__":
    mcp.run(transport='stdio')