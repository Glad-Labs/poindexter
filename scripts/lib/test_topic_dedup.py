"""Tests for the shared topic dedup utilities."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.topic_dedup import normalize_words, get_ngrams, extract_template_base, is_too_similar


def test_normalize_words():
    assert normalize_words("Hello, World!") == ["hello", "world"]
    assert normalize_words("AI Agents 101") == ["ai", "agents", "101"]
    assert normalize_words("") == []


def test_get_ngrams():
    words = ["the", "quick", "brown", "fox"]
    assert get_ngrams(words, 2) == {("the", "quick"), ("quick", "brown"), ("brown", "fox")}
    assert get_ngrams(words, 3) == {("the", "quick", "brown"), ("quick", "brown", "fox")}
    assert get_ngrams([], 2) == set()


def test_extract_template_base_short():
    assert extract_template_base("AI Agents 101") == "ai agents 101"


def test_extract_template_base_long():
    result = extract_template_base("The Hidden Costs of Local LLMs Nobody Talks About")
    assert result == "the hidden costs of ... llms nobody talks about"


def test_is_too_similar_exact_match():
    assert is_too_similar("Hello World", {"hello world"}) is True
    assert is_too_similar("Hello World", {"Hello World"}) is True


def test_is_too_similar_ngram_overlap():
    existing = {"The Developer's Guide to FastAPI"}
    assert is_too_similar("The Developer's Guide to PostgreSQL", existing) is True


def test_is_too_similar_no_match():
    existing = {"Kubernetes in Production"}
    assert is_too_similar("Why Solo Founders Love AI Agents", existing) is False


def test_is_too_similar_template_skeleton():
    existing = {"The Hidden Costs of Docker Nobody Talks About"}
    assert is_too_similar("The Hidden Costs of Redis Nobody Talks About", existing) is True


if __name__ == "__main__":
    test_normalize_words()
    test_get_ngrams()
    test_extract_template_base_short()
    test_extract_template_base_long()
    test_is_too_similar_exact_match()
    test_is_too_similar_ngram_overlap()
    test_is_too_similar_no_match()
    test_is_too_similar_template_skeleton()
    print("All tests passed!")
