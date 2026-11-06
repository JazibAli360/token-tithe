//! Word statistics.

/// Count whitespace-separated words.
pub fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}

/// Sum of the lengths of all words.
pub fn total_letters(text: &str) -> usize {
    text.split_whitespace().map(str::len).sum()
}

/// Longest word; the first one wins a tie.
pub fn longest_word(text: &str) -> Option<&str> {
    let mut best: Option<&str> = None;
    for w in text.split_whitespace() {
        if best.is_none_or(|b| w.len() > b.len()) {
            best = Some(w);
        }
    }
    best
}
