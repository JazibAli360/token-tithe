from boundkit.clamp import clamp


def test_low():
    assert clamp(-5, 0, 10) == 0
