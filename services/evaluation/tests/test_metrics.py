import math

import pytest

from slm_evaluation.metrics import (
    MetricUnavailable,
    accuracy,
    classification_f1,
    compute_metrics,
    corpus_bleu,
    json_validity,
    mean_rouge_l,
    mean_token_f1,
    normalize_text,
    rouge_l,
    token_f1,
)


def test_normalize_text():
    assert normalize_text("  The Quick, BROWN fox!! ") == "quick brown fox"
    assert normalize_text("An apple a day") == "apple day"


def test_accuracy():
    assert accuracy(["Paris", "london", "Rome"], ["paris", "London", "Berlin"]) == pytest.approx(2 / 3)
    assert accuracy(["Paris."], ["paris"]) == 1.0
    assert accuracy(["Paris."], ["paris"], normalize=False) == 0.0
    with pytest.raises(ValueError, match="predictions"):
        accuracy(["a"], ["a", "b"])
    with pytest.raises(ValueError, match="at least one"):
        accuracy([], [])


def test_token_f1_known_values():
    assert token_f1("the quick brown fox", "quick brown dog") == pytest.approx(2 / 3)
    assert token_f1("same words", "words same") == 1.0
    assert token_f1("abc", "xyz") == 0.0
    assert token_f1("", "") == 1.0 and token_f1("", "x") == 0.0
    # repeated tokens only count as often as they occur in both
    assert token_f1("x x x", "x") == pytest.approx(0.5)
    assert mean_token_f1(["quick brown fox", "x"], ["quick brown dog", "x"]) == pytest.approx((2 / 3 + 1) / 2)


def test_classification_f1():
    refs = ["pos", "pos", "neg", "neg", "neu"]
    preds = ["pos", "neg", "neg", "neg", "pos"]
    # pos: tp1 pred2 ref2 -> .5 ; neg: tp2 pred3 ref2 -> .8 ; neu: 0 -> macro = (.5+.8+0)/3
    assert classification_f1(preds, refs) == pytest.approx((0.5 + 0.8 + 0.0) / 3)
    assert classification_f1(preds, refs, average="micro") == pytest.approx(accuracy(preds, refs))
    assert classification_f1(["a"], ["a"]) == 1.0


def test_rouge_l():
    assert rouge_l("the cat sat on the mat", "the cat is on the mat") == pytest.approx(5 / 6)
    assert rouge_l("cat", "dog") == 0.0
    assert rouge_l("a b c d", "a b c d") == 1.0
    # LCS is order sensitive but allows gaps: "a c" inside "a b c"
    assert rouge_l("a c", "a b c") == pytest.approx(2 * (1 * (2 / 3)) / (1 + 2 / 3))
    assert mean_rouge_l(["x y", "p q"], ["x y", "r s"]) == pytest.approx(0.5)


def test_bleu_known_values():
    assert corpus_bleu(["the cat sat on the mat"], ["the cat sat on the mat"]) == pytest.approx(1.0)
    # hand-computed: p = 5/6, 4/6, 2/5, 1/4 (add-one above unigrams), BP = 1
    expected = math.exp(sum(math.log(x) for x in (5 / 6, 4 / 6, 2 / 5, 1 / 4)) / 4)
    got = corpus_bleu(["the cat sat on the mat"], ["the cat is on the mat"])
    assert got == pytest.approx(expected) and got == pytest.approx(0.4855, abs=1e-3)
    assert corpus_bleu(["the cat sat on the mat"], ["the cat is on the mat"], smooth=False) == 0.0
    assert corpus_bleu(["zzz"], ["the cat is on the mat"]) == 0.0
    assert corpus_bleu([""], ["something"]) == 0.0


def test_bleu_brevity_penalty_and_multiple_references():
    short = corpus_bleu(["the cat"], ["the cat sat on the mat"])
    full = corpus_bleu(["the cat sat on the mat"], ["the cat sat on the mat"])
    assert short < full
    ref_len, pred_len = 6, 2
    assert short <= math.exp(1 - ref_len / pred_len)
    multi = corpus_bleu(["the cat sat on the mat"], [["a dog barks loudly", "the cat sat on the mat"]])
    assert multi == pytest.approx(1.0)


def test_bleu_is_corpus_level_not_a_mean_of_sentences():
    preds = ["the cat sat on the mat", "completely different words here now"]
    refs = ["the cat sat on the mat", "nothing matches at all really"]
    score = corpus_bleu(preds, refs)
    assert 0 < score < 1


def test_json_validity():
    outputs = ['{"a": 1}', "not json", '```json\n{"a": 2}\n```', "[1, 2]", "", "{'a': 1}"]
    assert json_validity(outputs) == pytest.approx(3 / 6)
    assert json_validity(outputs, strip_code_fences=False) == pytest.approx(2 / 6)
    assert json_validity(outputs, required_keys=["a"]) == pytest.approx(2 / 6)  # arrays have no keys
    assert json_validity(['{"a":1,"b":2}', '{"a":1}'], required_keys=["a", "b"]) == 0.5
    with pytest.raises(ValueError):
        json_validity([])


def test_compute_metrics_dispatch():
    scores = compute_metrics(
        ["accuracy", "f1", "rouge", "bleu"],
        ["the cat sat on the mat", "paris"],
        ["the cat is on the mat", "Paris"],
    )
    assert set(scores) == {"accuracy", "f1", "rouge", "bleu"}
    assert scores["accuracy"] == 0.5
    assert 0 < scores["rouge"] <= 1
    assert compute_metrics(["json_validity"], ['{"x": 1}', "nope"]) == {"json_validity": 0.5}
    with pytest.raises(MetricUnavailable):
        compute_metrics(["hallucination"], ["x"], ["y"])
    with pytest.raises(ValueError, match="unknown metric"):
        compute_metrics(["perplexity"], ["x"], ["y"])
    with pytest.raises(ValueError, match="needs references"):
        compute_metrics(["accuracy"], ["x"])
