"""UpdateBot 主入口。

启动 Webhook 服务器接收 SMSForwarder 转发的消息，
通过 LLM 识别和分类通知，写入对应文档，每日定时推送。

用法:
    uv run updatebot              # 使用默认 config.yaml
    uv run updatebot --config /path/to/config.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import logging
import signal
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse

from .config import (
    get_api_key,
    get_category_file,
    get_github_pat,
    get_notification_base,
    load_config,
    validate_config,
)
from .gitops import (
    commit_and_push,
    ensure_repo,
    has_changes,
    pull_latest,
    sync_fork_with_upstream,
)
from .llm import classify_message, is_today_notification, validate_card
from .writer import add_notification

# ---------- 全局状态 ----------

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("updatebot")

# 全局配置
_config: Dict[str, Any] = {}

# 今日是否已完成 git 同步
_today_synced: Optional[str] = None  # 记录已同步的日期 (ISO)


# ---------- 辅助函数 ----------


def _is_update_day() -> bool:
    """检查今天是否是需要更新的日子。"""
    today_weekday = date.today().isoweekday()  # 1=Mon, 7=Sun
    update_days = _config["schedule"].get("update_days", [1, 2, 3, 4, 5])
    return today_weekday in update_days


def _verify_sign(timestamp: str, sign: str) -> bool:
    """验证 SMSForwarder 的签名（如果配置了 secret）。"""
    secret = _config["webhook"].get("secret", "")
    if not secret:
        return True  # 未配置 secret 则跳过验证

    try:
        string_to_sign = f"{timestamp}\n{secret}"
        mac = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        )
        import base64
        import urllib.parse

        expected = urllib.parse.quote_plus(base64.b64encode(mac.digest()))
        return hmac.compare_digest(sign, expected)
    except Exception:
        return False


async def _do_daily_sync() -> bool:
    """执行每日首次同步：同步 fork 与上游，拉取到本地。"""
    global _today_synced
    today_str = date.today().isoformat()

    if _today_synced == today_str:
        logger.info("今日已同步过，跳过")
        return True

    pat = get_github_pat()
    if not pat:
        logger.error("GITHUB_PAT 未设置，无法同步")
        return False

    work_dir = Path(_config["paths"]["work_dir"])
    fork_url = _config["repo"]["fork_url"]
    upstream_url = _config["repo"]["upstream_url"]
    branch = _config["repo"]["branch"]

    # 确保本地仓库存在
    if not ensure_repo(work_dir, fork_url, pat):
        return False

    # 0. 先保护本地未推送的更改（防止上次推送失败导致丢失）
    if has_changes(work_dir):
        logger.info("发现本地未推送的更改，先行推送...")
        commit_and_push(work_dir, fork_url, branch, pat)

    # 1. 同步 fork 与上游
    if not sync_fork_with_upstream(work_dir, fork_url, upstream_url, branch, pat):
        logger.warning("同步 fork 与上游失败，尝试继续...")

    # 2. 拉取最新到本地
    if not pull_latest(work_dir, fork_url, branch, pat):
        logger.error("拉取最新代码失败")
        return False

    _today_synced = today_str
    logger.info("每日同步完成")
    return True


async def _process_message(content: str) -> Dict[str, Any]:
    """处理一条消息：LLM 分类 + 写入通知。"""
    if not content or not content.strip():
        return {"status": "ignored", "reason": "空消息"}

    if not _is_update_day():
        logger.info("今天不是更新日，跳过处理")
        return {"status": "ignored", "reason": "非更新日"}

    api_key = get_api_key()
    model_config = _config["model"]
    categories = _config.get("categories", [])

    # 调用 LLM 分类
    result = await classify_message(
        content=content.strip(),
        model_url=model_config["url"],
        model_name=model_config["name"],
        api_key=api_key,
        categories=categories,
    )

    if result is None:
        logger.error("LLM 分类失败")
        return {"status": "error", "reason": "LLM 调用失败"}

    if not result.get("is_notification"):
        reason = result.get("reason", "非通知消息")
        logger.info("消息不是通知: %s", reason)
        return {"status": "ignored", "reason": reason}

    # 检查是否是今天的通知
    if not is_today_notification(result):
        return {"status": "ignored", "reason": "非当天通知"}

    card = result.get("card", {})
    category_key = result.get("category", "")

    # 校验卡片数据
    errors = validate_card(card)
    if errors:
        logger.error("卡片数据校验失败: %s", errors)
        return {"status": "error", "reason": f"卡片校验失败: {errors}"}

    # 查找对应的分类文件
    category_file = get_category_file(_config, category_key)
    if not category_file:
        logger.error("未知的分类: %s", category_key)
        return {"status": "error", "reason": f"未知分类: {category_key}"}

    # 写入通知
    base_dir = get_notification_base(_config)
    success = add_notification(base_dir, category_file, card)

    if success:
        logger.info("通知已写入: [%s] %s", category_key, card.get("title"))
        return {
            "status": "written",
            "category": category_key,
            "title": card.get("title"),
        }
    else:
        return {"status": "error", "reason": "写入文件失败"}


async def _do_daily_push() -> None:
    """每日定时任务：推送本地更新到 fork 仓库。"""
    logger.info("执行每日推送任务...")

    if not _is_update_day():
        logger.info("今天不是更新日，跳过推送")
        return

    pat = get_github_pat()
    if not pat:
        logger.error("GITHUB_PAT 未设置，无法推送")
        return

    work_dir = Path(_config["paths"]["work_dir"])
    fork_url = _config["repo"]["fork_url"]
    branch = _config["repo"]["branch"]

    if not has_changes(work_dir):
        logger.info("本地无新更改，跳过推送")
        return

    if commit_and_push(work_dir, fork_url, branch, pat):
        logger.info("每日推送完成")
    else:
        logger.error("每日推送失败")


# ---------- FastAPI 应用 ----------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化，关闭时清理。"""
    global _config, _today_synced

    # 启动
    logger.info("UpdateBot 启动中...")

    # 校验配置
    errors = validate_config(_config)
    if errors:
        for err in errors:
            logger.error("配置错误: %s", err)
        logger.error("请修复配置错误后重新启动")
        sys.exit(1)

    # 打印配置摘要
    logger.info("模型: %s @ %s", _config["model"]["name"], _config["model"]["url"])
    logger.info("仓库: %s -> %s", _config["repo"]["upstream_url"], _config["repo"]["fork_url"])
    logger.info("工作目录: %s", _config["paths"]["work_dir"])
    logger.info(
        "更新日: %s",
        ", ".join(str(d) for d in _config["schedule"]["update_days"]),
    )
    logger.info("推送时间: %s", _config["schedule"]["push_time"])

    # 启动调度器
    scheduler = AsyncIOScheduler()
    push_time = _config["schedule"]["push_time"]
    hour, minute = push_time.split(":")
    scheduler.add_job(
        _do_daily_push,
        CronTrigger(hour=int(hour), minute=int(minute)),
        id="daily_push",
        name="每日推送通知",
    )
    scheduler.start()
    logger.info("定时任务已启动: 每日 %s 推送", push_time)

    # 存储 scheduler 以便关闭时使用
    app.state.scheduler = scheduler

    yield

    # 关闭
    scheduler.shutdown(wait=False)
    logger.info("UpdateBot 已关闭")


