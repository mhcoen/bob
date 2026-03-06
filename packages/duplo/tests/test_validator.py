"""Tests for duplo.validator."""

from __future__ import annotations

import json
from unittest.mock import patch

from duplo.validator import ValidationResult, _parse_result, validate_product_url


class TestParseResult:
    def test_parses_single_product(self):
        raw = json.dumps(
            {
                "single_product": True,
                "product_name": "Acme Widget",
                "products": [],
                "reason": "Page describes one product.",
            }
        )
        result = _parse_result(raw)
        assert result.single_product is True
        assert result.product_name == "Acme Widget"
        assert result.products == []

    def test_parses_multiple_products(self):
        raw = json.dumps(
            {
                "single_product": False,
                "product_name": "",
                "products": ["Widget A", "Widget B", "Widget C"],
                "reason": "Portfolio page.",
            }
        )
        result = _parse_result(raw)
        assert result.single_product is False
        assert result.products == ["Widget A", "Widget B", "Widget C"]

    def test_handles_code_fences(self):
        inner = json.dumps(
            {
                "single_product": True,
                "product_name": "Fenced",
                "products": [],
                "reason": "ok",
            }
        )
        raw = f"```json\n{inner}\n```"
        result = _parse_result(raw)
        assert result.single_product is True
        assert result.product_name == "Fenced"

    def test_handles_invalid_json(self):
        result = _parse_result("not json at all")
        assert result.single_product is True
        assert "Could not parse" in result.reason

    def test_handles_non_dict_json(self):
        result = _parse_result("[1, 2, 3]")
        assert result.single_product is True
        assert "Unexpected" in result.reason

    def test_missing_fields_use_defaults(self):
        raw = json.dumps({"single_product": False})
        result = _parse_result(raw)
        assert result.single_product is False
        assert result.product_name == ""
        assert result.products == []
        assert result.reason == ""


class TestValidateProductUrl:
    def test_calls_api_with_fetched_text(self):
        mock_response = type(
            "Resp",
            (),
            {
                "content": [
                    type(
                        "Block",
                        (),
                        {
                            "text": json.dumps(
                                {
                                    "single_product": True,
                                    "product_name": "TestProd",
                                    "products": [],
                                    "reason": "Single product page.",
                                }
                            )
                        },
                    )()
                ]
            },
        )()

        mock_client = type(
            "Client",
            (),
            {
                "messages": type(
                    "Messages", (), {"create": staticmethod(lambda **kw: mock_response)}
                )()
            },
        )()

        result = validate_product_url(
            "https://example.com",
            client=mock_client,
            text="Product page content",
        )
        assert result.single_product is True
        assert result.product_name == "TestProd"

    def test_fetches_url_when_no_text_provided(self):
        mock_response = type(
            "Resp",
            (),
            {
                "content": [
                    type(
                        "Block",
                        (),
                        {
                            "text": json.dumps(
                                {
                                    "single_product": True,
                                    "product_name": "Fetched",
                                    "products": [],
                                    "reason": "ok",
                                }
                            )
                        },
                    )()
                ]
            },
        )()

        mock_client = type(
            "Client",
            (),
            {
                "messages": type(
                    "Messages", (), {"create": staticmethod(lambda **kw: mock_response)}
                )()
            },
        )()

        with patch("duplo.validator.fetch_text", return_value="fetched content") as m:
            result = validate_product_url(
                "https://example.com",
                client=mock_client,
            )
        m.assert_called_once_with("https://example.com")
        assert result.product_name == "Fetched"

    def test_multi_product_result(self):
        mock_response = type(
            "Resp",
            (),
            {
                "content": [
                    type(
                        "Block",
                        (),
                        {
                            "text": json.dumps(
                                {
                                    "single_product": False,
                                    "product_name": "",
                                    "products": ["Alpha", "Beta"],
                                    "reason": "Portfolio page.",
                                }
                            )
                        },
                    )()
                ]
            },
        )()

        mock_client = type(
            "Client",
            (),
            {
                "messages": type(
                    "Messages", (), {"create": staticmethod(lambda **kw: mock_response)}
                )()
            },
        )()

        result = validate_product_url(
            "https://company.com",
            client=mock_client,
            text="Company portfolio page content",
        )
        assert result.single_product is False
        assert result.products == ["Alpha", "Beta"]


class TestValidateUrlInMain:
    """Test _validate_url integration in main._first_run."""

    def test_single_product_proceeds(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=True,
            product_name="TestApp",
            products=[],
            reason="Single product page.",
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            url = _validate_url("https://example.com")

        assert url == "https://example.com"
        out = capsys.readouterr().out
        assert "TestApp" in out

    def test_multi_product_prompts_user(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=["Alpha", "Beta"],
            reason="Portfolio page.",
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value="https://alpha.example.com"):
                url = _validate_url("https://company.com")

        assert url == "https://alpha.example.com"
        out = capsys.readouterr().out
        assert "Alpha" in out
        assert "Beta" in out

    def test_multi_product_user_enters_empty(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=["X"],
            reason="Multiple products.",
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value=""):
                url = _validate_url("https://company.com")

        assert url == "https://company.com"

    def test_validation_error_proceeds(self, capsys):
        from duplo.main import _validate_url

        with patch(
            "duplo.main.validate_product_url",
            side_effect=RuntimeError("network error"),
        ):
            url = _validate_url("https://example.com")

        assert url == "https://example.com"
        out = capsys.readouterr().out
        assert "network error" in out
