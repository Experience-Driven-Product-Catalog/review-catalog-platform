SELECT 'CREATE DATABASE review_catalog'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'review_catalog')\gexec

