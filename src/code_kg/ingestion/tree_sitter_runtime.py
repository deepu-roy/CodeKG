"""Code parsing runtime for extraction using tree-sitter AST parsing."""

import logging
from pathlib import Path
from typing import Optional

try:
    from tree_sitter import Language, Parser
    from tree_sitter_typescript import language_typescript, language_tsx
    from tree_sitter_c_sharp import language as cs_language
    from tree_sitter_java import language as java_language
except ImportError as e:
    raise ImportError(
        "tree-sitter libraries required. Install with: "
        "pip install tree-sitter tree-sitter-typescript tree-sitter-c-sharp tree-sitter-java"
    ) from e

logger = logging.getLogger(__name__)

# Language names to file extensions
LANGUAGE_EXTENSIONS = {
    "typescript": [".ts", ".tsx", ".js", ".jsx"],
    "csharp": [".cs"],
    "java": [".java"],
    "markdown": [".md", ".markdown"],
}

# Reverse mapping
EXTENSION_TO_LANGUAGE = {}
for lang, exts in LANGUAGE_EXTENSIONS.items():
    for ext in exts:
        EXTENSION_TO_LANGUAGE[ext] = lang


class TSLanguageParser:
    """Wrapper for tree-sitter Language + Parser."""

    def __init__(self, language_capsule):
        """Initialize parser for a given language.

        Args:
            language_capsule: tree-sitter language capsule (output of language_xxx()).
        """
        self.language = Language(language_capsule)
        self.parser = Parser()
        self.parser.language = self.language

    def parse(self, code_bytes: bytes):
        """Parse code and return the AST root node.

        Args:
            code_bytes: Source code as bytes.

        Returns:
            tree-sitter Node (root of AST).
        """
        return self.parser.parse(code_bytes)


# Global parser cache
_PARSERS: dict[str, TSLanguageParser] = {}


def load_language(language_name: str) -> Optional[TSLanguageParser]:
    """Load and cache a tree-sitter language parser.

    Args:
        language_name: Language name (typescript, csharp, java).

    Returns:
        TSLanguageParser instance, or None if language not supported.
    """
    if language_name in _PARSERS:
        return _PARSERS[language_name]

    try:
        if language_name == "typescript":
            lang_capsule = language_typescript()
        elif language_name == "csharp":
            lang_capsule = cs_language()
        elif language_name == "java":
            lang_capsule = java_language()
        else:
            logger.warning(f"Language not supported: {language_name}")
            return None

        parser = TSLanguageParser(lang_capsule)
        _PARSERS[language_name] = parser
        return parser
    except Exception as e:
        logger.error(f"Failed to load language {language_name}: {e}")
        return None


def detect_language(file_path: str) -> Optional[str]:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(ext)


def _extract_text(code_bytes: bytes, start_byte: int, end_byte: int) -> str:
    """Extract text from byte range."""
    return code_bytes[start_byte:end_byte].decode("utf-8", errors="ignore")


def run_query(
    file_bytes: bytes,
    language_name: str,
) -> dict[str, list]:
    """
    Parse code and extract nodes using tree-sitter AST.

    Args:
        file_bytes: Source code as bytes.
        language_name: Language name (typescript or csharp).

    Returns:
        Dict mapping capture names to lists of extracted info.
    """
    parser = load_language(language_name)
    if parser is None:
        return {}

    try:
        tree = parser.parse(file_bytes)
    except Exception as e:
        logger.error(f"Failed to parse {language_name}: {e}")
        return {}

    captures: dict[str, list] = {}
    if language_name == "typescript":
        captures = _extract_typescript(file_bytes, tree.root_node)
    elif language_name == "csharp":
        captures = _extract_csharp(file_bytes, tree.root_node)
    elif language_name == "java":
        captures = _extract_java(file_bytes, tree.root_node)

    return captures


