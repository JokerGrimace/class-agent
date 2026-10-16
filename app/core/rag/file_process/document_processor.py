import hashlib
import io
import logging
import os
import sys
import time
from  datetime import datetime
from hashlib import md5
from typing import List

import bs4
import requests
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader, UnstructuredMarkdownLoader, \
    UnstructuredPowerPointLoader, CSVLoader
from langchain_core.documents import Document
from langchain_experimental.text_splitter import SemanticChunker
from langchain_community.embeddings import HuggingFaceEmbeddings
# 现有环境使用 LangChain 1.x，文本分块器由其独立包提供，沿用原分块类与参数。
from langchain_text_splitters import RecursiveCharacterTextSplitter
import re

from transformers import AutoTokenizer, AutoModel

from ..config import load_config, embeddings_name, html_embedding, mode
from .md_processor import MarkdownProcessor

logger = logging.getLogger(__name__)

"""
1、根据文件后缀选择读取文件方式


"""


class SmartDocumentProcessor:
    def __init__(self,embedding_model,html_embeddings = html_embedding):
        """
        :param embedding_model: 选用的embedding模型
        :param file_param: 上传的文件参数
        :param html_embeddings:网页内容语义判断embedding模型
        """

        self.embed_model =  embedding_model

        # 语义分块器
        self.semantic_spliter = SemanticChunker(
            embeddings=self.embed_model,  # 使用我们的嵌入模型
            breakpoint_threshold_amount=96,  # 设置断点阈值，块大小控制
            sentence_split_regex=r'(?<=[.!?！？。])\s*(?!$)',    #按中英文标点分句
            add_start_index=True  # 启用添加起始索引功能      记录位置方便溯源
        )

        # 段落分块器 保持段落完整性 父块保上下文，子块保检索精度
        self.splitter = RecursiveCharacterTextSplitter(keep_separator="end",
            chunk_size=512,add_start_index=True)

        #小块切分器，用于把大块切分成更小的子块
        self.child_splitter = RecursiveCharacterTextSplitter(keep_separator="end",
            chunk_size=200,add_start_index=True)

        #父块切分器，用于保留更大的上下文
        self.parent_splitter = RecursiveCharacterTextSplitter(keep_separator="end",chunk_size=2000,add_start_index=True)


        # 本地备课文件不需要网页模型；仅进入 HTML 分支时加载，避免无关模型阻断 TXT/MD 摄取。
        self.html_embeddings = html_embeddings
        self.html_tokenizer = None
        self.html_model = None




    # 可加载的文件方式
    def load_file(self, file_path: str) -> List[Document]:
        """根据文件类型选择合适的加载器"""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == '.docx':
            loader = Docx2txtLoader(file_path)
        elif ext == '.txt':
            loader = TextLoader(file_path, encoding='utf-8')
        elif ext == '.pptx':
            loader = UnstructuredPowerPointLoader(file_path)
        elif ext == '.csv':
            loader = CSVLoader(file_path)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

        return loader.load()


    # 文件内容过滤方法
    def file_filter(self,text):
        def is_heading(line: str) -> bool:
            """
            判断一行是否为标题行
            :param line: 单行文本
            :return: True（标题行）/False（非标题行）
            """
            stripped = line.strip()
            # 空行直接排除
            if not stripped:
                return False
            # 规则1：长度不超过阈值（例如30字符）
            if len(stripped) > 30:
                return False
            # 规则2：不以段落性标点结尾（如句号、逗号等）
            if stripped.endswith(('。', '，', '.', ',', '；', ';')):
                return False
            return True


        # 1. 压缩多余换行：连续3+个\n -> 替换为2个（保留段落分隔）
        text = re.sub(r'\n{3,}', '\n\n', text)     #把 3 个以上连续换行压成 2 个，保留段落感，但去掉多余空白
        # 英文词界和课程代码缩进依赖行内空格/制表符，清洗时不得把有效内容连接成另一段文本。
        lines = text.split('\n')
        max_text_len = 0
        parts = []
        # 2. 遍历每一行
        for i, line in enumerate(lines):
            max_text_len = max(max_text_len,len(line))
            parts.append(line)  # 添加当前行内容
            if i < len(lines) - 1:  # 非最后一行
                # 3. 判断当前行类型
                if ''==line:             # 空行：保留换行
                   parts.append('\n')

                elif is_heading(line):
                    parts.append('\n')  # 标题行：保留换行

                else:
                    parts.append(' ')  # 普通行：换行替换为空格

         # 4. 合并所有部分
        return ''.join(parts),max_text_len       # 返回处理后的文本和最大行长度（用于后续分块大小调整）

    # 文件处理方式
    def process_file(self, file_path: str,file_param) -> List[Document]:
        """处理单个文件，按段落、语义、token三级分块"""
        # 1. 加载文档
        docs = self.load_file(file_path)    #加载文件内容，返回Document对象列表
        max_len = 0
        if file_param:
            for doc in docs:
                doc.page_content,max_paragraph_len = self.file_filter(doc.page_content) #对文档内容进行过滤处理，返回处理后的文本和最大段落长度
                max_len = max(max_paragraph_len,max_len)
                doc.metadata.update(file_param)   #把文件参数添加到文档元数据中，方便后续使用

        # print(max_len)
        # 2. 按段落分块
        spliter = RecursiveCharacterTextSplitter(keep_separator="end",chunk_size=min(max_len,512), chunk_overlap=min(max_len,512)/2)
        # 根据最大段落长度动态调整分块大小和重叠，确保段落完整性，同时控制块大小不超过512
        paragraph_docs = spliter.split_documents(docs)


        return paragraph_docs


    # 文档分块处理主方法
    def process_documents(self,file_param) -> (List,int):
        """
         加载文档
         创建加载器列表，处理知识库中的PDF和文本文件
         检查文件是否存在

        :param file_paths: 文件路径
        :return:
        """

        # 验证参数不为空
        if not file_param:
            raise ValueError("参数 'file_param' 不能为空列表或 None")

        chunks = []
        input_timestamp = int(datetime.now().timestamp() * 1000)

        file_path = file_param['filePath']

        # 判断是否为本地文件还是html网页链接
        if os.path.exists(file_path):
            # 获取文件名和后缀
            file_head, file_ext = os.path.splitext(file_path)
            file_ext = file_ext.lower()

            # 判断后缀是否为.md和.pdf格式
            if file_ext in ('.md', '.pdf'):
                # 若为pdf文件，使用minerU工具进行文件转换
                if file_ext == '.pdf':
                    # MinerU 是 PDF 专用可选依赖，非 PDF 本地文件不应在导入解析器时加载它。
                    from .pdf_processor import PDFProcessor
                    pdf_convert = PDFProcessor()
                    markdown_path = pdf_convert.process(file_path, file_head)
                    file_path = markdown_path
                # 使用markdown分块逻辑进行切分
                md_spliter = MarkdownProcessor()
                chunks.extend(md_spliter.process(file_path, file_param))

            # 解析后缀不是.md和.pdf格式的文件
            elif self.load_file(file_path):
                chunks.extend(self.process_file(file_path, file_param))

            else:
                raise ValueError(f"不支持的文件类型:目前仅支持pdf、md文件")

        # 解析html网页文件
        elif file_path.startswith("http://") or file_path.startswith("https://"):
            try:
                # 网页树分析依赖也只属于此分支，不影响本地备课文件。
                from .html_processor import html_process
                # 网页分支继续使用原来的语义模型，首次需要时才构造。
                if self.html_tokenizer is None:
                    self.html_tokenizer = AutoTokenizer.from_pretrained(self.html_embeddings)
                    self.html_model = AutoModel.from_pretrained(self.html_embeddings)
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    "Referer": "https://www.shanghai.gov.cn/",  # 添加来源页
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive"
                }
                response = requests.get(file_path, headers=headers)
                response.raise_for_status()
                result = html_process(file_path, file_param, self.html_tokenizer, self.html_model)
                chunks.extend(result)

            except requests.exceptions.HTTPError as e:
                # 处理HTTP状态码错误
                logging.error(f"{file_path} 地址无法访问，状态码: {response.status_code}")
            except requests.exceptions.RequestException as e:
                # 处理网络连接错误、超时等问题
                logging.error(f"{file_path} 连接失败: {str(e)}")
            except Exception as e:
                # 处理其他意外错误
                logging.error(f"处理 {file_path} 时发生未知错误: {str(e)}")

        else:
            logging.error(f"文件不存在: {file_path}")





        final_chunks = []
        if mode == 'rag':

            """
        父节点+子节点，子节点被命中时，是否需要带出父节点的完整信息？（用于补全子节点的信息）
        问题1：如果用于补全子节点的完整信息，那需要对章节进行分配管理，例如，如果这个章节超过了最大的token（512），那以2000为分界线进行划分，2000个内容
        """

            # 遍历每个基本块，进行二次动态分块
            for chunk in chunks:

                i = 0
                # 内容长度超过512，进入后续逻辑
                if len(chunk.page_content) > 512 :

                    chunks =  self.parent_splitter.split_documents([chunk])

                    for index,chunk in enumerate(chunks):
                        parent_id = hashlib.sha256(f"{time.time()}_{index}_parent_{chunk.page_content[:50]}".encode('utf-8')).hexdigest()
                        chunk.metadata.update({
                            "chunk_id": parent_id,
                            "input_timestamp": input_timestamp
                        })
                        final_chunks.extend([chunk])

                        child_chunks = self.child_splitter.split_documents([chunk])

                        for index,chunk in enumerate(child_chunks):
                            chunk.metadata.update({
                                "chunk_id" : hashlib.sha256(f"{parent_id}_{index}".encode()).hexdigest(),
                                "parent_id":parent_id,
                                "input_timestamp": input_timestamp
                            })
                            final_chunks.extend([chunk])

                    # 进行语义分割
                    # chunks = self.semantic_spliter.split_documents([chunk])
                    # 进行章节段落切分
                    # for temp_chunk in chunks:
                    #     final_chunks.extend(self.splitter.split_documents([temp_chunk]))

                else:
                    final_chunks.extend([chunk])


        else:
            # agent网页调用工具切分逻辑
            if len(result.page_content) > 512:
                chunks = self.semantic_spliter.split_documents([result])
                for temp_chunk in chunks:
                    final_chunks.extend(self.splitter.split_documents([temp_chunk]))


                # 如果是普通文本，设置较大的块大小和重叠
            else:
                final_chunks.extend([result])
        # 遍历最终块列表，为每个块添加元数据
        for i, chunk in enumerate(final_chunks):
            # 管理页面按真实最终切片列表只读分页；该序号不表示章节或原文页码。
            chunk.metadata["chunk_order"] = i
            chunk.metadata.setdefault("chunk_id", f"chunk_{i}")
            chunk.metadata["input_timestamp"] = input_timestamp


        logger.info(f"文章分块成功，共切分为{len(final_chunks)}块")

        return final_chunks,input_timestamp
