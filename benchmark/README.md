# Benchmark

Your own observation was that no LLM answers these accurately. This proves it.

## Target: 60–100 questions

12 are seeded in `questions.jsonl` from NCC 2022 Part 10.2. Grow it by:
- extracting more NCC clauses
- adding manufacturer-specific numbers (Ardex, Sika, Mapei — the interesting ones,
  because they differ from each other and from the code)
- adding **sequence** questions ("which layer goes on first?") — models are worse
  at these than at numbers, and sequence errors are what actually fail on site

## The three bars

| Condition | What it tests |
|---|---|
| Muse Wonder, cold | Does the model already know this? (Expect: poorly) |
| Muse Wonder + rules.json in context | Does grounding fix it? |
| + verifier rejection and retry | Does inference-time search close the gap? |

Report exact-match accuracy on the numeric answer. One chart, three bars.

## Why this matters for scoring

This is where Technical depth (20%) lives now that measurement is out of scope.
It also justifies the whole architecture: if a plain LLM scored 95% cold, you
wouldn't need rule extraction at all.

## Honesty rule

Publish the cold score even if it is higher than you expect. A benchmark you can
only win is not a benchmark.
