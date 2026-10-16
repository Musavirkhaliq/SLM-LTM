"""Reference-based evaluation metrics in pure Python (no numpy / nltk / rouge dependency).

Text metrics share one normalisation (`normalize_text`: lower-case, strip punctuation,
collapse whitespace). Exact match and token F1 additionally drop English articles (SQuAD
convention); ROUGE and BLEU keep them, as their reference implementations do.
"""

from __future__ import annotations

import json
import math
import re
import string
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = str.maketrans("", "", string.punctuation)
_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


class MetricUnavailable(ValueError):
    """The metric needs an LLM judge or a model call and cannot be computed here."""


def normalize_text(text: str, *, drop_articles: bool = True) -> str:
    text = text.lower().translate(_PUNCT)
    if drop_articles:
        text = _ARTICLES.sub(" ", text)
    return " ".join(text.split())


def tokenize(text: str, *, drop_articles: bool = True) -> list[str]:
    return normalize_text(text, drop_articles=drop_articles).split()


def _check_aligned(predictions: Sequence[Any], references: Sequence[Any]) -> None:
    if len(predictions) != len(references):
        raise ValueError(f"got {len(predictions)} predictions but {len(references)} references")
    if not predictions:
        raise ValueError("at least one example is required")


# ---------------------------------------------------------------- accuracy --
def accuracy(predictions: Sequence[str], references: Sequence[str], *, normalize: bool = True) -> float:
    """Exact-match accuracy."""
    _check_aligned(predictions, references)
    prep: Callable[[str], str] = normalize_text if normalize else str.strip
    hits = sum(prep(p) == prep(r) for p, r in zip(predictions, references, strict=True))
    return hits / len(predictions)


# ---------------------------------------------------------------------- F1 --
def token_f1(prediction: str, reference: str) -> float:
    """Bag-of-tokens F1 between two strings (SQuAD-style)."""
    pred, ref = tokenize(prediction), tokenize(reference)
    if not pred or not ref:
        return float(pred == ref)
    overlap = sum((Counter(pred) & Counter(ref)).values())
    if overlap == 0:
        return 0.0
    precision, recall = overlap / len(pred), overlap / len(ref)
    return 2 * precision * recall / (precision + recall)


def mean_token_f1(predictions: Sequence[str], references: Sequence[str]) -> float:
    _check_aligned(predictions, references)
    return sum(token_f1(p, r) for p, r in zip(predictions, references, strict=True)) / len(predictions)


def classification_f1(
    predictions: Sequence[str],
    references: Sequence[str],
    *,
    average: Literal["macro", "micro"] = "macro",
    normalize: bool = True,
) -> float:
    """F1 over class labels. Macro averages per-label F1 over every label seen in either list;
    micro pools counts (for single-label tasks it equals accuracy)."""
    _check_aligned(predictions, references)
    prep: Callable[[str], str] = normalize_text if normalize else str.strip
    preds = [prep(p) for p in predictions]
    refs = [prep(r) for r in references]
    labels = sorted(set(preds) | set(refs))
    tp = Counter(p for p, r in zip(preds, refs, strict=True) if p == r)
    pred_count, ref_count = Counter(preds), Counter(refs)

    def f1(true_pos: int, predicted: int, actual: int) -> float:
        if true_pos == 0:
            return 0.0
        precision, recall = true_pos / predicted, true_pos / actual
        return 2 * precision * recall / (precision + recall)

    if average == "micro":
        return f1(sum(tp.values()), len(preds), len(refs))
    return sum(f1(tp[label], pred_count[label], ref_count[label]) for label in labels) / len(labels)


# ----------------------------------------------------------------- ROUGE-L --
def _lcs_length(a: Sequence[str], b: Sequence[str]) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = [0] * (len(b) + 1)
    for token_a in a:
        current = [0]
        for j, token_b in enumerate(b, start=1):
            current.append(previous[j - 1] + 1 if token_a == token_b else max(previous[j], current[j - 1]))
        previous = current
    return previous[-1]


def rouge_l(prediction: str, reference: str) -> float:
    """ROUGE-L F1 (longest common subsequence of tokens; beta = 1)."""
    pred, ref = tokenize(prediction, drop_articles=False), tokenize(reference, drop_articles=False)
    if not pred or not ref:
        return float(pred == ref)
    lcs = _lcs_length(pred, ref)
    if lcs == 0:
        return 0.0
    precision, recall = lcs / len(pred), lcs / len(ref)
    return 2 * precision * recall / (precision + recall)


