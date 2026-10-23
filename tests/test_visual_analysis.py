import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from PIL import Image, PngImagePlugin
from pydantic import SecretStr

from app.services.visual_analysis import analyze_visual


@pytest.fixture
def settings(monkeypatch):
    config = SimpleNamespace(
        gemini_api_key=SecretStr("test-only-key"), gemini_model="gemini-2.5-flash",
        visual_analysis_enabled=True, visual_analysis_timeout_seconds=35,
    )
    monkeypatch.setattr("app.services.visual_analysis.get_settings", lambda: config)
    return config


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "synthetic.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "Metadata that must not be transmitted")
    Image.new("RGB", (32, 32), "white").save(path, pnginfo=info)
    return path


def response_for(verdict="indications_found", category="possible_edit"):
    result = {"verdict": verdict, "summary": "Conferir uma possível diferença visual.", "findings": [
        {"category": category, "location": "Campo de valor, canto inferior", "observation": "Contorno do texto difere da região ao redor."},
    ]}
    response = Mock(status_code=200)
    response.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(result)}]}}]}
    return response


@pytest.mark.parametrize("requested,synthetic,enabled,has_key,expected", [
    (False, True, True, True, "not_requested"),
    (True, False, True, True, "synthetic_only"),
    (True, True, False, True, "not_configured"),
    (True, True, True, False, "not_configured"),
])
def test_no_network_without_explicit_synthetic_opt_in_and_configuration(
    settings, image_path, monkeypatch, requested, synthetic, enabled, has_key, expected,
):
    settings.visual_analysis_enabled = enabled
    if not has_key:
        settings.gemini_api_key = SecretStr("")
    post = Mock()
    monkeypatch.setattr("app.services.visual_analysis.requests.post", post)
    result = analyze_visual(image_path, requested=requested, synthetic_document=synthetic)
    assert result["status"] == expected
    assert result["external_request_attempted"] is False
    post.assert_not_called()


def test_image_sent_without_metadata_key_only_in_header(settings, image_path, monkeypatch):
    post = Mock(return_value=response_for())
    monkeypatch.setattr("app.services.visual_analysis.requests.post", post)
    result = analyze_visual(image_path, requested=True, synthetic_document=True)
    assert result["status"] == "completed"
    assert result["review_recommended"] is True
    post.assert_called_once()
    url = post.call_args.args[0]
    kwargs = post.call_args.kwargs
    assert "test-only-key" not in url
    assert kwargs["headers"]["x-goog-api-key"] == "test-only-key"
    assert kwargs["allow_redirects"] is False
    encoded = kwargs["json"]["contents"][0]["parts"][1]["inlineData"]["data"]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as sent:
        assert not sent.info
    assert "test-only-key" not in json.dumps(result)


@pytest.mark.parametrize("model,legacy_thinking", [("gemini-2.5-flash", True), ("gemini-3.1-flash-lite", False)])
def test_thinking_parameters_match_model_generation(settings, image_path, monkeypatch, model, legacy_thinking):
    settings.gemini_model = model
    post = Mock(return_value=response_for())
    monkeypatch.setattr("app.services.visual_analysis.requests.post", post)
    assert analyze_visual(image_path, requested=True, synthetic_document=True)["status"] == "completed"
    assert ("thinkingConfig" in post.call_args.kwargs["json"]["generationConfig"]) is legacy_thinking


def test_watermark_alone_is_not_an_editing_verdict(settings, image_path, monkeypatch):
    monkeypatch.setattr("app.services.visual_analysis.requests.post", Mock(return_value=response_for(category="watermark")))
    result = analyze_visual(image_path, requested=True, synthetic_document=True)
    assert result["status"] == "completed"
    assert result["verdict"] == "inconclusive"


def test_no_indications_does_not_assert_authenticity(settings, image_path, monkeypatch):
    response = response_for("no_obvious_indications", category="watermark")
    monkeypatch.setattr("app.services.visual_analysis.requests.post", Mock(return_value=response))
    result = analyze_visual(image_path, requested=True, synthetic_document=True)
    assert result["verdict"] == "no_obvious_indications"
    assert result["review_recommended"] is False
    assert "autenticidade" in " ".join(result["limitations"])


@pytest.mark.parametrize("status,expected", [(429, "quota_exceeded"), (403, "authentication_error"),
                                           (404, "model_unavailable"), (500, "provider_error")])
def test_provider_failures_remain_inconclusive(settings, image_path, monkeypatch, status, expected):
    post = Mock(return_value=Mock(status_code=status))
    monkeypatch.setattr("app.services.visual_analysis.requests.post", post)
    result = analyze_visual(image_path, requested=True, synthetic_document=True)
    assert result["status"] == expected
    assert result["review_recommended"] is True
    assert "verdict" not in result
    post.assert_called_once()


@pytest.mark.parametrize("exception,expected", [(requests.Timeout(), "timeout"), (requests.ConnectionError(), "provider_error")])
def test_connection_failures_do_not_raise(settings, image_path, monkeypatch, exception, expected):
    monkeypatch.setattr("app.services.visual_analysis.requests.post", Mock(side_effect=exception))
    assert analyze_visual(image_path, requested=True, synthetic_document=True)["status"] == expected


def test_truncated_or_invalid_responses_are_not_success(settings, image_path, monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"candidates": [{"finishReason": "MAX_TOKENS"}]}
    monkeypatch.setattr("app.services.visual_analysis.requests.post", Mock(return_value=response))
    assert analyze_visual(image_path, requested=True, synthetic_document=True)["status"] == "inconclusive"
    response.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"verdict":"authentic"}'}]}}]}
    assert analyze_visual(image_path, requested=True, synthetic_document=True)["status"] == "invalid_response"
