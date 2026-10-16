import hashlib
from  datetime import datetime
import re
import json
import logging
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path
from langchain_core.documents import  Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# -*- coding: utf-8 -*-
# 配置日志
logger = logging.getLogger(__name__)

@dataclass
class Section:
    title: str            # 完整标题（包含编号和文本）
    content: str   # 章节内容，每个段落作为列表的一个元素
    raw_title: str       # 不含编号的标题文本
    type: Optional[str] = None   # 章节类型,如 'abstract', 'references'




class MarkdownProcessor:
    """Markdown处理器：将Markdown解析为结构化JSON"""
    """
        1、按#方式提取出当前标题为什么,title
        2、按\n对文章进行划分
        3、如果遇到img，进行标注，并重新生成一行

    """


    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # 匹配标题的正则表达式（匹配 # 开头的行）
        self.title_pattern = re.compile(r'^(#+)\s*(\S.*?)$')

        # 匹配可能的标题行（非 # 开头，但包含数字编号和大写标题）
        self.potential_title_pattern = re.compile(r'^(?!#)(\d+(?:\.\d+)*)\s+([A-Z][A-Z\s\d:]+(?:\s*[A-Z][A-Za-z\s\d:]+)*)')

        # 匹配Markdown格式的图片
        self.image_pattern = re.compile(r'!\[(.*?)\]\((.*?)\)')

        # 匹配数学公式块
        self.latex_block_pattern = re.compile(r'^\$\$')

        self.logger.debug("初始化Markdown处理器完成")

        # self.child_splitter = RecursiveCharacterTextSplitter(keep_separator="end",
        #                                                      chunk_size=200,chunk_overlap=20, add_start_index=True)
        #
        # self.parent_splitter = RecursiveCharacterTextSplitter(keep_separator="end", chunk_size=2000,chunk_overlap=200,
        #                                                       add_start_index=True)
        self.max_text_len = 5000

    def prase_img(self,img) -> str:
        pass

    def get_max_len(self,content):

        for line in content.split('\n'):
            self.max_text_len = max(self.max_text_len, len(line))
        return self.max_text_len

    def process(self, markdown_path: str ) -> List[Document]:
        """将原来独立的 process_markdown_file 函数集成为类方法"""
        documents = []

        try:
            markdown_path = Path(markdown_path)


            # 读取文件
            self.logger.info(f"开始解析Markdown文件: {markdown_path}")
            content = markdown_path.read_text(encoding='utf-8')


            self.max_text_len = self.get_max_len(content)
            parent_chunk = RecursiveCharacterTextSplitter(keep_separator="end",
                                                  chunk_size=self.max_text_len,chunk_overlap=self.max_text_len/5, add_start_index=True).split_text(content)

            final_chunks = []

            # 文章整体结构的切割，以父节点+子节点的逻辑切分,图片附带在里面
            for index, chunk in enumerate(parent_chunk):
                input_timestamp = int(datetime.now().timestamp() * 1000)
                parent_id = hashlib.sha256(
                    f"{input_timestamp}_{index}_parent_{chunk[:50]}".encode('utf-8')).hexdigest()
                metadata = {
                    "type":"text",
                    "chunk_id": parent_id,
                    "input_timestamp": input_timestamp,
                    "parent_id":""
                }

                final_chunks.append(Document(page_content=chunk,metadata=metadata))

                child_split_size = self.max_text_len/10
                child_chunks = RecursiveCharacterTextSplitter(keep_separator="end",
                                                  chunk_size=child_split_size,chunk_overlap=child_split_size/5, add_start_index=True).split_text(content)


                for index, chunk in enumerate(child_chunks):
                    metadata = {
                        "type": "text",
                        "chunk_id": hashlib.sha256(f"{parent_id}_{index}".encode()).hexdigest(),
                        "parent_id": parent_id,
                        "input_timestamp": input_timestamp
                    }
                    final_chunks.append(Document(page_content=chunk,metadata=metadata))

            # 如果是纯图片信息的内容
            # 获取图片信息
            md_imgs = self.image_pattern.findall(content)
            md_imgs = ''
            for img in md_imgs:
                # 使用AI对图片内容进行描述
               des = self.prase_img(img)
               metadata= {
                   "type":"img",
                   "describe":des,
                   "img_path":img
               }
               final_chunks.append(Document(page_content=des,metadata=metadata))


            return final_chunks


        except Exception as e:
            self.logger.error(f"Markdown处理失败: {str(e)}", exc_info=True)
            raise
