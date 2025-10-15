\set db_user `echo "$DB_USER" || echo "postgres"`
\set db_password `echo "$DB_PASSWORD" || echo "postgres"`
\set db_name `echo "$DB_NAME" || echo "pgbot1010"`

CREATE USER :"db_user" WITH PASSWORD :'db_password';
CREATE DATABASE :"db_name" OWNER :"db_user";
GRANT ALL PRIVILEGES ON DATABASE :"db_name" TO :"db_user";
