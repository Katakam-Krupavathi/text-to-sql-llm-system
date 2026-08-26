from app.core.llm_client import generate_sql
from app.services.query_executor import execute_query
from app.core.sql_validator import validate_sql

schema = {
    "Customer": [
        "CustomerId",
        "FirstName",
        "LastName",
        "Email"
    ]
}

question = "Show all customer emails"

sql = generate_sql(
    question,
    schema
)

print("\nGenerated SQL:")
print(sql)

is_safe = validate_sql(sql)

if not is_safe:

    print("\nUnsafe SQL blocked")

else:

    result = execute_query(
        "app/uploads/Chinook.db",
        sql
    )

    print("\nResults:")
    print(result[:5])