; C# tree-sitter capture queries

; Classes and declarations
(class_declaration name: (identifier) @class.name) @class.def
(interface_declaration name: (identifier) @interface.name) @interface.def
(enum_declaration name: (identifier) @enum.name) @enum.def
(struct_declaration name: (identifier) @struct.name) @struct.def

; Methods
(method_declaration name: (identifier) @method.name) @method.def

; Properties
(property_declaration name: (identifier) @property.name) @property.def

; Fields
(field_declaration (variable_declarator (identifier) @field.name)) @field.def

; Method calls / invocations
(invocation_expression function: [
  (identifier) @call.name
  (member_access_expression name: (identifier) @call.name)
]) @call.site

; Using statements (imports)
(using_directive (qualified_name) @using.path) @using.def
(using_directive (identifier) @using.name) @using.def

; Namespace
(namespace_declaration name: (qualified_name) @namespace.name) @namespace.def

; Attributes (decorators)
(attribute name: (identifier) @attr.name) @attr.def
(attribute (generic_name name: (identifier) @attr.name)) @attr.def

; HTTP attributes and routes
(attribute name: (identifier) @http.attr (#match? @http.attr "^(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch|Route)$")) @http.def
(attribute (member_access_expression name: (identifier) @http.attr (#match? @http.attr "^(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch)$"))) @http.def

; Route attribute with string argument
(attribute name: (identifier) @route.attr (#eq? @route.attr "Route")
  (attribute_argument_list (attribute_argument (string_literal) @route.template)))

; HttpGet/HttpPost with route
(attribute name: (identifier) @http.attr (#match? @http.attr "^Http")
  (attribute_argument_list (attribute_argument (string_literal) @route.template)))

; Class inheritance
(base_list (identifier) @extends.name) @extends.def

; Constructor
(constructor_declaration name: (identifier) @ctor.name) @constructor.def

; Parameters in methods
(parameter name: (identifier) @param.name) @param.def

; Return type
(method_declaration return_type: (_) @return.type) @return.def

; Generic type parameters
(type_parameter (identifier) @generic.param) @generic.def

; Base types (first = base class if exists, rest = interfaces)
(base_list (identifier) @base.name) @base.def
