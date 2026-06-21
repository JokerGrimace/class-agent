from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class IClassApiError(RuntimeError):
    pass


class IClassApiClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.iclass_api_base_url).rstrip("/")
        self.token = (token if token is not None else settings.iclass_api_token).strip()
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.iclass_api_timeout_seconds
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _route(self, operation: str) -> str:
        routes = settings.iclass_api_routes
        operation_config = routes.get(operation) if isinstance(routes, dict) else None
        path = (
            operation_config.get("path")
            if isinstance(operation_config, dict)
            else None
        )
        if not isinstance(path, str) or not path or not path.startswith("/"):
            raise IClassApiError(
                f"Invalid or missing iClass API route: {operation}"
            )
        return path

    def _field(self, operation: str, field: str) -> str:
        routes = settings.iclass_api_routes
        operation_config = routes.get(operation) if isinstance(routes, dict) else None
        fields = (
            operation_config.get("fields")
            if isinstance(operation_config, dict)
            else None
        )
        field_name = fields.get(field) if isinstance(fields, dict) else None
        if not isinstance(field_name, str) or not field_name:
            raise IClassApiError(
                f"Invalid or missing iClass API field: {operation}.{field}"
            )
        return field_name

    def _unwrap_response(self, response: httpx.Response) -> Any:
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) != "200":
            raise IClassApiError(payload.get("message") or "iclass api request failed")
        return payload.get("body")

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}{path}",
                json={"body": body},
                headers=self._headers(),
            )
        return self._unwrap_response(response)

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        clean_params = {key: value for key, value in params.items() if value is not None}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}{path}",
                params=clean_params,
                headers=self._headers(),
            )
        return self._unwrap_response(response)

    async def _delete(self, path: str, body: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.request(
                "DELETE",
                f"{self.base_url}{path}",
                json={"body": body},
                headers=self._headers(),
            )
        return self._unwrap_response(response)

    async def publish_sign(self, teacher_code: str, course_code: str) -> Any:
        return await self._post(
            self._route("publish_sign"),
            {
                self._field("publish_sign", "teacher_code"): teacher_code,
                self._field("publish_sign", "course_code"): course_code,
            },
        )

    async def start_lesson(self, teacher_code: str, course_code: str) -> Any:
        return await self._post(
            self._route("start_lesson"),
            {
                self._field("start_lesson", "teacher_code"): teacher_code,
                self._field("start_lesson", "course_code"): course_code,
            },
        )

    async def end_lesson(self, lesson_id: int, teacher_code: str) -> Any:
        return await self._post(
            self._route("end_lesson"),
            {
                self._field("end_lesson", "lesson_id"): lesson_id,
                self._field("end_lesson", "teacher_code"): teacher_code,
            },
        )

    async def end_sign(self, sign_id: int, teacher_code: str) -> Any:
        return await self._post(
            self._route("end_sign"),
            {
                self._field("end_sign", "sign_id"): sign_id,
                self._field("end_sign", "teacher_code"): teacher_code,
            },
        )

    async def submit_sign(
        self,
        sign_id: int,
        student_code: str,
        sign_token: str | None = None,
    ) -> Any:
        return await self._post(
            self._route("submit_sign"),
            {
                self._field("submit_sign", "sign_id"): sign_id,
                self._field("submit_sign", "student_code"): student_code,
                self._field("submit_sign", "sign_token"): sign_token,
            },
        )

    async def manual_sign(
        self,
        sign_id: int,
        teacher_code: str,
        student_code: str,
        sign_status: int,
        leave_type: int | None = None,
    ) -> Any:
        return await self._post(
            self._route("manual_sign"),
            {
                self._field("manual_sign", "sign_id"): sign_id,
                self._field("manual_sign", "teacher_code"): teacher_code,
                self._field("manual_sign", "student_code"): student_code,
                self._field("manual_sign", "sign_status"): sign_status,
                self._field("manual_sign", "leave_type"): leave_type,
            },
        )

    async def get_sign_history(
        self,
        teacher_code: str,
        course_code: str,
        lesson_id: int | None = None,
        limit: int | None = None,
    ) -> Any:
        return await self._get(
            self._route("get_sign_history"),
            {
                self._field("get_sign_history", "teacher_code"): teacher_code,
                self._field("get_sign_history", "course_code"): course_code,
                self._field("get_sign_history", "lesson_id"): lesson_id,
                self._field("get_sign_history", "limit"): limit,
            },
        )

    async def get_sign_result(
        self,
        teacher_code: str,
        course_code: str,
        sign_id: int | None = None,
    ) -> Any:
        return await self._get(
            self._route("get_sign_result"),
            {
                self._field("get_sign_result", "teacher_code"): teacher_code,
                self._field("get_sign_result", "course_code"): course_code,
                self._field("get_sign_result", "sign_id"): sign_id,
            },
        )

    async def get_class_overview(
        self,
        teacher_code: str,
        course_code: str,
        limit: int | None = None,
    ) -> Any:
        return await self._get(
            self._route("get_class_overview"),
            {
                self._field("get_class_overview", "teacher_code"): teacher_code,
                self._field("get_class_overview", "course_code"): course_code,
                self._field("get_class_overview", "limit"): limit,
            },
        )

    async def list_prepare_courses(
        self,
        teacher_code: str,
        course_source: int | None = None,
    ) -> Any:
        return await self._get(
            self._route("list_prepare_courses"),
            {
                self._field("list_prepare_courses", "teacher_code"): teacher_code,
                self._field("list_prepare_courses", "course_source"): course_source,
            },
        )

    async def list_material_directory(
        self,
        user_code: str,
        user_type: int,
        course_code: str,
        teacher_code: str,
        folder_id: int | None = -1,
    ) -> Any:
        return await self._get(
            self._route("list_material_directory"),
            {
                self._field("list_material_directory", "user_code"): user_code,
                self._field("list_material_directory", "user_type"): user_type,
                self._field("list_material_directory", "course_code"): course_code,
                self._field("list_material_directory", "teacher_code"): teacher_code,
                self._field("list_material_directory", "folder_id"): folder_id,
            },
        )

    async def get_material_directory(
        self,
        user_code: str,
        user_type: int,
        course_code: str,
        teacher_code: str,
        folder_id: int | None = -1,
    ) -> Any:
        return await self.list_material_directory(
            user_code,
            user_type,
            course_code,
            teacher_code,
            folder_id,
        )

    async def preview_material(
        self,
        user_code: str,
        user_type: int,
        course_code: str,
        teacher_code: str,
        file_path: str,
    ) -> Any:
        return await self._post(
            self._route("preview_material"),
            {
                self._field("preview_material", "user_code"): user_code,
                self._field("preview_material", "user_type"): user_type,
                self._field("preview_material", "course_code"): course_code,
                self._field("preview_material", "teacher_code"): teacher_code,
                self._field("preview_material", "file_path"): file_path,
            },
        )

    async def download_material(
        self,
        user_code: str,
        user_type: int,
        course_code: str,
        teacher_code: str,
        file_path: str,
    ) -> Any:
        return await self._post(
            self._route("download_material"),
            {
                self._field("download_material", "user_code"): user_code,
                self._field("download_material", "user_type"): user_type,
                self._field("download_material", "course_code"): course_code,
                self._field("download_material", "teacher_code"): teacher_code,
                self._field("download_material", "file_path"): file_path,
            },
        )

    async def rename_material(
        self,
        teacher_code: str,
        course_code: str,
        file_path: str,
        file_name: str,
    ) -> Any:
        return await self._post(
            self._route("rename_material"),
            {
                self._field("rename_material", "teacher_code"): teacher_code,
                self._field("rename_material", "course_code"): course_code,
                self._field("rename_material", "file_path"): file_path,
                self._field("rename_material", "file_name"): file_name,
            },
        )

    async def rename_material_file(
        self,
        teacher_code: str,
        course_code: str,
        file_path: str,
        file_name: str,
    ) -> Any:
        return await self.rename_material(
            teacher_code,
            course_code,
            file_path,
            file_name,
        )

    async def move_material(
        self,
        teacher_code: str,
        course_code: str,
        file_path: str,
        target_folder_id: int,
    ) -> Any:
        return await self._post(
            self._route("move_material"),
            {
                self._field("move_material", "teacher_code"): teacher_code,
                self._field("move_material", "course_code"): course_code,
                self._field("move_material", "file_path"): file_path,
                self._field("move_material", "target_folder_id"): target_folder_id,
            },
        )

    async def move_material_file(
        self,
        teacher_code: str,
        course_code: str,
        file_path: str,
        target_folder_id: int,
    ) -> Any:
        return await self.move_material(
            teacher_code,
            course_code,
            file_path,
            target_folder_id,
        )

    async def delete_material(
        self,
        teacher_code: str,
        course_code: str,
        file_path: str,
    ) -> Any:
        return await self._delete(
            self._route("delete_material"),
            {
                self._field("delete_material", "teacher_code"): teacher_code,
                self._field("delete_material", "course_code"): course_code,
                self._field("delete_material", "file_path"): file_path,
            },
        )

    async def delete_material_file(
        self,
        teacher_code: str,
        course_code: str,
        file_path: str,
    ) -> Any:
        return await self.delete_material(teacher_code, course_code, file_path)

    async def create_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_name: str,
        parent_id: int | None = -1,
    ) -> Any:
        return await self._post(
            self._route("create_folder"),
            {
                self._field("create_folder", "teacher_code"): teacher_code,
                self._field("create_folder", "course_code"): course_code,
                self._field("create_folder", "parent_id"): parent_id,
                self._field("create_folder", "folder_name"): folder_name,
            },
        )

    async def create_material_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_name: str,
        parent_id: int | None = -1,
    ) -> Any:
        return await self.create_folder(
            teacher_code,
            course_code,
            folder_name,
            parent_id,
        )

    async def rename_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_id: int,
        folder_name: str,
    ) -> Any:
        return await self._post(
            self._route("rename_folder"),
            {
                self._field("rename_folder", "teacher_code"): teacher_code,
                self._field("rename_folder", "course_code"): course_code,
                self._field("rename_folder", "folder_id"): folder_id,
                self._field("rename_folder", "folder_name"): folder_name,
            },
        )

    async def rename_material_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_id: int,
        folder_name: str,
    ) -> Any:
        return await self.rename_folder(
            teacher_code,
            course_code,
            folder_id,
            folder_name,
        )

    async def move_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_id: int,
        target_parent_id: int | None = -1,
    ) -> Any:
        return await self._post(
            self._route("move_folder"),
            {
                self._field("move_folder", "teacher_code"): teacher_code,
                self._field("move_folder", "course_code"): course_code,
                self._field("move_folder", "folder_id"): folder_id,
                self._field("move_folder", "target_parent_id"): target_parent_id,
            },
        )

    async def move_material_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_id: int,
        target_parent_id: int | None = -1,
    ) -> Any:
        return await self.move_folder(
            teacher_code,
            course_code,
            folder_id,
            target_parent_id,
        )

    async def copy_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_id: int,
        target_parent_id: int | None = -1,
    ) -> Any:
        return await self._post(
            self._route("copy_folder"),
            {
                self._field("copy_folder", "teacher_code"): teacher_code,
                self._field("copy_folder", "course_code"): course_code,
                self._field("copy_folder", "folder_id"): folder_id,
                self._field("copy_folder", "target_parent_id"): target_parent_id,
            },
        )

    async def copy_material_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_id: int,
        target_parent_id: int | None = -1,
    ) -> Any:
        return await self.copy_folder(
            teacher_code,
            course_code,
            folder_id,
            target_parent_id,
        )

    async def delete_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_id: int,
    ) -> Any:
        return await self._delete(
            self._route("delete_folder"),
            {
                self._field("delete_folder", "teacher_code"): teacher_code,
                self._field("delete_folder", "course_code"): course_code,
                self._field("delete_folder", "folder_id"): folder_id,
            },
        )

    async def delete_material_folder(
        self,
        teacher_code: str,
        course_code: str,
        folder_id: int,
    ) -> Any:
        return await self.delete_folder(teacher_code, course_code, folder_id)
