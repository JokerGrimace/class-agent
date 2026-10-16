SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `openapi_tool_mapping_catalog` (
    `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
    `http_method` VARCHAR(16) NOT NULL COMMENT 'HTTP 方法',
    `api_path` VARCHAR(255) NOT NULL COMMENT 'OpenAPI 接口路径',
    `tool_name` VARCHAR(255) NOT NULL COMMENT 'Agent 工具名',
    `tool_display_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Agent 工具中文展示名',
    `page_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '页面名称',
    `requires_page_match` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否要求当前页面精确命中',
    `permission_decision` VARCHAR(16) NOT NULL DEFAULT 'allow' COMMENT '页面级权限决策',
    `permission_summary` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '权限提示文案',
    `permission_deny_message` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '拒绝提示文案',
    `support_user_revision` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否允许用户修改参数后执行',
    `visible_fields_json` JSON NOT NULL COMMENT '审批时可见字段列表',
    `editable_fields_json` JSON NOT NULL COMMENT '审批时可编辑字段列表',
    `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
    `current_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
    `update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT '更新时间'
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    UNIQUE KEY `uniq_openapi_tool_mapping_operation_scope` (
        `http_method`,
        `api_path`,
        `page_name`
    ),
    KEY `idx_openapi_tool_mapping_operation_active` (
        `http_method`,
        `api_path`,
        `is_active`
    ),
    KEY `idx_openapi_tool_mapping_tool_page_active` (
        `tool_name`,
        `page_name`,
        `is_active`
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='OpenAPI 工具映射目录表';

INSERT INTO `openapi_tool_mapping_catalog` (
    `http_method`,
    `api_path`,
    `tool_name`,
    `page_name`,
    `requires_page_match`,
    `permission_decision`,
    `permission_summary`,
    `permission_deny_message`,
    `support_user_revision`,
    `visible_fields_json`,
    `editable_fields_json`,
    `is_active`
) VALUES
    ('GET', '/resource/prepare-space/course/list', 'list_prepare_courses', 'lesson-prep-space-2', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/resource/course/material/directory', 'list_material_directory', 'lesson-prep-space-2', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/preview', 'preview_material', 'lesson-prep-space-2', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/download', 'download_material', 'lesson-prep-space-2', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/file/rename', 'rename_material', 'lesson-prep-space-2', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('file_name'), JSON_ARRAY('file_name'), 1),
    ('POST', '/resource/course/material/file/move', 'move_material', 'lesson-prep-space-2', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/file/copy', 'copy_material', 'lesson-prep-space-2', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('DELETE', '/resource/course/material/delete', 'delete_material', 'lesson-prep-space-2', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/folder/create', 'create_folder', 'lesson-prep-space-2', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('folder_name'), JSON_ARRAY('folder_name'), 1),
    ('POST', '/resource/course/material/folder/rename', 'rename_folder', 'lesson-prep-space-2', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('folder_name'), JSON_ARRAY('folder_name'), 1),
    ('POST', '/resource/course/material/folder/move', 'move_folder', 'lesson-prep-space-2', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/folder/copy', 'copy_folder', 'lesson-prep-space-2', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('DELETE', '/resource/course/material/folder/delete', 'delete_folder', 'lesson-prep-space-2', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/resource/prepare-space/course/list', 'list_prepare_courses', 'prep-workspace', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/resource/course/material/directory', 'list_material_directory', 'prep-workspace', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/preview', 'preview_material', 'prep-workspace', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/download', 'download_material', 'prep-workspace', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/file/rename', 'rename_material', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('file_name'), JSON_ARRAY('file_name'), 1),
    ('POST', '/resource/course/material/file/move', 'move_material', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/file/copy', 'copy_material', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/upload', 'upload_course_material_to_folder', 'prep-workspace', 1, 'ask', 'Upload chat attachments to the selected preparation chapter.', 'The user rejected the attachment upload. Do not repeat the same operation.', 0, JSON_ARRAY('folder_name'), JSON_ARRAY(), 1),
    ('DELETE', '/resource/course/material/delete', 'delete_material', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/folder/create', 'create_folder', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('folder_name'), JSON_ARRAY('folder_name'), 1),
    ('POST', '/resource/course/material/folder/rename', 'rename_folder', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('folder_name'), JSON_ARRAY('folder_name'), 1),
    ('POST', '/resource/course/material/folder/move', 'move_folder', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/resource/course/material/folder/copy', 'copy_folder', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('DELETE', '/resource/course/material/folder/delete', 'delete_folder', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/question/item/list_of_teacher', 'list_teacher_questions', 'prep-workspace', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/question/item/detail', 'query_question_detail', 'prep-workspace', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/question/item/create', 'create_question', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/question/item/update', 'update_question', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/question/item/archive', 'archive_question', 'prep-workspace', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/course/selection/students', 'list_selected_students', 'student-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/course/student-behavior/summary', 'list_student_behavior_summary', 'student-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/course/student-behavior/daily-details', 'list_student_behavior_daily_details', 'student-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/course/student-behavior/daily-actions', 'list_student_behavior_daily_actions', 'student-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/course/score/config', 'get_course_score_config', 'student-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/course/score/config', 'save_course_score_config', 'student-detail', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('GET', '/course/score/students', 'list_course_student_scores', 'student-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/course/score/recalculate', 'recalculate_course_scores', 'student-detail', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('GET', '/classroom/active', 'get_active_classroom', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/lesson/start', 'start_lesson', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('POST', '/lesson/end', 'end_lesson', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('POST', '/sign/publish', 'publish_sign', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('POST', '/sign/end', 'end_sign', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('GET', '/sign/result', 'get_sign_result', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/sign/manual', 'manual_sign', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('GET', '/course/selection/students', 'list_selected_students', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/question/item/list_of_teacher', 'list_teacher_questions', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/question/item/detail', 'query_question_detail', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/interact/question/list_of_teacher', 'list_published_questions', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/interact/question/detail', 'get_published_question_detail', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/interact/question/publish', 'publish_question', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('POST', '/interact/question/end', 'end_question', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('POST', '/interact/question/update', 'update_question_interaction', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('GET', '/interact/question/answer/list', 'list_class_question_answers', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/rush-answer', 'list_rush_answers', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/rush-answer', 'create_rush_answer', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('GET', '/roll-call', 'list_roll_calls', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/roll-call', 'create_roll_call', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('POST', '/reward/create', 'create_reward', 'classroom-session', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('POST', '/reward/lesson/list', 'list_lesson_rewards', 'classroom-session', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/sign/result', 'get_sign_result', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/sign/manual', 'manual_sign', 'lesson-detail', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('GET', '/course/selection/students', 'list_selected_students', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/interact/question/list_of_teacher', 'list_published_questions', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/interact/question/detail', 'get_published_question_detail', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/interact/question/answer/list', 'list_class_question_answers', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/rush-answer', 'list_rush_answers', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/roll-call', 'list_roll_calls', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('POST', '/reward/create', 'create_reward', 'lesson-detail', 1, 'ask', 'This action will modify remote data.', 'User denied this operation. Do not repeat it unchanged.', 1, JSON_ARRAY('body'), JSON_ARRAY('body'), 1),
    ('POST', '/reward/lesson/list', 'list_lesson_rewards', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/lesson/interaction/dashboard', 'get_interaction_dashboard', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1),
    ('GET', '/lesson/question-card/dashboard', 'get_question_card_dashboard', 'lesson-detail', 1, 'allow', '', '', 0, JSON_ARRAY(), JSON_ARRAY(), 1)
ON DUPLICATE KEY UPDATE
    `tool_name` = VALUES(`tool_name`),
    `requires_page_match` = VALUES(`requires_page_match`),
    `permission_decision` = VALUES(`permission_decision`),
    `permission_summary` = VALUES(`permission_summary`),
    `permission_deny_message` = VALUES(`permission_deny_message`),
    `support_user_revision` = VALUES(`support_user_revision`),
    `visible_fields_json` = VALUES(`visible_fields_json`),
    `editable_fields_json` = VALUES(`editable_fields_json`),
    `is_active` = VALUES(`is_active`);

UPDATE `openapi_tool_mapping_catalog`
SET `tool_display_name` = CASE `tool_name`
    WHEN 'archive_question' THEN '归档题目'
    WHEN 'copy_folder' THEN '复制章节'
    WHEN 'create_folder' THEN '创建章节'
    WHEN 'create_question' THEN '创建题目'
    WHEN 'create_reward' THEN '创建奖励'
    WHEN 'create_roll_call' THEN '发起点名'
    WHEN 'create_rush_answer' THEN '创建抢答'
    WHEN 'create_sign' THEN '发起签到'
    WHEN 'delete_folder' THEN '删除章节'
    WHEN 'delete_homework' THEN '删除作业'
    WHEN 'delete_material' THEN '删除资料'
    WHEN 'copy_material' THEN '复制资料'
    WHEN 'download_material' THEN '下载资料'
    WHEN 'download_public_resource' THEN '下载公共资源'
    WHEN 'end_lesson' THEN '结束课程'
    WHEN 'end_question' THEN '结束互动题'
    WHEN 'end_sign' THEN '结束签到'
    WHEN 'get_active_classroom' THEN '查看当前课堂'
    WHEN 'get_course_score_config' THEN '查看课程评分配置'
    WHEN 'get_interaction_dashboard' THEN '查看互动看板'
    WHEN 'get_published_question_detail' THEN '查看已发布题目详情'
    WHEN 'get_question_card_dashboard' THEN '查看问题卡看板'
    WHEN 'get_sign_result' THEN '查看签到结果'
    WHEN 'list_class_question_answers' THEN '查看班级题目回答'
    WHEN 'list_course_student_scores' THEN '查看课程学生成绩'
    WHEN 'list_homework' THEN '查看作业列表'
    WHEN 'list_lesson_rewards' THEN '查看课堂奖励'
    WHEN 'list_material_directory' THEN '查看资源目录'
    WHEN 'list_prepare_courses' THEN '查看备课课程'
    WHEN 'list_public_resources' THEN '查看公共资源'
    WHEN 'list_published_questions' THEN '查看已发布题目'
    WHEN 'list_roll_calls' THEN '查看点名记录'
    WHEN 'list_rush_answers' THEN '查看抢答记录'
    WHEN 'list_selected_students' THEN '查看已选学生'
    WHEN 'list_student_behavior_daily_actions' THEN '查看学生每日行为'
    WHEN 'list_student_behavior_daily_details' THEN '查看学生每日行为详情'
    WHEN 'list_student_behavior_summary' THEN '查看学生行为汇总'
    WHEN 'list_student_score_status' THEN '查看学生成绩状态'
    WHEN 'list_student_submission_status' THEN '查看学生提交状态'
    WHEN 'list_teacher_questions' THEN '查看教师题目'
    WHEN 'list_unsubmitted_students' THEN '查看未提交学生'
    WHEN 'manual_sign' THEN '手动签到'
    WHEN 'move_folder' THEN '移动章节'
    WHEN 'move_material' THEN '移动资料'
    WHEN 'preview_material' THEN '预览资料'
    WHEN 'preview_public_resource' THEN '预览公共资源'
    WHEN 'publish_homework' THEN '发布作业'
    WHEN 'publish_question' THEN '发布题目'
    WHEN 'publish_sign' THEN '发布签到'
    WHEN 'query_homework_detail' THEN '查看作业详情'
    WHEN 'query_homework_statistics' THEN '查看作业统计'
    WHEN 'query_question_detail' THEN '查看题目详情'
    WHEN 'recalculate_course_scores' THEN '重新计算课程成绩'
    WHEN 'rename_folder' THEN '重命名章节'
    WHEN 'rename_material' THEN '重命名资料'
    WHEN 'save_course_score_config' THEN '保存课程评分配置'
    WHEN 'save_homework_score' THEN '保存作业成绩'
    WHEN 'start_lesson' THEN '开始课程'
    WHEN 'update_homework' THEN '更新作业'
    WHEN 'update_question' THEN '更新题目'
    WHEN 'update_question_interaction' THEN '更新互动题'
    WHEN 'upload_course_material_to_folder' THEN '上传备课资料'
    ELSE `tool_name`
END;
