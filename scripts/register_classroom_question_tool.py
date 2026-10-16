"""部署课堂草稿工具到页面目录，不修改已有工具的治理权限。"""

from pathlib import Path
import sys

# 独立部署脚本从仓库根目录读取应用配置。
project_root = str(Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.core.tool.python_tool_catalog_db import PythonToolCatalogService
from app.core.agent_page_prompt.catalog_db import AgentPagePromptCatalogService


def register_classroom_question_prompt(service: AgentPagePromptCatalogService) -> dict:
    # 页面规则写入数据库，由现有系统自动注入；保留现有正文并避免重复追加相同规则。
    page = "classroom-session"
    section = (Path(project_root) / "docs/prompts/classroom-question-drafts.md").read_text(encoding="utf-8").strip()
    current = service.get_active_prompt(page)
    if current and section in current["content"]:
        return current
    content = (current["content"].rstrip() + "\n\n" if current else "") + section + "\n"
    return service.save_prompt(web_page_name=page, content=content,
                               version="classroom-question-current-page-refresh-20260903", uploader="Codex", active=True)


def register_classroom_question_tool(service: PythonToolCatalogService) -> dict:
    # 生成仅传递内存草稿，不触发写接口或审批；创建和发布由老师操作前端。
    return service.save_tool(
        tool_name="prepare_classroom_questions", tool_display_name="生成课堂问题草稿",
        module_path="app.tools.classroom_questions", page_name="classroom-session",
        requires_page_match=True, permission_decision="allow",
        permission_summary="生成题目并填入现场发布表单", visible_fields_json=[], editable_fields_json=[],
    )


if __name__ == "__main__":
    # 仅打印工具标识，不输出数据库连接信息。
    row = register_classroom_question_tool(PythonToolCatalogService())
    print(f"Registered {row['tool_name']} on {row['page_name']}")
    # 工具登记后启用页面规则，随后回读核对版本与正文，不输出连接配置。
    service = AgentPagePromptCatalogService()
    saved = register_classroom_question_prompt(service)
    active = service.get_active_prompt("classroom-session")
    if not active or active["content"] != saved["content"] or active["version"] != saved["version"]:
        raise RuntimeError("课堂页面提示词回读校验失败")
    print(f"Verified page prompt {active['web_page_name']} version {active['version']}")
