"""
Single entry point for the NL2SQL assistant: takes a natural-language
business question, generates SQL via Gemini, validates it (sqlglot +
BigQuery dry-run), retries on fixable errors, and executes the validated
query to return real results.

Usage:
    python answer_question.py "Which product categories generate the highest total sales revenue?"
"""
import sys
import json
from test_prompt import build_prompt, client, MODEL_NAME
from validate_sql import generate_and_validate

def generate_sql_fn(question: str, prior_error: str | None, prior_sql: str | None) -> str:
    prompt = build_prompt(question)
    if prior_error:
        prompt += (
            f"\n\nYour previous attempt failed validation.\n"
            f"Previous SQL:\n{prior_sql}\n\n"
            f"Error:\n{prior_error}\n\n"
            f"Fix the query and return only the corrected SQL, no explanation."
        )
    response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
    return response.text.strip() if response.text else ""


def answer_question(question: str, project: None | str = None) -> dict:
    return generate_and_validate(
        question,
        generate_sql_fn,
        project=project,
        max_retries=2,
        execute=True,
        row_limit=50,
    )


def print_results(result: dict) -> None:
    if not result["success"]:
        print("FAILED to produce a valid, executable query.\n")
        if result.get("execution_error"):
            print(f"Execution error (SQL was valid, run itself failed): {result['execution_error']}")
        print("Attempt history:")
        print(json.dumps(result["attempts"], indent=2))
        return

    print(f"SQL ({len(result['attempts'])} attempt(s)):\n{result['sql']}\n")
    print(f"Results ({len(result['results'])} row(s), capped at row_limit):")
    for row in result["results"]:
        print(row)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python answer_question.py "your question here"')
        sys.exit(1)

    question = sys.argv[1]
    result = answer_question(question)
    print_results(result)