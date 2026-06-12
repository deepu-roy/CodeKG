; Go tree-sitter capture queries

; Struct type declarations (mapped to "class" in the graph)
(type_declaration
  (type_spec name: (type_identifier) @class.name
             type: (struct_type))) @class.def

; Interface type declarations
(type_declaration
  (type_spec name: (type_identifier) @interface.name
             type: (interface_type))) @interface.def

; Top-level function declarations
(function_declaration name: (identifier) @function.name) @function.def

; Method declarations (receiver functions)
(method_declaration name: (field_identifier) @method.name) @method.def

; Function/method calls
(call_expression function: (identifier) @call.name) @call.site
(call_expression function: (selector_expression field: (field_identifier) @call.name)) @call.site

; Import declarations
(import_spec path: (interpreted_string_literal) @import.path) @import.def
