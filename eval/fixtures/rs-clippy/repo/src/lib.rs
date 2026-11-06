//! Word statistics.

/// Count whitespace-separated words.
pub fn word_count(text: &str) -> usize {
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.len() == 0 {
        return 0;
    }
    words.len()
}

/// Sum of the lengths of all words.
pub fn total_letters(text: &str) -> usize {
    let words: Vec<&str> = text.split_whitespace().collect();
    let mut total = 0;
    for i in 0..words.len() {
        total += words[i].len();
    }
    total
}

/// Longest word; the first one wins a tie.
pub fn longest_word(text: &str) -> Option<&str> {
    let mut best: Option<&str> = None;
    for w in text.split_whitespace() {
        if best.map_or(true, |b| w.len() > b.len()) {
            best = Some(w);
        }
    }
    return best;
}
