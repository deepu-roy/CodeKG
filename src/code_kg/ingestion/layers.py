"""Layer assignment heuristics for code nodes.

Assigns architecture layers to code nodes based on:
1. Naming patterns (e.g., *Controller, *Service, *Model, *Dto)
2. File path patterns (e.g., /controllers/, /services/, /models/)
3. Parent class / interface hierarchy (e.g., extends Controller, implements Repository)

Layers: controller, service, repository, model, utility, middleware, config,
        component, store, validator, migration, exception, test

Coverage by stack:
  .NET/Java  — controller, service, repository, model, middleware, config,
               validator, migration, utility (Factory, Mapper, Attribute, Hub)
  Angular    — component, store, service, test (spec/e2e)
  React/Vue  — component, store, service, test
  Spring     — controller, service, repository, model, config
  Express    — controller, service, middleware, model

Nodes that match no pattern get layer=NULL and are still queryable; they are
not connected to any Layer node. Run the post-ingest verification query
(see docs/quickstart.md) to measure how many nodes are unclassified and
tune these patterns for your codebase.
"""

import logging
import re
from typing import Optional

from code_kg.domain.models import RawNode

logger = logging.getLogger(__name__)

# Layer assignments based on naming patterns
NAMING_PATTERNS = {
    # ── Test (highest priority — *ServiceTests must not be misclassified as service) ──
    "test": [
        r".*Tests?$",
        r".*Spec$",
        r".*Fixture$",
        r".*TestBase$",
        r".*TestData$",
        r".*TestHelper$",
        r".*Mock$",
        r".*Stub$",
        r".*Fake$",
    ],

    # ── Frontend UI (Angular / React / Vue components, guards, pipes, directives) ───
    "component": [
        r".*Component$",
        r".*Guard$",
        r".*Pipe$",
        r".*Directive$",
        r".*Module$",
        r".*Page$",
        r".*Widget$",
        r".*Screen$",
    ],

    # ── State management (NgRx, Akita, Pinia, Redux) ─────────────────────────────
    "store": [
        r".*Store$",
        r".*Effect[s]?$",
        r".*Reducer$",
        r".*Actions?$",
        r".*Selector[s]?$",
        r".*State$",
        r".*Facade$",
    ],

    # ── Validation (FluentValidation, DataAnnotations validators) ────────────────
    "validator": [
        r".*Validator$",
        r".*Validation$",
        r".*ValidatorBase$",
        r".*Rule$",
    ],

    # ── Controllers / request handlers ───────────────────────────────────────────
    "controller": [
        r".*Controller$",
        r".*Handler$",
        r".*Router$",
        r".*Hub$",       # SignalR hubs
    ],

    # ── Services / business logic ─────────────────────────────────────────────────
    "service": [
        r".*Service$",
        r".*Manager$",
        r".*Provider$",
        r".*Processor$",
        r".*Resolver$",  # Angular route resolvers, GraphQL resolvers
    ],

    # ── Data access ───────────────────────────────────────────────────────────────
    "repository": [
        r".*Repository$",
        r".*Dao$",
        r".*DbContext$",
        r".*Context$",
        r".*UnitOfWork$",
    ],

    # ── Domain / data models ──────────────────────────────────────────────────────
    "model": [
        r".*Model$",
        r".*Entity$",
        r".*Dto$",
        r".*DTO$",
        r".*Response$",
        r".*Request$",
        r".*View$",
        r".*ViewModel$",
        r".*Payload$",
        r".*Event$",
        r".*Command$",
        r".*Query$",       # CQRS queries
        r".*Example$",     # Swagger / OpenAPI examples
        r".*Result$",      # ServiceResult, OperationResult, etc.
        r".*Output$",
        r".*Input$",
        r".*Summary$",
        r".*Info$",
    ],

    # ── Exceptions ────────────────────────────────────────────────────────────────
    "exception": [
        r".*Exception$",
        r".*Error$",
        r".*Fault$",
    ],

    # ── Utilities / helpers ───────────────────────────────────────────────────────
    "utility": [
        r".*Util$",
        r".*Utils$",
        r".*Helper$",
        r".*Tool$",
        r".*Common$",
        r".*Extension[s]?$",
        r".*Factory$",
        r".*Builder$",
        r".*Mapper$",      # AutoMapper profiles / object mappers
        r".*Profile$",     # AutoMapper profiles
        r".*Attribute$",   # Custom attributes / annotations
        r".*Decorator$",
        r".*Converter$",
    ],

    # ── Middleware / cross-cutting concerns ───────────────────────────────────────
    "middleware": [
        r".*Middleware$",
        r".*Interceptor$",
        r".*Filter$",
        r".*Pipeline$",
        r".*Behavior$",    # MediatR pipeline behaviors
    ],

    # ── Configuration ─────────────────────────────────────────────────────────────
    "config": [
        r".*Config$",
        r".*Configuration$",
        r".*Settings$",
        r".*Options$",
        r".*Constants?$",
        r".*Startup$",
    ],

    # ── Database migrations ───────────────────────────────────────────────────────
    "migration": [
        r".*Migration$",
        r".*Snapshot$",
        r".*Seed$",
        r".*Initialiser$",
        r".*Initializer$",
    ],
}

