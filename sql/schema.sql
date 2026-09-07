CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS dim_date (
  date_key DATE PRIMARY KEY,
  year INT NOT NULL,
  quarter INT NOT NULL,
  month INT NOT NULL,
  week_of_year INT NOT NULL,
  day_of_month INT NOT NULL,
  day_of_week INT NOT NULL,
  is_weekend BOOLEAN NOT NULL,
  is_campaign_day BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS dim_region (
  region_id INT PRIMARY KEY,
  province VARCHAR(50) NOT NULL,
  city VARCHAR(50) NOT NULL,
  area VARCHAR(20) NOT NULL,
  city_tier VARCHAR(10) NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_product (
  product_id BIGINT PRIMARY KEY,
  product_name VARCHAR(100) NOT NULL,
  category VARCHAR(50) NOT NULL,
  brand VARCHAR(50) NOT NULL,
  price NUMERIC(10,2) NOT NULL CHECK (price >= 0),
  stock INT NOT NULL CHECK (stock >= 0),
  shelf_date DATE NOT NULL,
  product_status VARCHAR(20) NOT NULL CHECK (product_status IN ('active', 'slow_moving', 'new_arrival'))
);

CREATE TABLE IF NOT EXISTS dim_user (
  user_id BIGINT PRIMARY KEY,
  register_date DATE NOT NULL,
  province VARCHAR(50) NOT NULL,
  city VARCHAR(50) NOT NULL,
  region_id INT NOT NULL REFERENCES dim_region(region_id),
  vip_level VARCHAR(10) NOT NULL CHECK (vip_level IN ('普通', '银卡', '金卡', '黑金'))
);

CREATE TABLE IF NOT EXISTS fact_orders (
  order_id BIGINT PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES dim_user(user_id),
  product_id BIGINT NOT NULL REFERENCES dim_product(product_id),
  region_id INT NOT NULL REFERENCES dim_region(region_id),
  order_date DATE NOT NULL REFERENCES dim_date(date_key),
  quantity INT NOT NULL CHECK (quantity > 0),
  amount NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
  discount_amount NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
  channel VARCHAR(20) NOT NULL CHECK (channel IN ('搜索', '推荐', '直播', '广告', '自然流量', '社群')),
  payment_method VARCHAR(20) NOT NULL CHECK (payment_method IN ('支付宝', '微信支付', '银行卡', '余额')),
  is_new_user BOOLEAN NOT NULL,
  status VARCHAR(10) NOT NULL CHECK (status IN ('paid', 'refunded', 'cancelled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fact_orders_order_date ON fact_orders(order_date);
CREATE INDEX IF NOT EXISTS idx_fact_orders_product_id ON fact_orders(product_id);
CREATE INDEX IF NOT EXISTS idx_fact_orders_user_id ON fact_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_fact_orders_region_id ON fact_orders(region_id);
CREATE INDEX IF NOT EXISTS idx_fact_orders_channel ON fact_orders(channel);
CREATE INDEX IF NOT EXISTS idx_fact_orders_status ON fact_orders(status);
CREATE INDEX IF NOT EXISTS idx_fact_orders_region_date ON fact_orders(region_id, order_date);
CREATE INDEX IF NOT EXISTS idx_dim_product_category ON dim_product(category);
CREATE INDEX IF NOT EXISTS idx_dim_user_register_date ON dim_user(register_date);

CREATE TABLE IF NOT EXISTS data_dictionary (
  id BIGSERIAL PRIMARY KEY,
  table_name VARCHAR(80) NOT NULL,
  column_name VARCHAR(80),
  description TEXT NOT NULL,
  synonyms TEXT[] NOT NULL DEFAULT '{}',
  embedding vector(1536)
);

CREATE TABLE IF NOT EXISTS metric_dictionary (
  metric_name VARCHAR(80) PRIMARY KEY,
  display_name VARCHAR(80) NOT NULL,
  description TEXT NOT NULL,
  expression_sql TEXT NOT NULL,
  filter_sql TEXT,
  synonyms TEXT[] NOT NULL DEFAULT '{}',
  embedding vector(1536)
);

