use wordstat::{longest_word, total_letters, word_count};

#[test]
fn behavior_unchanged() {
    assert_eq!(word_count("one two\tthree\nfour"), 4);
    assert_eq!(total_letters("ab cd e"), 5);
    assert_eq!(longest_word("aa bb c"), Some("aa"));
}
