import concurrent.futures
import logging
import os
import pickle
import threading
import time
from threading import local
from typing import Dict

from langchain_community.embeddings import HuggingFaceEmbeddings
from elasticsearch import Elasticsearch

from ..config import load_config, embeddings_name, es_url, es_user, es_password
from ..file_process.document_processor import SmartDocumentProcessor

logger = logging.getLogger(__name__)


def get_es_url():
    if es_url.startswith(("http://", "https://")):
        return es_url
    return f"http://{es_url}"


def create_elasticsearch_client():
    """创建不会把凭据拼进 URL 的 Elasticsearch 客户端。"""
    # 用户名和密码必须成对配置，避免部署时静默使用半套认证信息。
    client_options = {
        "hosts": [get_es_url()],
        "verify_certs": False,
        "ssl_show_warn": False,
    }
    if es_user and es_password:
        client_options["basic_auth"] = (es_user, es_password)
    elif es_user or es_password:
        raise RuntimeError(
            "ICLASS_RAG_ES_USER and ICLASS_RAG_ES_PASSWORD must be configured together"
        )
    return Elasticsearch(**client_options)

"""
能不能在添加时，对字段做索引类型设定
"""


def es_add(chunks, embedding, index_name):
    """
    es库新增操作
    :param chunks: 分片集合
    :param embedding: 语义向量模型
    :param index_name: 数据库名称
    :return: bool,_
    """
    # 旧向量库封装只在旧写入路径真正使用时加载，纯清理不依赖此可选组件。
    from langchain_elasticsearch import ElasticsearchStore
    vector_store = ElasticsearchStore(
        index_name,
        embedding=embedding,
        es_url=get_es_url(),
        es_user=es_user,
        es_password=es_password,
        es_params={'verify_certs': False}

    )
    vector_store.add_documents(chunks, bulk_kwargs={
        "chunk_size": 100,
        "max_chunk_bytes": 100000000
    })
    # 检查是否写入大小与切片大小一致
    return True, None


def es_query(es, index_name, file_index, input_cnt, input_timestamp=None):
    """
    es库查询操作，用于查询插入是否与分块大小一致
    :param es: es客户端
    :param index_name:数据库名称
    :param file_index: 文件id
    :param input_cnt: 文件分块长度
    :return:
    """

    # 要精确匹配的值（包含反斜杠需要转义）
    # filepaths = ['knowledge_base\长宁区鼓励科技创新的实施办法.pdf','knowledge_base\\test.txt']
    # source_value = r"knowledge_base\长宁区鼓励科技创新的实施办法.pdf"

    # 构建 term 查询
    query = {
        "query": {
            "term": {
                "metadata.id.keyword": file_index  # 使用 .keyword 确保精确匹配
            }

        }
    }
    # 校验本次写入的切片，而不是在清旧之后用全部版本的数量伪装本次成功。
    if input_timestamp is not None:
        query = {"query": {"bool": {"filter": [
            {"term": {"metadata.id.keyword": file_index}},
            {"term": {"metadata.input_timestamp": input_timestamp}},
        ]}}}

    # 执行查询
    response = es.count(index=index_name, body=query)
    cnt = response["count"]
    if cnt == input_cnt:
        logger.info(
            f"文件写入elastic库成功，文件保存index为:{index_name},总共存入切片大小为:{input_cnt}")
        return True
    else:
        raise ValueError(
            f"文件切片写入elastic库数量与系统切片数量不匹配，写入elastic库数量:{cnt},实际切分后数量为:{input_cnt}")


def es_replace(es, index_name, file_index, input_time):
    """
    es库文件替换,用于重复上传文件的替换
    :param es:es客户端
    :param index_name:数据库名称
    :param file_index: 文件id
    :param input_time: 最新文件上传时间
    :return:
    """
    # source_value = r"knowledge_base\长宁区鼓励科技创新的实施办法.pdf"  # 要更新的source值

    # 更新脚本 - 示例：添加新的metadata字段
    delete_query = {
        "query": {
            "bool": {
                "must": [
                    {
                        "term": {  # 使用 terms 查询匹配多个 source 值
                            "metadata.id.keyword": file_index
                        }
                    },
                    {
                        "range": {  # 使用 range 查询匹配 input_time < 当前时间
                            "metadata.input_timestamp": {
                                "lt": input_time
                            }
                        }
                    }
                ]
            }
        }
    }

    # 执行删除
    response = es.delete_by_query(
        index=index_name,
        body=delete_query,
        wait_for_completion=True,
        refresh=True
    )

    logger.info(response.body)
    return True


