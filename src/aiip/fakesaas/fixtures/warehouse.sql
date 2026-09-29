-- Databricks/Snowflake-style SQL warehouse stand-in (loaded into in-memory SQLite).
CREATE TABLE delivery_kpis (region TEXT, week TEXT, on_time_pct REAL, late_shipments INTEGER, avg_delay_hours REAL);
INSERT INTO delivery_kpis VALUES ('east', '2026-W37', 0.94, 12, 5.5), ('east', '2026-W38', 0.91, 18, 7.0),
  ('west', '2026-W37', 0.97, 4, 2.0), ('west', '2026-W38', 0.96, 6, 2.5);
CREATE TABLE carrier_performance (carrier TEXT, week TEXT, on_time_pct REAL, claims_open INTEGER);
INSERT INTO carrier_performance VALUES ('CARRIER-07', '2026-W38', 0.82, 3), ('CARRIER-02', '2026-W38', 0.98, 0);
CREATE TABLE customer_orders_pii (account TEXT, contact_email TEXT, card_last4 TEXT);
INSERT INTO customer_orders_pii VALUES ('ACC-1001', 'buyer@northwind.example', '0000');
