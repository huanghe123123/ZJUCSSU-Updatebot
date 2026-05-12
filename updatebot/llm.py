"""大模型集成模块。

通过 OpenAI 兼容 API 调用 LLM 进行通知识别、分类和信息提取。
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import date
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


def _build_system_prompt(categories: List[Dict[str, Any]]) -> str:
    """构建系统提示词，包含所有分类信息和输出格式说明。"""

    cat_lines = []
    for cat in categories:
        cat_lines.append(f"- **{cat['key']}** ({cat['name']}): {cat['description']}")

    cats_text = "\n".join(cat_lines)

    return f"""你是一个校园通知分类助手。你的任务是判断输入的消息是否为校园通知，如果是则对其进行分类和信息提取。

## 通知分类

{cats_text}

## 判断标准

以下情况**不是**通知，应标记 is_notification: false：
- 闲聊、问候、日常对话
- 纯提问、求助
- 广告、垃圾信息
- 与浙大校园无关的内容
- 表情包或纯图片（无实质文字）
- 消息内容过于简短且无实质信息（如"收到"、"好的"）

以下情况**是**通知：
- 教务/学工/后勤等部门发布的正式公告
- 课程、考试、选课相关的提醒
- 奖学金、助学金、评优等申报通知
- 学术讲座、竞赛、科研项目报名通知
- 就业、实习、招聘相关信息
- 校园活动、文体赛事通知
- 安全、后勤、放假等校园运行信息
- 对外交流、暑期项目等通知

## 输出格式

你必须严格输出以下 JSON 格式，不要输出任何其他内容：

```json
{{
  "is_notification": true/false,
  "reason": "如果不是通知，简要说明原因；如果是通知则填null",
  "category": "分类key，如 Academic；如is_notification为false则填null",
  "card": {{
    "title": "通知标题（简洁概括，去除多余的'【通知】'等前缀标记）",
    "detail": "通知摘要或关键信息（100字以内，保留时间、地点、联系方式等核心要素）",
    "href": "通知中提到的链接URL，没有则为空字符串",
    "ddl": "截止日期（YYYY-MM-DD格式），没有明确截止日期则留空字符串",
    "tags": [
      {{"text": "标签文本", "class": "tag-category"}}
    ]
  }}
}}
```

## 标签说明
- tags 数组必须包含 1-3 个标签
- tag-category: 用于分类标签（如"选课"、"奖学金"、"竞赛"）
- tag-priority: 用于重要/紧急标记（如"重要"、"紧急"）
- tag-target: 用于面向对象标记（如"本科生"、"2022级"、"研究生"）
- 每个通知至少有一个 tag-category 标签

