# Source documents: never commit them

This repo is public. The documents the rules come from are **not ours to publish**.

- NCC and Australian Standards text is under ABCB / Standards Australia copyright.
- Manufacturer installation manuals belong to the manufacturers.

Publishing the source material would be redistributing someone else's copyrighted
work. Publishing *our structured interpretation* of it, with a citation, is normal
and is what every compliance tool does.

## Never commit

- PDFs, scans, screenshots or photos of manual or standard pages
- Verbatim clause text, or long extracts of it
- Bulk dumps of extracted text (`*_extracted.txt`, OCR output, page-by-page JSON)
- Figures or diagrams lifted from a manual or standard
- Manual pages shown on screen in the demo video

Everything under `data/` is gitignored. Keep it that way. If a tool wants to write
extracted text, write it under `data/` — never in a tracked folder or the repo root.

## The pattern to use instead: paraphrase and cite

Anything we restate from a standard or manual must have:

- `text` — a **short paraphrase in our own words** stating the requirement.
  One sentence. Not a copied clause.
- `source` — the document title and a **public URL**, so anyone can check the
  original themselves

Good:
```json
{ "text": "The shower area floor must be at least 25 mm below the finished floor
            level outside the shower.",
  "source": "NCC2022-HP-10.2" }
```
That is a numeric requirement stated plainly. Facts and dimensions are not
copyrightable; the expression is. Keep it short and in our own words.

Bad: pasting the full clause, its sub-clauses, surrounding commentary, or a figure.

## When extracting rules with Muse Wonder

The model reads the PDF. It must emit **structured rules only** — never the source
text back into the repo.

- Prompt it for `{parameter, operator, value, unit, short paraphrase, clause ref}`
- Do not save the raw model input or the PDF text to a tracked file
- If a value cannot be confirmed from the source, **leave the rule out and flag it**.
  Never invent a number or a clause reference. A wrong dimension in a construction
  tool is worse than a missing one.

## Before adding a new source

Give it a title and a public URL first. If there is no URL a reader can check, the
citation is not verifiable and the value should not ship.
