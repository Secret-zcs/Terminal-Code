from calculator import divide


def test_divide_normal_case():
    assert divide(8, 2) == 4


def test_divide_by_zero_returns_none():
    assert divide(8, 0) is None


def test_divide_negative_numbers():
    assert divide(-8, 2) == -4
