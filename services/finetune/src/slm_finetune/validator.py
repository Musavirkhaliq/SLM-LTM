"""Validation of fine-tuning datasets (JSONL / CSV) before they are accepted for training.

Four record schemas are recognised, and a file must use one of them consistently:

* ``chat``                 - ``{"messages": [{"role": ..., "content": ...}, ...]}``
* ``alpaca``               - ``{"instruction": ..., "input"?: ..., "output": ...}``
* ``prompt_completion``    - ``{"prompt": ..., "completion": ...}``
* ``text``                 - ``{"text": ...}`` (continued pre-training)

CSV files use the same field names as columns; a ``messages`` column holds the chat JSON.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Schema = Literal["chat", "alpaca", "prompt_completion", "text"]
DatasetFormat = Literal["jsonl", "csv", "parquet"]

CHAT_ROLES = frozenset({"system", "user", "assistant"})
ROLE_MAP = {
    "human": "user",
    "user": "user",
    "assistant": "assistant",
    "gpt": "assistant",
    "bot": "assistant",
    "model": "assistant",
    "system": "system",
}
MIN_RECOMMENDED_ROWS = 10
LONG_ROW_CHARS = 32_000
CHARS_PER_TOKEN = 4  # rough heuristic used only for the token estimate
_REQUIRED: dict[Schema, tuple[str, ...]] = {
    "chat": ("messages",),
    "alpaca": ("instruction", "output"),
    "prompt_completion": ("prompt", "completion"),
    "text": ("text",),
}
_OPTIONAL: dict[Schema, tuple[str, ...]] = {"alpaca": ("input",), "chat": (), "prompt_completion": (), "text": ()}


@dataclass(frozen=True, slots=True)
class RowError:
    row: int  # 1-based line (JSONL) or data row (CSV, header excluded)
    message: str


@dataclass(slots=True)
class ValidationReport:
    format: DatasetFormat
    schema: Schema | None = None
    total_rows: int = 0
    valid_rows: int = 0
    errors: list[RowError] = field(default_factory=list)
    error_count: int = 0  # total, even when `errors` is capped
    warnings: list[str] = field(default_factory=list)
    duplicate_rows: int = 0
    total_chars: int = 0
    max_row_chars: int = 0
    truncated: bool = False  # stopped early at max_rows

    @property
    def ok(self) -> bool:
        return self.valid_rows > 0 and self.error_count == 0

    @property
    def estimated_tokens(self) -> int:
        return self.total_chars // CHARS_PER_TOKEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema": self.schema,
            "ok": self.ok,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "error_count": self.error_count,
            "errors": [{"row": e.row, "message": e.message} for e in self.errors],
            "warnings": self.warnings,
            "duplicate_rows": self.duplicate_rows,
            "estimated_tokens": self.estimated_tokens,
            "max_row_chars": self.max_row_chars,
            "truncated": self.truncated,
        }


def normalize_record(rec: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(rec)
    # ShareGPT: conversations -> messages
    if "conversations" in record and "messages" not in record:
        convs = record["conversations"]
        if isinstance(convs, list):
            mapped_msgs = []
            for m in convs:
                if isinstance(m, Mapping):
                    raw_role = str(m.get("from") or m.get("role") or "").lower()
                    role = ROLE_MAP.get(raw_role, raw_role)
                    content = m.get("value") or m.get("content") or ""
                    mapped_msgs.append({"role": role, "content": str(content)})
            record["messages"] = mapped_msgs
    # Prompt + response -> prompt + completion
    if "prompt" in record and "response" in record and "completion" not in record:
        record["completion"] = record["response"]
    # Input + output (without instruction) -> instruction = input
    if "input" in record and "output" in record and "instruction" not in record:
        record["instruction"] = record["input"]
        record["input"] = ""
    # Instruction + response -> instruction + output
    if "instruction" in record and "response" in record and "output" not in record:
        record["output"] = record["response"]
    # Context -> input (Dolly format)
    if "context" in record and "input" not in record:
        record["input"] = record["context"]
    return record


def detect_schema(keys: Iterable[str]) -> Schema | None:
    present = set(keys)
    if "messages" in present or "conversations" in present:
        return "chat"
    if ("instruction" in present and ("output" in present or "response" in present)) or ("input" in present and "output" in present):
        return "alpaca"
    if "prompt" in present and ("completion" in present or "response" in present):
        return "prompt_completion"
    if "text" in present:
        return "text"
    for schema in ("chat", "alpaca", "prompt_completion", "text"):
        if set(_REQUIRED[schema]) <= present:  # type: ignore[index]
            return schema  # type: ignore[return-value]
    return None


def _check_text(value: Any, name: str) -> str | None:
    if not isinstance(value, str):
        return f"'{name}' must be a string"
    if not value.strip():
        return f"'{name}' must not be empty"
    return None


def _check_messages(messages: Any) -> str | None:
    if not isinstance(messages, list) or not messages:
        return "'messages' must be a non-empty list"
    previous = None
    for i, message in enumerate(messages):
        if not isinstance(message, Mapping):
            return f"messages[{i}] must be an object"
        raw_role = str(message.get("role", "")).lower()
        role = ROLE_MAP.get(raw_role, raw_role)
        if role not in CHAT_ROLES:
            return f"messages[{i}].role must be one of {sorted(CHAT_ROLES)}"
        if problem := _check_text(message.get("content"), f"messages[{i}].content"):
            return problem
        if role == "system" and i != 0:
            return f"messages[{i}]: a system message is only allowed first"
        if role != "system" and role == previous:
            return f"messages[{i}]: consecutive '{role}' messages"
        previous = role
    if messages[-1]["role"] != "assistant" and messages[-1].get("role") not in ("assistant", "gpt", "model"):
        return "the last message must be from the assistant (it is the training target)"
    if not any(ROLE_MAP.get(str(m.get("role", "")).lower(), m.get("role")) == "user" for m in messages):
        return "a conversation needs at least one user message"
    return None


def _check_record(record: Mapping[str, Any], schema: Schema) -> tuple[str | None, int]:
    """(error message or None, characters of training text)."""
    for name in _REQUIRED[schema]:
        if name not in record:
            return f"missing required field '{name}' for the {schema} schema", 0
    if schema == "chat":
        if problem := _check_messages(record["messages"]):
            return problem, 0
        return None, sum(len(str(m["content"])) for m in record["messages"])
    chars = 0
    for name in _REQUIRED[schema]:
        if problem := _check_text(record[name], name):
            return problem, 0
        chars += len(record[name])
    for name in _OPTIONAL[schema]:
        value = record.get(name)
        if value not in (None, ""):
            if not isinstance(value, str):
                return f"'{name}' must be a string", 0
            chars += len(value)
    return None, chars  # unknown extra fields are tolerated; the trainer ignores them


class _Accumulator:
    def __init__(self, fmt: DatasetFormat, max_errors: int, forced: Schema | None) -> None:
        self.report = ValidationReport(format=fmt)
        self._max_errors = max_errors
        self._forced = forced
        self._seen: set[bytes] = set()

    def error(self, row: int, message: str) -> None:
        self.report.error_count += 1
        if len(self.report.errors) < self._max_errors:
            self.report.errors.append(RowError(row, message))

    def record(self, row: int, record: Any) -> None:
        r = self.report
        r.total_rows += 1
        if not isinstance(record, Mapping):
            self.error(row, "each row must be a JSON object")
            return
        record = normalize_record(record)
        if r.schema is None:
            r.schema = self._forced or detect_schema(record.keys())
            if r.schema is None:
                self.error(row, "row does not match any supported schema (chat, alpaca, prompt_completion, text)")
                return
        problem, chars = _check_record(record, r.schema)
        if problem:
            self.error(row, problem)
            return
        fingerprint = hashlib.sha256(
            json.dumps(record, sort_keys=True, default=str, ensure_ascii=False).encode()
        ).digest()
        if fingerprint in self._seen:
            r.duplicate_rows += 1
        self._seen.add(fingerprint)
        r.valid_rows += 1
        r.total_chars += chars
        r.max_row_chars = max(r.max_row_chars, chars)

    def finish(self) -> ValidationReport:
        r = self.report
        if r.total_rows == 0:
            self.error(0, "dataset is empty")
        elif r.valid_rows and r.valid_rows < MIN_RECOMMENDED_ROWS:
            r.warnings.append(f"only {r.valid_rows} valid rows; {MIN_RECOMMENDED_ROWS}+ are recommended")
        if r.duplicate_rows:
            r.warnings.append(f"{r.duplicate_rows} duplicate row(s)")
        if r.max_row_chars > LONG_ROW_CHARS:
            r.warnings.append(f"longest row has {r.max_row_chars} characters; it may be truncated at max_seq_len")
        return r


def _as_text(content: str | bytes) -> str:
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("dataset must be UTF-8 encoded") from exc
    return content.removeprefix("﻿")


def validate_jsonl(
    content: str | bytes,
    *,
    schema: Schema | None = None,
    max_errors: int = 100,
    max_rows: int | None = None,
) -> ValidationReport:
    acc = _Accumulator("jsonl", max_errors, schema)
    try:
        text = _as_text(content)
    except ValueError as exc:
        acc.error(0, str(exc))
        return acc.report
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if max_rows is not None and acc.report.total_rows >= max_rows:
            acc.report.truncated = True
            break
        try:
            record = json.loads(line)
        except ValueError:
            acc.report.total_rows += 1
            acc.error(line_no, "invalid JSON")
            continue
        acc.record(line_no, record)
    return acc.finish()


def validate_csv(
    content: str | bytes,
    *,
    schema: Schema | None = None,
    max_errors: int = 100,
    max_rows: int | None = None,
) -> ValidationReport:
    acc = _Accumulator("csv", max_errors, schema)
    try:
        text = _as_text(content)
    except ValueError as exc:
        acc.error(0, str(exc))
        return acc.report
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        return acc.finish()
    if len(set(header)) != len(header):
        acc.error(0, "duplicate column names in the header")
        return acc.report
    detected = schema or detect_schema(header)
    if detected is None:
        acc.error(0, f"header {header} does not match any supported schema (chat, alpaca, prompt_completion, text)")
        return acc.report
    acc.report.schema = detected
    for row_no, row in enumerate(reader, start=1):
        if not any(cell.strip() for cell in row):
            continue
        if max_rows is not None and acc.report.total_rows >= max_rows:
            acc.report.truncated = True
            break
        if len(row) != len(header):
            acc.report.total_rows += 1
            acc.error(row_no, f"expected {len(header)} columns, found {len(row)}")
            continue
        record: dict[str, Any] = dict(zip(header, row, strict=True))
        if detected == "chat":
            try:
                record["messages"] = json.loads(record["messages"])
            except ValueError:
                acc.report.total_rows += 1
                acc.error(row_no, "the 'messages' column must contain JSON")
                continue
        acc.record(row_no, record)
    return acc.finish()


def validate_parquet(
    content: bytes,
    *,
    schema: Schema | None = None,
    max_errors: int = 100,
    max_rows: int | None = None,
) -> ValidationReport:
    acc = _Accumulator("parquet", max_errors, schema)
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(io.BytesIO(content))
        data = table.to_pylist()
    except Exception as exc:
        acc.error(0, f"Failed to read Parquet data: {exc}")
        return acc.report
    for row_no, record in enumerate(data, start=1):
        if max_rows is not None and acc.report.total_rows >= max_rows:
            acc.report.truncated = True
            break
        acc.record(row_no, record)
    return acc.finish()


def validate_dataset(
    content: str | bytes, fmt: DatasetFormat, **options: Any
) -> ValidationReport:
    if fmt == "jsonl":
        return validate_jsonl(content, **options)
    if fmt == "csv":
        return validate_csv(content, **options)
    if fmt == "parquet":
        raw = content.encode("utf-8") if isinstance(content, str) else content
        return validate_parquet(raw, **options)
    raise ValueError(f"unsupported dataset format '{fmt}'")
