import sqlglot
from sqlglot import exp
from google.cloud import bigquery
from google.api_core.exceptions import BadRequest, NotFound

ALLOWED_TABLES = {
    "orders", "order_items", "products", "users", "distribution_centers"
}

FORBIDDEN_STATEMENT_TYPES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.AlterTable,
    exp.Create, exp.TruncateTable,
)


class SQLValidationError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def validate_sql(sql: str, dialect: str = "bigquery") -> exp.Expression:
    """
    Parses and validates a generated SQL query before execution.
    Raises SQLValidationError with a specific reason on failure.
    Returns the parsed expression tree on success.
    """
    # 1. Parse — catches syntax errors early
    try:
        parsed = sqlglot.parse_one(sql, read=dialect)
    except Exception as e:
        raise SQLValidationError(f"SQL failed to parse: {e}", retryable=True)

    # 2. Must be a SELECT statement only — if it's not, it's almost always a DML
    #    attempt (INSERT/UPDATE/DELETE/etc)
    if not isinstance(parsed, exp.Select):
        raise SQLValidationError(
            f"Only SELECT statements are allowed. Got: {type(parsed).__name__}",
            retryable=False,
        )

    # 3. Explicitly reject forbidden statement types anywhere in the tree
    #    (defends against e.g. a CTE hiding a DML statement).
    for node in parsed.walk():
        if isinstance(node, FORBIDDEN_STATEMENT_TYPES):
            raise SQLValidationError(
                f"Forbidden statement type found: {type(node).__name__}",
                retryable=False,
            )

    # 4. Validate every referenced table is in the allowed schema.
    referenced_tables = {t.name.lower() for t in parsed.find_all(exp.Table)}
    unknown_tables = referenced_tables - ALLOWED_TABLES
    if unknown_tables:
        raise SQLValidationError(
            f"Query references unknown/disallowed tables: {unknown_tables}",
            retryable=False,
        )

    return parsed


def dry_run_validate(sql: str, project: None | str = None, max_bytes_billed: int = 500 * 1024 ** 3) -> dict:
    """
    Sends the query to BigQuery with dry_run=True: no data is scanned or returned,
    and nothing is billed, but BigQuery's own parser and planner validate the query
    for real. This is the authoritative syntax/schema check — sqlglot's parser is
    lenient and can miss malformed SQL that still happens to resemble a SELECT
    (see validate_sql's known limitation).

    Returns a dict with total_bytes_processed (useful as a cost/scope sanity check)
    on success. Raises SQLValidationError with BigQuery's own message on failure.
    """
    client = bigquery.Client(project=project)
    job_config = bigquery.QueryJobConfig(
        dry_run=True,
        use_query_cache=False,
    )
    try:
        query_job = client.query(sql, job_config=job_config)
    except (BadRequest, NotFound) as e:
        raise SQLValidationError(f"BigQuery rejected the query: {e.message}", retryable=True)

    bytes_processed = query_job.total_bytes_processed
    if bytes_processed is None:
        raise SQLValidationError(
            "BigQuery did not return a byte estimate for this query.", retryable=True
        )

    if bytes_processed > max_bytes_billed:
        raise SQLValidationError(
            f"Query would scan {bytes_processed / 1024**3:.2f} GB, "
            f"exceeding the {max_bytes_billed / 1024**3:.0f} GB safety limit.",
            retryable=False,
        )

    return {"total_bytes_processed": bytes_processed}


def execute_query(sql: str, project: None | str = None, row_limit: int = 1000) -> list[dict]:
    """
    Actually runs the (already-validated) query against BigQuery and returns
    real results as a list of dicts. This is deliberately a separate call from
    validation — full_validate/dry_run_validate never scan real data or incur
    cost; this function does both. Always call full_validate() on the SQL
    first and only pass validated queries here.

    row_limit caps how many rows are materialized into memory/printed — a
    safety measure independent of the dry-run byte-scan cap, since a query
    can scan modestly but still return a huge result set (e.g. no GROUP BY).
    """
    client = bigquery.Client(project=project)
    query_job = client.query(sql)  # real run, not dry_run this time
    rows = []
    for i, row in enumerate(query_job.result()):
        if i >= row_limit:
            break
        rows.append(dict(row.items()))
    return rows


