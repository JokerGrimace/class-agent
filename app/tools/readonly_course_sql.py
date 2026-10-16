from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import json
from typing import Any, Optional

from app.core.tool.readonly_sql import (
    SqlColumnCatalog,
    mask_sensitive_row,
    prepare_readonly_select,
)
from app.core.tool.registry import tool
from app.core.tool.sql_query_catalog_db import SqlQueryCatalogService
from app.core.tool.types import ToolResult


READONLY_SQL_TOOL_NAME = "readonly_course_sql_query"
RENDER_SQL_CHART_TOOL_NAME = "render_query_result_chart"
RENDER_DASHBOARD_PANEL_TOOL_NAME = "render_dashboard_panel"
_sql_catalog_service_instance: Optional[SqlQueryCatalogService] = None


SQL_CHART_PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "完整图表展示 JSON。必须使用 snake_case 字段名：type、chart、table、"
        "chart_type、dimension_fields、metric_fields。"
    ),
    "properties": {
        "type": {
            "type": "string",
            "const": "sql_chart",
            "description": "固定为 sql_chart。",
        },
        "chart": {
            "type": "object",
            "description": "ECharts 图表配置和数据。",
            "properties": {
                "library": {
                    "type": "string",
                    "const": "echarts",
                    "description": "固定为 echarts。",
                },
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "line", "pie", "scatter"],
                    "description": "图表类型，只允许 bar、line、pie、scatter。",
                },
                "title": {
                    "type": "string",
                    "description": "中文图表标题。",
                },
                "dimension_fields": {
                    "type": "array",
                    "description": "维度字段名数组，字段名必须存在于 chart.dataset.columns。",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "metric_fields": {
                    "type": "array",
                    "description": "指标字段名数组，字段名必须存在于 chart.dataset.columns。",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "dataset": {
                    "type": "object",
                    "description": "图表数据集。",
                    "properties": {
                        "columns": {
                            "type": "array",
                            "description": "字段名字符串数组。",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                        "rows": {
                            "type": "array",
                            "description": "数据行对象数组，每行的 key 必须来自 columns。",
                            "items": {
                                "type": "object",
                                "additionalProperties": True,
                            },
                        },
                    },
                    "required": ["columns", "rows"],
                    "additionalProperties": False,
                },
            },
            "required": [
                "library",
                "chart_type",
                "title",
                "dimension_fields",
                "metric_fields",
                "dataset",
            ],
            "additionalProperties": False,
        },
        "table": {
            "type": "object",
            "description": "图表下方表格预览。",
            "properties": {
                "columns": {
                    "type": "array",
                    "description": "表格字段名字符串数组。",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "column_labels": {
                    "type": "object",
                    "description": "表头显示名映射，key 必须来自 columns，value 使用中文显示名。",
                    "additionalProperties": {"type": "string"},
                },
                "rows": {
                    "type": "array",
                    "description": "表格数据行对象数组，每行的 key 必须来自 columns。",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            },
            "required": ["columns", "rows"],
            "additionalProperties": False,
        },
    },
    "required": ["type", "chart", "table"],
    "additionalProperties": False,
}


DASHBOARD_PANEL_PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "完整数字面板展示 JSON。必须使用 snake_case 字段名：type、title、subtitle、"
        "summary_cards、sections、tables、data_sources。"
    ),
    "properties": {
        "type": {
            "type": "string",
            "const": "dashboard_panel",
            "description": "固定为 dashboard_panel。",
        },
        "title": {
            "type": "string",
            "description": "中文数字面板标题。",
        },
        "subtitle": {
            "type": "string",
            "description": "中文副标题，可用于显示当前课程、班级或统计范围。",
        },
        "summary_cards": {
            "type": "array",
            "description": "顶部核心指标卡片，建议 3 到 5 个。",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "指标名称。"},
                    "value": {"description": "指标值，可为字符串或数字。"},
                    "unit": {"type": "string", "description": "指标单位。"},
                    "status": {
                        "type": "string",
                        "enum": ["neutral", "good", "warning", "danger"],
                        "description": "指标状态，用于前端弱提示。",
                    },
                    "note": {"type": "string", "description": "短说明。"},
                },
                "required": ["label", "value"],
                "additionalProperties": False,
            },
        },
        "sections": {
            "type": "array",
            "description": "分组指标区。",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "分组标题。"},
                    "metrics": {
                        "type": "array",
                        "description": "分组内指标。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "指标名称。"},
                                "value": {"description": "指标值，可为字符串或数字。"},
                                "unit": {"type": "string", "description": "指标单位。"},
                                "status": {
                                    "type": "string",
                                    "enum": ["neutral", "good", "warning", "danger"],
                                    "description": "指标状态，用于前端弱提示。",
                                },
                                "note": {"type": "string", "description": "短说明。"},
                            },
                            "required": ["label", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "metrics"],
                "additionalProperties": False,
            },
        },
        "tables": {
            "type": "array",
            "description": "明细表数组。",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "表格标题。"},
                    "columns": {
                        "type": "array",
                        "description": "字段名字符串数组。",
                        "items": {"type": "string"},
                    },
                    "rows": {
                        "type": "array",
                        "description": "数据行对象数组，每行的 key 必须来自 columns。",
                        "items": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                },
                "required": ["title", "columns", "rows"],
                "additionalProperties": False,
            },
        },
        "data_sources": {
            "type": "array",
            "description": "数据来源表或工具名称。",
            "items": {"type": "string"},
        },
    },
    "required": ["type", "title", "summary_cards", "sections"],
    "additionalProperties": False,
}


