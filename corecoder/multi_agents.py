"""多 agent 协作。

在此处定义功能不同的专业子 agent，
将功能各异的子 agent 统一封装为工具列表交给主 agent 调度。

使用方式（在 cli.py 中）：
    from .multi_agents import SPECIALIZED_AGENTS
    agent = Agent(llm=llm, tools=ALL_TOOLS + SPECIALIZED_AGENTS, ...)
"""

from .tools.agent import SpecializedAgentTool
from .tools.bash import BashTool
from .tools.read import ReadFileTool
from .tools.write import WriteFileTool
from .tools.edit import EditFileTool
from .tools.glob_tool import GlobTool
from .tools.grep import GrepTool

# 规划agent
# 职责：分析需求，生成结构化执行计划
# 模型：轻量模型（deepseek-chat）
# 工具：只读，不能写文件
plan_agent = SpecializedAgentTool(
    name="plan",
    description="分析需求并制定执行计划。调用此工具来分解复杂任务为可执行的步骤。",
    system_prompt=(
        "你是任务规划专家。你的职责是：\n"
        "1. 分析用户需求，理解要达成的目标\n"
        "2. 将复杂任务拆解为可执行的步骤\n"
        "3. 明确每一步需要的工具和预期结果\n"
        "4. 评估依赖关系，确定执行顺序\n\n"
        "输出格式：返回一个编号的执行计划，每步包含「操作内容」和「预期结果」。"
        "不要执行任何代码或修改任何文件。"
    ),
    tools=[GlobTool(), GrepTool(), ReadFileTool()],
    model="deepseek-chat",
)

# 执行agent
# 职责：按计划编写代码，执行命令
# 模型：强模型（deepseek-reasoner），适合复杂编码
# 工具：完整工具链
execute_agent = SpecializedAgentTool(
    name="execute",
    description="按计划执行具体的编码任务。调用此工具来完成文件创建、代码修改、命令执行等操作。",
    system_prompt=(
        "你是代码执行专家。你的职责是：\n"
        "1. 按计划步骤执行具体的编码任务\n"
        "2. 写代码、改文件、运行命令\n"
        "3. 遇到问题时尝试修复\n"
        "4. 每步完成后汇报执行结果\n\n"
        "规则：\n"
        "- 先读取相关文件再修改\n"
        "- 一次只做一个步骤\n"
        "- 完成后返回执行摘要"
    ),
    tools=[BashTool(), ReadFileTool(), WriteFileTool(), EditFileTool(), GlobTool(), GrepTool()],
    model="deepseek-reasoner",
)

# 审查agent
# 职责：检查代码质量，发现潜在问题
# 模型：轻量模型（deepseek-chat）
# 工具：只读，不能写文件
review_agent = SpecializedAgentTool(
    name="review",
    description="审查代码质量和潜在问题。调用此工具来检查已完成的代码。",
    system_prompt=(
        "你是代码审查专家。你的职责是：\n"
        "1. 检查代码的正确性和完整性\n"
        "2. 发现潜在的 Bug、性能问题和安全隐患\n"
        "3. 检查代码风格和可读性\n"
        "4. 给出改进建议\n\n"
        "输出格式：列出发现的问题（如有），按严重程度排序。"
        "不要修改任何文件。"
    ),
    tools=[GlobTool(), GrepTool(), ReadFileTool()],
    model="deepseek-chat",
)

# 导出列表，供 cli.py 合并到主 agent 工具集
SPECIALIZED_AGENTS = [plan_agent, execute_agent, review_agent]
