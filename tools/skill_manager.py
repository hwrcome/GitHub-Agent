import yaml
import logging
from pathlib import Path
from typing import Callable

from langchain.tools import tool
from langchain.agents.middleware import ModelRequest, ModelResponse, AgentMiddleware
from langchain.messages import SystemMessage

logger = logging.getLogger(__name__)

@tool
def load_skill(skill_name: str) -> str:
    """Load the full content of a skill into the agent's context.

    Use this when you need detailed information about how to handle a specific
    type of request. This will provide you with comprehensive instructions,
    policies, and guidelines for the skill area.

    Args:
        skill_name: The name of the skill to load (e.g., "repo-recommendation-advisor")
    """
    # 定位到根目录下的 skills 文件夹
    skill_path = Path(__file__).resolve().parent.parent / "skills" / skill_name / "SKILL.md"
    
    if skill_path.exists():
        content = skill_path.read_text(encoding="utf-8")
        logger.info(f"✅ 智能体成功调用了工具，加载技能手册: {skill_name}")
        # 去除 YAML 头，只给大模型看正文
        if content.startswith("---"):
            return content.split("---", 2)[-1].strip()
        return content
    
    logger.warning(f"⚠️ 智能体试图加载不存在的技能: {skill_name}")
    return f"未找到技能: {skill_name}。请检查技能名称是否正确。"


class MarkdownSkillMiddleware(AgentMiddleware):
    """官方最新架构：拦截大模型请求并无感注入技能菜单的中间件"""
    
    # 自动把 load_skill 工具绑定给 Agent
    tools = [load_skill]

    def __init__(self):
        skills_list = []
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        
        # 扫描本地所有的 SKILL.md，提取 Metadata 生成菜单
        if skills_dir.exists():
            for skill_folder in skills_dir.iterdir():
                skill_file = skill_folder / "SKILL.md"
                if skill_folder.is_dir() and skill_file.exists():
                    try:
                        content = skill_file.read_text(encoding="utf-8")
                        if content.startswith("---"):
                            metadata = yaml.safe_load(content.split("---", 2)[1])
                            skills_list.append(f"- **{metadata['name']}**: {metadata['description']}")
                    except Exception as e:
                        logger.error(f"解析技能 {skill_folder.name} 失败: {e}")
        
        self.skills_prompt = "\n".join(skills_list)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """核心拦截器：把菜单悄悄塞进系统提示词的最末尾"""
        if not self.skills_prompt:
            return handler(request)

        # skills_addendum = (
        #     f"\n\n## Available Skills\n\n{self.skills_prompt}\n\n"
        #     "CRITICAL: Use the `load_skill` tool when you need detailed information "
        #     "about handling a specific type of request. You MUST read the skill manual before generating the report."
        # )
        skills_addendum = (
            f"\n\n## Available Skills\n\n{self.skills_prompt}\n\n"
             "NOTE: Use the load_skill tool if you need detailed instructions for formal tasks like writing a report "
            
         )

        
        # 官方精髓：追加内容到 SystemMessage
        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": skills_addendum}
        ]
        new_system_message = SystemMessage(content=new_content)
        modified_request = request.override(system_message=new_system_message)
        
        return handler(modified_request)