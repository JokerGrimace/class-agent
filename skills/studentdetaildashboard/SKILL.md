---
name: studentdetaildashboard
description: Use when current_workflow_page is student-detail and the user asks for student detail analysis, class score overview, numeric dashboards, score composition, attendance, homework, behavior, rankings, charts, or comparable course metrics.
---

# Student Detail Page Analytics

## Overview

Use this skill for analytics work on the student-detail page. Keep the visual style stable, choose truthful content from the available data, and avoid fixed one-chart or fixed-section behavior when the user request supports a better focused view.

## Page Scope

- Use this skill only when `runtime_context.current_workflow_page` is `student-detail`.
- Use `runtime_context.page_stage` to decide the default scope.
- `page_stage = overview`: a request for 数字面板, 图表, 成绩概览, or 班级概览 means the current class/course scope.
- `page_stage = detail`: a request for 数字面板, 图表, 学生详情, or 该学生 means the selected student scope.
- If `page_stage = detail` and the user explicitly says 班级, 课程, 全班, 所有学生, or 成绩概览, use the class/course scope.
- If a selected-student task needs `runtime_context.student_code` and it is missing, ask the user to select a student. Do not silently generate a class fallback.

## Required Context

- Use `runtime_context.course_code` as the current course identifier.
- Use `runtime_context.teacher_code` when a page tool requires teacher scope.
- Use `runtime_context.course_name` only when present; omit the class-name metric when it is missing.
- Use `runtime_context.student_code` as the selected student identifier.
- Use `runtime_context.student_name` only as a display label; do not use it as a database key.
- When showing student rows, resolve the roster and map exact `studentCode` to exact `studentName`. Do not display `studentCode` as the student name.

## Data Selection

- Use page-scoped tools for already-supported page data such as selected students, student scores, and student behavior summaries.
- Use SQL only when the requested aggregate, filter, join, or persisted detail is not covered by simpler page-scoped data.
- Before generating SQL, read `databaseoperation` and follow its SQL boundary and catalog rules.
- For whole-class score rows, rankings, and score distributions, use persisted current-course score data.
- For selected-student score composition, combine the selected student's behavior summary with the persisted score row when both are available.
- For recent concrete student events, first get available daily summary dates, then load daily action rows for the relevant dates.
- If the user gives a specific date, load concrete daily action rows for that date.

## Dashboard Style

The style is fixed; the content is flexible.

- Use the existing `dashboard_payload` contract as the primary output for 数字面板, 看板, 概览, or summary requests.
- Keep Chinese labels, compact section cards, summary cards, status colors, and risk tables consistent with the page renderer.
- Build the dashboard around the user's actual question, the page stage, and the successful data results.
- Select only sections supported by queried data and useful for the current analysis.
- Do not invent a metric to fill a section.
- Do not repeat the same number in multiple places.
- Do not make a dashboard look complete by adding low-value filler metrics.
- Render the dashboard first, then any chart payloads.
- Reply with one concise Chinese completion sentence after payloads are ready.

## Dashboard Composition

Start from intent, not from a fixed template:

- Overview intent: show current state, headline risk, and the most useful next comparison.
- Ranking intent: emphasize top/bottom rows, rank-related summary cards, and a ranking chart when data supports it.
- Distribution intent: emphasize bands, counts, rates, and distribution charts.
- Trend intent: emphasize dates, changes, and a line chart when dated rows exist.
- Diagnosis intent: emphasize abnormal metrics, cause-related sections, and a compact detail table.
- Selected-student intent: compare the selected student with class context only when the user asks for comparison or the data makes the contrast necessary.

Use this shape unless the user asks for something narrower:

- `summary_cards`: 2 to 4 headline metrics that answer the user's question directly.
- `sections`: 2 to 5 grouped metric blocks, each with related metrics.
- `tables`: only for useful detail rows such as risk students, abnormal records, ranking rows, or recent events.
- `data_sources`: actual table names or page tools used.

## Metric Examples

Metric examples are prompts, not required fields. Do not copy every example metric into every dashboard.

