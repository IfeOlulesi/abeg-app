CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE products (
  sku TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL,
  qty_on_hand INTEGER NOT NULL
);
CREATE TABLE reservations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'  -- active | consumed | cancelled | expired
);
CREATE TABLE reservation_items (
  reservation_id UUID NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
  sku TEXT NOT NULL REFERENCES products(sku),
  qty INTEGER NOT NULL,
  PRIMARY KEY (reservation_id, sku)
);
CREATE TABLE orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reference TEXT UNIQUE NOT NULL,
  customer_name TEXT,
  total NUMERIC(10,2) NOT NULL,
  status TEXT NOT NULL DEFAULT 'placed',  -- placed | cancelled
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE order_items (
  order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  sku TEXT NOT NULL REFERENCES products(sku),
  qty INTEGER NOT NULL,
  unit_price NUMERIC(10,2) NOT NULL,
  PRIMARY KEY (order_id, sku)
);
CREATE TABLE idempotency_keys (
  key TEXT PRIMARY KEY,
  order_id UUID REFERENCES orders(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