def _sql_catalog_service() -> SqlQueryCatalogService:
    global _sql_catalog_service_instance
    if _sql_catalog_service_instance is None:
        _sql_catalog_service_instance = SqlQueryCatalogService()
    return _sql_catalog_service_instance


def _extract_course_code(run_state: Any) -> Optional[str]:
    runtime_context = getattr(run_state, "runtime_context", None)
    if not isinstance(runtime_context, dict):
        return None
    value = runtime_context.get("course_code")
    if not isinstance(value, str):
        return None
    normalized_value = value.strip()
    return normalized_value or None


def _extract_max_rows(run_state: Any, default: int = 500) -> int:
    page_permission = getattr(run_state, "current_tool_page_permission", None)
    if isinstance(page_permission, dict):
        value = page_permission.get("max_rows")
        if isinstance(value, int) and value > 0:
            return value
    return default


@tool(
    name=READONLY_SQL_TOOL_NAME,
    description="执行一条当前课程范围内的只读 SELECT SQL。",
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "用户关于当前课程学生信息记录的查询问题。",
            },
            "sql": {
                "type": "string",
                "description": "模型生成的只读 SQL。",
            },
        },
        "required": ["question", "sql"],
        "additionalProperties": False,
    },
    visibility={"tool_types": ["level1"]},
)
async def readonly_course_sql_query(
    question: str,
    sql: str,
    __run_state: Any = None,
) -> ToolResult:
    course_code = _extract_course_code(__run_state)
    if course_code is None:
        return ToolResult(success=False, error="readonly_course_sql_query 需要 runtime_context.course_code")

    catalog = None
    try:
        service = _sql_catalog_service()
        catalog = service.load_snapshot()
        prepared = prepare_readonly_select(sql, catalog=catalog, course_code=course_code)
        rows, limited = service.execute_select(prepared.sql, _extract_max_rows(__run_state))
    except Exception as exc:
        return ToolResult(success=False, error=_format_readonly_sql_error(str(exc), catalog))

    columns = [
        column
        for key, column in catalog.columns.items()
        if key[0] in prepared.tables
    ]
    masked_rows = [_to_jsonable_row(mask_sensitive_row(row, columns)) for row in rows]
    column_payload = _build_column_payload(prepared, columns)
    content = {
        "type": "sql_query_result",
        "columns": column_payload,
        "rows": masked_rows,
        "row_count": len(masked_rows),
        "limited": limited,
    }
    return ToolResult(
        success=True,
        content=json.dumps(content, ensure_ascii=False),
        meta={
            "query_audit": {
                "question": question,
                "tables": prepared.tables,
                "columns": prepared.columns,
                "limited": limited,
            }
        },
    )


def _build_column_payload(prepared, columns: list[SqlColumnCatalog]) -> list[dict[str, Any]]:
    by_name = {column.column_name: column for column in columns}
    payload = []
    for column_name in prepared.columns:
        column = by_name.get(column_name)
        payload.append(
            {
                "name": column_name,
                "business_name": column.business_name if column else column_name,
                "data_type": column.data_category if column else "未知",
            }
        )
    return payload


def _to_jsonable_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _to_jsonable_value(value) for key, value in row.items()}


def _to_jsonable_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _format_readonly_sql_error(error: str, catalog: Any) -> str:
    base_error = (error or "SQL 查询失败").strip()
    if catalog is None:
        return base_error
    return f"{base_error}\n\n{_build_catalog_retry_guidance(catalog)}"


