"""Neo4j schema constants."""

# Node labels
LABELS = {
    "REPO": "Repo",
    "FILE": "File",
    "MODULE": "Module",
    "CLASS": "Class",
    "INTERFACE": "Interface",
    "FUNCTION": "Function",
    "METHOD": "Method",
    "ENUM": "Enum",
    "ENDPOINT": "Endpoint",
    "DOCUMENT": "Document",
    "SECTION": "Section",
    "CONCEPT": "Concept",
    "LAYER": "Layer",
    "CODE_NODE": "CodeNode",
    "DOC_NODE": "DocNode",
}

# Relationship types
RELATIONSHIPS = {
    "CONTAINS": "CONTAINS",
    "DEFINES": "DEFINES",
    "IMPORTS": "IMPORTS",
    "CALLS": "CALLS",
    "INSTANTIATES": "INSTANTIATES",
    "EXTENDS": "EXTENDS",
    "IMPLEMENTS": "IMPLEMENTS",
    "INHERITS": "INHERITS",
    "REFERENCES": "REFERENCES",
    "EXPOSES_ENDPOINT": "EXPOSES_ENDPOINT",
    "CALLS_HTTP": "CALLS_HTTP",
    "BELONGS_TO_LAYER": "BELONGS_TO_LAYER",
    "DOCUMENTS": "DOCUMENTS",
    "MENTIONS": "MENTIONS",
    "LINKS_TO": "LINKS_TO",
    "HAS_SECTION": "HAS_SECTION",
    "IN_REPO": "IN_REPO",
    "TESTS": "TESTS",
    "RENAMED_FROM": "RENAMED_FROM",
}

# CodeNode relationship types for quick reference
CALL_RELATIONSHIPS = [
    RELATIONSHIPS["CALLS"],
    RELATIONSHIPS["INSTANTIATES"],
]

# Document relationship types
DOC_RELATIONSHIPS = [
    RELATIONSHIPS["DOCUMENTS"],
    RELATIONSHIPS["MENTIONS"],
    RELATIONSHIPS["LINKS_TO"],
]
