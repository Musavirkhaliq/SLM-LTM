import json

import pytest

from slm_finetune.validator import (
    detect_schema,
    validate_csv,
    validate_dataset,
    validate_jsonl,
)


def jl(*records) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def chat(user="hi", assistant="hello", system=None) -> dict:
    messages = [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
    if system:
        messages.insert(0, {"role": "system", "content": system})
    return {"messages": messages}


def many(record_factory, n=12):
    return [record_factory(i) for i in range(n)]


def test_valid_chat_dataset():
    report = validate_jsonl(jl(*many(lambda i: chat(f"question {i}", f"answer {i}", system="be brief"))))
    assert report.ok and report.schema == "chat"
    assert (report.total_rows, report.valid_rows, report.error_count) == (12, 12, 0)
    assert report.warnings == [] and report.duplicate_rows == 0
    assert report.estimated_tokens == report.total_chars // 4 > 0


def test_valid_alpaca_prompt_completion_and_text():
    alpaca = validate_jsonl(jl(*many(lambda i: {"instruction": f"do {i}", "input": "ctx", "output": f"done {i}"})))
    assert alpaca.ok and alpaca.schema == "alpaca"
    no_input = validate_jsonl(jl(*many(lambda i: {"instruction": f"do {i}", "output": "ok"})))
    assert no_input.ok
    pc = validate_jsonl(jl(*many(lambda i: {"prompt": f"p{i}", "completion": f"c{i}"})))
    assert pc.ok and pc.schema == "prompt_completion"
    text = validate_jsonl(jl(*many(lambda i: {"text": f"document {i}"})))
    assert text.ok and text.schema == "text"


def test_schema_detection():
    assert detect_schema(["messages"]) == "chat"
    assert detect_schema(["instruction", "output", "input"]) == "alpaca"
    assert detect_schema(["prompt", "completion"]) == "prompt_completion"
    assert detect_schema(["text", "id"]) == "text"
    assert detect_schema(["foo"]) is None


def test_row_errors_are_reported_with_line_numbers():
    content = "\n".join(
        [
            json.dumps(chat("a", "b")),  # 1 ok
            "{not json",  # 2
            json.dumps([1, 2]),  # 3 not an object
            json.dumps({"messages": []}),  # 4
            json.dumps({"messages": [{"role": "user", "content": "x"}]}),  # 5 no assistant target
            json.dumps({"messages": [{"role": "bot", "content": "x"}]}),  # 6 bad role
            json.dumps({"messages": [{"role": "user", "content": " "}, {"role": "assistant", "content": "y"}]}),  # 7
            json.dumps({"instruction": "x", "output": "y"}),  # 8 wrong schema for this file
            "",  # blank lines are skipped
            json.dumps(chat("c", "d")),  # 10 ok
        ]
    )
    report = validate_jsonl(content)
    assert report.schema == "chat"
    assert (report.total_rows, report.valid_rows, report.error_count) == (9, 2, 7)
    assert not report.ok
    by_line = {e.row: e.message for e in report.errors}
    assert by_line[2] == "invalid JSON"
    assert "object" in by_line[3]
    assert "non-empty list" in by_line[4]
    assert "assistant" in by_line[5]
    assert "role" in by_line[6]
    assert "must not be empty" in by_line[7]
    assert "missing required field 'messages'" in by_line[8]


def test_conversation_structure_rules():
    bad = [
        {"messages": [{"role": "assistant", "content": "a"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "b"}]},
        {"messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}]},
        {"messages": [{"role": "user", "content": "a"}, {"role": "system", "content": "s"}, {"role": "assistant", "content": "c"}]},
        {"messages": [{"role": "system", "content": "s"}, {"role": "assistant", "content": "c"}]},
        {"messages": [{"role": "user", "content": 5}, {"role": "assistant", "content": "c"}]},
        {"messages": ["hi"]},
    ]
    # assistant-first is fine structurally when a user message exists later; the rest must fail
    report = validate_jsonl(jl(chat(), *bad))
    failing = {e.row for e in report.errors}
    assert failing == {3, 4, 5, 6, 7}, failing  # row 2 (assistant, user, assistant) is accepted


def test_error_list_is_capped_but_counted():
    report = validate_jsonl("\n".join(["{bad"] * 250), max_errors=100)
    assert report.error_count == 250 and len(report.errors) == 100


def test_duplicates_and_small_dataset_warnings():
    report = validate_jsonl(jl(chat("same", "same"), chat("same", "same"), chat("other", "x")))
    assert report.ok and report.duplicate_rows == 1
    assert any("duplicate" in w for w in report.warnings)
    assert any("recommended" in w for w in report.warnings)


def test_empty_and_garbage_input():
    for content in ("", "\n\n  \n"):
        report = validate_jsonl(content)
        assert not report.ok and report.errors[0].message == "dataset is empty"
    report = validate_jsonl(b"\xff\xfe\x00bad")
    assert not report.ok and "UTF-8" in report.errors[0].message


def test_bom_is_tolerated_and_max_rows_truncates():
    body = jl(*many(lambda i: {"text": f"doc {i}"}, 50))
    assert validate_jsonl("﻿" + body).ok
    report = validate_jsonl(body, max_rows=20)
    assert report.truncated and report.total_rows == 20


def test_forced_schema_overrides_detection():
    report = validate_jsonl(jl({"prompt": "p", "completion": "c", "text": "t"}), schema="text")
    assert report.schema == "text" and report.valid_rows == 1


def test_csv_valid_alpaca_and_prompt_completion():
    rows = "\n".join(f'"question {i}, with comma","answer {i}"' for i in range(12))
    report = validate_csv(f"instruction,output\n{rows}\n")
    assert report.ok and report.schema == "alpaca" and report.valid_rows == 12
    pc = validate_csv("prompt,completion\nhi,hello\n")
    assert pc.ok and pc.schema == "prompt_completion"


def test_csv_chat_column_holds_json():
    cell = json.dumps(chat("q", "a")["messages"]).replace('"', '""')
    ok = validate_csv(f'messages\n"{cell}"\n')
    assert ok.ok and ok.schema == "chat"
    bad = validate_csv('messages\n"not json"\n')
    assert not bad.ok and "JSON" in bad.errors[0].message


def test_csv_structural_errors():
    assert not validate_csv("").ok and validate_csv("").errors[0].message == "dataset is empty"
    assert "does not match" in validate_csv("a,b\n1,2\n").errors[0].message
    assert "duplicate column" in validate_csv("prompt,prompt\n1,2\n").errors[0].message
    report = validate_csv("prompt,completion\nonly-one\nok,fine\n,\n")
    assert report.total_rows == 2 and report.valid_rows == 1  # blank row skipped
    assert report.errors[0].row == 1 and "columns" in report.errors[0].message
    empty_cell = validate_csv("prompt,completion\nhi,\n")
    assert "must not be empty" in empty_cell.errors[0].message


def test_dispatch_and_serialisation():
    report = validate_dataset(jl(chat()), "jsonl")
    payload = report.to_dict()
    assert payload["format"] == "jsonl" and payload["schema"] == "chat" and payload["ok"] is True
    assert json.dumps(payload)  # JSON-serialisable for the API response
    assert validate_dataset("prompt,completion\na,b\n", "csv").valid_rows == 1
    with pytest.raises(ValueError):
        validate_dataset("x", "parquet")  # type: ignore[arg-type]
