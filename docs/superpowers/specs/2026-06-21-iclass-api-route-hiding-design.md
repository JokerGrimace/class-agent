# iClass API 路径隐藏设计

## 目标

公开源码中不保留 iClass 内部 API 路径。运行时通过现有 `OPENCLAW_` 环境变量体系加载完整路径映射。

## 配置契约

- 环境变量：`OPENCLAW_ICLASS_API_ROUTES`
- `Settings` 字段：`iclass_api_routes`
- 类型：嵌套 JSON 对象，即 `dict[str, dict[str, Any]]`
- 第一层键：`IClassApiClient` 中直接发起 HTTP 请求的方法名
- 每个操作包含：
  - `path`：以 `/` 开头的非空 API 路径
  - `fields`：源码参数名到真实请求字段名的映射

精确键集合：

- `publish_sign`
- `start_lesson`
- `end_lesson`
- `end_sign`
- `submit_sign`
- `manual_sign`
- `get_sign_history`
- `get_sign_result`
- `get_class_overview`
- `list_prepare_courses`
- `list_material_directory`
- `preview_material`
- `download_material`
- `rename_material`
- `move_material`
- `delete_material`
- `create_folder`
- `rename_folder`
- `move_folder`
- `copy_folder`
- `delete_folder`

调用其他方法的兼容别名不新增配置键。

## 运行时行为

`IClassApiClient` 通过 `_route(operation)` 读取路径，并通过 `_field(operation, field)` 读取请求字段名。

- 路径缺失、为空、不是字符串或不以 `/` 开头时抛出 `IClassApiError`。
- 字段缺失、为空或不是字符串时抛出 `IClassApiError`。
- 错误信息只包含源码操作名和源码参数名，不包含真实路径、字段名或其他配置内容。

## 安全边界

- 源码和测试不得出现原有内部路径及请求字段字面量。
- 不提交包含真实路径的 `.env.example`。
- 路径映射与基址、令牌一样由部署环境注入。

## 验证

- AST 测试确认 `Settings.iclass_api_routes` 存在且默认空映射。
- 环境变量测试确认嵌套 JSON 可由 Pydantic Settings 解析。
- 源码扫描确认 `client.py` 不包含 API 路径或原请求字段字面量。
- 运行时单元测试确认有效映射可读取，缺失和非法路径或字段会失败。
