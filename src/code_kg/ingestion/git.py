"""Git helpers — clone, fetch, diff, and repo metadata.

Uses subprocess git commands; no extra Python dependency required.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── exceptions ────────────────────────────────────────────────────────────────


class GitError(RuntimeError):
    """Raised when a git operation fails."""


# ── low-level git runner ───────────────────────────────────────────────────────


def _run(args: list[str], cwd: Optional[Path] = None, check: bool = True) -> str:
    """Run a git command and return stdout.

    Args:
        args: Command arguments (git is prepended automatically).
        cwd: Working directory.
        check: If True, raise GitError on non-zero exit.

    Returns:
        Decoded stdout string.

    Raises:
        GitError: When *check* is True and the command fails.
    """
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError:
        raise GitError("git not found on PATH — install git and retry")

    if check and result.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


# ── public helpers ────────────────────────────────────────────────────────────


def is_git_repo(path: Path) -> bool:
    """Return True if *path* is inside a git repository."""
    try:
        _run(["rev-parse", "--git-dir"], cwd=path)
        return True
    except GitError:
        return False


def get_head_sha(repo_path: Path) -> str:
    """Return the current HEAD commit SHA of *repo_path*.

    Raises:
        GitError: If the path is not a git repo or has no commits.
    """
    return _run(["rev-parse", "HEAD"], cwd=repo_path)


def clone_or_fetch(repo_url: str, workdir: Path) -> Path:
    """Clone *repo_url* into *workdir/<repo_name>*, or fetch if already cloned.

    Supports ``https://`` and ``git@`` URLs.  Local ``file://`` paths and bare
    filesystem paths are also accepted (useful for tests).

    Args:
        repo_url: Remote URL or local path.
        workdir: Parent directory for the clone.

    Returns:
        Path to the cloned/updated repository.

    Raises:
        GitError: On clone/fetch failure.
    """
    workdir.mkdir(parents=True, exist_ok=True)

    # Derive a safe directory name from the URL / path
    slug = re.sub(r"[^\w.-]", "_", Path(repo_url).stem or "repo")
    target = workdir / slug

    if target.exists() and is_git_repo(target):
        logger.info(f"Fetching {repo_url} into {target}")
        _run(["fetch", "--prune", "origin"], cwd=target)
    else:
        if target.exists():
            shutil.rmtree(target)
        logger.info(f"Cloning {repo_url} into {target}")
        _run(["clone", "--filter=blob:none", repo_url, str(target)])

    return target


class FileDiff:
    """Result of a ``git diff`` between two commits."""

    def __init__(
        self,
        added: list[str],
        modified: list[str],
        deleted: list[str],
        renamed: list[tuple[str, str]],
    ) -> None:
        self.added = added
        self.modified = modified
        self.deleted = deleted
        self.renamed = renamed  # list of (old_path, new_path)

    @property
    def changed(self) -> list[str]:
        """All added + modified + new names of renamed files."""
        result = list(self.added) + list(self.modified)
        result += [new for _, new in self.renamed]
        return result

    def __repr__(self) -> str:
        return (
            f"FileDiff(added={len(self.added)}, modified={len(self.modified)}, "
            f"deleted={len(self.deleted)}, renamed={len(self.renamed)})"
        )


def get_diff(repo_path: Path, base_sha: str, head_sha: str) -> FileDiff:
    """Compute a file-level diff between two commits.

    Args:
        repo_path: Path to the git repository.
        base_sha: Base (older) commit SHA.
        head_sha: Head (newer) commit SHA.

    Returns:
        FileDiff with categorised file lists.

    Raises:
        GitError: On git failure.
    """
    raw = _run(
        ["diff", "--name-status", "-M90%", f"{base_sha}..{head_sha}"],
        cwd=repo_path,
    )

    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    renamed: list[tuple[str, str]] = []

    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][:1]  # A / M / D / R / C / T

        if status == "A" and len(parts) >= 2:
            added.append(parts[1])
        elif status == "M" and len(parts) >= 2:
            modified.append(parts[1])
        elif status == "D" and len(parts) >= 2:
            deleted.append(parts[1])
        elif status == "R" and len(parts) >= 3:
            renamed.append((parts[1], parts[2]))
        elif status == "C" and len(parts) >= 3:
            # Copy treated like add
            added.append(parts[2])

    return FileDiff(added=added, modified=modified, deleted=deleted, renamed=renamed)


def get_file_list(
    repo_path: Path,
    patterns: list[str],
    exclude_dirs: frozenset[str] | None = None,
) -> list[str]:
    """Return all tracked files matching *patterns* in *repo_path*.

    Uses ``git ls-files`` so only tracked (non-ignored) files are returned.

    Args:
        repo_path: Repository root.
        patterns: Glob patterns relative to repo root (e.g. ``**/*.cs``).
        exclude_dirs: Directory names to exclude (e.g. ``node_modules``).

    Returns:
        Sorted list of relative file paths.
    """
    _EXCLUDE = exclude_dirs or frozenset({
        "node_modules", ".git", "dist", "build", "bin", "obj",
        ".next", "out", "coverage", "__pycache__", ".venv", "target",
    })

    args = ["ls-files"] + patterns
    raw = _run(args, cwd=repo_path)

    result = []
    for p in raw.splitlines():
        if p and not any(part in _EXCLUDE for part in Path(p).parts):
            result.append(p)

    return sorted(result)


def checkout_commit(repo_path: Path, commit_sha: str) -> None:
    """Detach HEAD to *commit_sha* (useful for inspecting a specific commit).

    Args:
        repo_path: Repository root.
        commit_sha: The commit to check out.

    Raises:
        GitError: On failure.
    """
    _run(["checkout", "--detach", commit_sha], cwd=repo_path)
