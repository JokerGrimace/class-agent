from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Callable, Optional

import httpx

from app.config import settings
from app.core.iclass.auth_context import get_current_iclass_api_token
from app.core.tool.openapi_mapping_db import OpenApiToolMappingService
from app.core.tool.registry import ToolRegistry, registry
from app.core.tool.types import ToolResult


_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_SUPPORTED_HTTP_METHODS = frozenset(method.upper() for method in _HTTP_METHODS)
_COURSE_MATERIAL_UPLOAD_PATH = "/resource/course/material/upload"
_PUBLIC_RESOURCE_UPLOAD_PATH = "/resource/public/upload"
_RUSH_ANSWER_ROUND_CREATE_PATH = "/rush-answer/rounds"
_RUSH_ANSWER_ROUND_END_PATH = "/rush-answer/rounds/end"
_RUSH_ANSWER_ROUND_CREATED_ACTION_TYPE = "rush_answer_round_created"
_RUSH_ANSWER_ROUND_ENDED_ACTION_TYPE = "rush_answer_round_ended"
_QUESTION_ITEM_CREATE_PATH = "/question/item/create"
_QUESTION_STATUS_FIELD = "questionStatus"
_QUESTION_STATUS_ENABLED = 1


def _camel_to_snake(name: str) -> str:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name or "")
    return normalized.replace("-", "_").lower()


_openapi_tool_mapping_service_instance: Optional[OpenApiToolMappingService] = None


def _openapi_tool_mapping_service() -> OpenApiToolMappingService:
    global _openapi_tool_mapping_service_instance
    if _openapi_tool_mapping_service_instance is None:
        _openapi_tool_mapping_service_instance = OpenApiToolMappingService()
    return _openapi_tool_mapping_service_instance


class IClassOpenApiOperationClient:
    async def call_openapi_operation(
        self,
        *,
        method: str,
        path: str,
        query_params: dict[str, object],
        body: object,
    ) -> object:
        normalized_method = (method or "").upper()
        if normalized_method not in _SUPPORTED_HTTP_METHODS:
            raise ValueError(f"unsupported openapi method: {method}")

        # SpringDoc 负责提供 operation 契约，这里按契约的方法和路径直接请求 iClass API。
        api_url = f"{settings.iclass_api_base_url.rstrip('/')}{path}"
        # 业务操作只能使用本轮已认证用户的凭证，测试身份和空上下文不能借用共享账户。
        token = get_current_iclass_api_token().strip()
        if not token:
            raise ValueError("iClass API authentication required")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

        # GET 传递 OpenAPI query 参数，其他已支持方法保持 ApiRequest.body 包装契约。
        request_kwargs: dict[str, Any] = {"headers": headers}
        if normalized_method == "GET":
            request_kwargs["params"] = {
                key: value
                for key, value in query_params.items()
                if value is not None
            }
        else:
            payload = body if isinstance(body, dict) else {}
            request_kwargs["json"] = {"body": payload}

        async with httpx.AsyncClient(
            timeout=settings.iclass_api_timeout_seconds
        ) as client:
            response = await client.request(
                normalized_method,
                api_url,
                **request_kwargs,
            )

        # iClass API 在 HTTP 成功响应外还有 code 业务状态，必须同时校验并只返回 body。
        response.raise_for_status()
        response_payload = response.json()
        if str(response_payload.get("code")) != "200":
            raise RuntimeError(
                response_payload.get("message") or "iclass api request failed"
            )
        return response_payload.get("body")


