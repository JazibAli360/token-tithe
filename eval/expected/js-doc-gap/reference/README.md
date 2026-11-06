# slugkit

Turn any string into a URL slug. Zero dependencies.

## Install

```sh
npm install slugkit
```

## Quick start

```js
const { slugify } = require('slugkit');
slugify('Hello, World!'); // 'hello-world'
```

## API

### `slugify(text, [options])`

Returns `text` as a URL slug.

| Option | Type | Default | Description |
|---|---|---|---|
| `separator` | `string` | `'-'` | Character placed between words. |
| `lowercase` | `boolean` | `true` | Lowercase the result. |
| `maxLength` | `number` | none | Truncate to at most this many characters, never mid-word. |

```js
slugify('a b c', { separator: '_' });          // 'a_b_c'
slugify('one two three', { maxLength: 9 });    // 'one-two'
```

### `isSlug(text, [separator])`

Returns `true` if `text` is already a valid slug for `separator` (default `'-'`).

```js
isSlug('hello-world'); // true
isSlug('Hello World'); // false
```

## License

MIT
