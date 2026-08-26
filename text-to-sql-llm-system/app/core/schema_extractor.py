import sqlite3

def extract_schema(db_path: str):

    conn = sqlite3.connect(db_path)

    cursor = conn.cursor()

    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table';"
    )

    tables = cursor.fetchall()

    schema = {}

    for table in tables:

        table_name = table[0]

        # quote table name
        cursor.execute(
            f'PRAGMA table_info("{table_name}")'
        )

        columns = cursor.fetchall()

        schema[table_name] = [
            column[1]
            for column in columns
        ]

    conn.close()

    return schema