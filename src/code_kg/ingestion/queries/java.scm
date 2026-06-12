; Java tree-sitter capture queries

; Classes
(class_declaration name: (identifier) @class.name) @class.def

; Interfaces
(interface_declaration name: (identifier) @interface.name) @interface.def

; Methods
(method_declaration name: (identifier) @method.name) @method.def

; Constructors
(constructor_declaration name: (identifier) @ctor.name) @constructor.def

; Import statements
(import_declaration package: (scoped_identifier) @import.path) @import.def
(import_declaration package: (identifier) @import.path) @import.def

; Variable declarations
(variable_declarator name: (identifier) @var.name) @var.def

; Method calls
(method_invocation name: (identifier) @call.name) @call.site
(method_invocation object: (identifier) @call.object name: (identifier) @call.name) @call.site

; Field declarations
(field_declaration (variable_declarator name: (identifier) @field.name)) @field.def

; Interface method declarations
(interface_method_declaration name: (identifier) @method.name) @method.def

; Annotations
(annotation name: (identifier) @annotation.name) @annotation.def

; Type annotations
(type_identifier) @type.annotation

; Inheritance and interface implementation
(class_declaration
  (superclass (type_identifier) @inherits.name)) @inherits.def

(class_declaration
  (super_interfaces
    (type_list (type_identifier) @implements.name))) @implements.def