def _extract_typescript(file_bytes: bytes, root_node) -> dict[str, list]:
    """Extract TypeScript AST nodes."""
    captures: dict[str, list] = {}

    def traverse(node):
        # Class declarations
        if node.type in ("class_declaration", "export_statement"):
            if node.type == "export_statement":
                # Check if it contains a class
                for child in node.children:
                    if child.type == "class_declaration":
                        _process_ts_class(child, file_bytes, captures)
            elif node.type == "class_declaration":
                _process_ts_class(node, file_bytes, captures)

        # Function declarations
        if node.type in (
            "function_declaration",
            "arrow_function",
            "function_expression",
        ):
            _process_ts_function(node, file_bytes, captures)

        # Method definitions inside classes
        if node.type == "method_definition":
            _process_ts_method(node, file_bytes, captures)

        # Call expressions
        if node.type == "call_expression":
            _process_ts_call(node, file_bytes, captures)

        # Import statements
        if node.type == "import_statement":
            _process_ts_import(node, file_bytes, captures)

        # Recursively traverse children
        for child in node.children:
            traverse(child)

    traverse(root_node)
    return captures


def _process_ts_class(node, file_bytes: bytes, captures: dict[str, list]):
    """Process TypeScript class node."""
    if "class.def" not in captures:
        captures["class.def"] = []

    # Extract class name
    name = None
    for child in node.children:
        if child.type == "type_identifier":
            name = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    if name:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        # Limit text to first line for readability
        first_line = text.split("\n")[0]
        captures["class.def"].append({
            "text": first_line,
            "name": name,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_ts_function(node, file_bytes: bytes, captures: dict[str, list]):
    """Process TypeScript function node."""
    if "function.def" not in captures:
        captures["function.def"] = []

    # Extract function name
    name = None
    for child in node.children:
        if child.type in ("identifier", "property_identifier"):
            name = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    if name:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        # Limit text to first line for readability
        first_line = text.split("\n")[0]
        captures["function.def"].append({
            "text": first_line,
            "name": name,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_ts_method(node, file_bytes: bytes, captures: dict[str, list]):
    """Process TypeScript method_definition (inside a class body)."""
    if "method.def" not in captures:
        captures["method.def"] = []

    name = None
    for child in node.children:
        if child.type in ("property_identifier", "identifier"):
            name = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    if name and name not in ("constructor",):
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        first_line = text.split("\n")[0]
        captures["method.def"].append({
            "text": first_line,
            "name": name,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_ts_call(node, file_bytes: bytes, captures: dict[str, list]):
    """Process TypeScript/JavaScript call_expression → stored in call.site.

    Handles:
    - Direct calls:   ``foo(...)``          → identifier child
    - Member calls:   ``obj.foo(...)``      → member_expression child
    - Await calls:    ``await foo(...)``    → descend into inner call
    """
    if "call.site" not in captures:
        captures["call.site"] = []

    call_name = None
    func_child = node.children[0] if node.children else None
    if func_child is None:
        return

    if func_child.type == "identifier":
        call_name = _extract_text(file_bytes, func_child.start_byte, func_child.end_byte)
    elif func_child.type == "member_expression":
        # obj.method(...)  — grab the property (last) identifier
        for child in reversed(func_child.children):
            if child.type in ("property_identifier", "identifier"):
                call_name = _extract_text(file_bytes, child.start_byte, child.end_byte)
                break

    if call_name:
        captures["call.site"].append({
            "name": call_name,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_ts_import(node, file_bytes: bytes, captures: dict[str, list]):
    """Process TypeScript import statement."""
    if "import.def" not in captures:
        captures["import.def"] = []

    # Extract import path
    path = None
    for child in node.children:
        if child.type == "string":
            # Remove quotes
            text = _extract_text(file_bytes, child.start_byte, child.end_byte)
            path = text.strip('"\'')
            break

    if path:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        captures["import.def"].append({
            "text": text,
            "path": path,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _extract_csharp(file_bytes: bytes, root_node) -> dict[str, list]:
    """Extract C# AST nodes."""
    captures: dict[str, list] = {}

    def traverse(node):
        # Class declarations
        if node.type == "class_declaration":
            _process_cs_class(node, file_bytes, captures)

        # Struct declarations (treated like classes)
        if node.type == "struct_declaration":
            _process_cs_class(node, file_bytes, captures, key="class.def")

        # Interface declarations
        if node.type == "interface_declaration":
            _process_cs_class(node, file_bytes, captures, key="interface.def")

        # Method declarations
        if node.type == "method_declaration":
            _process_cs_method(node, file_bytes, captures)

        # Constructor declarations
        if node.type == "constructor_declaration":
            _process_cs_constructor(node, file_bytes, captures)

        # Method call sites — invocation_expression covers both direct and chained calls
        if node.type == "invocation_expression":
            _process_cs_call(node, file_bytes, captures)

        # Using directives
        if node.type == "using_directive":
            _process_cs_using(node, file_bytes, captures)

        # Recursively traverse children
        for child in node.children:
            traverse(child)

    traverse(root_node)
    return captures


def _process_cs_class(
    node, file_bytes: bytes, captures: dict[str, list], key: str = "class.def"
):
    """Process C# class / struct / interface node."""
    if key not in captures:
        captures[key] = []

    # Extract class name
    name = None
    for child in node.children:
        if child.type == "identifier":
            name = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    if name:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        first_line = text.split("\n")[0]
        captures[key].append({
            "text": first_line,
            "name": name,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_cs_method(node, file_bytes: bytes, captures: dict[str, list]):
    """Process C# method node."""
    if "method.def" not in captures:
        captures["method.def"] = []

    # Extract method name and return type
    # In C# AST: modifiers, return_type, method_name, parameter_list, block
    # The pattern is usually: <modifiers> <return_type> <name> (...)
    # Return type can be: predefined_type, identifier, generic_type, etc.
    # Method name is always an identifier

    method_name = None
    return_type = None
    found_type = False

    # First pass: find return type
    for child in node.children:
        if child.type in ("predefined_type", "generic_type", "nullable_type"):
            return_type = _extract_text(file_bytes, child.start_byte, child.end_byte)
            found_type = True
            break

    # Second pass: find method name (last identifier, or first identifier if no type found)
    identifiers = []
    for child in node.children:
        if child.type == "identifier":
            identifiers.append(_extract_text(file_bytes, child.start_byte, child.end_byte))

    if identifiers:
        method_name = identifiers[-1]

        # If we didn't find a predefined return type, check if first identifier is return type
        if not found_type and len(identifiers) > 1:
            return_type = identifiers[0]

    if method_name:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        # Limit text to first line for readability
        first_line = text.split("\n")[0]
        captures["method.def"].append({
            "text": first_line,
            "name": method_name,
            "return_type": return_type or "unknown",
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_cs_constructor(node, file_bytes: bytes, captures: dict[str, list]):
    """Process C# constructor_declaration → stored in constructor.def."""
    if "constructor.def" not in captures:
        captures["constructor.def"] = []

    name = None
    for child in node.children:
        if child.type == "identifier":
            name = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    if name:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        first_line = text.split("\n")[0]
        captures["constructor.def"].append({
            "text": first_line,
            "name": name,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_cs_call(node, file_bytes: bytes, captures: dict[str, list]):
    """Process C# invocation_expression → stored in call.site.

    Handles both direct calls ``Method(...)`` and chained calls
    ``obj.Method(...)`` by inspecting the ``function`` child.
    """
    if "call.site" not in captures:
        captures["call.site"] = []

    call_name = None
    # invocation_expression has a first child that is the function expression
    func_child = node.children[0] if node.children else None
    if func_child is None:
        return

    if func_child.type == "identifier":
        # Direct call: Foo(...)
        call_name = _extract_text(file_bytes, func_child.start_byte, func_child.end_byte)
    elif func_child.type == "member_access_expression":
        # Chained call: obj.Foo(...)
        # Last child of member_access_expression is the method name identifier
        for child in reversed(func_child.children):
            if child.type == "identifier":
                call_name = _extract_text(file_bytes, child.start_byte, child.end_byte)
                break

    if call_name:
        captures["call.site"].append({
            "name": call_name,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_cs_using(node, file_bytes: bytes, captures: dict[str, list]):
    """Process C# using directive."""
    if "using.def" not in captures:
        captures["using.def"] = []

    # Extract namespace
    namespace = None
    for child in node.children:
        if child.type in ("identifier", "qualified_name"):
            namespace = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    if namespace:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        captures["using.def"].append({
            "text": text,
            "namespace": namespace,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _extract_java(file_bytes: bytes, root_node) -> dict[str, list]:
    """Extract Java AST nodes."""
    captures: dict[str, list] = {}

    def traverse(node):
        # Class declarations
        if node.type == "class_declaration":
            _process_java_class(node, file_bytes, captures)

        # Method declarations
        if node.type == "method_declaration":
            _process_java_method(node, file_bytes, captures)

        # Import statements
        if node.type == "import_declaration":
            _process_java_import(node, file_bytes, captures)

        # Interface declarations
        if node.type == "interface_declaration":
            _process_java_interface(node, file_bytes, captures)

        # Recursively traverse children
        for child in node.children:
            traverse(child)

    traverse(root_node)
    return captures


def _process_java_class(node, file_bytes: bytes, captures: dict[str, list]):
    """Process Java class node."""
    if "class.def" not in captures:
        captures["class.def"] = []

    # Extract class name
    name = None
    for child in node.children:
        if child.type == "identifier":
            name = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    if name:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        # Limit text to first line for readability
        first_line = text.split("\n")[0]
        captures["class.def"].append({
            "text": first_line,
            "name": name,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_java_method(node, file_bytes: bytes, captures: dict[str, list]):
    """Process Java method node."""
    if "method.def" not in captures:
        captures["method.def"] = []

    # Extract method name and return type
    # Java AST: modifiers, return_type, identifier (method_name), parameters, ...
    method_name = None
    return_type = None

    # First pass: find return type (can be identifier, type_identifier, etc.)
    for child in node.children:
        if child.type in ("type_identifier", "identifier", "generic_type", "void_type"):
            if child.type == "void_type":
                return_type = "void"
            else:
                return_type = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    # Second pass: find method name (last identifier or the identifier after type)
    identifiers = []
    for child in node.children:
        if child.type == "identifier":
            identifiers.append(_extract_text(file_bytes, child.start_byte, child.end_byte))

    if identifiers:
        method_name = identifiers[-1]
        # If we didn't find return type, check if first identifier is return type
        if not return_type and len(identifiers) > 1:
            return_type = identifiers[0]

    if method_name:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        # Limit text to first line for readability
        first_line = text.split("\n")[0]
        captures["method.def"].append({
            "text": first_line,
            "name": method_name,
            "return_type": return_type or "unknown",
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_java_interface(node, file_bytes: bytes, captures: dict[str, list]):
    """Process Java interface node."""
    if "interface.def" not in captures:
        captures["interface.def"] = []

    # Extract interface name
    name = None
    for child in node.children:
        if child.type == "identifier":
            name = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    if name:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        # Limit text to first line for readability
        first_line = text.split("\n")[0]
        captures["interface.def"].append({
            "text": first_line,
            "name": name,
            "start": node.start_byte,
            "end": node.end_byte,
        })


def _process_java_import(node, file_bytes: bytes, captures: dict[str, list]):
    """Process Java import statement."""
    if "import.def" not in captures:
        captures["import.def"] = []

    # Extract import path
    path = None
    for child in node.children:
        if child.type == "scoped_identifier":
            path = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break
        elif child.type == "identifier":
            path = _extract_text(file_bytes, child.start_byte, child.end_byte)
            break

    if path:
        text = _extract_text(file_bytes, node.start_byte, node.end_byte)
        captures["import.def"].append({
            "text": text,
            "path": path,
            "start": node.start_byte,
            "end": node.end_byte,
        })