app = FastAPI(
    title="UpdateBot",
    description="ZJU CSSU 网站通知自动更新机器人",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """健康检查端点。"""
    return {
        "status": "ok",
        "today_synced": _today_synced,
        "update_day": _is_update_day(),
    }


@app.get("/")
async def root():
    """根端点。"""
    return {"service": "UpdateBot", "version": "1.0.0"}


@app.post("/webhook")
async def webhook(request: Request):
    """接收 SMSForwarder 转发的消息。

    SMSForwarder 发送 POST 请求，Content-Type 为:
    - application/json (如果配置了 JSON webParams)
    - application/x-www-form-urlencoded (默认表单)

    字段:
    - from: 来源 (应用包名)
    - content / msg: 消息内容
    - timestamp: 毫秒时间戳
    - sign: 签名 (可选)
    """
    # 解析请求体
    content_type = request.headers.get("content-type", "")

    try:
        if "application/json" in content_type:
            data = await request.json()
        else:
            raw = await request.body()
            # 尝试解析为 JSON
            try:
                import json
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                # 尝试解析为表单
                try:
                    form = await request.form()
                    data = dict(form)
                except Exception:
                    logger.warning("无法解析请求体: %s", raw[:200])
                    return JSONResponse({"status": "error", "message": "无法解析请求体"}, status_code=400)
    except Exception as e:
        logger.error("解析请求失败: %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)

    # 提取字段
    from_ = data.get("from", "")
    content = data.get("content", "") or data.get("msg", "")
    timestamp = data.get("timestamp", "")
    sign = data.get("sign", "")

    logger.info("收到消息 from=%s, 内容=%s...", from_, content[:100] if content else "(空)")

    # 验签
    if not _verify_sign(timestamp, sign):
        logger.warning("签名验证失败")
        return JSONResponse({"status": "error", "message": "签名验证失败"}, status_code=403)

    # 首次消息 → git 同步
    await _do_daily_sync()

    # 处理消息
    result = await _process_message(content)

    return JSONResponse(result)


@app.post("/webhook/form")
async def webhook_form(
    from_: str = Form(default="", alias="from"),
    content: str = Form(default=""),
    msg: str = Form(default=""),
    timestamp: str = Form(default=""),
    sign: str = Form(default=""),
):
    """表单格式的 webhook 端点（备用）。"""
    text = content or msg
    logger.info("收到表单消息 from=%s", from_)

    if not _verify_sign(timestamp, sign):
        logger.warning("签名验证失败")
        return JSONResponse({"status": "error", "message": "签名验证失败"}, status_code=403)

    await _do_daily_sync()
    result = await _process_message(text)

    return JSONResponse(result)


# ---------- 入口 ----------


def main():
    """主入口函数。"""
    global _config

    parser = argparse.ArgumentParser(description="UpdateBot - CSSU 通知自动更新")
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "--host",
        help="监听地址 (覆盖配置文件)",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="监听端口 (覆盖配置文件)",
    )
    args = parser.parse_args()

    # 加载配置
    _config = load_config(args.config)
    logger.info("配置文件: %s", args.config)

    # CLI 参数覆盖配置
    host = args.host or _config["webhook"]["host"]
    port = args.port or _config["webhook"]["port"]

    # 启动服务器
    import uvicorn

    logger.info("Webhook 服务器启动在 %s:%d", host, port)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
