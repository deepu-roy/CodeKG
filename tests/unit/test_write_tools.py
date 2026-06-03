"""Unit tests for Phase 6 write tools (mocked Neo4j + filesystem)."""

import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_kg.domain.models import (
    DeleteRepoInput,
    IngestRepoInput,
    RefreshEnrichmentInput,
    ReindexFileInput,
)
from code_kg.ingestion.git import FileDiff, _run, get_diff, get_head_sha, is_git_repo
from code_kg.ingestion.cleanup import update_repo_meta, get_repo_meta
from code_kg.ingestion.pipeline import collect_files, pat_to_extensions
from code_kg.mcp.tools.write import (
    delete_repo,
    get_repo_state,
    ingest_repo,
    reindex_file,
    refresh_summaries,
    _is_local_path,
    _job_id,
)


# ── git.py unit tests ─────────────────────────────────────────────────────────


class TestGitHelpers:
    def test_is_git_repo_true(self, tmp_path):
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        assert is_git_repo(tmp_path) is True

    def test_is_git_repo_false(self, tmp_path):
        assert is_git_repo(tmp_path) is False

    def test_run_basic_command(self):
        out = _run(["--version"])
        assert "git" in out.lower()

    def test_run_raises_on_failure(self):
        from code_kg.ingestion.git import GitError
        with pytest.raises(GitError):
            _run(["invalid-subcommand-xyz"])

    def test_get_head_sha_real_repo(self, tmp_path):
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"],
                       check=True, capture_output=True)
        f = tmp_path / "hello.txt"
        f.write_text("hello")
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"],
                       check=True, capture_output=True)
        sha = get_head_sha(tmp_path)
        assert len(sha) == 40
        assert sha.isalnum()

    def test_file_diff_changed_includes_added_and_modified(self):
        diff = FileDiff(added=["a.cs"], modified=["b.cs"], deleted=["c.cs"],
                        renamed=[("old.cs", "new.cs")])
        assert set(diff.changed) == {"a.cs", "b.cs", "new.cs"}
        assert diff.deleted == ["c.cs"]

    def test_file_diff_repr(self):
        diff = FileDiff(added=["a"], modified=[], deleted=[], renamed=[])
        assert "added=1" in repr(diff)


class TestGitDiff:
    def _make_repo_with_two_commits(self, tmp_path: Path):
        """Helper: create a mini git repo with two commits for diff testing."""
        env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.com",
               "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.com"}
        run = lambda args: subprocess.run(args, cwd=tmp_path, check=True,
                                          capture_output=True, env=env)
        run(["git", "init"])
        (tmp_path / "a.cs").write_text("class A{}")
        run(["git", "add", "."])
        run(["git", "commit", "-m", "c1"])
        sha1 = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                        cwd=tmp_path).decode().strip()
        (tmp_path / "b.cs").write_text("class B{}")
        (tmp_path / "a.cs").write_text("class A{ void X(){} }")
        run(["git", "add", "."])
        run(["git", "commit", "-m", "c2"])
        sha2 = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                        cwd=tmp_path).decode().strip()
        return sha1, sha2

    def test_diff_detects_added_and_modified(self, tmp_path):
        sha1, sha2 = self._make_repo_with_two_commits(tmp_path)
        diff = get_diff(tmp_path, sha1, sha2)
        assert "b.cs" in diff.added
        assert "a.cs" in diff.modified
        assert diff.deleted == []


# ── pipeline.py helpers ───────────────────────────────────────────────────────


class TestPipelineHelpers:
    def test_collect_files_excludes_node_modules(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "App.cs").write_text("")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "lib.ts").write_text("")

        files = collect_files(tmp_path, ["**/*.cs", "**/*.ts"])
        assert "src/App.cs" in files
        assert not any("node_modules" in f for f in files)

    def test_collect_files_explicit_list(self, tmp_path):
        explicit = ["a.cs", "b.ts"]
        result = collect_files(tmp_path, ["**/*.cs"], explicit_files=explicit)
        assert result == sorted(explicit)

    def test_pat_to_extensions(self):
        assert pat_to_extensions("**/*.cs") == [".cs"]
        assert pat_to_extensions("**/*.ts") == [".ts"]
        assert pat_to_extensions("src/**") == []


# ── write.py helpers ──────────────────────────────────────────────────────────


