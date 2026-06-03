"""Configuration management via Pydantic Settings."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Neo4jSettings(BaseSettings):
    """Neo4j connection settings."""

    uri: str
    user: str
    password: str
    database: str = "neo4j"


class EmbeddingSettings(BaseSettings):
    """Embedding provider configuration."""

    provider: Literal["sentence_transformers", "openai"] = "sentence_transformers"
    model: str = "BAAI/bge-small-en-v1.5"
    api_key: str | None = None
    batch_size: int = 32

    @property
    def dimensions(self) -> int:
        """Return the embedding dimensions for the configured model."""
        dims = {
            "BAAI/bge-small-en-v1.5": 384,
            "BAAI/bge-base-en-v1.5": 768,
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
        }
        return dims.get(self.model, 384)


class SummarySettings(BaseSettings):
    """Summary/LLM provider configuration."""

    provider: Literal["ollama", "openai"] = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5-coder:7b"
    api_key: str | None = None
    temperature: float = 0.1
    max_concurrent: int = 4


class Settings(BaseSettings):
    """Root application settings, loaded from .env."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    neo4j: Neo4jSettings
    embedding: EmbeddingSettings = EmbeddingSettings()
    summary: SummarySettings = SummarySettings()
    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_http_host: str = "127.0.0.1"
    mcp_http_port: int = 8765
    log_level: str = "INFO"
    # Directory used for git clones by the ingest_repo write tool
    workdir: str = "./workdir"
