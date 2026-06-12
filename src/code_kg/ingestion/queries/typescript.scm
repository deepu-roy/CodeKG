; TypeScript/JavaScript tree-sitter capture queries

; Classes and declarations
(class_declaration name: (type_identifier) @class.name) @class.def
(interface_declaration name: (type_identifier) @interface.name) @interface.def
(enum_declaration name: (identifier) @enum.name) @enum.def

; Methods and properties
(method_definition name: (property_identifier) @method.name) @method.def
(property_signature name: (property_identifier) @property.name) @property.def

; Top-level functions
(function_declaration name: (identifier) @function.name) @function.def

; Arrow functions exported as const
(export_statement (lexical_declaration
  (variable_declarator name: (identifier) @function.name
    value: (arrow_function)))) @function.def

; Exported arrow functions inline
(lexical_declaration
  (variable_declarator name: (identifier) @function.name
    value: (arrow_function))) @function.def

; Function calls
(call_expression function: [
  (identifier) @call.name
  (member_expression property: (property_identifier) @call.name)
]) @call.site

; Method calls (member access followed by call)
(call_expression function: (member_expression
  object: (_) @call.object
  property: (property_identifier) @call.name
)) @call.site

; Variable declarations
(lexical_declaration (variable_declarator name: (identifier) @var.name)) @var.def

; Import statements
(import_statement source: (string (string_fragment) @import.path)) @import.def
(import_statement (import_clause (named_imports (import_specifier name: (identifier) @import.name))))

; Angular decorators (Component, Injectable, etc)
(decorator "@" (identifier) @decorator.name) @decorator.def
(decorator "@" (call_expression function: (identifier) @decorator.name)) @decorator.def

; Decorator with arguments
(decorator (call_expression
  function: (identifier) @decorator.name
  arguments: (arguments (object) @decorator.args)
)) @decorator.def

; Inheritance and interface implementation
(class_declaration
  (class_heritage
    (extends_clause
      (identifier) @inherits.name))) @inherits.def

(class_declaration
  (class_heritage
    (implements_clause
      (type_identifier) @implements.name))) @implements.def