def _build_catalog_retry_guidance(catalog: Any) -> str:
    table_names = sorted(catalog.tables)
    column_lines: list[str] = []
    for table_name in table_names:
        columns = sorted(
            column_name
            for column_table_name, column_name in catalog.columns
            if column_table_name == table_name
        )
        if columns:
            column_lines.append(f"- {table_name}({', '.join(columns)})")
        else:
            column_lines.append(f"- {table_name}")

    return "\n".join(
        [
            "请按以下规则重写 SQL：",
            "- 只能查询下方 catalog 中列出的表和字段，不要猜测 teachers、teacher_course、course、sqlite_master、information_schema 等未登记表。",
            "- 当前课程范围由工具根据 runtime_context.course_code 自动注入，不需要先查询 teacher_code。",
            "- 只生成一条 MySQL SELECT。",
            "当前可查询表和字段：",
            *column_lines,
        ]
    )


@tool(
    name=RENDER_SQL_CHART_TOOL_NAME,
    description="校验 SQL 查询结果图表数据，并封装为 pc-web 可渲染的 ECharts payload。",
    parameters={
        "type": "object",
        "properties": {
            "chart_payload": SQL_CHART_PAYLOAD_SCHEMA,
        },
        "required": ["chart_payload"],
        "additionalProperties": False,
    },
    visibility={"tool_types": ["level1"]},
)
async def render_query_result_chart(chart_payload: dict[str, Any]) -> ToolResult:
    """Validate and forward a complete chart payload; SQL querying happens first."""
    error = _validate_chart_payload(chart_payload)
    if error:
        return ToolResult(success=False, error=error)
    return ToolResult(
        success=True,
        content="图表已生成。",
        meta={
            "assistant_payload": {
                "action_type": RENDER_SQL_CHART_TOOL_NAME,
                "result_payload": chart_payload,
            }
        },
    )


@tool(
    name=RENDER_DASHBOARD_PANEL_TOOL_NAME,
    description="校验数字面板数据，并封装为 pc-web 可渲染的 dashboard_panel payload。",
    parameters={
        "type": "object",
        "properties": {
            "dashboard_payload": DASHBOARD_PANEL_PAYLOAD_SCHEMA,
        },
        "required": ["dashboard_payload"],
        "additionalProperties": False,
    },
    visibility={"tool_types": ["level1"]},
)
async def render_dashboard_panel(dashboard_payload: dict[str, Any]) -> ToolResult:
    error = _validate_dashboard_payload(dashboard_payload)
    if error:
        return ToolResult(success=False, error=error)
    return ToolResult(
        success=True,
        content="数字面板已生成。",
        meta={
            "assistant_payload": {
                "action_type": RENDER_DASHBOARD_PANEL_TOOL_NAME,
                "result_payload": dashboard_payload,
            }
        },
    )


def _validate_chart_payload(chart_payload: Any) -> str:
    errors: list[str] = []
    if not isinstance(chart_payload, dict):
        return "chart_payload 必须是对象"
    if chart_payload.get("type") != "sql_chart":
        errors.append("chart_payload.type 必须是 sql_chart")
    chart = chart_payload.get("chart")
    table = chart_payload.get("table")
    if not isinstance(chart, dict):
        errors.append("chart_payload.chart 必须是对象")
        chart = {}
    if not isinstance(table, dict):
        errors.append("chart_payload.table 必须是对象")
        table = {}
    if chart.get("library") != "echarts":
        errors.append("chart.library 必须是 echarts")
    if chart.get("chart_type") not in {"bar", "line", "pie", "scatter"}:
        errors.append("chart.chart_type 必须是 bar、line、pie、scatter 之一")
    if not isinstance(chart.get("title"), str):
        errors.append("chart.title 必须是字符串")
    dimension_fields = chart.get("dimension_fields")
    metric_fields = chart.get("metric_fields")
    if not _is_string_list(dimension_fields):
        errors.append("chart.dimension_fields 必须是字符串数组")
        dimension_fields = []
    if not _is_string_list(metric_fields):
        errors.append("chart.metric_fields 必须是字符串数组")
        metric_fields = []
    dataset = chart.get("dataset")
    if not isinstance(dataset, dict):
        errors.append("chart.dataset 必须是对象")
        dataset = {}
    dataset_columns = dataset.get("columns")
    dataset_rows = dataset.get("rows")
    if not _is_string_list(dataset_columns):
        errors.append("chart.dataset.columns 必须是字符串数组")
        dataset_columns = []
    if not _is_record_list(dataset_rows):
        errors.append("chart.dataset.rows 必须是对象数组")
        dataset_rows = []
    for field_name in dimension_fields:
        if field_name not in dataset_columns:
            errors.append("chart.dimension_fields 中的字段必须存在于 chart.dataset.columns")
            break
    for field_name in metric_fields:
        if field_name not in dataset_columns:
            errors.append("chart.metric_fields 中的字段必须存在于 chart.dataset.columns")
            break
    for row in dataset_rows:
        invalid_keys = sorted(set(row) - set(dataset_columns))
        if invalid_keys:
            errors.append(f"chart.dataset.rows 包含未知字段：{invalid_keys}")
            break
    if not _is_string_list(table.get("columns")):
        errors.append("table.columns 必须是字符串数组")
        table_columns = []
    else:
        table_columns = table["columns"]
    column_labels = table.get("column_labels")
    if column_labels is not None:
        if not isinstance(column_labels, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in column_labels.items()
        ):
            errors.append("table.column_labels 必须是字符串映射")
        elif any(key not in table_columns for key in column_labels):
            errors.append("table.column_labels 的 key 必须存在于 table.columns")
    if not _is_record_list(table.get("rows")):
        errors.append("table.rows 必须是对象数组")
        table_rows = []
    else:
        table_rows = table["rows"]
    for row in table_rows:
        invalid_keys = sorted(set(row) - set(table_columns))
        if invalid_keys:
            errors.append(f"table.rows 包含未知字段：{invalid_keys}")
            break
    return "；".join(errors)


