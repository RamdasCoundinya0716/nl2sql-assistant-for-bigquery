import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

SCHEMA = """
Table: orders (order_id, user_id, status, gender, created_at, shipped_at, delivered_at, returned_at, num_of_item)
Table: order_items (id, order_id, user_id, product_id, inventory_item_id, status, created_at, shipped_at, delivered_at, returned_at, sale_price)
Table: products (id, cost, category, name, brand, retail_price, department, sku, distribution_center_id)
Table: users (id, age, gender, state, city, country, traffic_source, created_at)
Table: distribution_centers (id, name, latitude, longitude)
"""

question = "Which product categories generate the highest total sales revenue?"

prompt = f"""You are a SQL expert. Given this BigQuery schema:
{SCHEMA}

Write a single BigQuery SQL query to answer this question:
"{question}"

Use fully qualified table names like `bigquery-public-data.thelook_ecommerce.products`.
Return ONLY the SQL query, no explanation, no markdown fences.
"""

model = genai.GenerativeModel("gemini-1.5-flash")
response = model.generate_content(prompt)
print(response.text)