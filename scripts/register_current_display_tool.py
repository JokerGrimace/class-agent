"""登记预览和全屏共用的读取工具及页面规则，不修改系统提示词或执行器。"""

from pathlib import Path
import sys

# 独立脚本需要从仓库根目录加载当前应用配置，沿用现有目录维护脚本的入口方式。
project_root = str(Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.core.tool.python_tool_catalog_db import PythonToolCatalogService
from app.core.agent_page_prompt.catalog_db import AgentPagePromptCatalogService


def register_current_display_tool(service: PythonToolCatalogService, page_name: str = "classroom-session") -> dict:
    # 目录表控制可见范围；本地读取不新增网络等待，保留原有治理配置与超时上限。
    rows = [row for row in service.list_tool_rows_for_page(page_name)
            if row["tool_name"] == "read_current_display_content" and row["page_name"] == page_name]
    previous = rows[0] if rows else {}
    return service.save_tool(
        tool_name="read_current_display_content", tool_display_name="读取当前展示页",
        module_path="app.tools.current_display", page_name=page_name,
        requires_page_match=True, permission_decision=previous.get("permission_decision", "allow"),
        permission_summary="读取本条消息发送时的展示页快照",
        permission_deny_message=previous.get("permission_deny_message", ""),
        visible_fields_json=previous.get("visible_fields_json", []),
        editable_fields_json=previous.get("editable_fields_json", []),
        timeout_ms=previous.get("timeout_ms", 5000), max_rows=previous.get("max_rows", 500),
    )


def register_current_display_prompt(service: AgentPagePromptCatalogService, page_name: str) -> dict:
    # 保存现有页面规则并创建新版本；重复部署相同正文不增加版本。
    section = (Path(project_root) / "docs/prompts/current-display-content.md").read_text(encoding="utf-8").strip()
    current = service.get_active_prompt(page_name)
    if current and section in current["content"]:
        return current
    content = current["content"] if current else ""
    # 旧版规则是独立首章，用同名章节替换；后续备课/课堂业务章节原样保留。
    if content.startswith("## 当前预览页与全屏页读取\n"):
        _, separator, remaining = content.partition("\n\n## ")
        content = "## " + remaining if separator else ""
    saved = service.save_prompt(web_page_name=page_name, content=section + "\n\n" + content,
                               version="current-display-current-page-refresh-20260903", uploader="Codex", active=True)
    active = service.get_active_prompt(page_name)
    if not active or active["content"] != saved["content"] or active["version"] != saved["version"]:
        raise RuntimeError("当前页读取提示词回读校验失败")
    return saved


if __name__ == "__main__":
    # 只输出登记结果标识，不打印数据库连接信息或文件正文。
    tools = PythonToolCatalogService()
    prompts = AgentPagePromptCatalogService()
    for page in ("prep-workspace", "classroom-session"):
        row = register_current_display_tool(tools, page)
        if row["tool_name"] not in tools.list_tool_names_for_page(page):
            raise RuntimeError("当前页工具目录回读校验失败")
        prompt = register_current_display_prompt(prompts, page)
        print(f"Verified {row['tool_name']} on {page}, prompt {prompt['version']}")
