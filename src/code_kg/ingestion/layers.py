"""Layer assignment heuristics for code nodes.

Assigns architecture layers to code nodes based on:
1. Naming patterns (e.g., *Controller, *Service, *Model, *Dto)
2. File path patterns (e.g., /controllers/, /services/, /models/)
3. Parent class / interface hierarchy (e.g., extends Controller, implements Repository)

Layers: controller, service, repository, model, utility, middleware, config,
        component, store, validator, migration, test

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
PATH_PATTERNS = {
    "test": [
        r".*[/\\]test[s]?[/\\].*",
        r".*[/\\]spec[s]?[/\\].*",
        r".*[/\\]__tests__[/\\].*",
        r".*[/\\]e2e[/\\].*",
        r".*\.spec\.[a-z]+$",
        r".*\.test\.[a-z]+$",
    ],
    "component": [
        r".*[/\\]component[s]?[/\\].*",
        r".*[/\\]page[s]?[/\\].*",
        r".*[/\\]view[s]?[/\\].*",
        r".*[/\\]guard[s]?[/\\].*",
        r".*[/\\]directive[s]?[/\\].*",
        r".*[/\\]pipe[s]?[/\\].*",
        r".*[/\\]widget[s]?[/\\].*",
        r".*[/\\]screen[s]?[/\\].*",
    ],
    "store": [
        r".*[/\\]store[s]?[/\\].*",
        r".*[/\\]state[/\\].*",
        r".*[/\\]reducer[s]?[/\\].*",
        r".*[/\\]effect[s]?[/\\].*",
        r".*[/\\]action[s]?[/\\].*",
        r".*[/\\]selector[s]?[/\\].*",
    ],
    "migration": [
        r".*[/\\]migration[s]?[/\\].*",
        r".*[/\\]seed[s]?[/\\].*",
        r".*[/\\]flyway[/\\].*",
        r".*[/\\]liquibase[/\\].*",
    ],
    "controller": [
        r".*[/\\]controller[s]?[/\\].*",
        r".*[/\\]route[s]?[/\\].*",
        r".*[/\\]handler[s]?[/\\].*",
        r".*[/\\]hub[s]?[/\\].*",
    ],
    "service": [
        r".*[/\\]service[s]?[/\\].*",
        r".*[/\\]logic[/\\].*",
        r".*[/\\]resolver[s]?[/\\].*",
    ],
    "repository": [
        r".*[/\\]repo[s]?[/\\].*",
        r".*[/\\]persistence[/\\].*",
        r".*[/\\]dbcontext[/\\].*",
    ],
    "model": [
        r".*[/\\]model[s]?[/\\].*",
        r".*[/\\]entity[/\\].*",
        r".*[/\\]entities[/\\].*",
        r".*[/\\]schema[/\\].*",
        r".*[/\\]dto[s]?[/\\].*",
    ],
    "validator": [
        r".*[/\\]validator[s]?[/\\].*",
        r".*[/\\]validation[s]?[/\\].*",
    ],
    "utility": [
        r".*[/\\]util[s]?[/\\].*",
        r".*[/\\]helper[s]?[/\\].*",
        r".*[/\\]common[/\\].*",
        r".*[/\\]extension[s]?[/\\].*",
        r".*[/\\]factory[/\\].*",
        r".*[/\\]factories[/\\].*",
        r".*[/\\]mapper[s]?[/\\].*",
    ],
    "middleware": [
        r".*[/\\]middleware[s]?[/\\].*",
        r".*[/\\]interceptor[s]?[/\\].*",
        r".*[/\\]filter[s]?[/\\].*",
        r".*[/\\]pipeline[s]?[/\\].*",
        r".*[/\\]authorization[/\\].*",
    ],
    "config": [
        r".*[/\\]config[/\\].*",
        r".*[/\\]configuration[/\\].*",
        r".*[/\\]settings[/\\].*",
        r".*[/\\]properties[/\\].*",
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
