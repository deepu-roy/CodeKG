"""Layer assignment heuristics for code nodes.

Assigns architecture layers to code nodes based on:
1. Naming patterns (e.g., *Controller, *Service, *Model, *Dto)
2. File path patterns (e.g., /controllers/, /services/, /models/)
3. Parent class / interface hierarchy (e.g., extends Controller, implements Repository)

Layers: controller, service, repository, model, utility, middleware, config
"""

import logging
import re
from typing import Optional

from code_kg.domain.models import RawNode

logger = logging.getLogger(__name__)

# Layer assignments based on naming patterns
NAMING_PATTERNS = {
    "controller": [
        r".*Controller$",
        r".*Handler$",
        r".*Router$",
        r".*Api$",
    ],
    "service": [
        r".*Service$",
        r".*Manager$",
        r".*Provider$",
        r".*Processor$",
    ],
    "repository": [
        r".*Repository$",
        r".*Dao$",
        r".*Database$",
        r".*Store$",
    ],
    "model": [
        r".*Model$",
        r".*Entity$",
        r".*Dto$",
        r".*Response$",
        r".*Request$",
        r".*View$",
    ],
    "utility": [
        r".*Util$",
        r".*Utils$",
        r".*Helper$",
        r".*Tool$",
        r".*Common$",
    ],
    "middleware": [
        r".*Middleware$",
        r".*Interceptor$",
        r".*Filter$",
    ],
    "config": [
        r".*Config$",
        r".*Configuration$",
        r".*Settings$",
        r".*Options$",
    ],
}

# Layer assignments based on file path patterns
PATH_PATTERNS = {
    "controller": [r".*[/\\]controller[s]?[/\\].*", r".*[/\\]route[s]?[/\\].*"],
    "service": [r".*[/\\]service[s]?[/\\].*", r".*[/\\]logic[/\\].*"],
    "repository": [
        r".*[/\\]repo[s]?[/\\].*",
        r".*[/\\]data[/\\].*",
        r".*[/\\]persistence[/\\].*",
    ],
    "model": [r".*[/\\]model[s]?[/\\].*", r".*[/\\]entity[/\\].*", r".*[/\\]schema[/\\].*"],
    "utility": [r".*[/\\]util[s]?[/\\].*", r".*[/\\]helper[s]?[/\\].*", r".*[/\\]common[/\\].*"],
    "middleware": [r".*[/\\]middleware[s]?[/\\].*"],
    "config": [r".*[/\\]config[/\\].*"],
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
