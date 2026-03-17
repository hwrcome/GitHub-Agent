import os
import logging
from dotenv import load_dotenv

# 导入你刚刚修改好的报告生成函数
from tools.report_generation import report_generation

# 设置日志，方便看报错
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# 加载环境变量 (确保你的 OPENROUTER_API_KEY 在这里能被读到)
load_dotenv()

def run_isolated_test():
    """单独测试报告生成模块"""
    print("🚀 开始独立测试报告生成模块...")

    # 1. 伪造一个完美的假 State，模拟前面所有节点跑完后的结果
    mock_state = {
        "user_query": "I am looking for lightweight chain-of-thought reasoning models for low-resource GPUs. Please run a static analysis and check for flake8 compliance.",
        "final_ranked": [
            {
                "full_name": "FakeAI/lightweight-cot",
                "title": "A super fast CoT model for 8GB GPUs",
                "link": "https://github.com/FakeAI/lightweight-cot",
                "stars": 1250,
                "activity_score": 95.5,
                "code_quality_score": 98,
                "code_quality_issues": 1,
                "commit_frequency": 45,
                "python_files": 12,
                "combined_doc": "This repository provides a lightweight Chain-of-Thought reasoning model. It is highly optimized for low-resource GPUs like RTX 3060."
            },
            {
                "full_name": "UniversityLab/TinyReasoning",
                "title": "Academic small reasoning model",
                "link": "https://github.com/UniversityLab/TinyReasoning",
                "stars": 340,
                "activity_score": 60.2,
                "code_quality_score": 75,
                "code_quality_issues": 15,
                "commit_frequency": 5,
                "python_files": 8,
                "combined_doc": "TinyReasoning is an academic project for reasoning. Note: code quality is a bit messy, some flake8 errors."
            },
            {
                "full_name": "OldDev/abandoned-cot",
                "title": "An old CoT attempt",
                "link": "https://github.com/OldDev/abandoned-cot",
                "stars": 890,
                "activity_score": -10.5,
                "code_quality_score": 40,
                "code_quality_issues": 50,
                "commit_frequency": 0,
                "python_files": 30,
                "combined_doc": "Archived. This was an attempt at CoT but is no longer maintained."
            }
        ]
    }

    # 2. 伪造一个空的 config (因为你的代码里暂时没强制依赖 config 里的复杂变量)
    mock_config = {}

    # 3. 直接调用函数！跳过前面的千山万水！
    try:
        result = report_generation(mock_state, mock_config)
        
        print("\n" + "="*50)
        print("🎉 报告生成成功！以下是大模型的输出：")
        print("="*50 + "\n")
        print(result["final_results"])
        
    except Exception as e:
        print(f"\n❌ 测试失败，报错信息: {e}")

if __name__ == "__main__":
    run_isolated_test()