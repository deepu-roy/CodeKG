"""Tests for code parsing runtime."""

from code_kg.ingestion.tree_sitter_runtime import detect_language, run_query


def test_detect_language():
    """Test language detection from file extension."""
    assert detect_language("app.ts") == "typescript"
    assert detect_language("app.tsx") == "typescript"
    assert detect_language("app.js") == "typescript"
    assert detect_language("service.cs") == "csharp"
    assert detect_language("unknown.xyz") is None


def test_parse_typescript_class():
    """Test extracting class from TypeScript code."""
    code = b"""
    export class MyClass {
      myMethod(): string {
        return "hello";
      }
    }
    """
    captures = run_query(code, "typescript")
    assert "class.def" in captures
    assert len(captures["class.def"]) > 0
    assert captures["class.def"][0]["name"] == "MyClass"


def test_parse_typescript_function():
    """Test extracting function from TypeScript code."""
    code = b"""
    export function myFunc() {
      return "test";
    }

    const arrow = () => {};
    """
    captures = run_query(code, "typescript")
    assert "function.def" in captures
    assert len(captures["function.def"]) >= 1


def test_parse_typescript_import():
    """Test extracting imports from TypeScript code."""
    code = b"import { Foo } from './bar';"
    captures = run_query(code, "typescript")
    assert "import.def" in captures
    assert captures["import.def"][0]["path"] == "./bar"


def test_parse_csharp_class():
    """Test extracting class from C# code."""
    code = b"""
    public class MyService
    {
        public string GetValue()
        {
            return "test";
        }
    }
    """
    captures = run_query(code, "csharp")
    assert "class.def" in captures
    assert captures["class.def"][0]["name"] == "MyService"


def test_parse_csharp_method():
    """Test extracting methods from C# code."""
    code = b"""
    public class Service
    {
        public string GetValue() => "test";
        public async Task DoWork() { }
    }
    """
    captures = run_query(code, "csharp")
    assert "method.def" in captures
    assert len(captures["method.def"]) >= 2


def test_parse_csharp_using():
    """Test extracting using statements from C# code."""
    code = b"using System.Collections;"
    captures = run_query(code, "csharp")
    assert "using.def" in captures
    assert captures["using.def"][0]["namespace"] == "System.Collections"
