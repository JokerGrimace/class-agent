---
name: databaseoperation
description: Use when a current-course request needs read-only SQL, persisted statistics, comparisons, dashboard data, chart data, or analysis from registered query catalog tables.
---

# Current-Course Database Query

## Overview

Use this skill to decide whether and how to use `readonly_course_sql_query` safely. The catalog is the source of truth for queryable tables, columns, relationships, and lineage. This skill defines query behavior; it is not a fixed schema handbook.

## Catalog Source Of Truth

- The SQL catalog lives in MySQL tables: `agent_sql_query_table_catalog`, `agent_sql_query_column_catalog`, `agent_sql_query_relationship_catalog`, and `agent_sql_query_lineage_catalog`.
- `readonly_course_sql_query` loads active catalog entries at execution time and rejects unregistered tables or columns.
- Do not copy a generated table or column snapshot into this skill.
- Do not infer table names, column names, join keys, status meanings, or JSON paths.
- If the exact table or field is not available through the catalog-backed SQL tool, say that the current catalog does not expose that data.

## SQL Boundary

- Generate one MySQL SELECT statement per SQL call.
- Do not generate `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `CALL`, `SHOW`, `DESCRIBE`, CTEs, `UNION`, or multi-statement SQL.
- Execute SQL only through `readonly_course_sql_query`; do not access the database directly.
- Query only tables and columns accepted by the catalog-backed SQL tool.
- Do not query `sqlite_master`, `information_schema`, or other schema-discovery tables.
- Do not use `SELECT *`; explicitly select only the fields needed by the answer or payload.
- Do not use subqueries in `WHERE`, derived tables, nested `SELECT`, or multi-layer query shapes.

## Course Scope

- Use `runtime_context.course_code` as the current course scope.
- Do not manually add a `course_code` filter. The SQL tool injects the course filter from each registered table's `course_scope_column`.
- Do not use `course_id` as a substitute for `course_code`.
- If the tool reports that a table has no course scope field, choose another exposed data source or explain that the table is not usable for current-course SQL.

## Query Reliability

- Prefer small single-table queries or straightforward joins through registered relationships.
- Join tables only through relationships registered in `agent_sql_query_relationship_catalog`; do not invent join keys.
- Use multiple simple SQL calls for unrelated metrics instead of one large query.
- In joins, qualify every real column with a table alias.
- Do not use a `SELECT` alias in `WHERE` or `GROUP BY`.
- Follow MySQL `ONLY_FULL_GROUP_BY`: every non-aggregated selected field must appear in `GROUP BY`.
- Add `LIMIT` to detail-row queries. Aggregate-only queries do not need `LIMIT`.
- For row-detail queries, keep `LIMIT` reasonable; avoid `LIMIT` values greater than 500 unless the user explicitly needs more and the result stays manageable.
- All fields used in `ORDER BY` must be registered columns in the catalog.
- Avoid heavy usage of `DISTINCT` on large datasets; prefer filtering or grouping before deduplication.
- Do not misuse `HAVING` to replace `WHERE`; `HAVING` can only reference aggregated fields.
- A top-level `ORDER BY` may use a top-level alias. If that fails, remove `ORDER BY` and sort the returned rows in the response or payload.
- If a SQL call fails because a table or field is not registered in the catalog, do not retry. Tell the user that the current catalog does not expose that data.
- If a SQL call fails because of runtime complexity, timeout, or syntax shape, read the error, simplify that SQL shape once, and retry once with changed SQL.
- Do not repeat the same invalid SQL pattern.
- Do not concatenate raw user text into `ORDER BY`, `GROUP BY`, or filter expressions. Map user intent to explicit catalog fields.
- Do not invent status codes, enum text values, or filter constants. Use only the business rules defined in this skill and the catalog.

## Business Rules

- For final score rankings and score distributions, use persisted score data when the catalog exposes it.
- For homework lists, use persisted homework data when the catalog exposes it.
- For homework submissions or attachments, use persisted homework attachment data when the catalog exposes it.
- For attendance status distribution, group by the exposed attendance status field when the catalog exposes it.
- Keep attendance states separate: `0` = `未签到`, `1` = `已签到`, `2` = `迟到`, `3` = `请假`.
- Do not merge `请假` into `未签到`, and do not label `请假` as absence.
- Student codes are identifiers. When showing student rows, use page roster data to map codes to display names when roster data is available.
- If roster data is unavailable, display the student code and state in Chinese that the student name is not available from the current context.

## Dashboard And Chart Payloads

- Build dashboard or chart payloads only from successful, non-empty query results or trusted page tools.
- Use `render_dashboard_panel` when the user asks for a numeric dashboard, dashboard panel, overview, or metric summary.
- Use `render_query_result_chart` when the user asks for a chart, trend, distribution, ranking, comparison, or when returned data has meaningful comparable categories.
- Do not call `render_query_result_chart` or `render_dashboard_panel` from a failed SQL result.
- Keep `table.columns` as data keys and set `table.column_labels` with Chinese display labels.
- Never show technical field names as visible chart-table headers.
- If chart payload validation fails, read the validation error, rebuild the payload from the latest successful data result, and retry chart rendering once.
- Keep charts readable: use bar or line charts for many categories, avoid pie charts for large category counts, and summarize or rank rows before charting when the result is too wide.

## Common Mistakes

| Mistake | Fix |
| --- | --- |
| Guessing a table or column name | Use only what the catalog-backed SQL tool accepts. |
| Using `SELECT *` | Select only the specific catalog fields needed. |
| Joining on an unregistered key | Join only through `agent_sql_query_relationship_catalog`. |
| Adding the current-course filter manually | Let `readonly_course_sql_query` inject the course scope. |
| Combining unrelated statistics into one complex SQL | Use several simple SQL calls. |
| Reusing the same failed SQL | Change the SQL shape based on the tool error. |
| Treating a student code as a student name | Map through roster data when it is available. |
| Counting `请假` as `未签到` | Keep all attendance statuses separate. |
