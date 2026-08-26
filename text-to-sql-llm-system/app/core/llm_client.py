from transformers import pipeline
import re

# idhi main , schema ni pampinchi sql generate cheydaniki  vadtham 

generator = pipeline(
    "text-generation",
    model="microsoft/Phi-3-mini-4k-instruct"
)

def generate_sql(question, schema):

    prompt = f"""
You are an expert SQL generator.

Database Schema:
{schema}

Rules:
1. Database type is SQLite
2. Use ONLY tables from schema
3. Never use information_schema
4. Never invent tables
5. Return SQL only
6. Generate only ONE SQL query
7. Do not generate BEGIN, COMMIT, TRANSACTION
8. End with semicolon

Question:
{question}
"""

    response = generator(
        prompt,
        max_new_tokens=80,
        return_full_text=False
    )

    output = response[0]["generated_text"]

    print("RAW OUTPUT:\n", output)

    match = re.search(
        r"(SELECT|INSERT|UPDATE|DELETE)[\s\S]*?;",
        output,
        re.IGNORECASE
    )

    if match:
        sql = match.group(0).strip()
        return sql

    return "INVALID_SQL"