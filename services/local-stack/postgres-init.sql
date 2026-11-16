-- One PostgreSQL for both services' databases, each with its own owner (local stack only).
CREATE USER extraction WITH PASSWORD 'extraction';
CREATE DATABASE extraction_db OWNER extraction;
CREATE USER conversion WITH PASSWORD 'conversion';
CREATE DATABASE conversion_db OWNER conversion;
