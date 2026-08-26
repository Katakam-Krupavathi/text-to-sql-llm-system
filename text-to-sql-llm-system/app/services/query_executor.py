import sqlite3


# idhi main thing endhukantey vachina sql ni idhi execute chestunddhi 
def execute_query(db_path, sql_query):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:

        cursor.execute(sql_query)

        # SELECT queries    # ildhi motham read ki
        if cursor.description:

            columns = [
                desc[0]
                for desc in cursor.description
            ]
            rows = cursor.fetchall() 
            result = []
            for row in rows:
                result.append(
                    dict(zip(columns, row))
                )
            conn.commit()
            return result
        # INSERT / UPDATE / DELETE   # idhi write ki 
        else:
            conn.commit()
            return {
                "message": "Query executed successfully",
                "rows_affected": cursor.rowcount
            }
    except Exception as e:

        return {
            "error": str(e)
        }
    finally:
        conn.close()