def _validate_dashboard_payload(dashboard_payload: Any) -> str:
    errors: list[str] = []
    if not isinstance(dashboard_payload, dict):
        return "dashboard_payload 必须是对象"
    if dashboard_payload.get("type") != "dashboard_panel":
        errors.append("dashboard_payload.type 必须是 dashboard_panel")
    if not isinstance(dashboard_payload.get("title"), str):
        errors.append("dashboard.title 必须是字符串")
    if "subtitle" in dashboard_payload and not isinstance(dashboard_payload.get("subtitle"), str):
        errors.append("dashboard.subtitle 必须是字符串")

    summary_cards = dashboard_payload.get("summary_cards")
    if not isinstance(summary_cards, list):
        errors.append("summary_cards 必须是数组")
        summary_cards = []
    for index, card in enumerate(summary_cards):
        _validate_dashboard_metric(card, f"summary_cards[{index}]", errors)

    sections = dashboard_payload.get("sections")
    if not isinstance(sections, list):
        errors.append("sections 必须是数组")
        sections = []
    for section_index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"sections[{section_index}] 必须是对象")
            continue
        if not isinstance(section.get("title"), str):
            errors.append(f"sections[{section_index}].title 必须是字符串")
        metrics = section.get("metrics")
        if not isinstance(metrics, list):
            errors.append(f"sections[{section_index}].metrics 必须是数组")
            continue
        for metric_index, metric in enumerate(metrics):
            _validate_dashboard_metric(metric, f"sections[{section_index}].metrics[{metric_index}]", errors)

    tables = dashboard_payload.get("tables", [])
    if not isinstance(tables, list):
        errors.append("tables 必须是数组")
        tables = []
    for table_index, table in enumerate(tables):
        if not isinstance(table, dict):
            errors.append(f"tables[{table_index}] 必须是对象")
            continue
        if not isinstance(table.get("title"), str):
            errors.append(f"tables[{table_index}].title 必须是字符串")
        columns = table.get("columns")
        rows = table.get("rows")
        if not _is_string_list(columns):
            errors.append(f"tables[{table_index}].columns 必须是字符串数组")
            columns = []
        if not _is_record_list(rows):
            errors.append(f"tables[{table_index}].rows 必须是对象数组")
            rows = []
        for row in rows:
            invalid_keys = sorted(set(row) - set(columns))
            if invalid_keys:
                errors.append(f"tables[{table_index}].rows 包含未知字段：{invalid_keys}")
                break

    data_sources = dashboard_payload.get("data_sources", [])
    if not _is_string_list(data_sources):
        errors.append("data_sources 必须是字符串数组")
    return "；".join(errors)


def _validate_dashboard_metric(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    if not isinstance(value.get("label"), str):
        errors.append(f"{path}.label 必须是字符串")
    if "value" not in value or value.get("value") is None:
        errors.append(f"{path}.value 不能为空")
    if "unit" in value and not isinstance(value.get("unit"), str):
        errors.append(f"{path}.unit 必须是字符串")
    if "note" in value and not isinstance(value.get("note"), str):
        errors.append(f"{path}.note 必须是字符串")
    if "status" in value and value.get("status") not in {"neutral", "good", "warning", "danger"}:
        errors.append(f"{path}.status 必须是 neutral、good、warning、danger 之一")


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)
