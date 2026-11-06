use wordstat::{longest_word, total_letters, word_count};

#[test]
fn counts_words() {
    assert_eq!(word_count("a bb  ccc"), 3);
    assert_eq!(word_count("   "), 0);
}

#[test]
fn totals_letters() {
    assert_eq!(total_letters("a bb ccc"), 6);
}

#[test]
fn finds_longest() {
    assert_eq!(longest_word("a bbb cc"), Some("bbb"));
    assert_eq!(longest_word(""), None);
}
