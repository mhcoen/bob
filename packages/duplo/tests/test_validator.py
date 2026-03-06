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

    def test_parses_unclear_boundaries(self):
        raw = json.dumps(
            {
                "single_product": False,
                "unclear_boundaries": True,
                "product_name": "",
                "products": [],
                "reason": "Generic landing page.",
            }
        )
        result = _parse_result(raw)
        assert result.single_product is False
        assert result.unclear_boundaries is True
        assert result.products == []

    def test_unclear_boundaries_defaults_false(self):
        raw = json.dumps(
            {
                "single_product": True,
                "product_name": "Widget",
                "products": [],
                "reason": "ok",
            }
        )
        result = _parse_result(raw)
        assert result.unclear_boundaries is False

    def test_missing_fields_use_defaults(self):
        raw = json.dumps({"single_product": False})
        result = _parse_result(raw)
        assert result.single_product is False
        assert result.product_name == ""
        assert result.products == []
        assert result.reason == ""
        assert result.unclear_boundaries is False


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
            url, name = _validate_url("https://example.com")

        assert url == "https://example.com"
        assert name == "TestApp"
        out = capsys.readouterr().out
        assert "TestApp" in out

    def test_multi_product_select_by_number(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=["Alpha", "Beta", "Gamma"],
            reason="Portfolio page.",
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value="2"):
                url, name = _validate_url("https://company.com")

        assert url == "https://company.com"
        assert name == "Beta"
        out = capsys.readouterr().out
        assert "1. Alpha" in out
        assert "2. Beta" in out
        assert "3. Gamma" in out
        assert "Selected: Beta" in out

    def test_multi_product_enter_url(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=["Alpha", "Beta"],
            reason="Portfolio page.",
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value="https://alpha.example.com"):
                url, name = _validate_url("https://company.com")

        assert url == "https://alpha.example.com"
        assert name == ""

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
                url, name = _validate_url("https://company.com")

        assert url == ""
        assert name == ""

    def test_multi_product_invalid_number(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=["Alpha", "Beta"],
            reason="Portfolio page.",
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value="5"):
                url, name = _validate_url("https://company.com")

        assert url == ""
        assert name == ""
        out = capsys.readouterr().out
        assert "Invalid selection" in out

    def test_multi_product_invalid_text(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=["Alpha"],
            reason="Portfolio page.",
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value="not-a-url"):
                url, name = _validate_url("https://company.com")

        assert url == ""
        assert name == ""
        out = capsys.readouterr().out
        assert "Not a valid number or URL" in out

    def test_multi_product_no_product_list(self, capsys):
        """When validator says multi-product but returns no product list."""
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=[],
            reason="Unclear page.",
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value="https://specific.example.com"):
                url, name = _validate_url("https://company.com")

        assert url == "https://specific.example.com"
        assert name == ""

    def test_unclear_boundaries_user_describes_product(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=[],
            reason="Generic AI platform page.",
            unclear_boundaries=True,
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value="Their chatbot widget"):
                url, name = _validate_url("https://vague-platform.com")

        assert url == "https://vague-platform.com"
        assert name == "Their chatbot widget"
        out = capsys.readouterr().out
        assert "unclear product boundaries" in out

    def test_unclear_boundaries_user_enters_url(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=[],
            reason="Generic landing page.",
            unclear_boundaries=True,
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value="https://specific.example.com/product"):
                url, name = _validate_url("https://vague.example.com")

        assert url == "https://specific.example.com/product"
        assert name == ""

    def test_unclear_boundaries_user_cancels(self, capsys):
        from duplo.main import _validate_url

        result = ValidationResult(
            single_product=False,
            product_name="",
            products=[],
            reason="Vague page.",
            unclear_boundaries=True,
        )
        with patch("duplo.main.validate_product_url", return_value=result):
            with patch("builtins.input", return_value=""):
                url, name = _validate_url("https://vague.example.com")

        assert url == ""
        assert name == ""
        out = capsys.readouterr().out
        assert "Cancelled" in out

    def test_validation_error_proceeds(self, capsys):
        from duplo.main import _validate_url

        with patch(
            "duplo.main.validate_product_url",
            side_effect=RuntimeError("network error"),
        ):
            url, name = _validate_url("https://example.com")

        assert url == "https://example.com"
        assert name == ""
        out = capsys.readouterr().out
        assert "network error" in out


class TestConfirmProduct:
    """Test _confirm_product product confirmation."""

    def test_confirms_known_product(self, capsys):
        from duplo.main import _confirm_product

        with patch("builtins.input", return_value=""):
            result = _confirm_product("Acme Widget", "https://acme.com")

        assert result == "Acme Widget"
        out = capsys.readouterr().out
        assert "Acme Widget" in out
        assert "https://acme.com" in out

    def test_confirms_with_yes(self, capsys):
        from duplo.main import _confirm_product

        with patch("builtins.input", return_value="y"):
            result = _confirm_product("Acme Widget", "https://acme.com")

        assert result == "Acme Widget"

    def test_user_corrects_product_name(self, capsys):
        from duplo.main import _confirm_product

        with patch("builtins.input", side_effect=["n", "Better Name"]):
            result = _confirm_product("Wrong Name", "https://example.com")

        assert result == "Better Name"

    def test_user_cancels_correction(self, capsys):
        from duplo.main import _confirm_product

        with patch("builtins.input", side_effect=["n", "q"]):
            result = _confirm_product("SomeProd", "https://example.com")

        assert result == ""

    def test_user_cancels_with_empty(self, capsys):
        from duplo.main import _confirm_product

        with patch("builtins.input", side_effect=["n", ""]):
            result = _confirm_product("SomeProd", "https://example.com")

        assert result == ""

    def test_no_product_name_prompts(self, capsys):
        from duplo.main import _confirm_product

        with patch("builtins.input", return_value="My Product"):
            result = _confirm_product("", "https://example.com")

        assert result == "My Product"
        out = capsys.readouterr().out
        assert "https://example.com" in out

    def test_no_product_name_empty_cancels(self, capsys):
        from duplo.main import _confirm_product

        with patch("builtins.input", return_value=""):
            result = _confirm_product("", "https://example.com")

        assert result == ""

    def test_no_url_no_name(self, capsys):
        from duplo.main import _confirm_product

        with patch("builtins.input", return_value="Something"):
            result = _confirm_product("", "")

        assert result == "Something"
        out = capsys.readouterr().out
        assert "No product URL" in out
