from __future__ import annotations


DEFAULT_SCHEMA_CONTEXT = """
数据库是一个电商星型模型：

fact_orders(order_id, user_id, product_id, region_id, order_date, quantity, amount,
discount_amount, channel, payment_method, is_new_user, status)
- amount 是实付金额，统计销售额/GMV 时只统计 status = 'paid'
- channel 可取：搜索、推荐、直播、广告、自然流量、社群
- status 可取：paid、refunded、cancelled

dim_product(product_id, product_name, category, brand, price, stock, shelf_date, product_status)
- category 是商品类目，stock 可用于判断滞销/动销

dim_user(user_id, register_date, province, city, region_id, vip_level)
- is_new_user 在 fact_orders 中标识该用户首单，可用于新客/老客分析

dim_region(region_id, province, city, area, city_tier)
- area 是大区，例如华东、华南、华北、西南、华中、西北、东北

dim_date(date_key, year, quarter, month, week_of_year, day_of_month, day_of_week,
is_weekend, is_campaign_day)
""".strip()


METRIC_CONTEXT = """
核心指标口径：
- 销售额/GMV：SUM(fact_orders.amount)，过滤 status = 'paid'
- 订单量：COUNT(DISTINCT fact_orders.order_id)，过滤 status = 'paid'
- 客单价：销售额 / 订单量
- 销量：SUM(fact_orders.quantity)，过滤 status = 'paid'
- 新客订单：fact_orders.is_new_user = true 且 status = 'paid'
- 复购：同一 user_id 在统计周期内 paid 订单数 >= 2
""".strip()

