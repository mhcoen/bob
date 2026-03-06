"""Tests for duplo.doc_tables."""

from __future__ import annotations

from duplo.doc_tables import extract_doc_structures


class TestFeatureTables:
    def test_table_with_feature_heading(self):
        html = """
        <html><body>
        <h2>Supported Features</h2>
        <table>
          <tr><th>Feature</th><th>Description</th></tr>
          <tr><td>Dark mode</td><td>Toggle dark theme</td></tr>
          <tr><td>Export</td><td>Export to CSV</td></tr>
        </table>
        </body></html>
        """
        result = extract_doc_structures(html, "https://example.com")
        assert len(result.feature_tables) == 1
        assert result.feature_tables[0].heading == "Supported Features"
        assert len(result.feature_tables[0].rows) == 2
        assert result.feature_tables[0].rows[0]["Feature"] == "Dark mode"
        assert result.feature_tables[0].source_url == "https://example.com"

    def test_feature_list_from_ul(self):
        html = """
        <html><body>
        <h3>Available Features</h3>
        <ul>
          <li>Real-time sync</li>
          <li>Offline mode</li>
          <li>Multi-user editing</li>
        </ul>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.feature_tables) == 1
        assert len(result.feature_tables[0].rows) == 3
        assert result.feature_tables[0].rows[0]["item"] == "Real-time sync"

    def test_unclassified_table_defaults_to_feature(self):
        html = """
        <html><body>
        <h2>Overview</h2>
        <table>
          <tr><th>Name</th><th>Status</th></tr>
          <tr><td>Widget A</td><td>Active</td></tr>
          <tr><td>Widget B</td><td>Beta</td></tr>
        </table>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.feature_tables) == 1


class TestOperationLists:
    def test_operations_table(self):
        html = """
        <html><body>
        <h2>API Methods</h2>
        <table>
          <tr><th>Method</th><th>Path</th></tr>
          <tr><td>GET</td><td>/users</td></tr>
          <tr><td>POST</td><td>/users</td></tr>
        </table>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.operation_lists) == 1
        assert len(result.operation_lists[0].items) == 2
        assert "GET" in result.operation_lists[0].items[0]

    def test_operations_from_ul(self):
        html = """
        <html><body>
        <h3>Available Commands</h3>
        <ul>
          <li>init - Initialize project</li>
          <li>build - Build the project</li>
          <li>test - Run tests</li>
        </ul>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.operation_lists) == 1
        assert len(result.operation_lists[0].items) == 3

    def test_operations_from_dl(self):
        html = """
        <html><body>
        <h3>Operations</h3>
        <dl>
          <dt>create</dt><dd>Create a resource</dd>
          <dt>delete</dt><dd>Delete a resource</dd>
        </dl>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.operation_lists) == 1
        assert result.operation_lists[0].items == ["create", "delete"]


class TestUnitLists:
    def test_units_table(self):
        html = """
        <html><body>
        <h2>Supported Units</h2>
        <table>
          <tr><th>Unit</th><th>Symbol</th></tr>
          <tr><td>Meter</td><td>m</td></tr>
          <tr><td>Kilogram</td><td>kg</td></tr>
        </table>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.unit_lists) == 1
        assert len(result.unit_lists[0].items) == 2

    def test_types_from_ul(self):
        html = """
        <html><body>
        <h3>Data Types</h3>
        <ul>
          <li>string</li>
          <li>number</li>
          <li>boolean</li>
        </ul>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.unit_lists) == 1
        assert result.unit_lists[0].items == ["string", "number", "boolean"]

    def test_enum_from_dl(self):
        html = """
        <html><body>
        <h3>Enum Values</h3>
        <dl>
          <dt>PENDING</dt><dd>Not started</dd>
          <dt>ACTIVE</dt><dd>In progress</dd>
        </dl>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.unit_lists) == 1


