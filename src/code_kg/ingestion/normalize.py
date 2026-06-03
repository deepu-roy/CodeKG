"""Normalization: symbol registry and call resolution.

Two-pass ingestion:
1. First pass: extract all code nodes (classes, functions, methods) → symbol registry
2. Second pass: resolve call sites and imports using the registry
"""

import logging
from collections import defaultdict
from typing import Optional

from code_kg.domain.ids import (
    make_class_id,
    make_function_id,
    make_interface_id,
    make_method_id,
)
from code_kg.domain.models import RawEdge, RawNode

logger = logging.getLogger(__name__)


def _generate_node_id(node: RawNode, repo: Optional[str] = None) -> str:
    """Generate a stable ID for a raw node.

    Args:
        node: The RawNode to generate an ID for.
        repo: Optional repository name (uses node.repo if not provided).

    Returns:
        A stable ID string.
    """
    repo = repo or node.repo
    file_path = node.file_path

    if node.type == "class":
        return make_class_id(repo, file_path, node.name)
    elif node.type == "interface":
        return make_interface_id(repo, file_path, node.name)
    elif node.type == "function":
        sig = node.signature or ""
        return make_function_id(repo, file_path, node.name, sig)
    elif node.type == "method":
        # For methods, we'd need the class name, which we don't have here
        # Fall back to function ID
        sig = node.signature or ""
        return make_function_id(repo, file_path, node.name, sig)
    else:
        # Fallback: use name as ID
        return f"{node.type}:{repo}:{file_path}:{node.name}"


class SymbolRegistry:
    """Registry of all extracted symbols (classes, functions, methods, etc.)."""

    def __init__(self):
        """Initialize symbol registry."""
        # Map from symbol name to list of (RawNode, file_path) tuples
        self.symbols_by_name: dict[str, list[tuple[RawNode, str]]] = defaultdict(list)

        # Map from (class_name, method_name) to list of RawNode for method overloads
        self.methods_by_class: dict[tuple[str, str], list[RawNode]] = defaultdict(list)

        # Map from file_path to set of imported namespaces/modules
        self.imports_by_file: dict[str, set[str]] = defaultdict(set)

    def register_symbol(self, node: RawNode, file_path: str) -> None:
        """Register a symbol in the registry.

        Args:
            node: The RawNode representing a symbol.
            file_path: The file path where the symbol is defined.
        """
        if node.type not in ("class", "interface", "function", "enum"):
            return

        # Register by simple name (for quick lookup)
        if node.name:
            self.symbols_by_name[node.name].append((node, file_path))

    def register_method(self, class_name: str, node: RawNode) -> None:
        """Register a method under its containing class.

        Args:
            class_name: The name of the class containing this method.
            node: The RawNode representing the method.
        """
        if node.type == "function" and node.name:
            key = (class_name, node.name)
            self.methods_by_class[key].append(node)

    def register_import(self, file_path: str, import_namespace: str) -> None:
        """Register an import for a file.

        Args:
            file_path: The file that contains the import.
            import_namespace: The namespace or module being imported.
        """
        self.imports_by_file[file_path].add(import_namespace)

    def resolve_symbol(self, symbol_name: str, file_path: Optional[str] = None) -> Optional[RawNode]:
        """Resolve a symbol by name, optionally scoped to a file.

        Args:
            symbol_name: The symbol name to resolve.
            file_path: Optional file path to scope the search.

        Returns:
            The resolved RawNode, or None if not found.
        """
        if symbol_name not in self.symbols_by_name:
            return None

        candidates = self.symbols_by_name[symbol_name]

        # If file_path is specified, prefer symbols from that file
        if file_path:
            for node, node_file in candidates:
                if node_file == file_path:
                    return node

        # Fall back to first match
        return candidates[0][0] if candidates else None

    def resolve_method(
        self, class_name: str, method_name: str
    ) -> Optional[RawNode]:
        """Resolve a method within a class.

        Args:
            class_name: The class name.
            method_name: The method name.

        Returns:
            The resolved RawNode method, or None if not found.
        """
        key = (class_name, method_name)
        if key not in self.methods_by_class:
            return None

        candidates = self.methods_by_class[key]
        return candidates[0] if candidates else None


class CallResolver:
    """Resolves call sites to their target symbols using the symbol registry."""

    def __init__(self, registry: SymbolRegistry):
        """Initialize call resolver.

        Args:
            registry: The symbol registry.
        """
        self.registry = registry

    def resolve_call(
        self, call_name: str, from_file: str, from_context: Optional[str] = None
    ) -> Optional[tuple[str, str]]:
        """Resolve a call site to a target symbol ID.

        Args:
            call_name: The name being called (e.g., 'foo.bar' or 'MyClass.method').
            from_file: The file path where the call originates.
            from_context: Optional context (e.g., the class name if call is from a method).

        Returns:
            A tuple of (symbol_type, symbol_id), or None if not resolvable.
        """
        # Simple cases: direct function/method call
        if "." in call_name:
            # Method call: foo.bar() → resolve foo as a type, then bar as a method
            parts = call_name.split(".")
            receiver = parts[0]
            method_name = parts[1]

            # Try to resolve receiver to a class
            class_node = self.registry.resolve_symbol(receiver, from_file)
            if class_node and class_node.type in ("class", "interface"):
                # Now resolve the method in that class
                method_node = self.registry.resolve_method(class_node.name, method_name)
                if method_node:
                    node_id = _generate_node_id(method_node)
                    return ("function", node_id)

        # Direct function call
        symbol_node = self.registry.resolve_symbol(call_name, from_file)
        if symbol_node and symbol_node.type == "function":
            node_id = _generate_node_id(symbol_node)
            return ("function", node_id)

        return None


async def normalize_nodes_and_edges(
    raw_nodes: list[RawNode],
    raw_edges: list[RawEdge],
    file_map: dict[str, str],  # Maps relative path to absolute path
) -> tuple[list[RawNode], list[RawEdge]]:
    """Normalize raw nodes and edges using two-pass extraction.

    First pass: Extract all nodes and build symbol registry.
    Second pass: Resolve call sites and imports.

    Args:
        raw_nodes: List of extracted raw nodes.
        raw_edges: List of extracted raw edges.
        file_map: Mapping from relative to absolute file paths.

    Returns:
        Tuple of (normalized_nodes, normalized_edges).
    """
    registry = SymbolRegistry()

    # First pass: register all symbols
    file_path_reverse = {v: k for k, v in file_map.items()}
    for node in raw_nodes:
        if node.file_path and node.file_path in file_path_reverse:
            rel_path = file_path_reverse[node.file_path]
            registry.register_symbol(node, rel_path)

    # Second pass: resolve edges
    resolver = CallResolver(registry)
    normalized_edges = []

    for edge in raw_edges:
        if edge.type == "CALLS":
            # Try to resolve the call site
            call_name = edge.metadata.get("call_name", "")
            # Note: from_id is not yet resolved, so we defer resolution

            normalized_edges.append(edge)
        elif edge.type == "IMPORTS":
            # Register imports in registry
            import_path = edge.metadata.get("import_path") or edge.metadata.get("namespace", "")
            if edge.from_id.startswith("file:"):
                from_file = edge.from_id.split(":")[-1]
                registry.register_import(from_file, import_path)

            normalized_edges.append(edge)
        else:
            normalized_edges.append(edge)

    return raw_nodes, normalized_edges
