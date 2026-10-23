"""EcoFusion PostgreSQL connection check."""

import os
import psycopg


def create_connection():
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "ecofusion-postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=10,
    )


def main():
    with create_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user")
            database, user = cursor.fetchone()

            print(f"Database: {database}")
            print(f"User: {user}")

            cursor.execute(
                "SELECT COUNT(*) "
                "FROM information_schema.tables "
                "WHERE table_schema = 'ecofusion'"
            )

            table_count = cursor.fetchone()[0]
            print(f"EcoFusion tables: {table_count}")

            if table_count != 3:
                raise RuntimeError(
                    f"Expected 3 tables, found {table_count}"
                )

    print("POSTGRESQL CONNECTION: SUCCESS")


if __name__ == "__main__":
    main()
