"""通知写入模块。

将 LLM 提取的通知卡片写入对应分类的 Markdown 文件中。
采用 YAML front matter 格式，与现有通知文档保持一致。
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


def read_front_matter(file_path: Path) -> tuple[Optional[Dict[str, Any]], str, str]:
    """读取 md 文件，返回 (front_matter 字典, front_matter 原始文本, body)。

    front matter 由开头的 --- 包裹，解析为 YAML dict。
    body 是 front matter 之后的所有内容。

    Returns:
        (parsed_dict, raw_front_matter_text, body_text)
        如果文件不存在或无 front matter，第一个元素为 None。
    """
    if not file_path.exists():
        return None, "", ""

    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("读取文件失败 %s: %s", file_path, e)
        return None, "", ""

    # 匹配 --- ... --- 的 front matter
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", raw, re.DOTALL)
    if not match:
        logger.warning("文件 %s 没有 front matter", file_path)
        return None, "", raw

    fm_raw = match.group(1)
    body = match.group(2)

    try:
        fm_dict = yaml.safe_load(fm_raw)
    except yaml.YAMLError as e:
        logger.error("解析 front matter 失败 %s: %s", file_path, e)
        return None, fm_raw, body

    return fm_dict or {}, fm_raw, body


def write_front_matter(file_path: Path, fm_dict: Dict[str, Any], body: str) -> bool:
    """将 front matter dict 和 body 写回 md 文件。

    保留 body 的格式不变，front matter 使用 YAML 序列化。
    """
    # 序列化 front matter，确保格式整洁
    fm_yaml = yaml.dump(
        fm_dict,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=200,  # 避免自动换行
    ).rstrip()

    content = f"---\n{fm_yaml}\n---\n{body}"

    try:
        file_path.write_text(content, encoding="utf-8")
        return True
    except OSError as e:
        logger.error("写入文件失败 %s: %s", file_path, e)
        return False


def is_duplicate(cards: List[Dict[str, Any]], title: str) -> bool:
    """检查是否已有相同标题的通知（去重）。"""
    for card in cards:
        if isinstance(card, dict) and card.get("title", "").strip() == title.strip():
            return True
    return False


def add_notification(
    base_dir: Path,
    category_file: str,
    card: Dict[str, Any],
) -> bool:
    """向指定分类文件中添加一条通知卡片。

    Args:
        base_dir: 通知根目录（如 /path/to/home/docs/Notification）
        category_file: 分类文件相对路径（如 Academic/Academic.md）
        card: 通知卡片数据，包含 title, detail, href, ddl, tags

    Returns:
        True 如果添加成功（或已存在则不重复添加），False 如果失败。
    """
    file_path = base_dir / category_file

    # 确保目录存在
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # 读取现有内容
    fm_dict, fm_raw, body = read_front_matter(file_path)

    if fm_dict is None:
        # 新文件
        fm_dict = {"cards": []}
        body = _default_body()

    # 确保 cards 列表存在
    cards = fm_dict.get("cards", [])
    if not isinstance(cards, list):
        cards = []
    fm_dict["cards"] = cards

    # 去重检查
    title = card.get("title", "")
    if title and is_duplicate(cards, title):
        logger.info("通知已存在，跳过: %s", title)
        return True

    # 准备卡片数据（只保留需要的字段）
    new_card: Dict[str, Any] = {
        "title": title,
        "detail": card.get("detail", ""),
        "href": card.get("href", ""),
    }

    # 只有在有值时才添加可选字段
    ddl = card.get("ddl", "")
    if ddl:
        new_card["ddl"] = ddl

    tags = card.get("tags", [])
    if tags:
        # 确保每个 tag 格式正确
        clean_tags = []
        for tag in tags:
            if isinstance(tag, dict) and "text" in tag and "class" in tag:
                clean_tags.append({"text": tag["text"], "class": tag["class"]})
        if clean_tags:
            new_card["tags"] = clean_tags

    # 将新卡片插入最前面
    cards.insert(0, new_card)

    # 写回
    if write_front_matter(file_path, fm_dict, body):
        logger.info("已添加通知到 %s: %s", category_file, title)
        return True

    return False


def _default_body() -> str:
    """生成新的通知分类页面的默认 body 内容。"""
    return """
!!! note 通知

    标题左侧的点暗了就代表通知已经截止了哦~

    如果进入后显示"404 - Not Found"，说明对应通知无链接~

{% import 'macros/card_macro.html' as card_macro %}

{{ card_macro.render_cards(cards) }}
"""
