from __future__ import annotations

from pathlib import Path
import sys
from urllib.parse import parse_qsl, urlparse

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_text = str(PROJECT_ROOT)
if project_root_text not in sys.path:
    sys.path.insert(0, project_root_text)

from app.config import settings


TABLE_META = {
    "course_assignment": ("上课安排", "记录课程的教师、教室、周次和节次安排，按课程编号过滤当前课程。"),
    "course_homework": ("课程作业", "记录教师发布的课程作业，按课程编号过滤当前课程。"),
    "course_homework_attachment": ("作业附件", "记录教师作业附件和学生提交附件，按课程编号过滤当前课程。"),
    "course_lesson": ("课堂课时", "记录课程课堂课时，按课程编号过滤当前课程。"),
    "course_public_resource": ("公共资源", "记录课程公共资源文件，按课程编号过滤当前课程。"),
    "course_public_resource_folder": ("公共资源文件夹", "记录课程公共资源文件夹，按课程编号过滤当前课程。"),
    "course_score_config": ("课程总评配置", "记录课程总评权重和奖励满分配置，按课程编号过滤当前课程。"),
    "course_student_score": ("学生总评成绩", "记录学生总评原始分、贡献分和总分，按课程编号过滤当前课程。"),
    "lesson_roll_call": ("课堂随机点名", "记录课堂随机点名学生明细，按课程编号过滤当前课程。"),
    "lesson_rush_answer": ("课堂抢答", "记录课堂抢答学生明细，按课程编号过滤当前课程。"),
    "reward": ("课堂奖励", "记录学生课堂奖励加分，按课程编号过滤当前课程。"),
    "sign": ("课堂签到", "记录教师发布的课堂签到活动，按课程编号过滤当前课程。"),
    "sign_content": ("学生签到明细", "记录学生签到状态明细，按课程编号过滤当前课程。"),
}


COLUMN_LABELS = {
    "actual_end_time": "实际结束时间",
    "actual_start_time": "实际开始时间",
    "assignment_id": "上课安排ID",
    "attendance_contribution": "签到贡献分",
    "attendance_raw_score": "签到原始分",
    "attendance_weight": "签到权重",
    "classroom_name": "上课地点",
    "course_code": "课程编号",
    "create_time": "创建时间",
    "calculate_time": "计算时间",
    "data_flag": "数据状态",
    "end_reason": "结束原因",
    "end_section": "结束节次",
    "end_time": "结束时间",
    "end_week": "结束周",
    "expire_time": "过期时间",
    "file_name": "文件名",
    "file_type": "文件类型",
    "folder_id": "文件夹ID",
    "folder_name": "文件夹名",
    "homework_contribution": "作业贡献分",
    "homework_id": "作业ID",
    "homework_raw_score": "作业原始分",
    "homework_weight": "作业权重",
    "id": "主键ID",
    "is_deleted": "删除标记",
    "leave_type": "请假类型",
    "lesson_code": "课时编号",
    "lesson_date": "课时日期",
    "lesson_id": "课时ID",
    "parent_id": "父文件夹ID",
    "publish_time": "发布时间",
    "question_id": "题目ID",
    "read_only": "只读标记",
    "reward_contribution": "奖励贡献分",
    "reward_full_score": "奖励满分",
    "reward_raw_score": "奖励原始分",
    "reward_reason": "奖励原因",
    "reward_score": "奖励分值",
    "reward_source": "奖励来源",
    "reward_time": "奖励时间",
    "reward_weight": "奖励权重",
    "roll_call_id": "点名活动ID",
    "roll_call_time": "点名时间",
    "rush_answer_id": "抢答活动ID",
    "rush_time": "抢答时间",
    "score": "得分",
    "sign_code": "签到活动ID",
    "sign_date": "签到日期",
    "sign_status": "签到状态",
    "start_section": "开始节次",
    "start_source": "开始来源",
    "start_time": "开始时间",
    "start_week": "开始周",
    "status": "状态",
    "student_code": "学生编号",
    "student_id": "学生ID",
    "teacher_code": "教师编号",
    "title": "标题",
    "total_score": "总分",
    "week_day": "星期",
    "week_type": "单双周类型",
    "attachment_role": "附件角色",
}


COLUMN_DESCRIPTIONS = {
    "course_code": "当前课程编号，用于课程范围过滤。",
    "teacher_code": "教师编号，用于区分授课或发布教师。",
    "student_code": "学生编号，用于区分学生。",
    "data_flag": "数据状态，通常 1 表示有效，0 表示无效或删除。",
    "is_deleted": "删除标记，0 表示未删除，1 表示已删除。",
    "course_homework_attachment.attachment_role": "附件角色，1 表示教师作业附件，2 表示学生提交附件。",
    "course_public_resource.folder_id": "所属公共资源文件夹ID，-1 表示根目录。",
    "course_public_resource_folder.parent_id": "父文件夹ID，-1 表示根目录。",
    "sign_content.sign_status": "签到状态，0 表示未签，1 表示正常，2 表示迟到，3 表示请假。",
    "sign_content.leave_type": "请假类型编码。",
    "course_assignment.week_type": "单双周类型，0 表示全周，1 表示单周，2 表示双周。",
}