Class/course examples:

- 班级概览: 课程编号, 班级名称, 学生人数.
- 总评分布: 平均分, 最高分, 最低分, 及格人数, 及格率.
- 成绩构成: 签到平均贡献分, 作业平均贡献分, 奖励平均贡献分.
- 出勤概况: 签到活动数, 已签到次数, 迟到次数, 请假次数, 未签到次数.
- 作业概况: 作业总数, 提交总次数, 平均提交率, 平均作业分.
- 课堂互动: 奖励次数, 奖励总分, 抢答次数, 点名次数.
- 风险学生: 低分学生, 未提交作业较多学生, 出勤异常学生.

Selected-student examples:

- 学生概览: 学生姓名, 学生编号, 当前课程, 当前班级.
- 总评概览: 总分, 排名, 是否及格, 计算时间.
- 成绩构成: 签到原始分, 签到贡献分, 作业原始分, 作业贡献分, 奖励原始分, 奖励贡献分.
- 出勤表现: 已签到次数, 迟到次数, 请假次数, 未签到次数, 出勤率.
- 作业表现: 作业总数, 已提交数, 未提交数, 作业平均分.
- 课堂互动: 奖励次数, 奖励总分, 抢答次数, 点名次数.

## Chart Rules

- Choose chart content from the user's question and successful data results.
- When returned data contains two or more comparable students, dates, score bands, attendance states, or metric components, render at least one chart.
- Do not force a chart from scalar-only data.
- Use ranking charts for student comparisons, distribution charts for score bands or attendance states, trend charts for dated series, and composition charts for component values.
- Never send an empty chart payload.
- If chart payload validation fails, read the validation error, rebuild the chart payload from the latest successful data result, and retry chart rendering once.
- Do not retry with the same invalid payload. Change only the fields needed to satisfy the chart contract.
- Stop after one retry only when the latest successful data is empty or cannot form a valid chart, and explain that limitation in Chinese.
- Keep `table.columns` as data keys and set `table.column_labels` with Chinese display labels.
- Use `studentName` as the chart label after roster mapping. If no name is available, explain that the roster mapping is missing instead of using the code as a name.

## Attendance Rules

- `sign_content.sign_status`: `0` = 未签到, `1` = 已签到, `2` = 迟到, `3` = 请假.
- Show all returned attendance statuses.
- Do not merge 请假 into 未签到.
- Do not label 请假 as absence.

## Risk Table Rules

- Use one compact table titled `风险学生` when risk rows exist.
- Use columns: `学生姓名`, `学生编号`, `问题指标`, `当前值`.
- Put low score, missing homework, and attendance anomalies into the same risk table.
- Use one row per student and problem indicator.

## Output Contracts

Dashboard payload:

- `type`: `dashboard_panel`
- `title`: Chinese dashboard title
- `subtitle`: current course or selected student scope
- `summary_cards`: highest-signal scalar metrics
- `sections`: related metric groups
- `tables`: compact detail lists only when useful rows exist
- `data_sources`: actual table names or page data sources used

Chart payload:

- `type`: `sql_chart`
- `chart.library`: `echarts`
- `chart.chart_type`: `bar`, `line`, `pie`, or `scatter`
- `chart.title`: Chinese chart title
- `chart.dimension_fields`: data keys for dimensions
- `chart.metric_fields`: data keys for numeric metrics
- `chart.dataset.columns`: every key used by chart rows
- `chart.dataset.rows`: non-empty chart rows
- `table.columns`: data keys
- `table.column_labels`: Chinese labels for every visible column
- `table.rows`: non-empty table rows

## Common Mistakes

| Mistake | Fix |
| --- | --- |
| Building a student dashboard without a selected student | Ask the user to select a student. |
| Showing `student_code` as a student name | Resolve roster names first. |
| Returning Markdown tables instead of the dashboard payload | Use the page dashboard payload as the primary output. |
| Always generating the same chart | Pick chart content from the actual question and comparable data. |
| Hiding 未签到 or merging 请假 with 未签到 | Show statuses separately using the exact mapping above. |
