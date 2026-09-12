from test_prompt import build_prompt, client, MODEL_NAME
from validate_sql import generate_and_validate

def generate_sql_fn(question, prior_error, prior_sql):
    prompt = build_prompt(question)
    if prior_error:
        prompt += f"\n\nYour previous attempt failed validation:\n{prior_sql}\n\nError: {prior_error}\n\nFix the query and try again."
    response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
    return response.text.strip() if response.text else ""

result = generate_and_validate(
    "Which product categories generate the highest total sales revenue?",
    generate_sql_fn,
)
print(result)