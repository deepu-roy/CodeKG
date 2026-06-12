; Python tree-sitter capture queries

; Classes
(class_definition name: (identifier) @class.name) @class.def

; Top-level functions
(function_definition name: (identifier) @function.name) @function.def

; Decorated functions (treat the inner function def as the definition)
(decorated_definition (function_definition name: (identifier) @function.name)) @function.def

; Method definitions (inside class body)
(class_definition
  body: (block
    (function_definition name: (identifier) @method.name))) @method.def

; Function calls
(call function: (identifier) @call.name) @call.site
(call function: (attribute attribute: (identifier) @call.name)) @call.site

; Import statements
(import_statement (dotted_name) @import.path) @import.def
(import_from_statement module_name: (dotted_name) @import.path) @import.def
(import_from_statement module_name: (relative_import) @import.path) @import.def