class TestFunctionRefs:
    def test_functions_table(self):
        html = """
        <html><body>
        <h2>API Functions</h2>
        <table>
          <tr><th>Function</th><th>Description</th></tr>
          <tr><td>create_user(name)</td><td>Creates a new user</td></tr>
          <tr><td>delete_user(id)</td><td>Deletes a user</td></tr>
        </table>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.function_refs) == 2
        assert result.function_refs[0].name == "create_user(name)"
        assert result.function_refs[0].description == "Creates a new user"

    def test_functions_from_dl(self):
        html = """
        <html><body>
        <h3>Functions</h3>
        <dl>
          <dt>def connect(host, port)</dt>
          <dd>Connect to the server.</dd>
          <dt>def disconnect()</dt>
          <dd>Close the connection.</dd>
        </dl>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.function_refs) == 2
        assert result.function_refs[0].name == "connect"
        assert result.function_refs[0].signature == "def connect(host, port)"
        assert result.function_refs[0].description == "Connect to the server."

    def test_signature_in_code_tag(self):
        html = """
        <html><body>
        <h3><code>fetch(url, options)</code></h3>
        <p>Fetches a resource from the given URL.</p>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.function_refs) == 1
        assert result.function_refs[0].name == "fetch"
        assert result.function_refs[0].description == ("Fetches a resource from the given URL.")

    def test_signature_in_dl_without_heading(self):
        html = """
        <html><body>
        <h3>Reference</h3>
        <dl>
          <dt>parse(text)</dt>
          <dd>Parse the input text.</dd>
        </dl>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.function_refs) == 1
        assert result.function_refs[0].name == "parse"


class TestDocStructures:
    def test_bool_empty(self):
        result = extract_doc_structures("<html><body></body></html>")
        assert not result

    def test_bool_with_data(self):
        html = """
        <html><body>
        <h2>Features</h2>
        <table>
          <tr><th>Name</th></tr>
          <tr><td>A</td></tr>
        </table>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert result

    def test_merge(self):
        html1 = """
        <html><body>
        <h2>Features</h2>
        <table>
          <tr><th>Name</th></tr>
          <tr><td>A</td></tr>
        </table>
        </body></html>
        """
        html2 = """
        <html><body>
        <h2>Capabilities</h2>
        <table>
          <tr><th>Name</th></tr>
          <tr><td>B</td></tr>
        </table>
        </body></html>
        """
        r1 = extract_doc_structures(html1)
        r2 = extract_doc_structures(html2)
        r1.merge(r2)
        assert len(r1.feature_tables) == 2

    def test_empty_html(self):
        result = extract_doc_structures("<html><body></body></html>")
        assert result.feature_tables == []
        assert result.operation_lists == []
        assert result.unit_lists == []
        assert result.function_refs == []

    def test_table_without_headers_skipped(self):
        html = """
        <html><body>
        <table>
          <tr><td>A</td><td>B</td></tr>
        </table>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert not result

    def test_short_list_skipped(self):
        html = """
        <html><body>
        <h3>Commands</h3>
        <ul>
          <li>one</li>
          <li>two</li>
        </ul>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.operation_lists) == 0

    def test_nav_list_skipped(self):
        html = """
        <html><body>
        <nav>
          <h3>Commands</h3>
          <ul>
            <li>one</li>
            <li>two</li>
            <li>three</li>
          </ul>
        </nav>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert len(result.operation_lists) == 0

    def test_list_without_heading_skipped(self):
        html = """
        <html><body>
        <ul>
          <li>alpha</li>
          <li>beta</li>
          <li>gamma</li>
        </ul>
        </body></html>
        """
        result = extract_doc_structures(html)
        assert not result

    def test_source_url_propagated(self):
        html = """
        <html><body>
        <h2>Supported Types</h2>
        <ul>
          <li>int</li>
          <li>float</li>
          <li>str</li>
        </ul>
        </body></html>
        """
        result = extract_doc_structures(html, "https://docs.example.com/ref")
        assert result.unit_lists[0].source_url == "https://docs.example.com/ref"