## 注意事项
- 因为消息来自钉钉群通知转发，可能包含"转发自..."等前缀，请忽略这些转发标识
- 如果消息中包含多个通知，只提取最核心的那个
- 日期统一使用 YYYY-MM-DD 格式
- 如果通知没有明确日期，ddl 留空
"""


def _build_user_message(content: str) -> str:
    """构建用户消息（包含待分类的消息内容）。"""
    today = date.today().isoformat()
    return f"今天的日期是 {today}。请判断以下消息是否为校园通知：\n\n{content}"


async def classify_message(
    content: str,
    model_url: str,
    model_name: str,
    api_key: str,
    categories: List[Dict[str, Any]],
    timeout: float = 30.0,
    max_retries: int = 2,
) -> Optional[Dict[str, Any]]:
    """调用 LLM 对消息进行分类和信息提取。

    Args:
        content: 待分类的消息文本
        model_url: LLM API 地址
        model_name: 模型名称
        api_key: API 密钥
        categories: 分类配置列表
        timeout: 请求超时时间（秒）
        max_retries: 最大重试次数

    Returns:
        解析后的结果字典，格式：
        {
            "is_notification": bool,
            "reason": str | None,
            "category": str | None,
            "card": {
                "title": str,
                "detail": str,
                "href": str,
                "ddl": str,
                "tags": [{"text": str, "class": str}]
            }
        }
        如果 LLM 调用失败或解析失败，返回 None。
    """
    # 确保 URL 以 /v1 结尾且不包含尾部斜杠
    base_url = model_url.rstrip("/")
    if not base_url.endswith("/chat/completions"):
        if base_url.endswith("/v1"):
            api_url = f"{base_url}/chat/completions"
        else:
            api_url = f"{base_url}/v1/chat/completions"
    else:
        api_url = base_url

    system_prompt = _build_system_prompt(categories)
    user_message = _build_user_message(content)

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        for attempt in range(max_retries + 1):
            try:
                logger.info("调用 LLM 进行分类 (尝试 %d/%d)...", attempt + 1, max_retries + 1)
                response = await client.post(api_url, json=payload, headers=headers)

                if response.status_code != 200:
                    error_body = response.text[:500]
                    logger.error("LLM API 返回错误 %d: %s", response.status_code, error_body)
                    if attempt < max_retries:
                        continue
                    return None

                data = response.json()
                raw = data["choices"][0]["message"]["content"]

                # 尝试从回复中提取 JSON
                result = _parse_llm_response(raw)
                if result is not None:
                    return result

                logger.warning("JSON 解析失败, 尝试重试...")
                if attempt < max_retries:
                    continue

            except httpx.TimeoutException:
                logger.error("LLM API 请求超时")
                if attempt < max_retries:
                    continue
            except Exception as e:
                logger.error("LLM API 请求异常: %s", e)
                logger.debug("详细堆栈: %s", traceback.format_exc())
                if attempt < max_retries:
                    continue

    return None


def _parse_llm_response(raw: str) -> Optional[Dict[str, Any]]:
    """从 LLM 回复中解析 JSON。兼容各种格式。"""
    # 移除可能的 markdown 代码块包裹
    text = raw.strip()
    if text.startswith("```"):
        # 找到第一个换行符后的内容
        lines = text.split("\n")
        # 移除开头的 ```json 或 ```
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 移除结尾的 ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    # 尝试找到 JSON 对象
    # 查找第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("JSON 解析失败, 原始文本: %s", raw[:500])
        return None

    # 验证必要字段
    required = ["is_notification", "category", "card"]
    for field in required:
        if field not in result:
            logger.warning("LLM 返回缺少字段: %s", field)
            return None

    if result["is_notification"] and result.get("card"):
        card = result["card"]
        if not all(k in card for k in ("title", "detail", "href", "ddl", "tags")):
            logger.warning("card 缺少必要字段")
            return None

    return result


def is_today_notification(result: Dict[str, Any]) -> bool:
    """检查通知是否是当天的。

    如果通知中包含日期信息且不是今天，返回 False。
    目前主要通过检查 ddl 来判断，如果 ddl 已经是过去日期则跳过。
    实际上，由于 SMSForwarder 实时转发，消息时间就是今天。
    """
    # 实时转发的消息默认就是今天的通知
    # 额外检查：如果通知内容明确提到的是过去的日期则过滤
    if not result.get("is_notification"):
        return False

    card = result.get("card", {})
    ddl = card.get("ddl", "")
    if ddl:
        try:
            ddl_date = date.fromisoformat(ddl)
            if ddl_date < date.today():
                logger.info("通知截止日期 %s 已过，跳过", ddl)
                return False
        except (ValueError, TypeError):
            pass

    return True


def validate_card(card: Dict[str, Any]) -> List[str]:
    """校验 card 字段，返回错误列表。"""
    errors = []
    if not card.get("title"):
        errors.append("title 为空")
    if not card.get("detail"):
        errors.append("detail 为空")
    if "href" not in card:
        errors.append("缺少 href 字段")
    if not isinstance(card.get("tags"), list):
        errors.append("tags 不是列表")
    else:
        for tag in card["tags"]:
            if not isinstance(tag, dict) or "text" not in tag or "class" not in tag:
                errors.append(f"标签格式错误: {tag}")
    return errors