# Layer assignments based on file path patterns
def _dir_contains(keyword: str) -> str:
    """Return a regex that matches any path whose directory segment CONTAINS keyword.

    Handles compound names like ``DevOnHire.Services/`` as well as plain
    ``services/`` or ``Service/``.  Case-insensitive when used with re.IGNORECASE.
    """
    return rf".*[/\\][^/\\]*{keyword}[^/\\]*[/\\].*"


def _file_named(keyword: str) -> str:
    """Return a regex matching any path whose filename CONTAINS keyword.

    Complements _dir_contains for cases where the layer signal is in the
    filename rather than the directory — e.g. Angular double-extension files
    (``app.component.ts``, ``candidate.service.ts``) or standalone helpers
    (``utils.ts``, ``SeedDataInitializer.cs``).
    """
    return rf".*[/\\][^/\\]*{keyword}[^/\\]*\.[a-z]+$"


PATH_PATTERNS = {
    # ── Test ─────────────────────────────────────────────────────────────────────
    "test": [
        _dir_contains("test"),
        _dir_contains("spec"),
        r".*[/\\]__tests__[/\\].*",
        r".*[/\\]e2e[/\\].*",
        r".*\.spec\.[a-z]+$",
        r".*\.test\.[a-z]+$",
    ],

    # ── Frontend components ───────────────────────────────────────────────────────
    "component": [
        _dir_contains("component"),
        _dir_contains("page"),
        _dir_contains("view"),
        _dir_contains("guard"),
        _dir_contains("directive"),
        _dir_contains("pipe"),
        _dir_contains("widget"),
        _dir_contains("screen"),
        # Angular double-extension filenames: app.component.ts, auth.guard.ts
        r".*\.component\.[a-z]+$",
        r".*\.guard\.[a-z]+$",
        r".*\.directive\.[a-z]+$",
        r".*\.pipe\.[a-z]+$",
        r".*\.module\.[a-z]+$",
    ],

    # ── State management ─────────────────────────────────────────────────────────
    "store": [
        _dir_contains("store"),
        _dir_contains("state"),
        _dir_contains("reducer"),
        _dir_contains("effect"),
        _dir_contains("action"),
        _dir_contains("selector"),
        # NgRx double-extension filenames
        r".*\.store\.[a-z]+$",
        r".*\.reducer\.[a-z]+$",
        r".*\.effects?\.[a-z]+$",
        r".*\.actions?\.[a-z]+$",
        r".*\.selectors?\.[a-z]+$",
    ],

    # ── Migrations ───────────────────────────────────────────────────────────────
    "migration": [
        _dir_contains("migration"),
        _dir_contains("seed"),
        _dir_contains("flyway"),
        _dir_contains("liquibase"),
        # Seed/init files not inside a seed/ dir (e.g. SeedDataInitializer.cs)
        _file_named("initializ"),
        _file_named("seed"),
    ],

    # ── Controllers ──────────────────────────────────────────────────────────────
    "controller": [
        _dir_contains("controller"),
        _dir_contains("route"),
        _dir_contains("handler"),
        _dir_contains("hub"),
    ],

    # ── Services ─────────────────────────────────────────────────────────────────
    "service": [
        _dir_contains("service"),
        _dir_contains("logic"),
        _dir_contains("resolver"),
        # Angular double-extension: candidate.service.ts
        r".*\.service\.[a-z]+$",
    ],

    # ── Repositories / data access ───────────────────────────────────────────────
    "repository": [
        _dir_contains("repositor"),   # matches repository / repositories
        _dir_contains("repo"),
        _dir_contains("persistence"),
        _dir_contains("dbcontext"),
    ],

    # ── Domain models ─────────────────────────────────────────────────────────────
    "model": [
        _dir_contains("model"),
        _dir_contains("entit"),       # matches entity / entities / DevOnHire.Entities
        _dir_contains("schema"),
        _dir_contains("dto"),
        _dir_contains("domain"),
        _dir_contains("example"),     # Swagger/OpenAPI example folders
    ],

    # ── Validation ────────────────────────────────────────────────────────────────
    "validator": [
        _dir_contains("validator"),
        _dir_contains("validation"),
    ],

    # ── Utilities ─────────────────────────────────────────────────────────────────
    "utility": [
        _dir_contains("util"),
        _dir_contains("helper"),
        _dir_contains("common"),
        _dir_contains("extension"),
        _dir_contains("factor"),      # factory / factories
        _dir_contains("mapper"),
        _dir_contains("constant"),
        # Utility files not inside a utils/ dir (e.g. utils.ts, helpers.ts)
        _file_named("util"),
        _file_named("helper"),
        _file_named("common"),
    ],

    # ── Middleware ────────────────────────────────────────────────────────────────
    "middleware": [
        _dir_contains("middleware"),
        _dir_contains("interceptor"),
        _dir_contains("filter"),
        _dir_contains("pipeline"),
        _dir_contains("authorization"),
        _dir_contains("modelbinder"),
        _dir_contains("custommodelbinder"),
    ],

    # ── Configuration ─────────────────────────────────────────────────────────────
    "config": [
        _dir_contains("config"),
        _dir_contains("configuration"),
        _dir_contains("setting"),
        _dir_contains("properties"),
        _dir_contains("injection"),   # DependencyInjection/ directories
        _file_named("startup"),       # Startup.cs, startup.ts
    ],

    # ── Exceptions ────────────────────────────────────────────────────────────────
    "exception": [
        _dir_contains("exception"),
        _dir_contains("error"),
        _dir_contains("fault"),
    ],
}


