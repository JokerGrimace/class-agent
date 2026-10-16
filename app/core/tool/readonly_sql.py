from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class SqlTableCatalog:
    table_name: str
    business_name: str
    business_description: str
    course_scope_column: str
    is_active: bool = True


@dataclass(frozen=True)
class SqlColumnCatalog:
    table_name: str
    column_name: str
    business_name: str
    business_description: str
    data_category: str
    is_queryable: bool = True
    is_dimension: bool = False
    is_metric: bool = False
    is_sensitive: bool = False
    masking_rule: str = ""


@dataclass(frozen=True)
class SqlCatalogSnapshot:
    tables: dict[str, SqlTableCatalog]
    columns: dict[tuple[str, str], SqlColumnCatalog]
    relationships: list[dict[str, Any]]
    lineages: list[dict[str, Any]]


@dataclass(frozen=True)
class PreparedReadonlySelect:
    sql: str
    tables: list[str]
    columns: list[str]


def build_catalog_snapshot(
    *,
    tables: list[SqlTableCatalog],
    columns: list[SqlColumnCatalog],
    relationships: list[dict[str, Any]],
    lineages: list[dict[str, Any]],
) -> SqlCatalogSnapshot:
    active_tables = {
        table.table_name: table
        for table in tables
        if table.is_active
    }
    active_columns = {
        (column.table_name, column.column_name): column
        for column in columns
        if column.is_queryable and column.table_name in active_tables
    }
    return SqlCatalogSnapshot(
        tables=active_tables,
        columns=active_columns,
        relationships=list(relationships),
        lineages=list(lineages),
    )


def prepare_readonly_select(
    sql: str,
    *,
    catalog: SqlCatalogSnapshot,
    course_code: str,
) -> PreparedReadonlySelect:
    normalized_sql = (sql or "").strip()
    if not normalized_sql:
        raise ValueError("sql 不能为空")
    normalized_course_code = (course_code or "").strip()
    if not normalized_course_code:
        raise ValueError("course_code 不能为空")

    statements = sqlglot.parse(normalized_sql, read="mysql")
    if len(statements) != 1:
        raise ValueError("只允许执行单条 SQL 语句")

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise ValueError("只允许执行 SELECT SQL")
    if _has_nested_select(statement):
        raise ValueError("readonly_course_sql_query 暂不支持嵌套 SELECT，请拆成多条简单 SELECT 查询")

    table_aliases = _extract_table_aliases(statement)
    if not table_aliases:
        raise ValueError("SELECT 必须引用至少一张表")

    for table_name in table_aliases.values():
        if table_name not in catalog.tables:
            raise ValueError(f"表不允许查询：{table_name}")

    columns = _extract_columns(statement, table_aliases, catalog)
    scoped_statement = _inject_course_scope(statement, table_aliases, catalog, normalized_course_code)
    return PreparedReadonlySelect(
        sql=scoped_statement.sql(dialect="mysql"),
        tables=sorted(set(table_aliases.values())),
        columns=columns,
    )


def _extract_table_aliases(statement: exp.Select) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        table_name = str(table.name or "").strip()
        if not table_name:
            continue
        alias = str(table.alias_or_name or table_name).strip()
        aliases[alias] = table_name
        aliases[table_name] = table_name
    return aliases


def _extract_columns(
    statement: exp.Select,
    table_aliases: dict[str, str],
    catalog: SqlCatalogSnapshot,
) -> list[str]:
    table_names = sorted(set(table_aliases.values()))
    select_aliases = _extract_select_aliases(statement)
    resolved_columns: list[str] = []
    for column in statement.find_all(exp.Column):
        column_name = str(column.name or "").strip()
        if not column_name:
            continue
        qualifier = str(column.table or "").strip()
        if not qualifier and column_name in select_aliases and column.find_ancestor(exp.Order):
            continue
        if qualifier:
            table_name = table_aliases.get(qualifier)
            if not table_name:
                raise ValueError(f"未知表别名：{qualifier}")
            if (table_name, column_name) not in catalog.columns:
                raise ValueError(f"字段不允许查询：{table_name}.{column_name}")
        elif len(table_names) == 1:
            table_name = table_names[0]
            if (table_name, column_name) not in catalog.columns:
                raise ValueError(f"字段不允许查询：{table_name}.{column_name}")
        else:
            matching_tables = [
                table_name
                for table_name in table_names
                if (table_name, column_name) in catalog.columns
            ]
            if len(matching_tables) != 1:
                raise ValueError(f"无法唯一解析字段，请给字段添加表别名：{column_name}")
        if column_name not in resolved_columns:
            resolved_columns.append(column_name)
    return resolved_columns


def _has_nested_select(statement: exp.Select) -> bool:
    return any(nested is not statement for nested in statement.find_all(exp.Select))


def _extract_select_aliases(statement: exp.Select) -> set[str]:
    aliases: set[str] = set()
    for expression in statement.expressions:
        alias = str(getattr(expression, "alias", "") or "").strip()
        if alias:
            aliases.add(alias)
    return aliases


def _inject_course_scope(
    statement: exp.Select,
    table_aliases: dict[str, str],
    catalog: SqlCatalogSnapshot,
    course_code: str,
) -> exp.Select:
    scoped_statement = statement.copy()
    conditions: list[exp.Expression] = []
    seen_tables: set[str] = set()
    for alias, table_name in table_aliases.items():
        if table_name in seen_tables:
            continue
        seen_tables.add(table_name)
        table = catalog.tables[table_name]
        scope_column = table.course_scope_column.strip()
        if not scope_column:
            raise ValueError(f"表未配置课程范围字段：{table_name}")
        if (table_name, scope_column) not in catalog.columns:
            raise ValueError(f"课程范围字段不允许查询：{table_name}.{scope_column}")
        conditions.append(
            exp.EQ(
                this=exp.column(scope_column, table=alias),
                expression=exp.Literal.string(course_code),
            )
        )
    if not conditions:
        raise ValueError("无法生成课程范围过滤条件")
    condition = conditions[0]
    for extra_condition in conditions[1:]:
        condition = exp.and_(condition, extra_condition)
    return scoped_statement.where(condition)


def mask_sensitive_row(
    row: dict[str, Any],
    columns: list[SqlColumnCatalog],
) -> dict[str, Any]:
    sensitive_rules = {
        column.column_name: column.masking_rule
        for column in columns
        if column.is_sensitive
    }
    if not sensitive_rules:
        return dict(row)
    masked = dict(row)
    for column_name, masking_rule in sensitive_rules.items():
        if column_name not in masked:
            continue
        masked[column_name] = _mask_value(masked[column_name], masking_rule)
    return masked


def _mask_value(value: Any, masking_rule: str) -> Any:
    if value is None:
        return None
    text = str(value)
    if masking_rule == "name":
        return text[:1] + "*" if text else text
    if masking_rule == "phone" and len(text) >= 7:
        return text[:3] + "****" + text[-4:]
    return "***"
