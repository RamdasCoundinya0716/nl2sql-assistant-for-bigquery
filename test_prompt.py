import os
from dotenv import load_dotenv
from google import genai
import logging

logging.getLogger("google_genai.models").setLevel(logging.ERROR)
load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

SCHEMA = """
Table: orders (order_id, user_id, status, gender, created_at, shipped_at, delivered_at, returned_at, num_of_item)
Table: order_items (id, order_id, user_id, product_id, inventory_item_id, status, created_at, shipped_at, delivered_at, returned_at, sale_price)
Table: products (id, cost, category, name, brand, retail_price, department, sku, distribution_center_id)
Table: users (id, age, gender, state, city, country, traffic_source, created_at)
Table: distribution_centers (id, name, latitude, longitude)
"""

FEW_SHOT_EXAMPLES = """
Note: Cancelled and Returned items never represent a final, realized transaction —
Cancelled means no sale occurred, and Returned means the sale was reversed and the
revenue refunded. Because of this, ANY calculation representing real revenue, cost,
or discount impact — whether it's a rate/percentage or a raw dollar total — should
exclude Cancelled and Returned items. Processing/Shipped items are additionally
excluded specifically from completion-rate or return-rate calculations, since those
items haven't yet reached a final outcome and would understate the rate either way.

Example 1:
Question: "What is our total revenue by brand?"
SQL:
SELECT p.brand, SUM(oi.sale_price) AS total_revenue
FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi
JOIN `bigquery-public-data.thelook_ecommerce.products` AS p ON oi.product_id = p.id
WHERE oi.status NOT IN ('Cancelled', 'Returned')
GROUP BY p.brand
ORDER BY total_revenue DESC

Example 2:
Question: "How many completed orders do we have per country?"
SQL:
SELECT u.country, COUNT(DISTINCT o.order_id) AS order_count
FROM `bigquery-public-data.thelook_ecommerce.orders` AS o
JOIN `bigquery-public-data.thelook_ecommerce.users` AS u ON o.user_id = u.id
WHERE o.status NOT IN ('Cancelled', 'Returned')
GROUP BY u.country
ORDER BY order_count DESC

Example 3:
Question: "What is our return rate by category?"
SQL:
SELECT
  p.category,
  COUNTIF(oi.status = 'Returned') AS returned_items,
  COUNTIF(oi.status IN ('Returned', 'Complete')) AS eligible_items,
  SAFE_DIVIDE(
    COUNTIF(oi.status = 'Returned'),
    COUNTIF(oi.status IN ('Returned', 'Complete'))
  ) * 100 AS return_rate_percentage
FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi
JOIN `bigquery-public-data.thelook_ecommerce.products` AS p ON oi.product_id = p.id
GROUP BY p.category
ORDER BY return_rate_percentage DESC

Example 4:
Question: "What percentage of orders are still in transit by distribution center?"
SQL:
SELECT
  dc.name AS distribution_center,
  SAFE_DIVIDE(
    COUNTIF(oi.status = 'Shipped'),
    COUNT(oi.id)
  ) * 100 AS in_transit_percentage
FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi
JOIN `bigquery-public-data.thelook_ecommerce.products` AS p
  ON oi.product_id = p.id
JOIN `bigquery-public-data.thelook_ecommerce.distribution_centers` AS dc
  ON p.distribution_center_id = dc.id
WHERE oi.status != 'Cancelled'
GROUP BY dc.name
ORDER BY in_transit_percentage DESC

Example 5:
Question: "How much revenue are we losing to discounts, comparing retail price vs actual sale price?"
SQL:
SELECT
  SUM(p.retail_price - oi.sale_price) AS lost_revenue
FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi
JOIN `bigquery-public-data.thelook_ecommerce.products` AS p ON oi.product_id = p.id
WHERE oi.status NOT IN ('Cancelled', 'Returned')
"""

question = "Which product categories generate the highest total sales revenue?"

MODEL_NAME = "gemini-3.6-flash" 

def build_prompt(question: str) -> str:
    return f"""You are a SQL expert. Given this BigQuery schema:
{SCHEMA}

Here are examples of questions and their corresponding SQL queries:
{FEW_SHOT_EXAMPLES}

Now write a single BigQuery SQL query to answer this question:
"{question}"

Use fully qualified table names like `bigquery-public-data.thelook_ecommerce.products`.
Return ONLY the SQL query, no explanation, no markdown fences.
"""

def generate_sql(question: str, model: str = MODEL_NAME) -> str:
    prompt = build_prompt(question)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    return response.text.strip() if response.text else ""


if __name__ == "__main__":
    sql = generate_sql(question)
    print(sql)