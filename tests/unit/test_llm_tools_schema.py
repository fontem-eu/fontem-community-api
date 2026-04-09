"""Test TOOLS schema in llm_service — kills mutmut mutations on dict keys/values."""
from src.services.llm_service import TOOLS


class TestToolsSchema:
    def test_first_tool_name_is_search_entities(self):
        assert TOOLS[0]["name"] == "search_entities"

    def test_first_tool_has_description(self):
        assert "Search for companies" in TOOLS[0]["description"]

    def test_first_tool_has_input_schema_key(self):
        assert "input_schema" in TOOLS[0]

    def test_input_schema_type_is_object(self):
        schema = TOOLS[0]["input_schema"]
        assert "type" in schema
        assert schema["type"] == "object"

    def test_input_schema_has_properties(self):
        schema = TOOLS[0]["input_schema"]
        assert "properties" in schema

    def test_properties_has_query(self):
        props = TOOLS[0]["input_schema"]["properties"]
        assert "query" in props

    def test_query_type_is_string(self):
        query = TOOLS[0]["input_schema"]["properties"]["query"]
        assert "type" in query
        assert query["type"] == "string"

    def test_query_has_description(self):
        query = TOOLS[0]["input_schema"]["properties"]["query"]
        assert "description" in query
        assert "Search query" in query["description"]

    def test_input_schema_required_includes_query(self):
        schema = TOOLS[0]["input_schema"]
        assert "required" in schema
        assert "query" in schema["required"]

    def test_limit_has_default_5(self):
        limit = TOOLS[0]["input_schema"]["properties"]["limit"]
        assert "default" in limit
        assert limit["default"] == 5
