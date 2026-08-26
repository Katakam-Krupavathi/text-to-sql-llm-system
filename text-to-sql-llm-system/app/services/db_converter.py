import sqlite3

# idhi just sqlite vadi vadu upload chesina db temp create chestham 
def create_temp_db(df, table_name="uploaded_data"):

    conn = sqlite3.connect("temp.db")

    df.to_sql(
        table_name,
        conn,
        if_exists="replace",
        index=False
    )

    conn.close()

    return "temp.db"