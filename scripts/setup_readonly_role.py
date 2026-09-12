"""
Script to create a dedicated read-only PostgreSQL role for the text-to-sql agent.
This provides database-level defense-in-depth against destructive queries.
"""

import argparse
import sys
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT


def setup_readonly_role(
    host: str,
    port: int,
    dbname: str,
    admin_user: str,
    admin_password: str,
    readonly_user: str = "sql_readonly",
    readonly_password: str = "readonly_password",
):
    print(f"Connecting to database '{dbname}' on {host}:{port} as admin '{admin_user}'...")
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=admin_user,
            password=admin_password,
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()

        role_ident = sql.Identifier(readonly_user)
        db_ident = sql.Identifier(dbname)

        # Check if role exists, create if not
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s;", (readonly_user,))
        exists = cur.fetchone()
        if not exists:
            print(f"Creating role '{readonly_user}'...")
            query = sql.SQL("CREATE ROLE {role} WITH LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;").format(
                role=role_ident
            )
            cur.execute(query, (readonly_password,))
        else:
            print(f"Role '{readonly_user}' already exists. Updating password and permissions...")
            query = sql.SQL("ALTER ROLE {role} WITH PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;").format(
                role=role_ident
            )
            cur.execute(query, (readonly_password,))

        # Grant read-only permissions
        print(f"Configuring read-only permissions for '{readonly_user}' on database '{dbname}'...")
        
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {db} TO {role};").format(
            db=db_ident, role=role_ident
        ))
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {role};").format(
            role=role_ident
        ))
        cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role};").format(
            role=role_ident
        ))
        cur.execute(sql.SQL("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};").format(
            role=role_ident
        ))
        
        # Default privileges for future tables
        cur.execute(sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role};"
        ).format(role=role_ident))
        cur.execute(sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO {role};"
        ).format(role=role_ident))

        # Explicitly revoke write permissions
        cur.execute(sql.SQL(
            "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM {role};"
        ).format(role=role_ident))

        cur.close()
        conn.close()
        print(f"Successfully configured read-only role '{readonly_user}'.")

    except Exception as e:
        print(f"Error setting up read-only role: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a read-only Postgres role.")
    parser.add_argument("--host", default="localhost", help="Postgres host")
    parser.add_argument("--port", type=int, default=5432, help="Postgres port")
    parser.add_argument("--dbname", default="text_to_sql_db", help="Target database name")
    parser.add_argument("--admin-user", default="postgres", help="Admin username")
    parser.add_argument("--admin-password", default="postgres", help="Admin password")
    parser.add_argument("--readonly-user", default="sql_readonly", help="Read-only username")
    parser.add_argument("--readonly-password", default="readonly_password", help="Read-only password")

    args = parser.parse_args()
    setup_readonly_role(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        admin_user=args.admin_user,
        admin_password=args.admin_password,
        readonly_user=args.readonly_user,
        readonly_password=args.readonly_password,
    )
