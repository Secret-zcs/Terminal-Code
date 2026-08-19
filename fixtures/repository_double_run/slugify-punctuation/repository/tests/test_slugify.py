from slugify import slugify


def test_slugify_basic_words():
    assert slugify("Hello World") == "hello-world"


def test_slugify_trims_outer_whitespace():
    assert slugify("  Hello World  ") == "hello-world"


def test_slugify_collapses_repeated_spaces():
    assert slugify("Hello   World") == "hello-world"


def test_slugify_removes_punctuation():
    assert slugify("Hello, World!") == "hello-world"
