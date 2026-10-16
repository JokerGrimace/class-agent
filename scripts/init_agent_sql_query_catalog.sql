SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `agent_python_tool_catalog` (
    `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
    `tool_name` VARCHAR(255) NOT NULL COMMENT 'Python 端 Agent 工具名',
    `tool_display_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Agent 工具中文展示名',
    `module_path` VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Python 工具模块路径',
    `page_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '页面标识，空字符串表示全局工具',
    `requires_page_match` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否要求当前页面精确命中',
    `permission_decision` VARCHAR(16) NOT NULL DEFAULT 'allow' COMMENT '页面级权限决策',
    `permission_summary` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '权限提示文案',
    `permission_deny_message` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '拒绝提示文案',
    `visible_fields_json` JSON NOT NULL COMMENT '权限确认时可见字段',
    `editable_fields_json` JSON NOT NULL COMMENT '权限确认时可编辑字段',
    `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
    `timeout_ms` INT NOT NULL DEFAULT 5000 COMMENT '单次工具超时时间',
    `max_rows` INT NOT NULL DEFAULT 500 COMMENT 'SQL 查询结果最大返回行数',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uniq_agent_python_tool_catalog_tool_page` (`tool_name`, `page_name`),
    KEY `idx_agent_python_tool_catalog_page_active` (`page_name`, `is_active`),
    KEY `idx_agent_python_tool_catalog_tool_active` (`tool_name`, `is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent Python 端工具管理表';

CREATE TABLE IF NOT EXISTS `agent_sql_query_table_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `table_name` VARCHAR(255) NOT NULL COMMENT '物理表名',
  `business_name` VARCHAR(255) NOT NULL COMMENT '业务名称',
  `business_description` TEXT NOT NULL COMMENT '业务说明',
  `course_scope_column` VARCHAR(255) NOT NULL COMMENT '课程范围字段名',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_sql_query_table_catalog_table_name` (`table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 可查询表目录';

CREATE TABLE IF NOT EXISTS `agent_sql_query_column_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `table_name` VARCHAR(255) NOT NULL COMMENT '物理表名',
  `column_name` VARCHAR(255) NOT NULL COMMENT '物理字段名',
  `business_name` VARCHAR(255) NOT NULL COMMENT '字段业务名称',
  `business_description` TEXT NOT NULL COMMENT '字段业务说明',
  `data_category` VARCHAR(64) NOT NULL COMMENT '数据分类',
  `is_queryable` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否允许查询',
  `is_dimension` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否允许作为图表维度',
  `is_metric` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否允许作为图表指标',
  `is_sensitive` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否敏感字段',
  `masking_rule` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '脱敏规则',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_sql_query_column_catalog_column` (`table_name`, `column_name`),
  KEY `idx_agent_sql_query_column_catalog_queryable` (`table_name`, `is_queryable`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 可查询字段目录';

CREATE TABLE IF NOT EXISTS `agent_sql_query_relationship_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `source_table_name` VARCHAR(255) NOT NULL COMMENT '来源表名',
  `source_column_name` VARCHAR(255) NOT NULL COMMENT '来源字段名',
  `target_table_name` VARCHAR(255) NOT NULL COMMENT '目标表名',
  `target_column_name` VARCHAR(255) NOT NULL COMMENT '目标字段名',
  `relationship_type` VARCHAR(64) NOT NULL COMMENT '关系类型',
  `business_description` TEXT NOT NULL COMMENT '业务关系说明',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_agent_sql_query_relationship_source` (`source_table_name`, `is_active`),
  KEY `idx_agent_sql_query_relationship_target` (`target_table_name`, `is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 表业务关系目录';

CREATE TABLE IF NOT EXISTS `agent_sql_query_lineage_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `source_table_name` VARCHAR(255) NOT NULL COMMENT '上游表名',
  `source_column_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上游字段名',
  `target_table_name` VARCHAR(255) NOT NULL COMMENT '下游表名',
  `target_column_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '下游字段名',
  `lineage_type` VARCHAR(64) NOT NULL COMMENT '血缘类型',
  `transform_description` TEXT NOT NULL COMMENT '转换说明',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_agent_sql_query_lineage_source` (`source_table_name`, `is_active`),
  KEY `idx_agent_sql_query_lineage_target` (`target_table_name`, `is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent SQL 表字段血缘目录';

INSERT INTO `agent_python_tool_catalog` (
    `tool_name`,
    `tool_display_name`,
    `module_path`,
    `page_name`,
    `requires_page_match`,
    `permission_decision`,
    `visible_fields_json`,
    `editable_fields_json`,
    `is_active`,
    `timeout_ms`,
    `max_rows`
) VALUES
    ('exec', '执行命令', 'app.tools.builtin', '', 0, 'ask', JSON_ARRAY('cmd'), JSON_ARRAY(), 1, 30000, 500),
    ('read_file', '读取文件', 'app.tools.builtin', '', 0, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500),
    ('write_file', '写入文件', 'app.tools.builtin', '', 0, 'ask', JSON_ARRAY('path'), JSON_ARRAY(), 1, 5000, 500),
    ('expand_tool', '展开工具结果', 'app.tools.expand', '', 0, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500),
    ('web_search', '网页搜索', 'app.tools.web_search', '', 0, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 15000, 500),
    ('web_fetch', '网页抓取', 'app.tools.web_fetch', '', 0, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 20000, 500),
    ('read_cached_file_content', '读取文件内容缓存', 'app.tools.iclass_d_resource.prase_file', '', 0, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500),
    ('read_workflow_content', '读取工作流内容', 'app.tools.workflow_tools', '', 0, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500),
    ('create_workflow_run', '创建工作流运行', 'app.tools.workflow_tools', '', 0, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500),
    ('dispatch_workflow_batch_and_wait', '推进工作流批次', 'app.tools.workflow_tools', '', 0, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 300000, 500),
    ('get_workflow_run', '查看工作流运行', 'app.tools.workflow_tools', '', 0, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500),
    ('cancel_workflow_run', '取消工作流运行', 'app.tools.workflow_tools', '', 0, 'ask', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500),
    ('readonly_course_sql_query', '只读课程SQL查询', 'app.tools.readonly_course_sql', 'student-detail', 1, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500),
    ('render_dashboard_panel', '生成数字面板', 'app.tools.readonly_course_sql', 'student-detail', 1, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500),
    ('render_query_result_chart', '绘制查询结果图表', 'app.tools.readonly_course_sql', 'student-detail', 1, 'allow', JSON_ARRAY(), JSON_ARRAY(), 1, 5000, 500)
ON DUPLICATE KEY UPDATE
    `tool_display_name` = VALUES(`tool_display_name`),
    `module_path` = VALUES(`module_path`),
    `requires_page_match` = VALUES(`requires_page_match`),
    `permission_decision` = VALUES(`permission_decision`),
    `visible_fields_json` = VALUES(`visible_fields_json`),
    `editable_fields_json` = VALUES(`editable_fields_json`),
    `is_active` = VALUES(`is_active`),
    `timeout_ms` = VALUES(`timeout_ms`),
    `max_rows` = VALUES(`max_rows`);
