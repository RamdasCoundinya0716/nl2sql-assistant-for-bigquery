# NL2SQL Assistant for BigQuery

A natural-language-to-SQL assistant that translates plain-English business
questions into validated, executable BigQuery SQL — built against Google's
public `thelook_ecommerce` dataset to explore applied LLM/prompt-engineering
patterns for data engineering and analytics workflows.

## What it does

```
question (plain English)
    │
    ▼
Gemini generates SQL (schema-aware prompt + few-shot examples)
    │
    ▼
Validate: sqlglot (statement type, table allowlist)
    │       + BigQuery dry-run (authoritative syntax/schema check, no cost)
    ▼
   Fail? ──► retryable? ──► yes: feed error back to Gemini, retry (max 2x)
    │                  └──► no: stop immediately (scope violation, DML attempt)
    ▼
   Pass: execute the real query, return results
```

Run it:
```
python answer_question.py "Which product categories generate the highest total sales revenue?"
```

## Architecture

- **`test_prompt.py`** — schema definition, few-shot examples, prompt construction, Gemini API call (`google-genai` SDK)
- **`validate_sql.py`** — two-layer SQL validation, retry classification, and query execution
- **`answer_question.py`** — single entry point tying generation → validation → retry → execution together

## Scope, honestly stated

This is **not** a RAG system. There's no vector database, no LangChain/LlamaIndex,
no retrieval step — the full 5-table schema is passed directly in the prompt.
That's a deliberate choice at this scale (5 tables is small enough that
retrieval would add complexity without benefit), not an oversight, but it's
worth being precise about rather than calling this "RAG-style" as the project
was originally pitched.

**Tables in scope:** `orders`, `order_items`, `products`, `users`, `distribution_centers`
**Deliberately excluded:** `events` (different grain, clickstream data), `inventory_items` (deferred to a possible v2)

## Validation approach

Two layers, verified to catch genuinely different failure classes (not redundant):

1. **`sqlglot` (local, no network)** — parses the query, rejects non-`SELECT`
   statements (including DML hidden inside a CTE), checks every referenced
   table against an explicit allowlist.
   - **Known limitation:** `sqlglot`'s parser is lenient — a genuinely garbled
     query (e.g. `SELEC * FORM orders`) doesn't reliably raise a parse error;
     it can get parsed into an unrelated expression type and only gets caught
     incidentally by the "must be SELECT" check. This layer's real guarantee
     is statement-type and table-scope enforcement, not syntax correctness.
2. **BigQuery dry-run (`dry_run=True`, no cost, no data scanned)** — the
   authoritative check, since it uses BigQuery's real parser and schema.
   Confirmed via testing that it catches things layer 1 structurally cannot,
   e.g. a hallucinated column name that's a syntactically valid `SELECT`.

## Retry logic

Not every validation failure is worth retrying. Failures are classified:

- **Retryable** — a parse/syntax error, or a hallucinated column/table name
  caught by BigQuery's dry-run. These are things the model has a real shot
  at fixing given the specific error message.
- **Non-retryable** — a reference to a table outside the allowed schema, a
  DML attempt (`INSERT`/`UPDATE`/`DELETE`/etc.), or a query exceeding the
  byte-scan safety cap. Retrying these guarantees the same failure — there's
  no fix available within the given schema/constraints — so the loop stops
  immediately instead of burning API calls.

## Findings from testing (the actual point of this project)

Testing 8 of 12 planned business questions surfaced real, non-obvious behavior:

- **Under-generalization:** a bare schema prompt (no few-shot examples)
  produced syntactically correct but business-logic-incomplete SQL — it
  included cancelled/returned orders in a "total revenue" calculation.
  Adding few-shot examples with an explicit stated rule fixed it.
- **Over-generalization (the more interesting failure):** once the model
  learned to exclude cancelled/returned orders from revenue questions, it
  started applying that filter even to a raw revenue total question, and,
  in a different case, to a query where it wasn't clearly warranted. This
  is the mirror-image failure to under-generalization — the model
  over-applies a learned rule when it merely pattern-matches "this looks like
  the same kind of question," rather than reasoning about *why* the rule
  applied in the first place. Fixed by rewriting the rule's scoping language
  to explain the underlying principle (only exclude never-realized or
  reversed transactions), not just show more instances of it, then verified
  against the sharpest edge case (a raw revenue total that should legitimately
  differ from a completion-rate calculation).
- **A real join-fan-out bug:** a "percentage of orders in transit by
  distribution center" query counted `DISTINCT order_id` after joining
  through `products`, which double-counts an order across two DCs if its
  items ship from different distribution centers. Fixed by changing the
  grain to count `order_items` (each row maps to exactly one product/DC)
  instead of distinct orders.
- **A genuine data limitation, not a bug:** a "stuck orders" query needs to
  know when an order entered `Processing`, but `shipped_at` is `NULL` for
  every `Processing` row — there's no such timestamp in the dataset.
  `created_at` is the best available proxy, with the caveat that it may
  overstate "stuck" duration for orders that passed through earlier states.

## Known limitations

- `answer_question.py` caps results at 50 rows for console readability —
  large result sets (e.g. a full date-range trend) are silently truncated
  past that without a warning to the user.
- Only 8 of 12 originally scoped business questions have been tested this way.
- No UI — this is a CLI/script-based demo, not a deployed service.

## Setup

```
pip install -r requirements.txt
gcloud auth application-default login
```

Set `GEMINI_API_KEY` in a `.env` file (see `.env.example` if present, or
create your own — never commit `.env`).