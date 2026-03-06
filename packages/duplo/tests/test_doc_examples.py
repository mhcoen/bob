"""Tests for duplo.doc_examples."""

from __future__ import annotations

from duplo.doc_examples import extract_code_examples


class TestLabeledPairs:
    def test_input_output_headings(self):
        html = """
        <html><body>
        <h3>Input</h3>
        <pre><code>x = 1 + 2</code></pre>
        <h3>Output</h3>
        <pre><code>3</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html, "https://example.com/docs")
        assert len(examples) == 1
        assert examples[0].input == "x = 1 + 2"
        assert examples[0].expected_output == "3"
        assert examples[0].source_url == "https://example.com/docs"

    def test_example_result_headings(self):
        html = """
        <html><body>
        <h3>Example</h3>
        <pre><code>curl /api/users</code></pre>
        <h3>Result</h3>
        <pre><code>{"users": []}</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].input == "curl /api/users"
        assert examples[0].expected_output == '{"users": []}'

    def test_request_response_paragraphs(self):
        html = """
        <html><body>
        <p>Request example</p>
        <pre><code>GET /api/health</code></pre>
        <p>Response</p>
        <pre><code>{"status": "ok"}</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].input == "GET /api/health"

    def test_no_match_without_labels(self):
        html = """
        <html><body>
        <h3>Setup</h3>
        <pre><code>pip install foo</code></pre>
        <h3>Configuration</h3>
        <pre><code>foo.ini</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 0

    def test_language_from_class(self):
        html = """
        <html><body>
        <h3>Example code</h3>
        <pre><code class="language-python">print("hi")</code></pre>
        <h3>Output</h3>
        <pre><code>hi</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].language == "python"


class TestDoctest:
    def test_simple_doctest(self):
        html = """
        <html><body>
        <pre><code>>>> 1 + 2
3</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].input == "1 + 2"
        assert examples[0].expected_output == "3"
        assert examples[0].language == "python"

    def test_multiline_doctest(self):
        html = """
        <html><body>
        <pre><code>>>> for i in range(3):
...     print(i)
0
1
2</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert "for i in range(3):" in examples[0].input
        assert "    print(i)" in examples[0].input
        assert examples[0].expected_output == "0\n1\n2"

    def test_multiple_doctests_keeps_last(self):
        html = """
        <html><body>
        <pre><code>>>> 1 + 1
2
>>> 3 + 4
7</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].input == "3 + 4"
        assert examples[0].expected_output == "7"

    def test_no_output_skipped(self):
        html = """
        <html><body>
        <pre><code>>>> import os</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 0


class TestShell:
    def test_simple_shell(self):
        html = """
        <html><body>
        <pre><code>$ echo hello
hello</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].input == "echo hello"
        assert examples[0].expected_output == "hello"
        assert examples[0].language == "shell"

    def test_percent_prompt(self):
        html = """
        <html><body>
        <pre><code>% ls
file1.txt
file2.txt</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].input == "ls"
        assert examples[0].expected_output == "file1.txt\nfile2.txt"

    def test_multiline_output(self):
        html = """
        <html><body>
        <pre><code>$ cat /etc/hostname
myhost</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].expected_output == "myhost"

    def test_no_output_skipped(self):
        html = """
        <html><body>
        <pre><code>$ pip install requests</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 0

    def test_lang_class_preserved(self):
        html = """
        <html><body>
        <pre><code class="language-bash">$ echo hi
hi</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].language == "bash"


class TestEdgeCases:
    def test_empty_html(self):
        assert extract_code_examples("<html><body></body></html>") == []

    def test_no_code_blocks(self):
        html = "<html><body><p>No code here</p></body></html>"
        assert extract_code_examples(html) == []

    def test_empty_code_block(self):
        html = "<html><body><pre><code></code></pre></body></html>"
        assert extract_code_examples(html) == []

    def test_inline_code_skipped(self):
        html = "<html><body><p>Use <code>pip install</code> to install.</p></body></html>"
        assert extract_code_examples(html) == []

    def test_pre_without_code(self):
        html = """
        <html><body>
        <h3>Example</h3>
        <pre>some input</pre>
        <h3>Output</h3>
        <pre>some output</pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 1
        assert examples[0].input == "some input"
        assert examples[0].expected_output == "some output"

    def test_source_url_default_empty(self):
        html = """
        <html><body>
        <pre><code>>>> 1
1</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert examples[0].source_url == ""

    def test_lang_class_prefix(self):
        html = """
        <html><body>
        <pre><code class="lang-js">>>> 1
1</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert examples[0].language == "js"

    def test_mixed_strategies(self):
        html = """
        <html><body>
        <h3>Usage example</h3>
        <pre><code>fetch("/api")</code></pre>
        <h3>Returns</h3>
        <pre><code>{"ok": true}</code></pre>

        <pre><code>$ curl localhost
pong</code></pre>
        </body></html>
        """
        examples = extract_code_examples(html)
        assert len(examples) == 2
