#引入了短期记忆
from dotenv import load_dotenv
load_dotenv() # 这句话的意思是：强制把 .env 文件里的变量塞进系统环境变量里！
import os
import logging
import getpass
from pathlib import Path
from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field
from dataclasses import dataclass, field
from typing import List, Any

# ---------------------------
# Import node functions
# ---------------------------
from tools.convert_query import convert_searchable_query
from tools.analyze_intent import analyze_intent
from tools.parse_hardware import parse_hardware_spec
from tools.github2 import ingest_github_repos
from tools.dense_retrieval import hybrid_dense_retrieval
from tools.cross_encoder_reranking1 import cross_encoder_reranking
from tools.filtering import threshold_filtering
from tools.dependency_analysis import dependency_analysis
from tools.activity_analysis import repository_activity_analysis
from tools.decision_maker import decision_maker
from tools.code_quality import code_quality_analysis
from tools.merge_analysis import merge_analysis
from tools.ranking import multi_factor_ranking
from tools.report_generation import report_generation

# ---------------------------
# Logging & Environment Setup
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

dotenv_path = Path(__file__).resolve().parent/ ".env"
print(f"Looking for .env at: {dotenv_path}")
if dotenv_path.exists():
    load_dotenv(dotenv_path)

if "GITHUB_API_KEY" not in os.environ:
    os.environ["GITHUB_API_KEY"] = getpass.getpass("Enter your GitHub API key: ")

# ---------------------------
# State & Configuration
# ---------------------------
#@dataclass是一个装饰器，用于创建一个干净简单的数据容器，用于存储数据，可以实现自动初始化，打印对象等简单方法
@dataclass(kw_only=True)
class AgentState:
    user_query: str = field(default="")
    is_query_clear: bool = field(default=True)
    searchable_query: str = field(default="")
    retry_count: int = field(default=0)                      # 记录当前重试了多少次
    search_history: List[str] = field(default_factory=list)  # 记录用过的废弃关键词
    hardware_spec: str = field(default="")               
    repositories: List[Any] = field(default_factory=list)
    semantic_ranked: List[Any] = field(default_factory=list)
    reranked_candidates: List[Any] = field(default_factory=list)
    filtered_candidates: List[Any] = field(default_factory=list)
    hardware_filtered: List[Any] = field(default_factory=list)
    activity_candidates: List[Any] = field(default_factory=list)
    quality_candidates: List[Any] = field(default_factory=list)
    final_ranked: List[Any] = field(default_factory=list)
    final_results: str = field(default="")
#field是为每个变量/属性创建一个全新的列表，这样不会数据揉到一起，列表在每次使用时都是全新的，而不是共享的。
@dataclass(kw_only=True)
class AgentStateInput:
    user_query: str = field(default="")

@dataclass(kw_only=True)
class AgentStateOutput:
    final_results: str = field(default="")
    is_query_clear: bool = field(default=True)
    retry_count: int = field(default=0)
    search_history: List[str] = field(default_factory=list)
    repositories: List[Any] = field(default_factory=list)
    filtered_candidates: List[Any] = field(default_factory=list)

#basemodel核心用于数据验证、类型转换和配置管理。
class AgentConfiguration(BaseModel):
    max_results: int = Field(100, title="Max Results", description="Max GitHub results")
    per_page: int = Field(15, title="Per Page", description="GitHub results per page")
    dense_retrieval_k: int = Field(40, title="Dense K", description="Top‑K for dense retrieval")
    cross_encoder_top_n: int = Field(10, title="Cross‑encoder N", description="Top‑N after re‑rank")
    min_stars: int = Field(50, title="Min Stars", description="Minimum star count")
    cross_encoder_threshold: float = Field(5.5, title="CE Threshold", description="Cross‑encoder score cutoff")
    sem_model_name: str = Field("all-mpnet-base-v2", title="SentenceTransformer model")
    cross_encoder_model_name: str = Field("/data1/hanwenrui/model/ms", title="Cross‑encoder model")
#Field是给配置项添加人类可读的描述和规则，本质是提高代码的可读性。
#元数据是指数据的一些属性和背景信息
    @classmethod
    def from_runnable_config(cls, config: Any = None) -> "AgentConfiguration":
        cfg = (config or {}).get("configurable", {})
        raw = {k: os.environ.get(k.upper(), cfg.get(k)) for k in cls.__fields__.keys()}
        values = {k: v for k, v in raw.items() if v is not None}
        return cls(**values)

# -------------------------------------------------------
# Build & Compile the Workflow Graph
# -------------------------------------------------------
def route_based_on_intent(state: AgentState) -> str:
    """交警函数：根据意图清晰度决定图的走向"""
    if not state.is_query_clear:
        return "end_early"  # 走向捷径，提前结束
    else:
        return "proceed_to_search"    # 走向主干道，去转换搜索词

def route_after_filtering(state: AgentState) -> str:
    """检查阈值过滤后的结果，决定是继续分析，还是回炉重造"""
    
    # 1. 如果有及格的仓库，直接放行去走后续的并行分析
    if len(state.filtered_candidates) > 0:
        return ["go_dependency", "go_activity", "go_decision"]
    
    # 2. 如果全军覆没（过滤后剩 0 个）
    else:
        # 设置最大重试次数为 2 次，防止无限死循环耗尽 API 额度
        if state.retry_count < 2:
            print(f"⚠️ 候选仓库质量太低，触发第 {state.retry_count + 1} 次重试搜素...")
            return "retry"
        else:
            print("❌ 已达到最大重试次数，放弃搜索。")
            return "give_up"