RELATIONSHIP_DESCRIPTIONS = {
    ("course_lesson", "assignment_id", "course_assignment", "id"): "课时通过上课安排ID关联上课安排。",
    ("sign", "lesson_id", "course_lesson", "id"): "签到活动通过课时ID关联课堂课时。",
    ("sign_content", "sign_code", "sign", "id"): "签到明细通过签到活动ID关联签到活动。",
    ("sign_content", "lesson_id", "course_lesson", "id"): "签到明细通过课时ID关联课堂课时。",
    ("lesson_roll_call", "lesson_id", "course_lesson", "id"): "随机点名记录通过课时ID关联课堂课时。",
    ("lesson_rush_answer", "lesson_id", "course_lesson", "id"): "抢答记录通过课时ID关联课堂课时。",
    ("reward", "lesson_code", "course_lesson", "lesson_code"): "奖励记录通过课时编号关联课堂课时。",
    ("course_homework_attachment", "homework_id", "course_homework", "id"): "作业附件通过作业ID关联课程作业。",
    ("course_public_resource", "folder_id", "course_public_resource_folder", "id"): "公共资源通过文件夹ID关联公共资源文件夹。",
    ("course_student_score", "course_code", "course_score_config", "course_code"): "学生总评成绩通过课程编号关联课程总评配置。",
    ("reward", "student_code", "course_student_score", "student_code"): "奖励记录可通过学生编号关联学生总评成绩。",
    ("sign_content", "student_code", "course_student_score", "student_code"): "签到明细可通过学生编号关联学生总评成绩。",
    ("lesson_roll_call", "student_code", "course_student_score", "student_code"): "随机点名记录可通过学生编号关联学生总评成绩。",
    ("lesson_rush_answer", "student_code", "course_student_score", "student_code"): "抢答记录可通过学生编号关联学生总评成绩。",
}


LINEAGE_DESCRIPTIONS = {
    ("sign_content", "sign_status", "course_student_score", "attendance_raw_score"): "签到状态参与生成学生总评的签到原始分。",
    ("reward", "reward_score", "course_student_score", "reward_raw_score"): "奖励分值参与生成学生总评的奖励原始分。",
    ("course_score_config", "attendance_weight", "course_student_score", "attendance_contribution"): "签到权重参与生成学生总评的签到贡献分。",
    ("course_score_config", "homework_weight", "course_student_score", "homework_contribution"): "作业权重参与生成学生总评的作业贡献分。",
    ("course_score_config", "reward_weight", "course_student_score", "reward_contribution"): "奖励权重参与生成学生总评的奖励贡献分。",
    ("course_student_score", "total_score", "course_student_score", "total_score"): "总评成绩是当前课程学生综合表现的输出指标。",
}


def _connect():
    url = settings.mysql_readonly_url or settings.mysql_url
    username = settings.mysql_readonly_username or settings.mysql_username
    password = settings.mysql_readonly_password or settings.mysql_password
    parsed = urlparse(url.removeprefix("jdbc:"))
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    charset = query_params.get("characterEncoding", "utf8mb4").replace("-", "")
    if charset.lower() in {"utf8", "utf8mb3"}:
        charset = "utf8mb4"
    return pymysql.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 3306,
        user=username,
        password=password,
        database=parsed.path.lstrip("/"),
        charset=charset,
        autocommit=False,
    )


def _column_description(table_name: str, column_name: str) -> tuple[str, str]:
    label = COLUMN_LABELS.get(column_name, column_name)
    description = COLUMN_DESCRIPTIONS.get(
        f"{table_name}.{column_name}",
        COLUMN_DESCRIPTIONS.get(column_name, f"{label}。"),
    )
    return label, description


def main() -> None:
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            for table_name, (business_name, business_description) in TABLE_META.items():
                cursor.execute(
                    """
                    UPDATE agent_sql_query_table_catalog
                    SET business_name=%s,
                        business_description=%s,
                        course_scope_column='course_code',
                        is_active=1
                    WHERE table_name=%s
                    """,
                    (business_name, business_description, table_name),
                )

            cursor.execute("SELECT table_name, column_name FROM agent_sql_query_column_catalog")
            for table_name, column_name in cursor.fetchall():
                business_name, business_description = _column_description(table_name, column_name)
                cursor.execute(
                    """
                    UPDATE agent_sql_query_column_catalog
                    SET business_name=%s,
                        business_description=%s
                    WHERE table_name=%s AND column_name=%s
                    """,
                    (business_name, business_description, table_name, column_name),
                )

            for key, description in RELATIONSHIP_DESCRIPTIONS.items():
                cursor.execute(
                    """
                    UPDATE agent_sql_query_relationship_catalog
                    SET business_description=%s
                    WHERE source_table_name=%s
                      AND source_column_name=%s
                      AND target_table_name=%s
                      AND target_column_name=%s
                    """,
                    (description, *key),
                )

            for key, description in LINEAGE_DESCRIPTIONS.items():
                cursor.execute(
                    """
                    UPDATE agent_sql_query_lineage_catalog
                    SET transform_description=%s
                    WHERE source_table_name=%s
                      AND source_column_name=%s
                      AND target_table_name=%s
                      AND target_column_name=%s
                    """,
                    (description, *key),
                )
        conn.commit()
    finally:
        conn.close()

    print("Agent SQL catalog ?????????")


if __name__ == "__main__":
    main()
