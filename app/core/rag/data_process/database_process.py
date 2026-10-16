from typing import List, Dict

from ..config import embeddings_name, chroma_url, normal_database


def database_add_process(file_param, model_name=embeddings_name, batch_size=32):
    try:
        if file_param.inputDatabase == "":
           file_param.inputDatabase = normal_database
        if file_param.inputDatabase == 'ElasticsearchStore':
            # 每个后端仅在被选中时加载，避免无关的可选依赖阻断其他入库方式。
            from .elasticsearch_process import es_add_process

            es_add_process(model_name, file_param.fileParam, batch_size=batch_size)
        elif file_param.inputDatabase == 'Faiss':
            # FAISS 后端在调用时加载，保持 Elasticsearch-only 部署的依赖边界。
            from .faiss_process import faiss_addprocess

            return faiss_addprocess(file_param.fileParam, embeddings_name)
        elif file_param.inputDatabase == 'Chroma':
            # Chroma 后端在调用时加载，纯查询服务无需安装其摄取依赖。
            from .chroma_data_process import chroma_add_process

            chroma_add_process(model_name, file_param.fileParam, batch_size)
        # 返回成功响应
        return True
    except Exception as e:
        # 返回失败响应（可包含错误信息）
        print({"success": False, "error": str(e)})
        return False


def database_replace_process(file_param, model_name=embeddings_name, batch_size=32):
    try:
        if file_param.inputDatabase == 'ElasticsearchStore':
            # 替换流程与新增流程使用相同的按需后端加载策略。
            from .elasticsearch_process import es_repalce_process

            es_repalce_process(model_name, file_param.fileParam, batch_size)
        elif file_param.inputDatabase == 'Faiss':
            # 只有执行 FAISS 替换时才引入本地索引依赖。
            from .faiss_process import faiss_repalce

            return faiss_repalce(file_params=file_param.fileParam, embeddings_name=model_name)
        elif file_param.inputDatabase == 'Chroma':
            # 只有执行 Chroma 替换时才引入其客户端和嵌入依赖。
            from .chroma_data_process import chroma_repalce_process

            chroma_repalce_process(model_name, file_param.fileParam, batch_size)
        # 返回成功响应
        return True
    except Exception as e:
        # 返回失败响应（可包含错误信息）
        print({"success": False, "error": str(e)})
        return False


def database_delete_process(file_param, inputDatabase):
    try:
        if inputDatabase == 'ElasticsearchStore':
            # 删除逻辑同样延迟加载，避免模块导入时初始化全部后端。
            from .elasticsearch_process import es_delete

            deleted = es_delete(file_param["databaseName"], file_param["id"])
            return deleted > 0
        elif inputDatabase == 'Faiss':
            # FAISS 删除只依赖当前选择的后端实现。
            from .faiss_process import faiss_delete

            faiss_delete(database_name=file_param["databaseName"], ids=file_param["id"])
        elif inputDatabase == 'Chroma':
            # Chroma 删除需要客户端和批量删除辅助函数，均在此分支内加载。
            import chromadb

            from .chroma_data_process import chroma_delete, chroma_query_limit_check

            persistent_client = chromadb.PersistentClient(chroma_url)

            collection = persistent_client.get_or_create_collection(file_param["databaseName"])
            delete_ids, delete_batch, operate_batch_size = chroma_query_limit_check(collection, file_param["id"])
            if delete_batch > 0:
                for i in range(delete_batch):
                    start_index = i * operate_batch_size
                    end_index = min((i + 1) * operate_batch_size, len(delete_ids))
                    chroma_delete(collection, delete_ids[start_index:end_index])
            else:
                chroma_delete(collection, delete_ids)
        # 返回成功响应
        return True
    except Exception as e:
        # 返回失败响应（可包含错误信息）
        return False


class InputDatabaseRequest():
    inputDatabase: str
    fileParam: List[Dict]


class ReplaceDatabaseRequest():
    inputDatabase: str
    fileParam: List[Dict]


class DeleteDatabaseRequest():
    inputDatabase: str
    fileParam: List[Dict]


    # file_params = {"id": "101",
    #          "databaseName": "policy"}
    #
    # database_delete_process(file_params, "ElasticsearchStore")