builder = StateGraph(
    AgentState,
    input=AgentStateInput,
    output=AgentStateOutput,
    config_schema=AgentConfiguration
)

# Core nodes
builder.add_node("analyze_intent", analyze_intent)
builder.add_node("convert_searchable_query", convert_searchable_query)
builder.add_node("parse_hardware",         parse_hardware_spec)
builder.add_node("ingest_github_repos",    ingest_github_repos)
builder.add_node("neural_dense_retrieval", hybrid_dense_retrieval)
builder.add_node("cross_encoder_reranking",cross_encoder_reranking)
builder.add_node("threshold_filtering",    threshold_filtering)
builder.add_node("dependency_analysis",    dependency_analysis)
builder.add_node("repository_activity_analysis", repository_activity_analysis)
builder.add_node("decision_maker",         decision_maker)
builder.add_node("code_quality_analysis",  code_quality_analysis)
builder.add_node("merge_analysis",         merge_analysis)
builder.add_node("multi_factor_ranking",   multi_factor_ranking)
builder.add_node("report_generation",      report_generation)

# Edges (dataflow)
builder.add_edge(START, "analyze_intent")
builder.add_conditional_edges(
    "analyze_intent", 
    route_based_on_intent, 
    {
        "end_early": END,                           # 模糊：直接结束，返回反问的话
        "proceed_to_search": "convert_searchable_query"       # 清晰：放行进入流水线
    }
)
builder.add_edge("convert_searchable_query","parse_hardware")
builder.add_edge("parse_hardware",          "ingest_github_repos")
builder.add_edge("ingest_github_repos",     "neural_dense_retrieval")
builder.add_edge("neural_dense_retrieval",  "cross_encoder_reranking")
builder.add_edge("cross_encoder_reranking", "threshold_filtering")

# **Parallel branches** after filtering:
# builder.add_edge("threshold_filtering",     "dependency_analysis")
# builder.add_edge("threshold_filtering",     "repository_activity_analysis")
# builder.add_edge("threshold_filtering",     "decision_maker")
builder.add_conditional_edges(
    "threshold_filtering",   # 裁判站在这里
    route_after_filtering,   # 裁判的判定逻辑
    {
        # 情况A：找到了好仓库，直接分发给三个并行节点去深入分析
        # LangGraph 支持把一个出口映射到一个列表，自动触发并行执行！
        "go_dependency": "dependency_analysis",
        "go_activity": "repository_activity_analysis",
        "go_decision": "decision_maker",
        
        # 情况B：没找到好仓库，时光倒流，回到第一步换词重新搜！
        "retry": "convert_searchable_query",
        
        # 情况C：实在找不到，跳过分析直接去写报告（报告节点会坦诚告诉用户没找到）
        "give_up": "report_generation" 
    }
)
# Merge the outputs of the three parallel paths:
builder.add_edge("dependency_analysis",     "code_quality_analysis")
builder.add_edge("decision_maker",          "code_quality_analysis")
builder.add_edge("repository_activity_analysis", "merge_analysis")
builder.add_edge("code_quality_analysis",   "merge_analysis")

builder.add_edge("merge_analysis",          "multi_factor_ranking")
builder.add_edge("multi_factor_ranking",    "report_generation")
builder.add_edge("report_generation",       END)

# graph = builder.compile()
memory = MemorySaver()
graph = builder.compile(checkpointer=memory)
# -------------------------------------------------------
# CLI entrypoint
# -------------------------------------------------------
if __name__ == "__main__":
    print("🤖 GitHub 智能推荐 Agent 已启动！(输入 'quit' 或 'exit' 退出)")
    
    context_query = ""
    
    # ✨ 3. 为当前用户的聊天分配一个专属的“会话 ID”
    # 如果未来做成 Web 服务，这里就可以传用户的 UserID 或 SessionID
    thread_config = {"configurable": {"thread_id": "user_hanwenrui_001"}}
    
    while True:
        user_input = input("\n🧑 你: ")
        
        if user_input.lower() in ['quit', 'exit']:
            print("👋 再见！")
            break
            
        if context_query:
            context_query = f"{context_query}。补充要求：{user_input}"
        else:
            context_query = user_input
            
        initial = {"user_query": context_query}
        
        # ✨ 4. 每次 invoke 时，必须把 config 传进去，让图知道去哪里读写记忆
        result = graph.invoke(initial, config=thread_config)
        
        print(f"🤖 Agent: {result.get('final_results', '')}")
        current_state = graph.get_state(thread_config)
        print("\n--- 🧠 记忆快照 (Checkpoint) ---")
        print(f"保存的 user_query: {current_state.values.get('user_query')}")
        print(f"保存的 is_query_clear: {current_state.values.get('is_query_clear')}")
        print(f"保存的 search_history (废弃词库): {current_state.values.get('search_history')}")
        print(f"保存的 retry_count (重试次数): {current_state.values.get('retry_count')}")
        print("--------------------------------\n")
        
        if result.get("is_query_clear", True):
            context_query = ""
# if __name__ == "__main__":
#     initial = AgentStateInput(
#         user_query=(
#             "找几个好用的 Python 框架"
#         )
#     )
#     result = graph.invoke(initial)
#     print(result["final_results"])
#其它字段的初始值是在工作流开始时，AgentState类中定义的默认值，例如空字符串或空列表。
#之前写的代码是要全部显示赋值的，因为我用的typedict，它只是一个类型提示，不能定义默认值。
#而dataclass可以定义默认值，所以可以省略不必要的赋值。
