import math
import logging
from uuid import uuid4

from chromadb import ClientAPI, Settings
from chromadb.utils import embedding_functions
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
import chromadb
from sentence_transformers import SentenceTransformer

from ..config import chroma_url, embeddings_name
from ..file_process.document_processor import SmartDocumentProcessor

logger = logging.getLogger(__name__)

from chromadb.api.types import EmbeddingFunction, Embeddings
from sentence_transformers import SentenceTransformer
from typing import List




def chroma_add(collection,dabase_name,embedding_model,documents,file_index,input_timestamp,batch_size = 5000):
    """
    chroma写入逻辑
    :param collection: chroma客户端
    :param dabase_name: 数据库名称
    :param embedding_model: 语义模型
    :param documents: 分片文件集
    :param file_index: 文件id
    :param input_timestamp: 写入时间
    :param batch_size: 批次处理大小
    :return:
    """
    # 指定对应数据库名称

    mata_data_list = []
    page_content_list = []
    ids = []
    embedding_list = []
    for chunck in documents:
        mata_data_list.append(chunck.metadata)
        embeddings = embedding_model.embed_documents([chunck.page_content])
        embedding_list.extend(embeddings)
        page_content_list.append(chunck.page_content)
        ids.append(str(uuid4()))

    n_batches = math.ceil(len(ids) / batch_size)

    for i in range(n_batches):
        start_index = i * batch_size
        end_index = min((i + 1) * batch_size, len(ids))
        collection.add(
            documents=page_content_list[start_index:end_index],
            metadatas=mata_data_list[start_index:end_index],
            ids=ids[start_index:end_index],
            embeddings=embedding_list[start_index:end_index]
        )

    delete_result = collection.get(where={"$and": [{"id": file_index},
                                                   {"input_timestamp": {
                                                       "$lt": input_timestamp
                                                   }}]})
    delete_ids = delete_result['ids']
    if len(ids) > batch_size:
        delete_batch = math.ceil(len(delete_ids) / batch_size)
        for i in range(delete_batch):
            start_index = i * batch_size
            end_index = min((i + 1) * batch_size, len(delete_ids))
            chroma_delete(collection,delete_ids[start_index:end_index])
    else:
        chroma_delete(collection,delete_ids)
    input_chunk_size = len(page_content_list)
    result = collection.get(where={"id": file_index}, include=["documents"])

    if input_chunk_size == len(result.get("ids")):
        logger.info(
            f"文件写入chroma库成功，文件保存的数据库名:{dabase_name},总共存入切片大小为:{input_chunk_size}")
        return True
    else:
        raise ValueError(
            f"文件切片写入chroma库数量与系统切片数量不匹配，写入chroma库数量:{len(result)},系统切片数量为:{input_chunk_size}")

def chroma_delete(collection,ids):
    """
    chroma数据库删除操作
    :param client: chroma客户端
    :param ids: 数据id集合
    :return:
    """
    if len(ids) == 0:
        return
    collection.delete(ids=ids)


def chroma_add_process(model_name,file_params,batch_size=32):
    """
    chroma文件写入总流程
    :param model_name: 语义模型名称
    :param file_params: 文件请求体
    :param batch_size: 批次处理大小
    :return:
    """

    embedding_model = HuggingFaceEmbeddings(model_name=model_name,
                                            model_kwargs={"device": "cuda"},
                                            encode_kwargs={"batch_size": batch_size,
                                                           'normalize_embeddings': True}, )

    persistent_client = chromadb.PersistentClient(chroma_url)

    processor = SmartDocumentProcessor(embedding_model)

    database_name = None
    collection = None

    for file_param in file_params:
        chunks, input_timestamp = processor.process_documents(file_param)
        index_name = file_param['databaseName']
        file_index = file_param['id']

        if database_name != index_name:
            collection = persistent_client.get_or_create_collection(name=index_name)

        chroma_add(collection, index_name, embedding_model, chunks, file_index, input_timestamp)




def chroma_repalce_process(model_name,file_params,batch_size=32,operate_batch_size=5000):
    """
    chroma替换文件逻辑
    :param model_name: 语义模型名称
    :param file_params: 文件请求体
    :param batch_size: 模型操作批次大小
    :param operate_batch_size: 文件处理批次大小
    :return:
    """
    embedding_model = HuggingFaceEmbeddings(model_name=model_name,
                                            model_kwargs={"device": "cuda"},
                                            encode_kwargs={"batch_size": batch_size,
                                                           'normalize_embeddings': True}, )
    persistent_client = chromadb.PersistentClient(chroma_url)

    processor = SmartDocumentProcessor(embedding_model)

    database_name = None
    collection = None
    for file_param in file_params:

        chunks, input_timestamp = processor.process_documents(file_param)
        index_name = file_param['databaseName']
        file_index = file_param['newId']

        if database_name != index_name:
            collection = persistent_client.get_or_create_collection(name=index_name)

        delete_result = collection.get(where={"id":file_param['pastId']},include=["documents"])
        delete_ids = delete_result['ids']
        if len(delete_ids) > operate_batch_size:
            delete_batch = math.ceil(len(delete_ids) / operate_batch_size)
            for i in range(delete_batch):
                start_index = i * operate_batch_size
                end_index = min((i + 1) * operate_batch_size, len(delete_ids))
                chroma_delete(collection, delete_ids[start_index:end_index])
        else:
            chroma_delete(collection, delete_ids)
        chroma_add(collection, index_name, embedding_model, chunks, file_index, input_timestamp)


def chroma_query_demo():
     text = '中国联通的在不在名单上?'

     embedding_model = HuggingFaceEmbeddings(model_name=embeddings_name,
                                             model_kwargs={"device": "cuda"},
                                             encode_kwargs={"batch_size": 32,
                                                            'normalize_embeddings': True}, )
     query_embeddings = embedding_model.embed_query(text)


     persistent_client = chromadb.PersistentClient(chroma_url)
     collection = persistent_client.get_or_create_collection('langchain')

     response = collection.query(query_embeddings=query_embeddings)
     print(response)


def chroma_query_limit_check(collection,file_id,operate_batch_size=5000):
    delete_result = collection.get(where={"id":file_id},include=["documents"])
    delete_ids = delete_result['ids']
    if len(delete_ids) > operate_batch_size:
        delete_batch = math.ceil(len(delete_ids) / operate_batch_size)
        return delete_ids,delete_batch,operate_batch_size
    else:
        return delete_ids,0,0


     #
     # Settings.chroma_api_impl = "chromadb.api.segment.SegmentAPI"
     # Settings.chroma_db_impl = "duckdb+parquet"
     # Settings.persist_directory = chroma_url
     # persistent_client = chromadb.PersistentClient()
     # collection = persistent_client.get_or_create_collection(name="langchain")
     # result = collection.get(where={"id": "103"}, include=["documents"])
     # print(result.get("ids"))
     # ids = result.get("ids")
     # max_len = 8000
     # if len(ids) > max_len:
     #    delete_batch = math.ceil(len(ids) / max_len)
     #    for i in range(delete_batch):
     #        start_index = i*max_len
     #        end_index = min((i+1)*max_len,len(ids))
     #        chroma_delete(persistent_client,'langchain',ids[start_index:end_index])
