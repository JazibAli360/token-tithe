from textkit.case import snake_case


def test_camel():
    assert snake_case("HelloWorld") == "hello_world"


def test_spaces_and_dashes():
    assert snake_case("hello big-world") == "hello_big_world"
