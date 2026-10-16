import copy
import io
import json
import re
import sys
import time
import torch
from bs4 import Comment
from langchain_community.embeddings import HuggingFaceHubEmbeddings
from collections import defaultdict
from typing import List, Tuple, Dict, Optional, Any, cast
import requests
import numpy as np
from anytree import Node
import bs4
from anytree import PreOrderIter
from anytree.exporter import DotExporter
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import HTMLSectionSplitter
import os
import pathlib

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from transformers import AutoTokenizer, AutoModel

from .. import config


# 删除注释、标签中的所有属性信息、标签后无内容的标签


def simplify_html(soup, keep_attr: bool = False) -> str:
    """
    :param soup: html文件内容
    :param keep_attr: 是否保持html标签内的内容
    :return:
    """
    for script in soup(["script", "style"]):

        script.decompose() # decompose()：彻底删除标签及其内容，释放内存。
    #  remove all attributes
    # 删除标签中的所有属性信息
    if not keep_attr:
        for tag in soup.find_all(True): # find_all 递归查找 HTML文档中的所有标签节点
            tag.attrs = {} # tag.attrs 是标签的属性字典
    #  remove empty tags recursively
    # 删除所有无内容的标签
    while True:
        removed = False
        for tag in soup.find_all():
            if not tag.text.strip():
                tag.decompose()
                removed = True
        if not removed:
            break
    #  remove href attributes
    for tag in soup.find_all("a"):
        del tag["href"]
    #  remove comments
    comments = soup.find_all(string=lambda text: isinstance(text, Comment))
    for comment in comments:
        comment.extract()

    def concat_text(text):
        text = "".join(text.split("\n"))
        text = "".join(text.split("\t"))
        text = "".join(text.split(" "))
        return text

    # remove all tags with no text
    for tag in soup.find_all():
        children = [child for child in tag.contents if not isinstance(child, str)]
        if len(children) == 1:
            tag_text = tag.get_text()
            child_text = "".join([child.get_text() for child in tag.contents if not isinstance(child, str)])
            if concat_text(child_text) == concat_text(tag_text):
                tag.replace_with_children()
    #  if html is not wrapped in a html tag, wrap it

    # remove empty lines
    res = str(soup)
    lines = [line for line in res.split("\n") if line.strip()]
    res = "\n".join(lines)
    return res

def all_content(soup, meta_data):
    title = soup.title.text.strip() if soup.title else ""

    # 方法2：从ivs_title元素提取（更精确）
    title_div = soup.find('div', {'id': 'ivs_title'})
    if title_div:
        # 提取标题文本（忽略small标签中的日期信息）
        title = title_div.find(text=True, recursive=False).strip()

    meta_data['fileName'] = title
    # 提取正文内容
    content_div = soup.find('div', {'id': 'ivs_content'})
    if content_div:
        clean_text = content_div.get_text(separator='\n', strip=True)
    else:
        # 备选方案：如果找不到特定div
        clean_text = soup.body.get_text(separator='\n', strip=True)

    # 合并标题和正文内容
    full_content = f"{title}\n\n{clean_text}" if title else clean_text

    # 可选：清理多余的空白和空行
    final_content = '\n'.join([line.strip() for line in full_content.splitlines() if line.strip()])

    return final_content


def sub_block_tree(parent_tree,result_trees,max_node_words,meta_data):

    # 对table标签内容进行处理
    if parent_tree.name == 'tbody':
        soup = bs4.BeautifulSoup(str(result_trees[-1][0]), 'html.parser')
        table_title = soup.get_text(strip=True)
        table_content = "".join(str(child) for child in parent_tree.contents )
        meta_data['table_title'] = table_title
        meta_data['content'] = table_content

    # 对其他标签进行处理
    for child in parent_tree.contents:
        if isinstance(child, bs4.element.Tag):
            new_tree = child
            # 如果html有title，则视作这段标签块的标题
            if child.name and child.name == 'title':
                meta_data['fileName'] = child.text
            word_count = len(child.get_text())
            # 判断子模块是否超过512字符限制，超过进入递归
            if word_count>max_node_words:
                sub_block_tree(child,result_trees,max_node_words,meta_data)

            else:
                result_node = copy.deepcopy((new_tree,meta_data))
                result_trees.append(result_node)

            # 重置由table标签时的metadata信息
            if meta_data['content'] and child.name == 'tbody':
                meta_data['content'] = ''
            if meta_data.get('table_title') and child.name == 'tbody':
                del meta_data['table_title']