def es_delete(index_name, file_id):
    """
    es库对指定数据库文件id的删除操作
    :param index_name: 数据库名称
    :param file_id: 文件id
    :return:
    """
    # 复用统一客户端构造，防止特殊字符凭据破坏 URL 或出现在日志中。
    es = create_elasticsearch_client()
    # source_value = r"knowledge_base\长宁区鼓励科技创新的实施办法.pdf"  # 要更新的source值

    if isinstance(file_id, str):
        # 更新脚本 - 示例：添加新的metadata字段
        delete_query = {
            "query": {
                "term": {  # 使用 terms 查询匹配多个 source 值
                    "metadata.id.keyword": file_id
                }
            }
        }

    else:
        delete_query = {
            "query": {
                "terms": {  # 使用 terms 查询匹配多个 source 值
                    "metadata.id.keyword": file_id
                }
            }
        }
    response = es.delete_by_query(
        index=index_name,
        body=delete_query,
        wait_for_completion=True
    )

    logger.info(response.body)
    return response.body.get("deleted", 0)


def es_repalce_process(model_name, file_params, batch_size=32):
    """
    es新老文件替换，删除指定数据库下的文件id，添加指定数据库下的文件id
    :param model_name: 语义模型
    :param file_params: 文件请求体
    :param batch_size: 批次处理大小
    :return:
    """
    # 替换流程与其他写操作共享相同的认证和 TLS 配置。
    es = create_elasticsearch_client()

    embedding_model = HuggingFaceEmbeddings(model_name=model_name,
                                            model_kwargs={"device": "cpu"},
                                            encode_kwargs={"batch_size": batch_size, 'normalize_embeddings': True}, )

    processor = SmartDocumentProcessor(embedding_model)
    for file_param in file_params:
        # 旧替换协议的逻辑 ID 必须已经与 newId 一致，不猜测或重写缺失/不匹配字段。
        if file_param.get("id") != file_param["newId"]:
            raise ValueError("replacement id must exactly match newId")
        chunks, input_timestamp = processor.process_documents(file_param)
        index_name = file_param["databaseName"]
        file_index = file_param["newId"]
        # es_add 返回 (bool, error)，必须检查首项，非空失败元组不能被当作成功。
        success, _ = es_add(chunks, embedding_model, index_name)
        if not success:
            raise ValueError(f"Failed to add chunks to index {index_name}")
        if not es_query(es, index_name, file_index, len(chunks), input_timestamp):
            raise ValueError(f"Failed to query chunks to index {index_name}")
        # 新版确认有效后才清旧；同一逻辑 ID 只能按旧时间清理，不能整文件删除新片段。
        if file_param["pastId"] == file_index:
            es_replace(es, index_name, file_index, input_timestamp)
        else:
            es_delete(index_name, file_param["pastId"])


def es_add_process(model_name, file_params, batch_size=32):
    """
    es新增文件流程，先将文件写入，再删除写入时间前的数据（防止有旧数据重复），最后匹配需插入条数和数据库内的条数是否一致
    :param model_name: 语义模型
    :param file_params: 文件请求体
    :param batch_size: 批次处理大小
    :return:
    """
    # 新增流程与查询流程复用同一客户端构造逻辑。
    es = create_elasticsearch_client()

    embedding_model = HuggingFaceEmbeddings(model_name=model_name,
                                            model_kwargs={"device": "cpu"},
                                            encode_kwargs={"batch_size": batch_size, 'normalize_embeddings': True}, )

    processor = SmartDocumentProcessor(embedding_model)
    for file_param in file_params:

        chunks, input_timestamp = processor.process_documents(file_param)
        index_name = file_param["databaseName"]
        file_index = file_param["id"]
        # 明确读取写入元组的成功字段，在计数确认前保留旧片段。
        success, _ = es_add(chunks, embedding_model, index_name)
        if not success:
            raise ValueError(f"Failed to add chunks to index {index_name}")
        if not es_query(es, index_name, file_index, len(chunks), input_timestamp):
            raise ValueError(f"Failed to query chunks to index {index_name}")
        if not es_replace(es, index_name, file_index, input_timestamp):
            raise ValueError(f"Failed to delete chunks to index {index_name}")


"""
1、为了查找是否存在相同的文本文件
2、若存在相同的文本文件，获取对应的ids TODO:是否可以改成时间方式,或进行分批删除处理
3、在完成上传文件后，进行删除之前的内容
"""

    # #1、初始化RAG数据库，如果没有就返回空
    # retriever = HybridRetriever(model_name=model_path)
    # #2、如果需要添加RAG文件，将数据写入
