from clashhero.utils.tags import is_valid_tag, normalize_tag


def test_normalize():
    assert normalize_tag(" 2pp ") == "#2PP"
    assert normalize_tag("#ygo8") == "#YG08"


def test_valid():
    assert is_valid_tag("#2PPQLRJ")
    assert not is_valid_tag("#ABC")
