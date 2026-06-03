"""CLI entry point for code-kg."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import typer

from code_kg.config import Settings
from code_kg.graph.client import Neo4jClient
from code_kg.graph.migrations import run_migrations
from code_kg.ingestion.pipeline import run_pipeline
from code_kg.logging import configure_logging

logger = logging.getLogger(__name__)

app = typer.Typer(help="Code Knowledge Graph — knowledge extraction + querying")


async def _resolve_repo_slug(client: Neo4jClient, slug: str) -> str:
    """Return the canonical repo slug stored in the graph, case-insensitively.

    If the slug matches an existing :Repo node (ignoring case), return the
    stored casing (e.g. 'devonhire' → 'DevOnHire').  If no match is found,
    return the slug as-is so that new repos are created with the user-supplied
    casing.
    """
    rows = await client.execute_query(
        "MATCH (r:Repo) WHERE toLower(r.name) = toLower($slug) RETURN r.name AS name LIMIT 1",
        {"slug": slug},
    )
    if rows:
        canonical = rows[0]["name"]
        if canonical != slug:
            typer.echo(f"ℹ️  Resolved repo slug '{slug}' → '{canonical}'")
        return canonical
    return slug


@app.command()
def bootstrap() -> None:
    """Run migrations on the Neo4j database."""
    settings = Settings()
    configure_logging(settings)
    asyncio.run(run_migrations(settings))


@app.command()
def version() -> None:
    """Show version."""
    from code_kg import __version__
    print(f"code-kg {__version__}")


@app.command()
def mcp(
    transport: str = typer.Argument("stdio", help="Transport: stdio or http"),
) -> None:
    """Start the MCP server.

    For Claude Code (stdio transport):
        code-kg mcp stdio

    For HTTP / Copilot (streamable-http transport):
        code-kg mcp http
    """
    settings = Settings()
    configure_logging(settings)

    from code_kg.mcp.server import create_server
    server = create_server(settings)

    if transport == "http":
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")


_GENERIC_SRC_DIRS = frozenset({"src", "lib", "app", "source", "main", "code"})


@app.command()
def ingest(
    repo_root: str = typer.Argument(..., help="Repository root directory"),
    pattern: str = typer.Option(
        "**/*.ts,**/*.tsx,**/*.js,**/*.jsx,**/*.cs,**/*.md",
        "--pattern",
        "-p",
        help="File glob patterns to ingest (comma-separated)",
    ),
    repo: str = typer.Option(
        "",
        "--repo",
        "-r",
        help=(
            "Repository slug (used as node namespace). "
            "Defaults to the directory name, or parent directory name "
            "when the last segment is generic (src, lib, app, …)."
        ),
    ),
) -> None:
    """Extract code from a repository and ingest into Neo4j.

    Example:
        code-kg ingest /path/to/repo --pattern "**/*.ts,**/*.cs" --repo my-service
    """
    settings = Settings()
    configure_logging(settings)

    asyncio.run(_ingest_async(settings, repo_root, pattern, repo_slug=repo or None))


async def _ingest_async(
    settings: Settings,
    repo_root: str,
    pattern: str,
    repo_slug: str | None = None,
) -> None:
    """Async implementation of ingest command — delegates to pipeline.run_pipeline."""
    repo_path = Path(repo_root)
    if not repo_path.is_dir():
        typer.echo(f"Error: {repo_root} is not a valid directory", err=True)
        raise typer.Exit(code=1)

    patterns = [p.strip() for p in pattern.split(",")]

    # Smart repo slug: use parent name when last segment is generic (src, lib, …)
    if not repo_slug:
        if repo_path.name.lower() in _GENERIC_SRC_DIRS and repo_path.parent.name:
            repo_slug = repo_path.parent.name
        else:
            repo_slug = repo_path.name

    typer.echo(f"📂 Ingesting from {repo_root} (repo={repo_slug})")
    typer.echo(f"📋 Patterns: {', '.join(patterns)}")

    client = Neo4jClient(settings.neo4j)
    await client.connect()

    try:
        result = await run_pipeline(
            client=client,
            repo_path=repo_path,
            repo_slug=repo_slug,
            patterns=patterns,
            # No commit_sha from local CLI — skip soft-delete
            commit_sha=None,
            do_soft_delete=False,
        )

        if result.errors:
            for err in result.errors:
                typer.echo(f"⚠️  {err}", err=True)
            raise typer.Exit(code=1)

        typer.echo(f"✨ Ingestion complete!")
        typer.echo(f"   • {result.nodes_added} nodes upserted")
        typer.echo(f"   • {result.edges_added} edges processed")
        typer.echo(f"   • {result.duration_seconds}s")

    except typer.Exit:
        raise
    except Exception as e:
        logger.error(f"Ingestion failed: {e}", exc_info=True)
        typer.echo(f"❌ Ingestion failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        await client.close()


@app.command()
def enrich(
    repo: Optional[str] = typer.Option(
        None,
        "--repo",
        "-r",
        help="Limit enrichment to a specific repo slug. Enriches all repos if omitted.",
    ),
    limit: int = typer.Option(
        500,
        "--limit",
        "-n",
        help="Maximum number of nodes to enrich per run.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Build provider objects and test connectivity, but don't enrich or write.",
    ),
) -> None:
    """Generate summaries and embeddings for nodes that haven't been enriched yet.

    Requires a running Neo4j instance (bootstrap first) and either:
      - Ollama running locally  (SUMMARY__PROVIDER=ollama, default)
      - OpenAI API key set      (SUMMARY__PROVIDER=openai, SUMMARY__API_KEY=...)

    Example:
        code-kg enrich --repo my-service --limit 100
    """
    settings = Settings()
    configure_logging(settings)
    asyncio.run(_enrich_async(settings, repo, limit, dry_run))


async def _enrich_async(
    settings: Settings,
    repo: Optional[str],
    limit: int,
    dry_run: bool,
) -> None:
    """Async implementation of the enrich command."""
    from code_kg.ingestion.enrichment import enrich_batch, load_unenriched_nodes
    from code_kg.providers.embedding.base import build_embedding_provider
    from code_kg.providers.summary.base import build_summary_provider

    typer.echo(f"🔧 Building providers (summary={settings.summary.provider}, "
               f"embedding={settings.embedding.provider})...")

    try:
        summary_provider = build_summary_provider(settings.summary)
        embedding_provider = build_embedding_provider(settings.embedding)
    except Exception as e:
        typer.echo(f"❌ Failed to build providers: {e}", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo("✅ Dry run: providers initialised successfully. No enrichment performed.")
        return

    client = Neo4jClient(settings.neo4j)
    await client.connect()

    try:
        if repo:
            repo = await _resolve_repo_slug(client, repo)

        typer.echo(f"🔍 Loading unenriched nodes (limit={limit}"
                   + (f", repo={repo!r}" if repo else "") + ")...")
        nodes = await load_unenriched_nodes(client, repo=repo, limit=limit)

        if not nodes:
            typer.echo("✅ No nodes need enrichment.")
            return

        typer.echo(f"📝 Enriching {len(nodes)} nodes "
                   f"(max_concurrent={settings.summary.max_concurrent})...")

        enriched, written = await enrich_batch(
            nodes,
            summary_provider=summary_provider,
            embedding_provider=embedding_provider,
            client=client,
            max_concurrent=settings.summary.max_concurrent,
        )

        typer.echo(f"✨ Enrichment complete!")
        typer.echo(f"   • {enriched} nodes enriched")
        typer.echo(f"   • {written} nodes written to Neo4j")

    except Exception as e:
        logger.error(f"Enrichment failed: {e}", exc_info=True)
        typer.echo(f"❌ Enrichment failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        await client.close()


@app.command("test-map")
def test_map(
    repo_root: str = typer.Argument(..., help="Repository root directory"),
    repo: str = typer.Option(
        "", "--repo", "-r", help="Repository slug (auto-detected if omitted)."
    ),
    min_weight: float = typer.Option(
        0.5, "--min-weight", help="Minimum confidence weight to emit a TESTS edge."
    ),
) -> None:
    """Infer TESTS edges from test functions to production code.

    Analyses CALLS edges, file-name heuristics, and path mirroring to
    link test functions to the production code they exercise.

    Example:
        code-kg test-map /path/to/repo --repo my-service --min-weight 0.6
    """
    settings = Settings()
    configure_logging(settings)
    asyncio.run(_test_map_async(settings, repo_root, repo or None, min_weight))


async def _test_map_async(
    settings: Settings,
    repo_root: str,
    repo_slug: Optional[str],
    min_weight: float,
) -> None:
    repo_path = Path(repo_root)
    if not repo_path.is_dir():
        typer.echo(f"Error: {repo_root} is not a valid directory", err=True)
        raise typer.Exit(code=1)

    if not repo_slug:
        if repo_path.name.lower() in _GENERIC_SRC_DIRS and repo_path.parent.name:
            repo_slug = repo_path.parent.name
        else:
            repo_slug = repo_path.name

    client = Neo4jClient(settings.neo4j)
    await client.connect()
    try:
        repo_slug = await _resolve_repo_slug(client, repo_slug)
        from code_kg.ingestion.tests_mapper import map_tests
        typer.echo(f"🔍 Mapping tests for repo={repo_slug}...")
        result = await map_tests(client, repo_slug, min_weight=min_weight)
        typer.echo(f"✨ Test mapping complete!")
        typer.echo(f"   • {result.edges_written} TESTS edges written")
        typer.echo(f"   • {result.edges_skipped} skipped (below min_weight)")
        if result.errors:
            for err in result.errors:
                typer.echo(f"   ⚠️  {err}", err=True)
    except Exception as e:
        logger.error(f"test-map failed: {e}", exc_info=True)
        typer.echo(f"❌ test-map failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        await client.close()


@app.command("link-docs")
def link_docs(
    repo_root: str = typer.Argument(..., help="Repository root directory"),
    repo: str = typer.Option(
        "", "--repo", "-r", help="Repository slug (auto-detected if omitted)."
    ),
    no_embeddings: bool = typer.Option(
        False, "--no-embeddings", help="Skip vector-neighbour candidate lookup."
    ),
    batch_size: int = typer.Option(
        20, "--batch-size", help="Sections per LLM batch."
    ),
    min_confidence: float = typer.Option(
        0.3, "--min-confidence", help="Minimum LLM confidence to emit a DOCUMENTS edge."
    ),
) -> None:
    """Infer DOCUMENTS edges from doc sections to code nodes using the LLM.

    Example:
        code-kg link-docs /path/to/repo --repo my-service --min-confidence 0.5
    """
    settings = Settings()
    configure_logging(settings)
    asyncio.run(
        _link_docs_async(
            settings, repo_root, repo or None,
            not no_embeddings, batch_size, min_confidence,
        )
    )


async def _link_docs_async(
    settings: Settings,
    repo_root: str,
    repo_slug: Optional[str],
    use_embeddings: bool,
    batch_size: int,
    min_confidence: float,
) -> None:
    repo_path = Path(repo_root)
    if not repo_path.is_dir():
        typer.echo(f"Error: {repo_root} is not a valid directory", err=True)
        raise typer.Exit(code=1)

    if not repo_slug:
        if repo_path.name.lower() in _GENERIC_SRC_DIRS and repo_path.parent.name:
            repo_slug = repo_path.parent.name
        else:
            repo_slug = repo_path.name

    client = Neo4jClient(settings.neo4j)
    await client.connect()
    try:
        repo_slug = await _resolve_repo_slug(client, repo_slug)
        from code_kg.ingestion.docs_linker import link_docs as _link_docs
        typer.echo(f"🔍 Linking docs for repo={repo_slug}...")
        result = await _link_docs(
            client, settings, repo_slug,
            batch_size=batch_size,
            use_embeddings=use_embeddings,
            min_confidence=min_confidence,
        )
        typer.echo(f"✨ Doc linking complete!")
        typer.echo(f"   • {result.sections_processed} sections processed")
        typer.echo(f"   • {result.edges_written} DOCUMENTS edges written")
        typer.echo(f"   • {result.edges_skipped} skipped (below min_confidence)")
        if result.errors:
            for err in result.errors:
                typer.echo(f"   ⚠️  {err}", err=True)
    except Exception as e:
        logger.error(f"link-docs failed: {e}", exc_info=True)
        typer.echo(f"❌ link-docs failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        await client.close()


eval_app = typer.Typer(help="Evaluation commands")
app.add_typer(eval_app, name="eval")


@eval_app.command("run")
def eval_run(
    server: str = typer.Option(
        "http://localhost:8765",
        "--server",
        "-s",
        help="Base URL of the MCP HTTP server.",
    ),
    questions: Optional[str] = typer.Option(
        None,
        "--questions",
        "-q",
        help="Path to questions YAML (defaults to tests/eval/questions.yaml).",
    ),
    output_dir: str = typer.Option(
        "eval-results",
        "--output-dir",
        "-o",
        help="Directory for report output.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Suppress per-question progress output.",
    ),
) -> None:
    """Run the full evaluation suite against the MCP HTTP server.

    Example:
        code-kg eval run --server http://localhost:8765
    """
    from code_kg.eval.runner import run_eval

    questions_path = Path(questions) if questions else None

    report = asyncio.run(
        run_eval(
            server_url=server,
            questions_path=questions_path,
            output_dir=Path(output_dir),
            verbose=not quiet,
        )
    )

    if report.pass_rate < 70.0:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