def assign_layer(node: RawNode) -> Optional[str]:
    """Assign a layer to a code node based on heuristics.

    Args:
        node: The RawNode to assign a layer to.

    Returns:
        The assigned layer name, or None if no layer could be assigned.
    """
    # Only assign layers to classes/interfaces/functions
    if node.type not in ("class", "interface", "function", "enum"):
        return None

    # Try file path patterns first (more specific)
    if node.file_path:
        file_path = node.file_path.lower()
        for layer, patterns in PATH_PATTERNS.items():
            for pattern in patterns:
                if re.match(pattern, file_path, re.IGNORECASE):
                    logger.debug(f"Assigned {layer} to {node.name} via path pattern {pattern}")
                    return layer

    # Try naming patterns second
    name = node.name or ""
    for layer, patterns in NAMING_PATTERNS.items():
        for pattern in patterns:
            if re.match(pattern, name):
                logger.debug(f"Assigned {layer} to {node.name} via naming pattern {pattern}")
                return layer

    # No layer could be assigned
    logger.debug(f"No layer assigned for {node.name} ({node.type})")
    return None


def assign_layers_to_nodes(nodes: list[RawNode]) -> dict[str, str]:
    """Assign layers to all nodes in the list.

    Args:
        nodes: List of RawNodes.

    Returns:
        Mapping from node ID (or name if ID not available) to assigned layer.
    """
    assignments = {}

    for node in nodes:
        layer = assign_layer(node)
        if layer:
            # Use node identity; in a real system would use stable IDs
            node_id = f"{node.type}:{node.name}:{node.file_path}"
            assignments[node_id] = layer

    return assignments