def build_block_tree(html: str,meta_data, max_node_words: int=512, zh_char=False) ->  List[Tuple[bs4.element.Tag, Dict]]:
    soup = bs4.BeautifulSoup(html, 'html.parser')
    meta_data['content'] = ''
    word_count = len(soup.get_text()) if zh_char else len(soup.get_text().split())
    # 大于512的soup内容，进入下面的递归
    if word_count > max_node_words:
        possible_trees = [(soup, [])]
        target_trees = []  # [(tag, path, is_leaf)]
        #  split the entire dom tee into subtrees, until the length of the subtree is less than max_node_words words
        #  find all possible trees
        while True:
            if len(possible_trees) == 0:
                break
            tree = possible_trees.pop(0)
            bare_word_count = 0


            #  check if the tree can be split
            for child in tree[0].contents:

                if isinstance(child, bs4.element.Tag):
                        sub_block_tree(child,target_trees,max_node_words,meta_data)

                else:
                    bare_word_count += len(str(child)) if zh_char else len(str(child).split())

    # 若soup内容不超过512直接遍历所有平级tag输出
    else:
        soup_children = [c for c in soup.contents if isinstance(c, bs4.element.Tag)]
        if len(soup_children) == 1:
            meta_data["content"] = soup_children[0]
            target_trees = [(soup_children[0], meta_data)]
        else:
            # add an html tag to wrap all children
            new_soup = bs4.BeautifulSoup("", 'html.parser')
            new_tag = new_soup.new_tag("html")
            new_soup.append(new_tag)
            for child in soup_children:
                new_tag.append(child)
            meta_data["content"] = new_tag
            target_trees = [(new_tag,meta_data)]

    return target_trees



def concat_content( block_tree, tokenizer, model, similarity_rate=0.8):
    block_tree = [
        content
        for content in block_tree
        if not (re.search(r'\|',bs4.BeautifulSoup(str(content[0]), 'html.parser').get_text(strip=True, separator="|")) and not
                content[1].get("table_title"))
    ]

    new_content = []
    # python中元组不可修改内容
    head_sentence = ""
    head_metadata = {}
    i = 0
    while (i < len(block_tree)):
        if not head_sentence:
            head_sentence = bs4.BeautifulSoup(str(block_tree[i][0]), 'html.parser').get_text(strip=True, separator="|")
            head_metadata = block_tree[i][1]
            i = i + 1
            continue
        # 同时对两个句子进行编码
        next_sentence = bs4.BeautifulSoup(str(block_tree[i][0]), 'html.parser').get_text(strip=True, separator="|")
        inputs = tokenizer(
            [head_sentence, next_sentence],  # 同时传入两个句子
            padding=True,
            truncation=True,
            return_tensors='pt'
        )

        # 使用模型获取嵌入
        with torch.no_grad():
            model_output = model(**inputs)

        # 提取 CLS token 作为句子嵌入（批量处理）
        sentence_embeddings = model_output.last_hidden_state[:, 0]  # [2, hidden_size]

        # 分离两个句子的嵌入向量
        head_embedding = sentence_embeddings[0]  # 第一个句子的嵌入
        next_embedding = sentence_embeddings[1]  # 第二个句子的嵌入

        # 计算相似度（可选）
        similarity = torch.nn.functional.cosine_similarity(head_embedding, next_embedding, dim=0)
        if similarity > similarity_rate:
            head_sentence += next_sentence
            i = i + 1
        else:
            new_content.append(Document(page_content=head_sentence, metadata=head_metadata))
            head_sentence = ""
            head_metadata = {}

    return new_content


def html_process(file_path,meta_data,html_tokenizer,html_model,similarity_rate=0.8):
    """
    :param file_path: 域名
    :param html_tokenizer: 分词模型
    :param html_model: 编码模型
    :param similarity_rate: 余弦相似度阈值，超过该阈值的句子将进行合并
    :return:
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://www.shanghai.gov.cn/",  # 添加来源页
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive"
    }
    response = requests.get(file_path, headers=headers)
    # response = requests.get(file_path)
    response = response.text
    soup = bs4.BeautifulSoup(response, 'html.parser')
    if config.mode == 'rag':
        html_content = simplify_html(soup, True)
        block_tree = build_block_tree(html_content, meta_data, zh_char=True)
        result = concat_content(block_tree, html_tokenizer, html_model, similarity_rate)
    else:
        html_content = all_content(soup, meta_data)
        meta_data['source'] = html_content
        result = Document(page_content=html_content, metadata=meta_data)

    return result


def fetch_dynamic_content(url):
    # 配置Chrome无头模式（不打开浏览器窗口）
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # 启动浏览器
    driver = webdriver.Chrome(options=chrome_options)
    driver.get(url)
    time.sleep(3)  # 等待JavaScript渲染完成（根据网速调整）

    # 获取渲染后的完整HTML
    html = driver.page_source
    driver.quit()

    # 解析内容（需根据实际渲染后的结构调整）
    soup = bs4.BeautifulSoup(html, "html.parser")
    return html





    # print(soup.title.string)
