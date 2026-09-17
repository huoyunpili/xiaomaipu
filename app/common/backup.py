import hashlib


def database_fingerprint(cursor):
    """Compare actual PostgreSQL row contents without returning private values."""
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    tables = [row[0] for row in cursor.fetchall()]
    result = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        digest = hashlib.sha256()
        count = 0
        cursor.execute(
            f'SELECT to_jsonb(t)::text FROM public.{quoted} t ORDER BY to_jsonb(t)::text COLLATE "C"'
        )
        while rows := cursor.fetchmany(1000):
            for row in rows:
                digest.update(row[0].encode("utf-8") + b"\n")
                count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


if __name__ == "__main__":
    # Also runs against the previous release image during pre-upgrade backup.
    import json
    import os

    import psycopg

    with psycopg.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        options="-c timezone=UTC",
    ) as verified_connection:
        with verified_connection.cursor() as verified_cursor:
            print(json.dumps(database_fingerprint(verified_cursor), sort_keys=True))
