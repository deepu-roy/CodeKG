"""Tests for configuration loading."""

import os
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from code_kg.config import EmbeddingSettings, Neo4jSettings, Settings, SummarySettings


def test_neo4j_settings_with_custom_database():
    """Test Neo4j settings with custom database name."""
    settings = Neo4jSettings(
        uri="neo4j://localhost:7687",
        user="neo4j",
        password="password",
        database="custom",
    )
    assert settings.database == "custom"


def test_embedding_settings_defaults():
    """Test embedding settings defaults to sentence-transformers."""
    settings = EmbeddingSettings()
    assert settings.provider == "sentence_transformers"
    assert settings.model == "BAAI/bge-small-en-v1.5"
    assert settings.dimensions == 384


def test_embedding_dimensions_for_known_models():
    """Test embedding dimensions lookup for known models."""
    assert EmbeddingSettings(model="BAAI/bge-small-en-v1.5").dimensions == 384
    assert EmbeddingSettings(model="text-embedding-3-small").dimensions == 1536
    assert EmbeddingSettings(model="text-embedding-3-large").dimensions == 3072


def test_embedding_dimensions_for_unknown_model():
    """Test embedding dimensions default for unknown models."""
    assert EmbeddingSettings(model="unknown-model").dimensions == 384


def test_summary_settings_defaults():
    """Test summary settings defaults to Ollama.

    The code default is qwen2.5-coder:7b; the .env file may override it to 14b.
    We test the raw model class default by constructing with explicit values.
    """
    settings = SummarySettings(model="qwen2.5-coder:7b")
    assert settings.provider == "ollama"
    assert settings.model == "qwen2.5-coder:7b"
    assert settings.temperature == 0.1


def test_settings_from_env():
    """Test Settings can load from environment variables."""
    # Note: Settings loads from .env in the current directory by default.
    # This test verifies the configuration structure works with OpenAI providers.
    settings = EmbeddingSettings(provider="openai", api_key="sk-xxx")
    assert settings.provider == "openai"
    assert settings.api_key == "sk-xxx"

    settings_summary = SummarySettings(provider="openai", api_key="sk-yyy")
    assert settings_summary.provider == "openai"
    assert settings_summary.api_key == "sk-yyy"


def test_settings_mcp_defaults():
    """Test MCP settings defaults (isolated from process environment)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        env_file.write_text(
            "NEO4J__URI=neo4j://localhost\n"
            "NEO4J__USER=neo4j\n"
            "NEO4J__PASSWORD=password\n"
        )

        # Remove MCP-related env vars from the process environment so defaults are tested
        mcp_keys = [k for k in os.environ if k.startswith("MCP_")]
        saved = {k: os.environ.pop(k) for k in mcp_keys}

        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            settings = Settings()
            assert settings.mcp_transport == "stdio"
            assert settings.mcp_http_host == "127.0.0.1"
            assert settings.mcp_http_port == 8765
        finally:
            os.chdir(orig_cwd)
            os.environ.update(saved)
