'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { slugify, isSlug } = require('..');

test('basic', () => assert.strictEqual(slugify('Hello, World!'), 'hello-world'));
test('accents', () => assert.strictEqual(slugify('Crème Brûlée'), 'creme-brulee'));
test('separator', () => assert.strictEqual(slugify('a b c', { separator: '_' }), 'a_b_c'));
test('maxLength never cuts a word', () =>
  assert.strictEqual(slugify('one two three', { maxLength: 9 }), 'one-two'));
test('isSlug', () => {
  assert.ok(isSlug('hello-world'));
  assert.ok(!isSlug('Hello World'));
});