class IClassOpenApiToolRegistrar:
    """
    这个类的作用：
        把 OpenAPI 文档里的接口，自动注册成系统里的 Tool 工具。

    简单说：
        OpenAPI 里定义了很多接口，比如：
            GET /user/list
            POST /user/create

        这个类会把这些接口变成可以被系统调用的工具。
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        client_factory: Optional[Callable[[], Any]] = None,
        mapping_service: Optional[OpenApiToolMappingService] = None,
    ) -> None:
        """
        初始化注册器。

        registry:
            工具注册中心，所有工具最终都会注册到这里。

        client_factory:
            用来创建 OpenAPI 接口调用客户端。
            后面真正调用接口时，会通过它创建 client。

        mapping_service:
            用来查询页面和 OpenAPI 接口到页面工具的映射关系。
        """

        # 如果外部传了 registry，就用外部传入的；
        # 如果没传，就从全局变量 globals() 里拿默认 registry。
        self.registry = registry or globals()["registry"]

        # 如果外部传了 client_factory，就用外部传入的；
        # 如果没传，就使用默认的 IClassOpenApiOperationClient。
        self.client_factory = client_factory or IClassOpenApiOperationClient

        # 如果外部传了 mapping_service，就用外部传入的；
        # 如果没传，就调用 _openapi_tool_mapping_service() 创建默认映射服务。
        self.mapping_service = mapping_service or _openapi_tool_mapping_service()

    def register_from_spec(
        self,
        spec: dict[str, Any],
        *,
        page_name: str,
    ) -> list[str]:
        """
        根据 OpenAPI spec 注册工具。

        spec:
            OpenAPI 文档，通常是一个 dict。

        page_name:
            当前页面标识。
            注册逻辑必须先根据当前页面查询映射表中的可用工具，
            再根据映射行里的 http_method 和 api_path 去 OpenAPI 文档中定位接口。

        返回值:
            registered_names，表示成功注册的工具名称列表。
        """

        # 用来保存最终成功注册的工具名。
        registered_names: list[str] = []

        # 先标准化当前页面标识。
        normalized_page_name = (page_name or "").strip()

        # 如果页面标识为空，说明当前没有页面上下文。
        # 按规则这里不能猜测页面归属，直接返回空列表。
        if not normalized_page_name:
            return registered_names

        # 从 OpenAPI 文档中取出 paths。
        paths = spec.get("paths")

        # 如果 paths 不是 dict，说明 OpenAPI 文档结构不合法，
        # 直接返回空列表。
        if not isinstance(paths, dict):
            return registered_names

        # 先根据当前页面，从映射表里取出该页面可用的所有映射行。
        mapping_rows = self.mapping_service.list_mappings_for_page(normalized_page_name)

        # 遍历该页面下的所有映射行。
        for mapping_row in mapping_rows:
            # 从映射行中取出工具名、HTTP 方法和接口路径。
            tool_name = str(mapping_row.get("tool_name") or "").strip()
            normalized_method = str(mapping_row.get("http_method") or "").strip().upper()
            api_path = str(mapping_row.get("api_path") or "").strip()

            # 如果关键字段不完整，说明映射行无效，直接跳过。
            if not tool_name or not normalized_method or not api_path:
                continue

            # 只处理当前支持的 HTTP 方法。
            method_name = normalized_method.lower()
            if method_name not in _HTTP_METHODS:
                continue

            # 根据 api_path 从 OpenAPI 文档中取出对应 path_item。
            path_item = paths.get(api_path)
            if not isinstance(path_item, dict):
                continue

            # 再根据 HTTP 方法，从 path_item 中取出对应 operation。
            operation = path_item.get(method_name)
            if not isinstance(operation, dict):
                continue

            # 如果当前接口包含二进制参数，比如文件上传，
            # 当前注册器不处理，直接跳过。
            if self._has_binary_parameters(operation):
                if api_path not in {_COURSE_MATERIAL_UPLOAD_PATH, _PUBLIC_RESOURCE_UPLOAD_PATH}:
                    continue
                if self._register_binary_frontend_action_tool(
                    tool_name=tool_name,
                    api_path=api_path,
                    operation=operation,
                    visibility={
                        "tool_types": ["level1"],
                        "page_names": [normalized_page_name],
                        "requires_page_match": bool(
                            mapping_row.get("requires_page_match", False)
                        ),
                    },
                ):
                    registered_names.append(tool_name)
                continue

            # 根据当前映射行直接注册一个 OpenAPI 工具。
            # 这里不再复用旧的本地 iclass 工具实现，
            # 而是把映射表里的 tool_name 直接注册成一个通用 OpenAPI 调用工具。
            if self._register_mapped_operation_tool(
                tool_name=tool_name,
                method=normalized_method,
                path=api_path,
                operation=operation,
                spec=spec,
                visibility={
                    # tool_types 为空，表示这里不限制工具类型。
                    "tool_types": ["level1"],

                    # 当前注册动作是基于单个页面展开的，
                    # 所以 page_names 只写入当前页面。
                    "page_names": [normalized_page_name],

                    # 是否要求页面精确匹配，直接沿用映射表配置。
                    "requires_page_match": bool(
                        mapping_row.get("requires_page_match", False)
                    ),
                },
            ):
                # 如果注册成功，把工具名加入已注册列表。
                registered_names.append(tool_name)

        # 返回所有成功注册的工具名。
        return registered_names

    def _register_mapped_operation_tool(
        self,
        *,
        tool_name: str,
        method: str,
        path: str,
        operation: dict[str, Any],
        spec: dict[str, Any],
        visibility: dict[str, Any],
    ) -> bool:
        """
        根据映射表和 OpenAPI operation 注册一个页面工具。

        场景：
            当前页面已经通过映射表明确声明了：
                某个 tool_name 对应某个 http_method 和 api_path。
            这里直接使用映射表里的 tool_name 注册工具，
            不再依赖旧的本地 iclass 工具模块预先导入。

        返回值：
            True 表示注册成功。
            False 表示注册失败。
        """

        # 根据 OpenAPI operation 构建工具参数。
        # 这里传入完整 spec，
        # 是因为 requestBody 或 body 内部可能继续引用 components.schemas。
        parameters = self._build_function_parameters(operation, spec=spec)

        # 根据 OpenAPI operation 构建“工具参数名 -> OpenAPI 原始参数名”的映射。
        # 这样模型侧继续使用 snake_case 参数，
        # 真正发请求时仍然可以回写成 OpenAPI 定义里的字段名。
        query_parameter_name_map = self._build_query_parameter_name_map(
            operation,
            spec=spec,
        )

        # 根据 HTTP 方法、路径和参数名，创建真正执行 OpenAPI 请求的工具函数。
        tool_func = self._build_generic_tool_function(
            method=method,
            path=path,
            query_parameter_name_map=query_parameter_name_map,
        )

        # 根据 HTTP 方法推导最基础的治理标签。
        # GET 归为只读接口，其他方法归为变更类接口。
        governance = self._build_tool_governance(method)

        # 直接把映射表里的 tool_name 注册到 registry。
        # 这里每次注册都会覆盖同名旧定义，
        # 让当前页面对应的 OpenAPI 描述、参数和可见性成为最新版本。
        self.registry.register(
            tool_name,
            self._operation_description(operation),
            parameters,
            tool_func,
            governance=governance,
            visibility=visibility,
        )

        return True

    def _register_binary_frontend_action_tool(
        self,
        *,
        tool_name: str,
        api_path: str,
        operation: dict[str, Any],
        visibility: dict[str, Any],
    ) -> bool:
        is_public_resource_upload = api_path == _PUBLIC_RESOURCE_UPLOAD_PATH
        properties = {
            "file_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Attachment file_id values from the current assistant "
                    "conversation."
                ),
            },
            "file_extra": {
                "type": "string",
                "description": "Optional public resource fileExtra metadata.",
            },
        }
        required = ["file_ids"]
        if not is_public_resource_upload:
            properties.update(
                {
                    "folder_id": {
                        "type": "integer",
                        "description": (
                            "Target course material folder id. Use the exact id "
                            "returned by list_material_directory."
                        ),
                    },
                    "folder_name": {
                        "type": "string",
                        "description": "Target chapter or folder name for user feedback.",
                    },
                }
            )
            required.append("folder_id")

        parameters = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        async def _tool_func(
            file_ids: list[str],
            folder_id: Optional[int] = None,
            folder_name: Optional[str] = None,
            file_extra: Optional[str] = None,
        ) -> ToolResult:
            normalized_file_ids = [
                file_id.strip()
                for file_id in file_ids
                if isinstance(file_id, str) and file_id.strip()
            ]
            if not normalized_file_ids:
                return ToolResult(
                    success=False,
                    error="file_ids must contain at least one attachment file_id",
                )
            if (
                not is_public_resource_upload
                and (not isinstance(folder_id, int) or isinstance(folder_id, bool))
            ):
                return ToolResult(
                    success=False,
                    error="folder_id must be an integer",
                )

            normalized_folder_name = (
                folder_name.strip()
                if isinstance(folder_name, str)
                else ""
            )
            normalized_file_extra = (
                file_extra.strip()
                if isinstance(file_extra, str)
                else ""
            )
            result_payload = {
                "file_ids": normalized_file_ids,
            }
            if is_public_resource_upload:
                if normalized_file_extra:
                    result_payload["file_extra"] = normalized_file_extra
                target_label = "public resources"
            else:
                result_payload["folder_id"] = folder_id
                result_payload["folder_name"] = normalized_folder_name
                target_label = normalized_folder_name or f"folder {folder_id}"
            assistant_payload = {
                "reply_text": f"Requested attachment upload to {target_label}",
                "action_type": tool_name,
                "needs_confirmation": False,
                "pending_action": None,
                "ui_refresh_hint": {"refresh_directory": True},
                "result_payload": result_payload,
            }
            return ToolResult(
                success=True,
                content=json.dumps(
                    {
                        "action": tool_name,
                        "success": True,
                        **result_payload,
                    },
                    ensure_ascii=False,
                ),
                meta={
                    "assistant_payload": assistant_payload,
                    "operation_id": operation.get("operationId"),
                },
            )

        self.registry.register(
            tool_name,
            "Request the frontend to upload selected assistant attachments.",
            parameters,
            _tool_func,
            governance={
                "tool_tags": ["frontend_action", "resource_management", "mutating"],
            },
            visibility=visibility,
        )
        return True

    @staticmethod
    def _build_tool_governance(method: str) -> dict[str, Any]:
        """
        根据 HTTP 方法构建最基础的治理标签。

        说明：
            当前链路的权限主入口是页面映射表里的 permission_decision。
            这里补充的 governance 主要用于让工具保持最基础的 remote_api 身份。
        """

        # 先给所有动态 OpenAPI 工具打上 remote_api 标记。
        tool_tags = ["remote_api"]

        # GET 视为只读；其他 HTTP 方法视为有副作用的变更操作。
        if (method or "").strip().upper() == "GET":
            tool_tags.append("read_only")
        else:
            tool_tags.append("mutating")

        return {
            "tool_tags": tool_tags,
        }

    def _iter_query_tool_parameters(
        self,
        operation: dict[str, Any],
        *,
        spec: dict[str, Any],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for parameter in operation.get("parameters", []):
            if not isinstance(parameter, dict):
                continue

            original_name = str(parameter.get("name") or "").strip()
            if not original_name:
                continue

            schema = parameter.get("schema")
            resolved_schema = self._resolve_schema(schema, spec)
            # Some generated OpenAPI operations wrap query fields in a single
            # parameter named "request". Expose the nested fields as top-level
            # snake_case tool arguments, then map them back to the original
            # OpenAPI field names when the HTTP request is sent.
            if (
                original_name == "request"
                and isinstance(resolved_schema, dict)
                and isinstance(resolved_schema.get("properties"), dict)
            ):
                required_fields = set(resolved_schema.get("required") or [])
                for field_name, field_schema in resolved_schema["properties"].items():
                    if not isinstance(field_name, str) or not field_name.strip():
                        continue
                    normalized_field_name = _camel_to_snake(field_name)
                    if not normalized_field_name:
                        continue
                    result.append(
                        {
                            "normalized_name": normalized_field_name,
                            "original_name": field_name,
                            "schema": dict(field_schema)
                            if isinstance(field_schema, dict)
                            else {"type": "string"},
                            "required": field_name in required_fields,
                        }
                    )
                continue

            normalized_name = _camel_to_snake(original_name)
            if not normalized_name:
                continue
            if isinstance(resolved_schema, dict):
                property_schema = dict(resolved_schema)
            elif isinstance(schema, dict):
                property_schema = dict(schema)
            else:
                property_schema = {"type": "string"}
            if parameter.get("description"):
                property_schema["description"] = parameter["description"]
            result.append(
                {
                    "normalized_name": normalized_name,
                    "original_name": original_name,
                    "schema": property_schema,
                    "required": bool(parameter.get("required")),
                }
            )

        return result

    def _build_query_parameter_name_map(
        self,
        operation: dict[str, Any],
        *,
        spec: dict[str, Any],
    ) -> dict[str, str]:
        return {
            parameter["normalized_name"]: parameter["original_name"]
            for parameter in self._iter_query_tool_parameters(operation, spec=spec)
        }

    def _build_generic_tool_function(
        self,
        *,
        method: str,
        path: str,
        query_parameter_name_map: dict[str, str],
    ):
        """
        创建一个通用工具函数。

        这个方法返回的是一个 async 函数。

        当工具被调用时，这个 async 函数会：
            1. 接收用户传入的参数 kwargs
            2. 把非 body 参数放到 query_params
            3. 把 body 参数作为请求体
            4. 调用真实 OpenAPI 接口
            5. 把接口结果包装成 ToolResult 返回
        """

        async def _tool_func(**kwargs) -> ToolResult:
            """
            这个函数是真正被工具系统调用的函数。

            kwargs:
                工具调用时传入的参数。
            """

            # 构造 query 参数。
            # 这里遍历的是“工具参数名 -> OpenAPI 参数名”映射，
            # 这样外部传入 snake_case 参数后，
            # 真正请求时仍然会用 OpenAPI 文档要求的原始字段名。
            query_params = {
                original_name: kwargs[normalized_name]
                for normalized_name, original_name in query_parameter_name_map.items()
                if normalized_name in kwargs and kwargs[normalized_name] is not None
            }

            # 取出 body 参数。
            body = kwargs.get("body")
            if path == _QUESTION_ITEM_CREATE_PATH and isinstance(body, dict):
                body = dict(body)
                body.setdefault(_QUESTION_STATUS_FIELD, _QUESTION_STATUS_ENABLED)

            # 创建 OpenAPI 调用客户端。
            client = self.client_factory()

            # 调用真实 OpenAPI 接口。
            result = await client.call_openapi_operation(
                method=method,
                path=path,
                query_params=query_params,
                body=body,
            )

            # 抢答轮次创建成功时，后端返回的轮次标识是结束操作唯一需要回传给前端的业务状态。
            # 仅针对精确的创建接口构造操作卡数据，其他动态 OpenAPI 工具继续保持通用结果格式。
            meta = {
                "method": method,
                "path": path,
                "query_params": query_params,
                "body": body,
                "result": result,
            }
            rush_answer_action_payload = self._build_rush_answer_round_action_payload(
                method=method,
                path=path,
                body=body,
                result=result,
            )
            if rush_answer_action_payload is not None:
                meta["assistant_payload"] = rush_answer_action_payload

            # 把结果包装成 ToolResult 返回。
            return ToolResult(
                success=True,
                content=json.dumps(
                    {
                        "method": method,
                        "path": path,
                        "query_params": query_params,
                        "body": body,
                        "result": result,
                    },
                    ensure_ascii=False,
                ),
                meta=meta,
            )

        # 返回这个内部函数。
        return _tool_func

    @staticmethod
    def _build_rush_answer_round_action_payload(
        *,
        method: str,
        path: str,
        body: object,
        result: object,
    ) -> Optional[dict[str, Any]]:
        """
        为抢答轮次创建或结束的成功响应生成前端状态数据。

        创建操作仅接受响应中完整的 rushAnswerId、teacherCode、courseCode；结束操作只读取
        已由 OpenAPI 请求体定义的 rushAnswerId。两种状态都避免前端从聊天文本推断业务字段。
        """

        if method.strip().upper() != "POST":
            return None
        if path == _RUSH_ANSWER_ROUND_CREATE_PATH:
            return IClassOpenApiToolRegistrar._build_rush_answer_round_created_payload(result)
        if path == _RUSH_ANSWER_ROUND_END_PATH:
            return IClassOpenApiToolRegistrar._build_rush_answer_round_ended_payload(body)
        return None

    @staticmethod
    def _build_rush_answer_round_created_payload(result: object) -> Optional[dict[str, Any]]:
        if not isinstance(result, dict):
            return None

        rush_answer_id = result.get("rushAnswerId")
        teacher_code = result.get("teacherCode")
        course_code = result.get("courseCode")
        if not isinstance(rush_answer_id, int) or isinstance(rush_answer_id, bool):
            return None
        if not isinstance(teacher_code, str) or not teacher_code.strip():
            return None
        if not isinstance(course_code, str) or not course_code.strip():
            return None

        return {
            "action_type": _RUSH_ANSWER_ROUND_CREATED_ACTION_TYPE,
            "result_payload": {
                "rush_answer_id": rush_answer_id,
                "teacher_code": teacher_code.strip(),
                "course_code": course_code.strip(),
            },
        }

    @staticmethod
    def _build_rush_answer_round_ended_payload(body: object) -> Optional[dict[str, Any]]:
        if not isinstance(body, dict):
            return None
        rush_answer_id = body.get("rushAnswerId")
        if not isinstance(rush_answer_id, int) or isinstance(rush_answer_id, bool):
            return None

        return {
            "action_type": _RUSH_ANSWER_ROUND_ENDED_ACTION_TYPE,
            "result_payload": {
                "rush_answer_id": rush_answer_id,
            },
        }

    def _build_function_parameters(
        self,
        operation: dict[str, Any],
        *,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []

        for parameter in self._iter_query_tool_parameters(operation, spec=spec):
            normalized_name = parameter["normalized_name"]
            properties[normalized_name] = parameter["schema"]
            if parameter["required"]:
                required.append(normalized_name)

        request_body_schema = self._resolve_request_body_schema(operation, spec)
        if request_body_schema is not None:
            body_property = request_body_schema.get("properties", {}).get("body")
            if isinstance(body_property, dict):
                resolved_body_property = self._resolve_schema(body_property, spec)
                properties["body"] = (
                    resolved_body_property
                    if resolved_body_property is not None
                    else dict(body_property)
                )
            else:
                properties["body"] = {"type": "object"}
            if "body" in request_body_schema.get("required", []):
                required.append("body")

        parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            parameters["required"] = required
        return parameters

    def _resolve_request_body_schema(
        self,
        operation: dict[str, Any],
        spec: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """
        解析 OpenAPI operation 里的 requestBody schema。

        OpenAPI 里的 requestBody 大概长这样：

        {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": "#/components/schemas/UserCreateRequest"
                        }
                    }
                }
            }
        }
        """

        # 取 requestBody。
        request_body = operation.get("requestBody")
        if not isinstance(request_body, dict):
            return None

        # 取 content。
        content = request_body.get("content")
        if not isinstance(content, dict):
            return None

        # 遍历 content 里的 media_type。
        for media_type in content.values():
            if not isinstance(media_type, dict):
                continue

            # 取 schema，并继续解析 $ref。
            schema = media_type.get("schema")
            resolved_schema = self._resolve_schema(schema, spec)
            if resolved_schema is not None:
                return resolved_schema

        return None

    def _resolve_schema(
        self,
        schema: Any,
        spec: dict[str, Any],
        *,
        seen_refs: Optional[set[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """
        解析 schema。

        它主要做两件事：
            1. 解析当前 schema 里的 $ref 引用
            2. 递归解析 properties 和 items 里的子 schema
        """

        # schema 必须是 dict，否则没法解析。
        if not isinstance(schema, dict):
            return None

        # 先解析当前 schema 自己的 $ref。
        resolved_schema = self._resolve_schema_reference(
            schema,
            spec,
            seen_refs=seen_refs or set(),
        )
        if resolved_schema is None:
            return None

        # 如果有 properties，就递归解析每个字段。
        properties = resolved_schema.get("properties")
        if isinstance(properties, dict):
            resolved_properties: dict[str, Any] = {}
            for property_name, property_schema in properties.items():
                child_schema = self._resolve_schema(
                    property_schema,
                    spec,
                    seen_refs=set(seen_refs or set()),
                )
                resolved_properties[property_name] = (
                    child_schema
                    if child_schema is not None
                    else property_schema
                )
            resolved_schema["properties"] = resolved_properties

        # 如果有 items，也继续递归解析。
        items = resolved_schema.get("items")
        if isinstance(items, dict):
            resolved_items = self._resolve_schema(
                items,
                spec,
                seen_refs=set(seen_refs or set()),
            )
            if resolved_items is not None:
                resolved_schema["items"] = resolved_items

        return resolved_schema

    def _resolve_schema_reference(
        self,
        schema: dict[str, Any],
        spec: dict[str, Any],
        *,
        seen_refs: set[str],
    ) -> Optional[dict[str, Any]]:
        """
        解析 schema 里的 $ref 引用。

        OpenAPI 里常见写法：

        {
            "$ref": "#/components/schemas/UserDTO"
        }

        这个函数会去 spec["components"]["schemas"]["UserDTO"] 里找到真正定义。
        """

        # 取 $ref。
        ref = schema.get("$ref")

        # 如果没有 $ref，说明它不是引用，直接返回深拷贝。
        if not isinstance(ref, str):
            return deepcopy(schema)

        # 如果这个 ref 已经解析过了，说明可能出现循环引用。
        if ref in seen_refs:
            return None

        # 当前只支持解析 "#/components/schemas/XXX" 这种格式。
        if not ref.startswith("#/components/schemas/"):
            return None

        # 从 ref 中取 schema 名称。
        schema_name = ref.split("/")[-1]

        # 依次取 components 和 schemas。
        components = spec.get("components")
        if not isinstance(components, dict):
            return None

        schemas = components.get("schemas")
        if not isinstance(schemas, dict):
            return None

        # 根据 schema_name 找到被引用的 schema。
        referenced_schema = schemas.get(schema_name)
        if not isinstance(referenced_schema, dict):
            return None

        # 递归解析被引用的 schema。
        resolved_reference = self._resolve_schema_reference(
            referenced_schema,
            spec,
            seen_refs=seen_refs | {ref},
        )
        if resolved_reference is None:
            return None

        # 把引用解析结果和原 schema 上除 $ref 外的字段合并。
        merged_schema = deepcopy(resolved_reference)
        for key, value in schema.items():
            if key == "$ref":
                continue
            if key == "description" and merged_schema.get("description"):
                continue
            if key not in merged_schema:
                merged_schema[key] = deepcopy(value)

        return merged_schema

    @staticmethod
    def _has_binary_parameters(operation: dict[str, Any]) -> bool:
        """
        判断 operation 的 parameters 里是否包含二进制参数。

        二进制参数通常表示文件上传，例如：
            type: string
            format: binary

        如果发现有二进制参数，就返回 True。
        """

        # 遍历 operation 的 parameters。
        for parameter in operation.get("parameters", []):
            if not isinstance(parameter, dict):
                continue

            # 取参数 schema，并判断其中是否包含 binary。
            schema = parameter.get("schema")
            if IClassOpenApiToolRegistrar._schema_contains_binary(schema):
                return True

        return False

    @staticmethod
    def _schema_contains_binary(schema: Any) -> bool:
        """
        判断一个 schema 是否包含 binary 格式。

        支持递归检查数组 items。
        """

        # schema 不是 dict，直接认为不包含 binary。
        if not isinstance(schema, dict):
            return False

        # 如果 format 是 binary，说明是二进制参数。
        if schema.get("format") == "binary":
            return True

        # 如果 schema 是数组，就继续检查 items。
        items = schema.get("items")
        if isinstance(items, dict):
            return IClassOpenApiToolRegistrar._schema_contains_binary(items)

        return False

    @staticmethod
    def _operation_description(operation: dict[str, Any]) -> str:
        """
        获取工具描述。

        优先级：
            1. summary
            2. description
            3. operationId

        也就是说：
            如果有 summary，用 summary；
            如果没有 summary，但有 description，用 description；
            如果都没有，用 operationId。
        """

        # 优先取 summary。
        summary = (operation.get("summary") or "").strip()
        if summary:
            return summary

        # 其次取 description。
        description = (operation.get("description") or "").strip()
        if description:
            return description

        # 最后取 operationId。
        return str(operation.get("operationId") or "").strip()


async def load_iclass_openapi_tools(
    *,
    registry: ToolRegistry | None = None,
    openapi_url: Optional[str] = None,
    client_factory: Optional[Callable[[], Any]] = None,
    mapping_service: Optional[OpenApiToolMappingService] = None,
    page_name: Optional[str] = None,
) -> list[str]:
    # 先标准化页面标识。
    normalized_page_name = (page_name or "").strip()

    # 如果页面标识为空，说明当前没有页面上下文。
    # 页面化注册链路不能猜测页面，所以直接返回空列表。
    if not normalized_page_name:
        return []

    # 如果外部没有指定 OpenAPI 地址，就使用默认地址。
    target_url = (openapi_url or "").strip()
    if not target_url:
        target_url = f"{settings.iclass_api_base_url.rstrip('/')}/v3/api-docs/agent"

    # 先拉取远端 OpenAPI 文档。
    try:
        async with httpx.AsyncClient(timeout=settings.iclass_api_timeout_seconds) as client:
            response = await client.get(target_url)
            response.raise_for_status()
            spec = response.json()
    except Exception:
        return []

    # 创建注册器，并按当前页面注册工具。
    registrar = IClassOpenApiToolRegistrar(
        registry=registry or globals()["registry"],
        client_factory=client_factory,
        mapping_service=mapping_service,
    )
    return registrar.register_from_spec(spec, page_name=normalized_page_name)