def mean_rouge_l(predictions: Sequence[str], references: Sequence[str]) -> float:
    _check_aligned(predictions, references)
    return sum(rouge_l(p, r) for p, r in zip(predictions, references, strict=True)) / len(predictions)


# -------------------------------------------------------------------- BLEU --
def _ngrams(tokens: Sequence[str], n: int) -> Counter[tuple[str, ...]]:
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def corpus_bleu(
    predictions: Sequence[str],
    references: Sequence[str | Sequence[str]],
    *,
    max_n: int = 4,
    smooth: bool = True,
) -> float:
    """Corpus BLEU on normalised word tokens. Each reference may be a string or a list of
    alternatives. `smooth=True` adds one to the numerator and denominator of every n-gram order
    above 1 (Lin & Och add-one), so short texts do not collapse to 0."""
    _check_aligned(predictions, references)
    matches = [0] * max_n
    totals = [0] * max_n
    pred_len = ref_len = 0
    for prediction, refs in zip(predictions, references, strict=True):
        candidates = [refs] if isinstance(refs, str) else list(refs)
        ref_tokens = [tokenize(r, drop_articles=False) for r in candidates]
        pred_tokens = tokenize(prediction, drop_articles=False)
        pred_len += len(pred_tokens)
        # closest reference length; ties go to the shorter one
        ref_len += min((len(r) for r in ref_tokens), key=lambda n: (abs(n - len(pred_tokens)), n))
        for n in range(1, max_n + 1):
            pred_counts = _ngrams(pred_tokens, n)
            max_ref: Counter[tuple[str, ...]] = Counter()
            for ref in ref_tokens:
                for gram, count in _ngrams(ref, n).items():
                    max_ref[gram] = max(max_ref[gram], count)
            matches[n - 1] += sum(min(c, max_ref[g]) for g, c in pred_counts.items())
            totals[n - 1] += sum(pred_counts.values())
    if pred_len == 0:
        return 0.0
    log_precision = 0.0
    for n in range(max_n):
        match, total = matches[n], totals[n]
        if smooth and n > 0:
            match, total = match + 1, total + 1
        if match == 0 or total == 0:
            return 0.0
        log_precision += math.log(match / total)
    brevity = 1.0 if pred_len > ref_len else math.exp(1 - ref_len / pred_len)
    return brevity * math.exp(log_precision / max_n)


# ----------------------------------------------------------- JSON validity --
def json_validity(
    outputs: Sequence[str],
    *,
    required_keys: Sequence[str] = (),
    strip_code_fences: bool = True,
) -> float:
    """Share of outputs that parse as JSON; with `required_keys` they must be objects holding
    those keys. Markdown code fences (a common model habit) are stripped unless disabled."""
    if not outputs:
        raise ValueError("at least one output is required")
    valid = 0
    for text in outputs:
        candidate = text.strip()
        if strip_code_fences and (m := _FENCE.match(candidate)):
            candidate = m.group(1)
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if required_keys and not (
            isinstance(parsed, dict) and all(key in parsed for key in required_keys)
        ):
            continue
        valid += 1
    return valid / len(outputs)


# ---------------------------------------------------------------- registry --
_LLM_JUDGED = {"hallucination", "faithfulness", "groundedness", "toxicity"}
SUPPORTED_METRICS = ("accuracy", "f1", "rouge", "rouge_l", "bleu", "json_validity")


def compute_metrics(
    names: Sequence[str],
    predictions: Sequence[str],
    references: Sequence[str | Sequence[str]] | None = None,
    **options: Any,
) -> dict[str, float]:
    """Compute the named metrics (names follow `evaluations.metrics`). `references` is
    optional only for `json_validity`; LLM-judged metrics raise `MetricUnavailable`."""
    scores: dict[str, float] = {}
    for name in names:
        if name in _LLM_JUDGED:
            raise MetricUnavailable(f"'{name}' requires an LLM judge")
        if name not in SUPPORTED_METRICS:
            raise ValueError(f"unknown metric '{name}'")
        if name == "json_validity":
            scores[name] = json_validity(predictions, **_pick(options, "required_keys", "strip_code_fences"))
            continue
        if references is None:
            raise ValueError(f"metric '{name}' needs references")
        single = [r if isinstance(r, str) else r[0] for r in references]
        if name == "accuracy":
            scores[name] = accuracy(predictions, single)
        elif name == "f1":
            scores[name] = mean_token_f1(predictions, single)
        elif name in ("rouge", "rouge_l"):
            scores[name] = mean_rouge_l(predictions, single)
        else:
            scores[name] = corpus_bleu(predictions, references, **_pick(options, "max_n", "smooth"))
    return scores


def _pick(options: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: options[k] for k in keys if k in options}
