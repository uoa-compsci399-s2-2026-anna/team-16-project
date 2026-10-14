# The two brand typefaces

`Kai Commitment_Brand Guidelines_v1-Oct25.pdf` specifies **Geologica Bold** for
headings and **Kumbh Sans Regular** for body text, and recommends self-hosting
them. Both are Google Fonts. They are served from this repository rather than
from `fonts.googleapis.com` because contract `docs/interfaces.md` §7.6 rule 7
forbids a public page from fetching any runtime asset from a third-party host;
a server-rendered PDF is the stricter case of the same rule, not an exception.

## Licence

**SIL Open Font License, Version 1.1.** `LICENCE-fonts-brand.txt` beside this
file carries both families' copyright notices and the licence text, verbatim
from each family's own `OFL.txt` as published in `google/fonts`.

Clause 2 is why that file exists: a redistributed copy of the Font Software has
to carry the copyright notice and the licence, and **each of the three
directories below is a redistributed copy** — they are shipped in three
different images and two different wheels. Clause 1 is the licence's one
prohibition and nothing here engages it: the fonts are not sold on their own.
Clause 3 binds nothing, because neither family declares a Reserved Font Name,
so the Latin subsets below may keep their family names.

## Why there are three copies

| Directory | Who reads it |
| --- | --- |
| `web/assets/fonts/` | the browser, served by nginx out of the `web` image |
| `api/assets/fonts/` | WeasyPrint, embedded into the exported PDF |
| `admin/static/fonts/` | the browser, served by the admin panel |

**None of the three can reach either of the others.** `pyproject.toml`'s
`package-data` cannot cross a package boundary, `api/` may not import `admin/`,
and `docker/api.Dockerfile` copies only `admin/ api/ db/ engine/` into its
build context. All three files are byte-identical and a test asserts it, so the
public page and the exported document cannot drift into different type.

## What the files are

Both are **228-glyph Latin subsets** of their upstream families, not the full
faces: the panel and the calculator set Latin text, and the full families are
several times the size for glyphs neither will ever render. A subset is a
Modified Version under the OFL, which permits it.

| File | SHA-256 | Bytes |
| --- | --- | --- |
| `geologica-bold.woff2` | `7f7f79c5a8bcfdae1dffc5b90dc49fd145951a50e2fecb92f97b93be6c7d5bfa` | 14,412 |
| `kumbh-sans-regular.woff2` | `1230484599b2ab89392b1a9bad7a990598dfcca88866b7e1596a24e60e7e1d93` | 12,960 |

They were taken from the `woff2` URL inside the stylesheet Google Fonts' own
`css2` endpoint returns:

```sh
# The pattern, for either family:
curl -s 'https://fonts.googleapis.com/css2?family=Geologica:wght@700&display=swap'
curl -s 'https://fonts.googleapis.com/css2?family=Kumbh+Sans:wght@400&display=swap'
# then fetch the src: url(...) the response names.
```

`geologica-bold.woff2` carries the internal family name *"Geologica Thin Roman
Bold"*, an artefact of exporting one named instance from the variable font. It
is harmless and deliberately left alone: `@font-face` declares the family as
`Geologica` and that is the name the browser keys on.

## What this licence does not cover

The **Kai Commitment's logo, name and brand** are the client's property, used
with the client's permission, and are not licensed by anything in this
directory or by the repository's own `LICENSE`. The brand guidelines state that
the logo may not be altered in any way. The typefaces are Google's under the
OFL; the brand that chose them is not.