class TestWriteHelpers:
    def test_is_local_path_absolute(self):
        assert _is_local_path("/Users/x/repo") is True

    def test_is_local_path_relative(self):
        assert _is_local_path("./repo") is True

    def test_is_local_path_url(self):
        assert _is_local_path("https://github.com/x/y") is False

    def test_is_local_path_file_url(self):
        assert _is_local_path("file:///tmp/repo") is True

    def test_job_id_unique(self):
        ids = {_job_id() for _ in range(20)}
        assert len(ids) == 20


# ── write tool: delete_repo ───────────────────────────────────────────────────


class TestDeleteRepo:
    async def test_delete_requires_confirm(self):
        client = MagicMock()
        inp = DeleteRepoInput(repo_slug="my-repo", confirm=False)
        result = await delete_repo(client, inp)
        assert result["status"] == "aborted"
        assert "confirm=False" in result["reason"]

    async def test_delete_calls_hard_delete_when_confirmed(self):
        client = MagicMock()
        inp = DeleteRepoInput(repo_slug="my-repo", confirm=True)
        with patch("code_kg.mcp.tools.write.hard_delete_repo",
                   new=AsyncMock(return_value=42)) as mock_del:
            result = await delete_repo(client, inp)
        assert result["status"] == "completed"
        assert result["nodes_deleted"] == 42
        mock_del.assert_awaited_once_with(client, "my-repo")


# ── write tool: ingest_repo (local path) ──────────────────────────────────────


class TestIngestRepoLocal:
    async def test_nonexistent_local_path_returns_failed(self):
        client = MagicMock()
        settings = MagicMock()
        inp = IngestRepoInput(
            repo_url="/nonexistent/path",
            repo_slug="test",
            commit_sha="abc123",
        )
        result = await ingest_repo(client, settings, inp)
        assert result.status == "failed"
        assert result.errors

    async def test_local_path_ingestion(self, tmp_path):
        """Happy path: single .cs file in a temp directory."""
        (tmp_path / "hello.cs").write_text("public class Hello {}")
        client = MagicMock()
        settings = MagicMock()
        settings.workdir = str(tmp_path / "workdir")
        settings.summary.max_concurrent = 1

        with patch("code_kg.ingestion.pipeline.normalize_and_upsert",
                   new=AsyncMock(return_value=(1, 0))), \
             patch("code_kg.ingestion.cleanup.mark_files_seen", new=AsyncMock()), \
             patch("code_kg.ingestion.cleanup.soft_delete_orphans", new=AsyncMock(return_value=0)), \
             patch("code_kg.ingestion.cleanup.update_repo_meta", new=AsyncMock()):

            inp = IngestRepoInput(
                repo_url=str(tmp_path),
                repo_slug="test",
                commit_sha="abc123",
                patterns=["**/*.cs"],
            )
            result = await ingest_repo(client, settings, inp)

        assert result.status == "completed"
        assert result.errors == []


# ── write tool: refresh_summaries ─────────────────────────────────────────────


class TestRefreshSummaries:
    async def test_returns_completed_when_no_nodes(self):
        client = MagicMock()
        settings = MagicMock()
        settings.summary.max_concurrent = 1
        settings.summary.provider = "ollama"
        settings.embedding.provider = "sentence_transformers"

        with patch("code_kg.ingestion.enrichment.load_unenriched_nodes",
                   new=AsyncMock(return_value=[])), \
             patch("code_kg.providers.summary.base.build_summary_provider", return_value=MagicMock()), \
             patch("code_kg.providers.embedding.base.build_embedding_provider", return_value=MagicMock()):
            inp = RefreshEnrichmentInput(repo="myrepo", only_if_missing=True)
            result = await refresh_summaries(client, settings, inp)

        assert result["status"] == "completed"
        assert result["enriched"] == 0


# ── write tool: get_repo_state ────────────────────────────────────────────────


class TestGetRepoState:
    async def test_returns_not_exists_for_unknown_repo(self):
        client = MagicMock()
        with patch("code_kg.mcp.tools.write.get_repo_meta",
                   new=AsyncMock(return_value=None)):
            state = await get_repo_state(client, "unknown-repo")
        assert state.exists is False
        assert state.last_commit_sha is None

    async def test_returns_meta_for_known_repo(self):
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[{"cnt": 42}])
        with patch("code_kg.mcp.tools.write.get_repo_meta",
                   new=AsyncMock(return_value={
                       "last_commit_sha": "abc123",
                       "last_ingest_at": "2026-06-01",
                       "name": "myrepo",
                   })):
            state = await get_repo_state(client, "myrepo")
        assert state.exists is True
        assert state.last_commit_sha == "abc123"
        assert state.node_count == 42
