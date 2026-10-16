import os
import logging
from pathlib import Path
from typing import List
from uuid import uuid4

from langchain_core.documents import Document
import faiss
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings

from ..config import embeddings_name, faiss_url
from ..file_process.document_processor import SmartDocumentProcessor

logger = logging.getLogger(__name__)


def faiss_add(index_name,chunks,embeddings):
    """
    faiss新增操作
    :param index_name:数据库名称
    :param chunks: 文件块列表
    :param embeddings: 语义模型
    :return:
    """
    if os.path.exists(f"{faiss_url}/{index_name}.faiss"):
        vector_store = FAISS.load_local(folder_path=faiss_url, embeddings=embeddings, index_name=index_name,allow_dangerous_deserialization=True)
        ids = [str(uuid4()) for _ in range(len(chunks))]
        vector_store.add_documents(chunks,ids=ids)
    else:
        ids = [str(uuid4()) for _ in range(len(chunks))]
        vector_store = FAISS.from_documents(chunks, embeddings,**{"ids":ids})

    vector_store.save_local(folder_path=faiss_url,index_name=index_name)

    logger.info(
                f"文件写入Faiss库成功，文件保存的数据库名:{index_name},总共存入切片大小为:{len(chunks)}")
    return True,(faiss_url,index_name,ids)

def faiss_delete(database_name:str,ids:List[str],save_path = faiss_url):
    """
    faiss删除操作
    :param save_path: 保存路径
    :param database_name: 数据库名称
    :param ids: 需删除的索引
    :return:
    """
    try:
        embeddings = HuggingFaceEmbeddings(model_name=embeddings_name,
                                           model_kwargs={"device": "cuda"},
                                           encode_kwargs={"batch_size": 32, 'normalize_embeddings': True}, )
        if os.path.exists(f"{save_path}/{database_name}.faiss"):
            vector_store = FAISS.load_local(save_path,embeddings,database_name,allow_dangerous_deserialization=True)
            vector_store.delete(ids)
            vector_store.save_local(save_path,database_name)
        else:
            raise ValueError(f"{save_path}/{database_name}.faiss文件不存在!")
    except Exception as e:
        raise ValueError(f"删除失败：{e}")


def faiss_repalce(file_params,embeddings_name,faiss_url=faiss_url):
    """
    faiss文件替换操作
    :param file_params:文本请求体
    :return:
    """
    embeddings = HuggingFaceEmbeddings(model_name=embeddings_name,
                                       model_kwargs={"device": "cuda"},
                                       encode_kwargs={"batch_size": 32, 'normalize_embeddings': True}, )

    processor = SmartDocumentProcessor(embeddings)

    faiss_message = []

    for file_param in file_params:
        database_name = file_param['databaseName']
        past_ids = file_param['pastId']
        try:

            if os.path.exists(f"{faiss_url}/{database_name}.faiss"):
                vector_store = FAISS.load_local(faiss_url, embeddings, database_name, allow_dangerous_deserialization=True)
                vector_store.delete(past_ids)
                vector_store.save_local(faiss_url, database_name)
                logger.info(f"旧文件:{database_name}删除成功！")
            else:
                raise ValueError(f"{faiss_url}/{database_name}.faiss文件不存在!")
            chunks,_ = processor.process_documents(file_param)
            _,(faiss_url, index_name, ids) = faiss_add(index_name=database_name,chunks=chunks,embeddings=embeddings)
            faiss_message.append({"faiss_url": faiss_url,
                                  "index_name": index_name,
                                  "ids": ids})


        except Exception as e:
            raise ValueError(f"更新失败！{e}")
    return faiss_message

def faiss_addprocess(file_params,embeddings_name):
    """
    faiss新增流程
    :param file_params:文件请求体
    :param  embeddings_name:模型名称
    :return:
    """
    embeddings = HuggingFaceEmbeddings(model_name=embeddings_name,
                                       model_kwargs={"device": "cuda"},
                                       encode_kwargs={"batch_size": 32, 'normalize_embeddings': True}, )

    processor = SmartDocumentProcessor(embeddings)

    faiss_message = []

    for file_param in file_params:
        chunks, _ = processor.process_documents(file_param)
        if len(chunks) == 0:
            continue
        index_name = file_param['databaseName']
        _, (faiss_url, index_name, ids) = faiss_add(index_name=index_name,chunks=chunks,embeddings=embeddings)
        faiss_message.append({"faiss_url":faiss_url,
                              "index_name":index_name,
                              "ids":ids})

    return faiss_message




    # print(vector_store.search("您好", search_type="similarity"))
    # vector_store = FAISS.load_local(folder_path=r"/path/to/faiss_data",embeddings=embeddings,allow_dangerous_deserialization=True)
    # print(vector_store.index_to_docstore_id.values())
    # vector_store.delete(['4bf61e78-046d-45e7-96f8-c77410409408'])
    # # 4bf61e78-046d-45e7-96f8-c77410409408
    # print(vector_store.index_to_docstore_id.values())
    # vector_store.save_local('langchain')
