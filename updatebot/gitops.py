"""Git 操作模块。

处理 fork 仓库与上游同步、本地拉取、以及每日推送。
所有认证通过 PAT 完成，不依赖 SSH。
"""

from __future__ import annotations

import logging
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _run(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str, str]:
    """运行 git 命令，返回 (返回码, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        logger.error("Git 命令超时: %s", " ".join(cmd))
        return -1, "", "timeout"
    except Exception as e:
        logger.error("Git 命令执行失败: %s - %s", " ".join(cmd), e)
        return -1, "", str(e)


def _auth_url(url: str, pat: str) -> str:
    """将 GitHub HTTPS URL 转换为带 PAT 的认证 URL。"""
    # https://github.com/owner/repo.git → https://PAT@github.com/owner/repo.git
    if url.startswith("https://github.com/") and "@" not in url:
        return url.replace("https://github.com/", f"https://{pat}@github.com/")
    return url


def ensure_repo(work_dir: Path, fork_url: str, pat: str) -> bool:
    """确保本地仓库存在并可正常操作。

    如果 work_dir 不是一个 git 仓库，尝试 clone。
    """
    git_dir = work_dir / ".git"
    if git_dir.is_dir():
        return True

    # 尝试 clone
    logger.info("本地仓库不存在，开始 clone: %s", fork_url)
    parent = work_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    auth_fork = _auth_url(fork_url, pat)
    code, _, stderr = _run(["git", "clone", auth_fork, str(work_dir.name)], parent)
    if code != 0:
        logger.error("Clone 失败: %s", stderr)
        return False
    return True


def sync_fork_with_upstream(
    work_dir: Path,
    fork_url: str,
    upstream_url: str,
    branch: str,
    pat: str,
) -> bool:
    """将上游仓库的最新提交同步到 fork 仓库。

    步骤：
    1. 确保 upstream remote 存在
    2. fetch upstream
    3. 将 upstream/branch 推送到 origin（即 fork）

    这样 fork 仓库就与上游同步了。
    """
    logger.info("开始同步 fork 与上游...")

    # 1. 添加或更新 upstream
    code, remotes, _ = _run(["git", "remote"], work_dir)
    if "upstream" not in remotes.split("\n"):
        _run(["git", "remote", "add", "upstream", upstream_url], work_dir)

    # 2. 配置带 PAT 的 origin URL
    auth_fork = _auth_url(fork_url, pat)
    _run(["git", "remote", "set-url", "origin", auth_fork], work_dir)

    # 3. Fetch upstream
    code, _, stderr = _run(["git", "fetch", "upstream", branch], work_dir)
    if code != 0:
        logger.error("Fetch upstream 失败: %s", stderr)
        return False

    # 4. 推送到 origin (fork)
    logger.info("推送 upstream/%s 到 origin/%s...", branch, branch)
    code, _, stderr = _run(
        ["git", "push", "origin", f"refs/remotes/upstream/{branch}:refs/heads/{branch}", "--force"],
        work_dir,
    )
    if code != 0:
        logger.error("推送同步到 fork 失败: %s", stderr)
        return False

    logger.info("Fork 同步完成")
    return True


def pull_latest(work_dir: Path, fork_url: str, branch: str, pat: str) -> bool:
    """从 fork 仓库拉取最新代码到本地。

    在 sync_fork_with_upstream 之后调用，确保本地与 fork 一致。
    """
    logger.info("拉取最新代码...")

    auth_fork = _auth_url(fork_url, pat)
    _run(["git", "remote", "set-url", "origin", auth_fork], work_dir)

    # fetch origin
    code, _, stderr = _run(["git", "fetch", "origin", branch], work_dir)
    if code != 0:
        logger.error("Fetch origin 失败: %s", stderr)
        return False

    # 强制重置到 origin/branch（确保干净状态）
    code, _, stderr = _run(["git", "checkout", branch], work_dir)
    if code != 0:
        logger.error("Checkout %s 失败: %s", branch, stderr)
        return False

    code, _, stderr = _run(["git", "reset", "--hard", f"origin/{branch}"], work_dir)
    if code != 0:
        logger.error("Reset 失败: %s", stderr)
        return False

    logger.info("本地代码已更新到最新")
    return True


def has_changes(work_dir: Path) -> bool:
    """检查是否有未提交的更改。"""
    code, stdout, _ = _run(["git", "status", "--porcelain"], work_dir)
    return code == 0 and bool(stdout)


def commit_and_push(
    work_dir: Path,
    fork_url: str,
    branch: str,
    pat: str,
) -> bool:
    """提交并推送本地更改到 fork 仓库。"""
    if not has_changes(work_dir):
        logger.info("没有需要推送的更改")
        return True

    auth_fork = _auth_url(fork_url, pat)
    _run(["git", "remote", "set-url", "origin", auth_fork], work_dir)

    today = date.today().isoformat()
    _run(["git", "add", "docs/Notification/"], work_dir)

    code, _, stderr = _run(
        ["git", "commit", "-m", f"Auto-update notifications {today}"],
        work_dir,
    )
    if code != 0:
        # 可能没有变化
        if "nothing to commit" in stderr:
            logger.info("没有新的更改需要提交")
            return True
        logger.error("提交失败: %s", stderr)
        return False

    code, _, stderr = _run(["git", "push", "origin", branch], work_dir)
    if code != 0:
        logger.error("推送失败: %s", stderr)
        return False

    logger.info("已成功推送通知更新")
    return True
