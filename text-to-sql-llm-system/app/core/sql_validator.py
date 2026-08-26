def validate_sql(sql):

    blocked_keywords = [
        "DROP",
        "TRUNCATE",
        "ALTER",
        "INFORMATION_SCHEMA"
    ]

    sql_upper = sql.upper()

    for keyword in blocked_keywords:
        if keyword in sql_upper:
            return False

    return True