def full_validate(sql: str, project: None | str = None) -> dict:
    """
    Runs both validation layers in order: cheap local checks first (sqlglot —
    no network call, rejects obvious statement-type/table violations fast),
    then the authoritative BigQuery dry-run (network call, but confirms real
    syntax and schema correctness). Fail fast on the cheap layer before
    spending a network round-trip on the expensive one.
    """
    validate_sql(sql)  # raises SQLValidationError on failure
    return dry_run_validate(sql, project=project)


def generate_and_validate(
    question: str,
    generate_sql_fn,
    project: None | str = None,
    max_retries: int = 2,
    execute: bool = False,
    row_limit: int = 1000,
) -> dict:
    """
    Full generate -> validate -> retry -> (optionally) execute loop.

    generate_sql_fn: a callable taking (question: str, prior_error: str | None,
    prior_sql: str | None) -> str, which builds the prompt (feeding the error
    and prior attempt back in on a retry) and returns the model's SQL text.
    This is intentionally left as an injected function rather than importing
    the Gemini client directly here, so validate_sql.py stays independent of
    which LLM SDK is in use.

    Only retries when the failure is classified as retryable (see the
    `retryable` flag set at each raise site in validate_sql/dry_run_validate).
    A non-retryable failure (scope violation, DML attempt, cost-limit breach)
    stops immediately — retrying it would just burn an API call on a
    guaranteed repeat failure.

    If execute=True and validation succeeds, actually runs the query and
    includes real results in the returned dict. If execution itself fails
    for some reason (rare, since it was just dry-run validated — but e.g. a
    transient API error), that failure is NOT retried by regenerating SQL;
    it's surfaced as its own error, since the SQL itself was already proven
    valid and regenerating it would not address an execution-time problem.

    Returns a dict: {"success": bool, "sql": str | None, "results": list[dict]
    | None, "attempts": [...]} where each attempt records the SQL tried and
    the outcome, for transparency.
    """
    attempts = []
    prior_error = None
    prior_sql = None

    for attempt_num in range(1, max_retries + 2):  # first try + max_retries
        sql = generate_sql_fn(question, prior_error, prior_sql)

        try:
            full_validate(sql, project=project)
            attempts.append({"attempt": attempt_num, "sql": sql, "status": "success"})

            results = None
            if execute:
                try:
                    results = execute_query(sql, project=project, row_limit=row_limit)
                except Exception as e:
                    return {
                        "success": False,
                        "sql": sql,
                        "results": None,
                        "attempts": attempts,
                        "execution_error": str(e),
                    }

            return {"success": True, "sql": sql, "results": results, "attempts": attempts}

        except SQLValidationError as e:
            attempts.append({
                "attempt": attempt_num,
                "sql": sql,
                "status": "failed",
                "error": str(e),
                "retryable": e.retryable,
            })

            if not e.retryable:
                # Non-retryable: stop now, don't waste further API calls.
                return {"success": False, "sql": None, "results": None, "attempts": attempts}


            prior_error = str(e)
            prior_sql = sql
            # loop continues to the next attempt if retries remain

    # Exhausted all retries on retryable errors
    return {"success": False, "sql": None, "results": None, "attempts": attempts}


if __name__ == "__main__":
    # Quick self-tests
    good_sql = """
        SELECT p.category, SUM(oi.sale_price) AS total_revenue
        FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi
        JOIN `bigquery-public-data.thelook_ecommerce.products` AS p ON oi.product_id = p.id
        WHERE oi.status NOT IN ('Cancelled', 'Returned')
        GROUP BY p.category
        ORDER BY total_revenue DESC
    """
    bad_sql_dml = "DELETE FROM `bigquery-public-data.thelook_ecommerce.orders` WHERE order_id = 1"
    bad_sql_table = "SELECT * FROM `bigquery-public-data.thelook_ecommerce.secret_table`"
    bad_sql_syntax = "SELEC * FORM orders"

    for label, sql in [
        ("good_sql", good_sql),
        ("bad_sql_dml", bad_sql_dml),
        ("bad_sql_table", bad_sql_table),
        ("bad_sql_syntax", bad_sql_syntax),
    ]:
        try:
            validate_sql(sql)
            print(f"{label}: PASSED local (sqlglot) validation")
        except SQLValidationError as e:
            print(f"{label}: REJECTED locally — {e}")
            continue  # don't bother with the network call if local check already failed

        try:
            result = dry_run_validate(sql)
            print(f"{label}: PASSED BigQuery dry-run — would scan "
                  f"{result['total_bytes_processed'] / 1024**2:.2f} MB")
        except SQLValidationError as e:
            print(f"{label}: REJECTED by BigQuery dry-run — {e}")