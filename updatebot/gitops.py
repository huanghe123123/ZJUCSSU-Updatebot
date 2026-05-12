"""Git 操作模块。

处理 fork 仓库与上游同步、本地拉取、以及每日推送。
所有认证通过 PAT 完成，不依赖 SSH。
**绝不修改本地仓库的 remote 配置**，所有认证 URL 仅用于单次命令。
"""

from __future__ import annotations

import logging
import subprocess
from datetime import date
from pathlib import Path

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
    """将 GitHub URL 转换为带 PAT 的 HTTPS 认证 URL（仅用于单次 git 命令参数）。

    支持输入格式：
    - HTTPS: https://github.com/owner/repo.git
    - SSH:   git@github.com:owner/repo.git

    均输出: https://PAT@github.com/owner/repo.git
    """
    if pat in url:
        return url  # 已经包含 PAT，不重复处理

    # SSH 格式：git@github.com:owner/repo.git
    if url.startswith("git@github.com:"):
        path = url.split("git@github.com:")[1]
        return f"https://{pat}@github.com/{path}"

    # HTTPS 格式：https://github.com/owner/repo.git
    if url.startswith("https://github.com/"):
        return url.replace("https://github.com/", f"https://{pat}@github.com/")

    return url


def ensure_repo(work_dir: Path, fork_url: str, pat: str) -> bool:
    """确保本地仓库存在。如果不存在则 clone。

    Clone 时使用带 PAT 的 URL，clone 后 origin 即为 fork 的原始 URL（不含 PAT）。
    """
    git_dir = work_dir / ".git"
    if git_dir.is_dir():
        return True

    logger.info("本地仓库不存在，开始 clone: %s", fork_url)
    parent = work_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    auth_fork = _auth_url(fork_url, pat)
    # clone 时用带 PAT 的 URL 完成认证，但 clone 后 git 会把 origin 存为原始 URL
    # 为防止 PAT 泄漏到 git config，clone 后手动改回干净的 URL
    code, _, stderr = _run(["git", "clone", auth_fork, str(work_dir.name)], parent)
    if code != 0:
        logger.error("Clone 失败: %s", stderr)
        return False
    # 确保 origin 是干净 URL（不含 PAT）
    _run(["git", "-C", str(work_dir), "remote", "set-url", "origin", fork_url], parent)
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
    1. 确保 upstream remote 存在（使用原始无 PAT 的 URL）
    2. fetch upstream
    3. 使用带 PAT 的 URL 将 upstream/branch 推送到 fork

    整个过程不修改 origin 的 URL。
    """
    logger.info("开始同步 fork 与上游...")

    # 1. 添加 upstream（使用干净的原始 URL）
    code, remotes, _ = _run(["git", "remote"], work_dir)
    if "upstream" not in (remotes.split("\n") if remotes else []):
        _run(["git", "remote", "add", "upstream", upstream_url], work_dir)

    # 2. Fetch upstream（不需要认证，公开仓库）
    code, _, stderr = _run(["git", "fetch", "upstream", branch], work_dir)
    if code != 0:
        logger.error("Fetch upstream 失败: %s", stderr)
        return False

    # 3. 用带 PAT 的 URL 直接推送（不修改 remote 配置）
    auth_fork = _auth_url(fork_url, pat)
    logger.info("推送 upstream/%s 到 fork (%s)...", branch, fork_url)
    code, _, stderr = _run(
        ["git", "push", auth_fork, f"refs/remotes/upstream/{branch}:refs/heads/{branch}", "--force"],
        work_dir,
    )
    if code != 0:
        logger.error("推送同步到 fork 失败: %s", stderr)
        return False

    logger.info("Fork 同步完成")
    return True


def pull_latest(work_dir: Path, fork_url: str, branch: str, pat: str) -> bool:
    """从 fork 仓库拉取最新代码到本地。

    使用带 PAT 的 URL 直接 fetch + reset，不修改 origin 配置。
    """
    logger.info("拉取最新代码...")

    auth_fork = _auth_url(fork_url, pat)

    # fetch 时直接用带 PAT 的 URL
    code, _, stderr = _run(["git", "fetch", auth_fork, branch], work_dir)
    if code != 0:
        logger.error("Fetch origin 失败: %s", stderr)
        return False

    # 切换到目标分支
    code, _, stderr = _run(["git", "checkout", branch], work_dir)
    if code != 0:
        logger.error("Checkout %s 失败: %s", branch, stderr)
        return False

    # 重置到 FETCH_HEAD（即刚 fetch 下来的最新提交）
    code, _, stderr = _run(["git", "reset", "--hard", "FETCH_HEAD"], work_dir)
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
    """提交并推送本地更改到 fork 仓库。

    推送时使用带 PAT 的 URL 直接 push，不修改 origin 配置。
    """
    if not has_changes(work_dir):
        logger.info("没有需要推送的更改")
        return True

    today = date.today().isoformat()
    _run(["git", "add", "docs/Notification/"], work_dir)

    code, _, stderr = _run(
        ["git", "commit", "-m", f"Auto-update notifications {today}"],
        work_dir,
    )
    if code != 0:
        if "nothing to commit" in stderr:
            logger.info("没有新的更改需要提交")
            return True
        logger.error("提交失败: %s", stderr)
        return False

    # 直接用带 PAT 的 URL 推送，不修改 origin
    auth_fork = _auth_url(fork_url, pat)
    code, _, stderr = _run(["git", "push", auth_fork, branch], work_dir)
    if code != 0:
        logger.error("推送失败: %s", stderr)
        return False

    logger.info("已成功推送通知更新")
    return True
