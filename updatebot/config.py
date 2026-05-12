"""配置加载与校验模块。

从 config.yaml 读取用户配置，从 .env 加载密钥。
支持深合并默认值和用户配置，缺失必要项时尽早报错。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv

# ---------- 默认配置 ----------

DEFAULT_CONFIG: Dict[str, Any] = {
    "model": {
        "url": "https://api.openai.com/v1",
        "name": "gpt-4o",
    },
    "repo": {
        "fork_url": "",
        "upstream_url": "https://github.com/ZJU-CSSU-Dev/home.git",
        "branch": "main",
    },
    "schedule": {
        "update_days": [1, 2, 3, 4, 5],
        "push_time": "22:30",
    },
    "paths": {
        "work_dir": "",
        "env_file": "",
        "python_path": "python3",
    },
    "webhook": {
        "host": "0.0.0.0",
        "port": 8080,
        "secret": "",
    },
    "categories": [
        {
            "key": "Academic",
            "name": "教学事务",
            "file": "Academic/Academic.md",
            "description": "选课、考试、培养方案、毕业设计、论文等教学相关通知",
        },
        {
            "key": "Awards",
            "name": "评优评先和资助",
            "file": "Awards/Awards.md",
            "description": "奖学金、助学金、评奖评优、勤工助学、贷学金、荣誉称号评选、资助等",
        },
        {
            "key": "Growth",
            "name": "形策二课",
            "file": "Growth/PolicyAndSecondCourse.md",
            "description": "形势与政策、第二课堂、第三课堂、心理健康、志愿者、年级大会等",
        },
        {
            "key": "Research",
            "name": "学业科研",
            "file": "Research/SchoolworkResearch.md",
            "description": "科研训练、学科竞赛、学术讲座、暑研、考研保研、招生宣讲、SQTP等",
        },
        {
            "key": "Career",
            "name": "就业发展",
            "file": "Career/Career.md",
            "description": "就业招聘、实习、宣讲会、入伍、西部计划、选调生等职业发展通知",
        },
    ],
    "notification": {
        "base_dir": "docs/Notification",
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    """递归地将 override 合并到 base 中（原地修改 base）。"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """加载并校验配置。

    1. 读取 config.yaml（若文件不存在则使用默认值）
    2. 加载 .env 中的环境变量
    3. 用环境变量覆盖对应配置项
    """
    config = DEFAULT_CONFIG.copy()

    # --- 确定 config.yaml 位置 ---
    # 如果 config_path 不是绝对路径，则相对于当前工作目录
    resolved_config = Path(config_path)
    if not resolved_config.is_absolute():
        resolved_config = Path.cwd() / config_path

    if resolved_config.exists():
        with open(resolved_config, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        _deep_merge(config, user_config)

    # --- 确定 .env 位置 ---
    env_file = config["paths"].get("env_file", "")
    if env_file:
        env_path = Path(env_file)
        if not env_path.is_absolute():
            env_path = Path.cwd() / env_path
    else:
        # 默认在与 config.yaml 同目录下查找 .env
        env_path = resolved_config.parent / ".env"

    if env_path.exists():
        load_dotenv(env_path)
    else:
        # 尝试从当前目录加载
        load_dotenv()

    return config


def get_api_key() -> str:
    """从环境变量获取 LLM API Key。"""
    return os.environ.get("LLM_API_KEY", "")


def get_github_pat() -> str:
    """从环境变量获取 GitHub PAT。"""
    return os.environ.get("GITHUB_PAT", "")


def validate_config(config: Dict[str, Any]) -> List[str]:
    """校验配置完整性，返回错误消息列表。"""
    errors: List[str] = []

    if not get_api_key():
        errors.append("LLM_API_KEY 未设置（在 .env 中配置）")
    if not get_github_pat():
        errors.append("GITHUB_PAT 未设置（在 .env 中配置）")
    if not config["repo"].get("fork_url"):
        errors.append("repo.fork_url 未配置")
    if not config["paths"].get("work_dir"):
        errors.append("paths.work_dir 未配置")

    work_dir = Path(config["paths"]["work_dir"])
    if work_dir and not work_dir.is_dir():
        errors.append(f"工作目录不存在: {work_dir}")

    days = config["schedule"].get("update_days", [])
    if not isinstance(days, list) or not all(isinstance(d, int) and 1 <= d <= 7 for d in days):
        errors.append("schedule.update_days 格式错误，应为 1-7 的整数列表")

    push_time = config["schedule"].get("push_time", "")
    try:
        parts = push_time.split(":")
        if len(parts) != 2 or not (0 <= int(parts[0]) <= 23) or not (0 <= int(parts[1]) <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        errors.append(f"schedule.push_time 格式错误: {push_time}，应为 HH:MM")

    return errors


def get_notification_base(config: Dict[str, Any]) -> Path:
    """返回通知目录的绝对路径。"""
    work_dir = Path(config["paths"]["work_dir"])
    base_dir = config["notification"].get("base_dir", "docs/Notification")
    return work_dir / base_dir


def get_category_file(config: Dict[str, Any], category_key: str) -> Optional[str]:
    """根据分类 key 返回对应的 md 文件路径。"""
    for cat in config.get("categories", []):
        if cat["key"] == category_key:
            return cat["file"]
    return None
