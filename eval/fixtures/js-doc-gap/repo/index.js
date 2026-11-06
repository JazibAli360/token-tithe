'use strict';

/**
 * Convert text into a URL slug.
 * @param {string} text
 * @param {object} [options]
 * @param {string} [options.separator='-'] character placed between words
 * @param {boolean} [options.lowercase=true] lowercase the result
 * @param {number} [options.maxLength] truncate to at most this many characters (never mid-word)
 * @returns {string}
 */
function slugify(text, options = {}) {
  const { separator = '-', lowercase = true, maxLength } = options;
  let words = String(text)
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean);
  if (lowercase) words = words.map((w) => w.toLowerCase());
  let slug = words.join(separator);
  if (maxLength !== undefined && slug.length > maxLength) {
    const cut = slug.slice(0, maxLength + 1);
    const idx = cut.lastIndexOf(separator);
    slug = idx > 0 ? cut.slice(0, idx) : slug.slice(0, maxLength);
  }
  return slug;
}

/**
 * True if `text` is already a valid slug for the given separator.
 * @param {string} text
 * @param {string} [separator='-']
 * @returns {boolean}
 */
function isSlug(text, separator = '-') {
  return slugify(text, { separator, lowercase: false }) === text && text.length > 0;
}

module.exports = { slugify, isSlug };
