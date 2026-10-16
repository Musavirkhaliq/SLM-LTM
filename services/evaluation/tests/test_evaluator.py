import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from slm_evaluation.config import EvaluationSettings
from slm_evaluation.evaluator import _infer_single, extract_prompt_and_ref
from slm_evaluation.judge import LLMJudge, _extract_json
from slm_evaluation.s3 import download_dataset_rows


def test_extract_prompt_and_ref():
    # 1. messages format
    row_msgs = {
        "messages": [
            {"role": "user", "content": "What is AI?"},
            {"role": "assistant", "content": "Artificial Intelligence"},
        ]
    }
    p, r = extract_prompt_and_ref(row_msgs)
    assert p == "What is AI?"
    assert r == "Artificial Intelligence"

    # 2. prompt + completion
    row_pc = {"prompt": "Solve 2+2", "completion": "4"}
    p, r = extract_prompt_and_ref(row_pc)
    assert p == "Solve 2+2"
    assert r == "4"

    # 3. instruction + output (alpaca)
    row_alpaca = {"instruction": "Summarize", "input": "Long text", "output": "Short text"}
    p, r = extract_prompt_and_ref(row_alpaca)
    assert "Summarize\n\nLong text" == p
    assert r == "Short text"

    # 4. question + answer
    row_qa = {"question": "Capital?", "answer": "Paris"}
    p, r = extract_prompt_and_ref(row_qa)
    assert p == "Capital?"
    assert r == "Paris"

    # 5. empty/unknown format
    assert extract_prompt_and_ref({}) == ("", "")


def test_download_dataset_rows_local():
    settings = EvaluationSettings()

    # JSONL
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as f:
        f.write('{"prompt": "hello", "completion": "world"}\n{"prompt": "foo", "completion": "bar"}\n')
        f_path = f.name
    try:
        rows = download_dataset_rows(f_path, settings)
        assert len(rows) == 2
        assert rows[0]["prompt"] == "hello"
    finally:
        Path(f_path).unlink(missing_ok=True)

    # CSV
    with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as f:
        f.write("prompt,completion\nhi,there\n")
        f_path = f.name
    try:
        rows = download_dataset_rows(f_path, settings)
        assert len(rows) == 1
        assert rows[0]["prompt"] == "hi"
    finally:
        Path(f_path).unlink(missing_ok=True)

    # Missing file raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        download_dataset_rows("/non/existent/path/data.jsonl", settings)

    # Empty path raises ValueError
    with pytest.raises(ValueError, match="storage_uri is empty"):
        download_dataset_rows("", settings)


def test_judge_json_extraction():
    # Code fence
    text_fence = '```json\n{"verdicts": ["supported", "contradicted"], "faithfulness_score": 0.5}\n```'
    parsed = _extract_json(text_fence)
    assert parsed["verdicts"] == ["supported", "contradicted"]
    assert parsed["faithfulness_score"] == 0.5

    # Plain text
    text_plain = '{"toxicity_score": 0.8}'
    parsed_plain = _extract_json(text_plain)
    assert parsed_plain["toxicity_score"] == 0.8


@pytest.mark.asyncio
async def test_judge_evaluation_mocked():
    judge = LLMJudge(judge_model="test-judge", endpoint_url="http://test-judge:8000")

    mock_resp = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "claims": ["The sky is blue"],
                        "verdicts": ["supported"],
                        "faithfulness_score": 1.0,
                        "groundedness_score": 0.95,
                        "hallucination_score": 0.0,
                    })
                }
            }
        ]
    }

    with patch.object(httpx.AsyncClient, "post") as mock_post:
        mock_post.return_value = httpx.Response(200, json=mock_resp)
        f, g, h = await judge.evaluate_faithfulness_and_hallucination("What color is sky?", "The sky is blue.", "The sky is blue.")
        assert f == 1.0
        assert g == 0.95
        assert h == 0.0


@pytest.mark.asyncio
async def test_judge_toxicity_fallback():
    judge = LLMJudge(judge_model="test-judge", endpoint_url="http://unreachable:9999", timeout=0.1)
    # Heuristic fallback for toxic text
    score = await judge.evaluate_toxicity("You are an idiot and a bastard!")
    assert score > 0.0

    # Clean text
    clean_score = await judge.evaluate_toxicity("Good morning, have a wonderful day.")
    assert clean_score == 0.0


@pytest.mark.asyncio
async def test_infer_single_does_not_fake_ground_truth_on_failure():
    import asyncio
    sem = asyncio.Semaphore(1)

    # When inference endpoint returns 500
    with patch.object(httpx.AsyncClient, "post") as mock_post:
        mock_post.return_value = httpx.Response(500, text="Internal Server Error")
        async with httpx.AsyncClient() as client:
            content, lat_ms, p_tok, c_tok, success = await _infer_single(
                sem, client, "http://endpoint", "test-model", "test prompt"
            )
            assert not success
            assert content == ""  # MUST NOT be ground truth!

    # When inference raises network exception
    with patch.object(httpx.AsyncClient, "post", side_effect=httpx.ConnectError("Connection refused")):
        async with httpx.AsyncClient() as client:
            content, lat_ms, p_tok, c_tok, success = await _infer_single(
                sem, client, "http://endpoint", "test-model", "test prompt"
            )
            assert not success
            assert content == ""
