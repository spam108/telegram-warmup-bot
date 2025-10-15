\set db_user `echo "$DB_USER" || echo "postgres"`
\set db_password `echo "$DB_PASSWORD" || echo "postgres"`
\set db_name `echo "$DB_NAME" || echo "pgbot1010"`

CREATE USER :"db_user" WITH PASSWORD :'db_password';
CREATE DATABASE :"db_name" OWNER :"db_user";
GRANT ALL PRIVILEGES ON DATABASE :"db_name" TO :"db_user";

\connect :"db_name"

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'accounts'
    ) THEN
        EXECUTE $$
            CREATE TABLE IF NOT EXISTS reaction_settings (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                setting_key TEXT NOT NULL,
                setting_value TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (account_id, setting_key)
            )
        $$;
        EXECUTE $$
            CREATE TABLE IF NOT EXISTS warmup_logs (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT,
                status TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        $$;
    END IF;
END
$$;
