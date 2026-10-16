import re
import json
import logging
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path

from langchain_core.documents import Document

from ..images import build_image, serialize_images

# -*- coding: utf-8 -*-
# 日志配置
logger = logging.getLogger(__name__)


@dataclass
class Section:
    title: str
    content: str
    raw_title: str
    type: Optional[str] = None


class MarkdownProcessor:
    """Markdown 处理器：将 Markdown 解析为结构化章节，并生成可入库的 Document 列表。"""

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # 匹配 Markdown 标题行，如 `# 标题`
        self.title_pattern = re.compile(r'^(#+)\s*(\S.*?)$')

        # 匹配章节编号，如 1 / 1.2 / 1.2.3
        self.section_number_pattern = re.compile(r'^(\d+(?:\.\d+)*)(\.?)\s*(.*?)$')

        # 匹配可能的非 # 标题行，主要兼容带编号的大写标题
        self.potential_title_pattern = re.compile(
            r'^(?!#)(\d+(?:\.\d+)*)\s+([A-Z][A-Z\s\d:]+(?:\s*[A-Z][A-Za-z\s\d:]+)*)'
        )

        # 匹配图表说明行
        self.figure_table_pattern = re.compile(
            r'''
            ^(?:
                (?:Figure|Fig\.|Table|Tab\.)
                (?:\s+\(?\d+(?:\.\d+)?\)?\.?:?)
                |
                (?:IMAGE|DIAGRAM)
                (?:\s+\d+:?)
                |
                (?:Figure|Table)
                (?:\s+[IVX]+:?)
            )
            ''',
            re.IGNORECASE | re.VERBOSE
        )

        # 匹配 Markdown 图片语法
        self.image_pattern = re.compile(r'!\[(.*?)\]\((.*?)\)')

        # 匹配 LaTeX 公式块
        self.latex_block_pattern = re.compile(r'^\$\$')

        self.logger.debug("MarkdownProcessor 初始化完成")

    def parse_section_number(self, title: str):
        """解析章节标题，提取去编号后的标题文本。"""
        match = self.section_number_pattern.match(title)
        if match:
            number, dot, raw_title = match.groups()
            return raw_title.strip()
        return title.strip()

    def parse(self, content: str) -> Dict[str, Any]:
        """解析整个 Markdown 文本，输出文档标题和章节结构。"""
        lines = content.split('\n')

        result = {
            'title': '',
            'authors_info': '',
            'sections': []
        }

        current_section = None
        current_content = []
        collecting_authors = False
        has_started = False

        for line in lines:
            title_match = self.title_pattern.match(line)

            if line.strip() == '' and not title_match:
                continue

            if line.strip().startswith('#') and not title_match:
                continue

            if title_match:
                title_text = title_match.group(2).strip()

                if not has_started:
                    result['title'] = title_text
                    has_started = True
                    continue

                if current_section and not collecting_authors:
                    current_section.content = '\n'.join(current_content)
                    result['sections'].append(vars(current_section))

                raw_title = self.parse_section_number(title_text)
                current_section = Section(
                    title=title_text,
                    content='',
                    raw_title=raw_title
                )
                current_content = []

            if line and not has_started:
                has_started = True

            current_content.append(line)

        if current_section:
            current_section.content = '\n'.join(current_content)
            result['sections'].append(vars(current_section))

        if not current_section and current_content:
            current_section = Section(
                title='',
                content='',
                raw_title=''
            )
            current_section.content = '\n'.join(current_content)
            result['sections'].append(vars(current_section))

        return result

    def process(self, markdown_path: str, metadata) -> List[Document]:
        """读取 Markdown 文件，按章节整理内容，并转换为 Document 列表。"""
        documents = []

        def build_metadata(**extra_fields):
            doc_metadata = dict(metadata)
            doc_metadata.update(extra_fields)
            return doc_metadata

        try:
            markdown_path = Path(markdown_path)

            # 读取 Markdown 文件
            self.logger.info(f"开始解析Markdown文件: {markdown_path}")
            content = markdown_path.read_text(encoding='utf-8')

            # 解析 Markdown 结构
            result = self.parse(content)
            metadata['article_title'] = result['title']

            for section in result['sections']:
                current_content = ''
                related_images = []

                def append_document(page_content, images):
                    first_image_path = images[0]["path"] if images else ""
                    documents.append(Document(
                        page_content=page_content,
                        metadata=build_metadata(
                            content_title=section['title'],
                            type='text',
                            relation_img=first_image_path,
                            related_images=serialize_images(images),
                        )
                    ))

                for line in section['content'].split('\n'):
                    image_matches = list(self.image_pattern.finditer(line))
                    if image_matches:
                        related_images.extend(
                            build_image(match.group(1), match.group(2), metadata)
                            for match in image_matches
                        )
                        remaining_text = self.image_pattern.sub('', line).strip()
                        if remaining_text:
                            current_content += remaining_text + '\n'
                        continue

                    # Images immediately after a paragraph belong to that paragraph.
                    # Images before any text stay pending and belong to the following text.
                    if related_images and current_content.strip():
                        append_document(current_content, related_images)
                        current_content = ''
                        related_images = []
                    current_content = current_content + line + '\n'

                if current_content.strip():
                    append_document(current_content, related_images)
                elif related_images:
                    # Keep image-only sections searchable through their alt text/title.
                    image_text = "\n".join(
                        image["alt"] for image in related_images
                    ) or section['title']
                    append_document(image_text, related_images)

            return documents

        except Exception as e:
            self.logger.error(f"Markdown处理失败: {str(e)}", exc_info=True)
            raise
