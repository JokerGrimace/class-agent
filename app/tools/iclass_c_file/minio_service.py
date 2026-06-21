from io import BytesIO
from pathlib import Path
from typing import Optional

from app.config import settings

WORKFLOW_OBJECT_PREFIX = "iclass/workflow"


def get_minio_client():
    from minio import Minio

    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE.lower() == "true",
    )


def download_minio_file_bytes(object_name: str) -> bytes:
    minio_client = get_minio_client()
    response = None
    try:
        response = minio_client.get_object(
            bucket_name=settings.MINIO_BUCKET,
            object_name=object_name,
        )
        return response.read()
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def build_html_object_name(source_file_path: str) -> str:
    source_path = Path(source_file_path)
    return source_path.parent.joinpath("convert", source_path.with_suffix(".html").name).as_posix()


def upload_html_for_source_file(source_file_path: str, html_text: str) -> str:
    minio_client = get_minio_client()
    target_object_name = build_html_object_name(source_file_path)
    html_bytes = html_text.encode("utf-8")
    html_stream = BytesIO(html_bytes)
    minio_client.put_object(
        bucket_name=settings.MINIO_BUCKET,
        object_name=target_object_name,
        data=html_stream,
        length=len(html_bytes),
        content_type="text/html; charset=utf-8",
    )
    return target_object_name


def build_workflow_object_name(workflow_file_name: str) -> str:
    file_name = Path(workflow_file_name).name
    if not file_name:
        raise ValueError("workflow_file_name must not be blank")
    if Path(file_name).suffix.lower() != ".md":
        raise ValueError(f"Workflow file must be a markdown file (.md), got: {workflow_file_name}")
    return f"{WORKFLOW_OBJECT_PREFIX}/{file_name}"


def upload_workflow_file(local_file_path: str, workflow_file_name: Optional[str] = None) -> str:
    local_path = Path(local_file_path)
    target_object_name = build_workflow_object_name(workflow_file_name or local_path.name)
    workflow_bytes = local_path.read_bytes()
    workflow_stream = BytesIO(workflow_bytes)
    get_minio_client().put_object(
        bucket_name=settings.MINIO_BUCKET,
        object_name=target_object_name,
        data=workflow_stream,
        length=len(workflow_bytes),
        content_type="text/markdown; charset=utf-8",
    )
    return target_object_name


def query_exist_file(source_file_path: str) -> bool:
    try:
        target_object_name = build_html_object_name(source_file_path)
        get_minio_client().stat_object(settings.MINIO_BUCKET, target_object_name)
        return True
    except:
        return False


def list_minio_files():
    from minio import S3Error

    minio_client = get_minio_client()
    bucket_name = settings.MINIO_BUCKET
    folder_name = "iclass/"

    try:
        objects = minio_client.list_objects(
            bucket_name=bucket_name,
            prefix=folder_name,
            recursive=True,
        )
        return [obj.object_name for obj in objects]
    except S3Error:
        return []
if __name__ == '__main__':
    file_lists = list_minio_files()
    for file_path in file_lists:
        print(file_path)
        print("\n")
