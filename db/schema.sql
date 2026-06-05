-- ============================================================================
-- AMAZON OPERATIONS INTELLIGENCE PLATFORM — PostgreSQL Schema
-- ============================================================================
-- Every table includes store_id and marketplace_id for multi-store isolation.
-- No cross-store data mixing.
-- All tables have created_at/updated_at timestamps.
-- ============================================================================

BEGIN;

-- ============================================================================
-- 1. STORES
-- ============================================================================
CREATE TABLE IF NOT EXISTS stores (
    store_id            VARCHAR(64) PRIMARY KEY,
    name                VARCHAR(255) NOT NULL,
    marketplace_ids     TEXT[] NOT NULL DEFAULT '{ATVPDKIKX0DER}',
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 2. PRODUCTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS products (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    sku                 VARCHAR(255) NOT NULL,
    asin                VARCHAR(32),
    fnsku               VARCHAR(64),
    upc                 VARCHAR(32),
    ean                 VARCHAR(32),
    title               TEXT,
    brand               VARCHAR(255),
    product_group       VARCHAR(255),
    category_id         VARCHAR(64),
    category_name       VARCHAR(255),
    unit_cost           NUMERIC(12,4) DEFAULT 0,
    weight_pounds       NUMERIC(8,2),
    dimensions          VARCHAR(128),
    is_active           BOOLEAN DEFAULT TRUE,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, sku)
);

CREATE INDEX idx_products_asin ON products(asin);
CREATE INDEX idx_products_sku ON products(sku);
CREATE INDEX idx_products_store ON products(store_id, marketplace_id);

-- ============================================================================
-- 3. INVENTORY SNAPSHOT
-- ============================================================================
CREATE TABLE IF NOT EXISTS inventory_snapshot (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    sku                 VARCHAR(255) NOT NULL,
    fnsku               VARCHAR(64),
    asin                VARCHAR(32),
    total_units         INTEGER NOT NULL DEFAULT 0,
    inbound_units       INTEGER NOT NULL DEFAULT 0,
    sellable_units      INTEGER NOT NULL DEFAULT 0,
    unsellable_units    INTEGER NOT NULL DEFAULT 0,
    reserved_units      INTEGER NOT NULL DEFAULT 0,
    days_of_supply      NUMERIC(10,2),
    forecast_depletion  DATE,
    warehouse_location  VARCHAR(128),
    snapshot_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, sku, snapshot_date)
);

CREATE INDEX idx_inv_store_date ON inventory_snapshot(store_id, snapshot_date);
CREATE INDEX idx_inv_sku ON inventory_snapshot(sku);
CREATE INDEX idx_inv_days ON inventory_snapshot(days_of_supply);

-- ============================================================================
-- 4. ORDERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS orders (
    id                      BIGSERIAL PRIMARY KEY,
    store_id                VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id          VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    source                  VARCHAR(32) NOT NULL DEFAULT 'amazon',
    amazon_order_id         VARCHAR(64) NOT NULL,
    purchase_date           TIMESTAMPTZ,
    last_updated_date       TIMESTAMPTZ,
    order_status            VARCHAR(32),
    fulfillment_channel     VARCHAR(32),
    sales_channel           VARCHAR(128),
    order_total_amount      NUMERIC(12,2),
    order_total_currency    VARCHAR(8) DEFAULT 'USD',
    payment_method          VARCHAR(64),
    number_of_items_shipped INTEGER DEFAULT 0,
    number_of_items_unshipped INTEGER DEFAULT 0,
    buyer_email             VARCHAR(255),
    buyer_name              VARCHAR(255),
    ship_country            VARCHAR(8),
    ship_state              VARCHAR(64),
    ship_city               VARCHAR(128),
    ship_postal_code        VARCHAR(32),
    is_business_order       BOOLEAN DEFAULT FALSE,
    is_prime                BOOLEAN DEFAULT FALSE,
    is_premium_order        BOOLEAN DEFAULT FALSE,
    raw_data                JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, amazon_order_id)
);

CREATE INDEX idx_orders_store_date ON orders(store_id, purchase_date);
CREATE INDEX idx_orders_status ON orders(order_status);
CREATE INDEX idx_orders_ship ON orders(ship_country);

-- ============================================================================
-- 5. ORDER ITEMS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_items (
    id                  BIGSERIAL PRIMARY KEY,
    order_id            BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    amazon_order_id     VARCHAR(64) NOT NULL,
    asin                VARCHAR(32),
    seller_sku          VARCHAR(255),
    title               TEXT,
    quantity_ordered    INTEGER NOT NULL DEFAULT 0,
    quantity_shipped    INTEGER NOT NULL DEFAULT 0,
    item_price_amount   NUMERIC(12,2),
    item_price_currency VARCHAR(8),
    shipping_price      NUMERIC(12,2),
    shipping_discount   NUMERIC(12,2),
    promotion_discount  NUMERIC(12,2),
    tax_amount          NUMERIC(12,2),
    gift_wrap_price     NUMERIC(12,2),
    gift_wrap_tax       NUMERIC(12,2),
    condition_id        VARCHAR(32),
    condition_note      TEXT,
    fulfillment_channel VARCHAR(32),
    is_gift             BOOLEAN DEFAULT FALSE,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_order_items_asin ON order_items(asin);
CREATE INDEX idx_order_items_sku ON order_items(seller_sku);

-- ============================================================================
-- 6. INBOUND SHIPMENTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS inbound_shipments (
    id                              BIGSERIAL PRIMARY KEY,
    store_id                        VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id                  VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    shipment_id                     VARCHAR(64) NOT NULL,
    shipment_name                   VARCHAR(255),
    shipment_status                 VARCHAR(64),
    destination_fulfillment_center  VARCHAR(64),
    label_prep_type                 VARCHAR(64),
    shipped_date                    TIMESTAMPTZ,
    estimated_arrival_date          TIMESTAMPTZ,
    received_date                   TIMESTAMPTZ,
    total_units_shipped             INTEGER DEFAULT 0,
    total_units_received            INTEGER DEFAULT 0,
    discrepancy_units               INTEGER DEFAULT 0,
    raw_data                        JSONB,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, shipment_id)
);

CREATE INDEX idx_shipments_store ON inbound_shipments(store_id);
CREATE INDEX idx_shipments_status ON inbound_shipments(shipment_status);

-- ============================================================================
-- 7. SHIPMENT DISCREPANCIES  (HIGHEST PRIORITY MODULE)
-- ============================================================================
CREATE TABLE IF NOT EXISTS shipment_discrepancies (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    shipment_id         VARCHAR(64) NOT NULL,
    shipment_item_id    VARCHAR(64),
    sku                 VARCHAR(255) NOT NULL,
    fnsku               VARCHAR(64),
    quantity_shipped    INTEGER NOT NULL DEFAULT 0,
    quantity_received   INTEGER NOT NULL DEFAULT 0,
    quantity_damaged    INTEGER NOT NULL DEFAULT 0,
    quantity_lost       INTEGER NOT NULL DEFAULT 0,
    discrepancy_type    VARCHAR(32) NOT NULL CHECK (discrepancy_type IN ('damaged', 'missing', 'overage', 'mismatch')),
    estimated_loss      NUMERIC(12,2) DEFAULT 0,
    claim_filed         BOOLEAN NOT NULL DEFAULT FALSE,
    claim_id            VARCHAR(64),
    claim_status        VARCHAR(64) DEFAULT 'not_filed',
    claim_filed_date    TIMESTAMPTZ,
    claim_resolved_date TIMESTAMPTZ,
    evidence_ready      BOOLEAN NOT NULL DEFAULT FALSE,
    evidence_path       TEXT,
    notes               TEXT,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, shipment_id, sku)
);

CREATE INDEX idx_disc_shipment ON shipment_discrepancies(shipment_id);
CREATE INDEX idx_disc_claim ON shipment_discrepancies(claim_filed);
CREATE INDEX idx_disc_store ON shipment_discrepancies(store_id);

-- ============================================================================
-- 8. ADS CAMPAIGNS
-- ============================================================================
CREATE TABLE IF NOT EXISTS ads_campaigns (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    campaign_id         VARCHAR(64) NOT NULL,
    campaign_name       VARCHAR(255),
    campaign_type       VARCHAR(32) CHECK (campaign_type IN ('sponsoredProducts', 'sponsoredBrands', 'sponsoredDisplay')),
    campaign_status     VARCHAR(32),
    daily_budget        NUMERIC(10,2),
    start_date          DATE,
    end_date            DATE,
    is_active           BOOLEAN DEFAULT TRUE,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, campaign_id)
);

-- ============================================================================
-- 9. ADS PERFORMANCE (DAILY)
-- ============================================================================
CREATE TABLE IF NOT EXISTS ads_performance (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    campaign_id         VARCHAR(64) NOT NULL,
    ad_group_id         VARCHAR(64),
    sku                 VARCHAR(255),
    asin                VARCHAR(32),
    keyword             VARCHAR(255),
    match_type          VARCHAR(32),
    date                DATE NOT NULL,
    impressions         INTEGER NOT NULL DEFAULT 0,
    clicks              INTEGER NOT NULL DEFAULT 0,
    spend               NUMERIC(12,2) NOT NULL DEFAULT 0,
    sales               NUMERIC(12,2) NOT NULL DEFAULT 0,
    orders              INTEGER NOT NULL DEFAULT 0,
    units_sold          INTEGER NOT NULL DEFAULT 0,
    acos                NUMERIC(8,4) GENERATED ALWAYS AS (
                            CASE WHEN sales > 0 THEN spend / sales ELSE NULL END
                        ) STORED,
    roas                NUMERIC(8,4) GENERATED ALWAYS AS (
                            CASE WHEN spend > 0 THEN sales / spend ELSE NULL END
                        ) STORED,
    ctr                 NUMERIC(8,4) GENERATED ALWAYS AS (
                            CASE WHEN impressions > 0 THEN clicks::NUMERIC / impressions ELSE NULL END
                        ) STORED,
    cvr                 NUMERIC(8,4) GENERATED ALWAYS AS (
                            CASE WHEN clicks > 0 THEN orders::NUMERIC / clicks ELSE NULL END
                        ) STORED,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, campaign_id, ad_group_id, sku, keyword, date)
);

CREATE INDEX idx_ads_campaign ON ads_performance(campaign_id);
CREATE INDEX idx_ads_date ON ads_performance(date);
CREATE INDEX idx_ads_sku ON ads_performance(sku);
CREATE INDEX idx_acos_alert ON ads_performance(acos) WHERE acos > 0.25;

-- ============================================================================
-- 10. PROFIT DAILY SNAPSHOT
-- ============================================================================
CREATE TABLE IF NOT EXISTS profit_daily_snapshot (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    sku                 VARCHAR(255) NOT NULL,
    asin                VARCHAR(32),
    date                DATE NOT NULL,
    units_sold          INTEGER NOT NULL DEFAULT 0,
    units_refunded      INTEGER NOT NULL DEFAULT 0,
    revenue             NUMERIC(12,2) NOT NULL DEFAULT 0,
    refund_amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
    product_cost        NUMERIC(12,2) NOT NULL DEFAULT 0,
    fba_fees            NUMERIC(12,2) NOT NULL DEFAULT 0,
    referral_fees       NUMERIC(12,2) NOT NULL DEFAULT 0,
    shipping_charge     NUMERIC(12,2) NOT NULL DEFAULT 0,
    shipping_cost       NUMERIC(12,2) NOT NULL DEFAULT 0,
    inbound_shipping_cost NUMERIC(12,2) NOT NULL DEFAULT 0,
    advertising_spend   NUMERIC(12,2) NOT NULL DEFAULT 0,
    storage_fees        NUMERIC(12,2) NOT NULL DEFAULT 0,
    returns_cost        NUMERIC(12,2) NOT NULL DEFAULT 0,
    other_costs         NUMERIC(12,2) NOT NULL DEFAULT 0,
    gross_revenue       NUMERIC(12,2) GENERATED ALWAYS AS (revenue + shipping_charge) STORED,
    gross_profit        NUMERIC(12,2) GENERATED ALWAYS AS (
                            revenue + shipping_charge - product_cost
                        ) STORED,
    net_profit          NUMERIC(12,2) GENERATED ALWAYS AS (
                            revenue + shipping_charge - refund_amount - product_cost
                            - fba_fees - referral_fees - shipping_cost - inbound_shipping_cost
                            - advertising_spend - storage_fees - returns_cost - other_costs
                        ) STORED,
    margin_pct          NUMERIC(8,4) GENERATED ALWAYS AS (
                            CASE WHEN (revenue + shipping_charge) > 0
                            THEN (revenue + shipping_charge - refund_amount - product_cost - fba_fees
                                  - referral_fees - shipping_cost - inbound_shipping_cost
                                  - advertising_spend - storage_fees - returns_cost - other_costs)
                                 / (revenue + shipping_charge)
                            ELSE 0 END
                        ) STORED,
    contribution_margin NUMERIC(12,2) GENERATED ALWAYS AS (
                            revenue + shipping_charge - refund_amount - product_cost
                            - fba_fees - referral_fees - shipping_cost - inbound_shipping_cost
                        ) STORED,
    tacos               NUMERIC(8,4) GENERATED ALWAYS AS (
                            CASE WHEN (revenue + shipping_charge) > 0
                            THEN advertising_spend / (revenue + shipping_charge)
                            ELSE NULL END
                        ) STORED,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, sku, date)
);

CREATE INDEX idx_profit_store_date ON profit_daily_snapshot(store_id, date);
CREATE INDEX idx_profit_sku ON profit_daily_snapshot(sku);
CREATE INDEX idx_profit_margin ON profit_daily_snapshot(margin_pct);

-- ============================================================================
-- 11. CUSTOMER FEEDBACK  (Voice of Customer)
-- ============================================================================
CREATE TABLE IF NOT EXISTS customer_feedback (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    asin                VARCHAR(32),
    sku                 VARCHAR(255),
    source              VARCHAR(32) NOT NULL CHECK (
                            source IN ('amazon_review', 'amazon_return', 'amazon_qna',
                                       'walmart_review', 'tiktok_comment', 'facebook_comment')
                        ),
    feedback_id         VARCHAR(128) NOT NULL,
    feedback_type       VARCHAR(32) NOT NULL CHECK (
                            feedback_type IN ('review', 'return_reason', 'question', 'comment')
                        ),
    star_rating         INTEGER CHECK (star_rating BETWEEN 1 AND 5),
    title               TEXT,
    body                TEXT,
    sentiment           VARCHAR(16) CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    is_verified_purchase BOOLEAN DEFAULT FALSE,
    language            VARCHAR(8) DEFAULT 'en',
    customer_id         VARCHAR(128),
    purchase_date       TIMESTAMPTZ,
    feedback_date       TIMESTAMPTZ,
    helpful_votes       INTEGER DEFAULT 0,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, feedback_id)
);

CREATE INDEX idx_feedback_asin ON customer_feedback(asin);
CREATE INDEX idx_feedback_sentiment ON customer_feedback(sentiment);
CREATE INDEX idx_feedback_source ON customer_feedback(source);
CREATE INDEX idx_feedback_date ON customer_feedback(feedback_date);

-- ============================================================================
-- 12. FEEDBACK TOPICS (Classified from VoC)
-- ============================================================================
CREATE TABLE IF NOT EXISTS feedback_topics (
    id                  BIGSERIAL PRIMARY KEY,
    feedback_id         BIGINT NOT NULL REFERENCES customer_feedback(id) ON DELETE CASCADE,
    topic               VARCHAR(128) NOT NULL,
    sub_topic           VARCHAR(128),
    sentiment           VARCHAR(16),
    confidence_score    NUMERIC(5,4) CHECK (confidence_score BETWEEN 0 AND 1),
    keywords            TEXT[],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ft_feedback ON feedback_topics(feedback_id);
CREATE INDEX idx_ft_topic ON feedback_topics(topic);

-- ============================================================================
-- 13. RECOMMENDATION LOG  (Every action logged with user/store/timestamp)
-- ============================================================================
CREATE TABLE IF NOT EXISTS recommendation_log (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    recommendation_id   VARCHAR(128) NOT NULL UNIQUE,
    recommendation_type VARCHAR(64) NOT NULL,
    recommendation_text TEXT NOT NULL,
    confidence_score    NUMERIC(5,4) CHECK (confidence_score BETWEEN 0 AND 1),
    risk_level          VARCHAR(16) CHECK (risk_level IN ('low', 'medium', 'high')),
    human_action        VARCHAR(32) DEFAULT 'pending' CHECK (
                            human_action IN ('pending', 'accepted', 'rejected', 'modified', 'auto_executed')
                        ),
    actioned_by         VARCHAR(255),
    actioned_at         TIMESTAMPTZ,
    human_notes         TEXT,
    before_state        JSONB,
    after_state         JSONB,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rl_store ON recommendation_log(store_id);
CREATE INDEX idx_rl_action ON recommendation_log(human_action);
CREATE INDEX idx_rl_type ON recommendation_log(recommendation_type);

-- ============================================================================
-- 14. FEEDBACK LOOP  (Track outcomes and learn)
-- ============================================================================
CREATE TABLE IF NOT EXISTS feedback_loop (
    id                  BIGSERIAL PRIMARY KEY,
    recommendation_id   VARCHAR(128) NOT NULL REFERENCES recommendation_log(recommendation_id),
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    outcome             VARCHAR(32) NOT NULL CHECK (outcome IN ('accepted', 'rejected', 'modified')),
    impact_metric       VARCHAR(64),
    impact_value        NUMERIC(14,4),
    impact_description  TEXT,
    threshold_adjusted  BOOLEAN DEFAULT FALSE,
    previous_threshold  NUMERIC(5,4),
    new_threshold       NUMERIC(5,4),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_fl_recommendation ON feedback_loop(recommendation_id);

-- ============================================================================
-- 15. AUDIT LOG (Every action logged — no exceptions)
-- ============================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    action              VARCHAR(64) NOT NULL,
    actioned_by         VARCHAR(255) NOT NULL,
    resource_type       VARCHAR(64),
    resource_id         VARCHAR(128),
    before_state        JSONB,
    after_state         JSONB,
    status              VARCHAR(32) NOT NULL DEFAULT 'success',
    error_message       TEXT,
    ip_address          VARCHAR(64),
    user_agent          TEXT,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_store ON audit_log(store_id);
CREATE INDEX idx_audit_action ON audit_log(action);
CREATE INDEX idx_audit_time ON audit_log(created_at);

-- ============================================================================
-- TRIGGER: Auto-update updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_stores_updated_at
    BEFORE UPDATE ON stores
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- VIEW: Daily profit summary per store
-- ============================================================================
CREATE OR REPLACE VIEW v_profit_summary AS
SELECT
    store_id,
    date,
    COUNT(DISTINCT sku) AS active_skus,
    SUM(units_sold) AS total_units,
    SUM(revenue) AS total_revenue,
    SUM(net_profit) AS total_net_profit,
    AVG(margin_pct) AS avg_margin,
    SUM(advertising_spend) AS total_ad_spend,
    AVG(tacos) AS avg_tacos
FROM profit_daily_snapshot
GROUP BY store_id, date
ORDER BY date DESC;

-- ============================================================================
-- VIEW: Pending claims
-- ============================================================================
CREATE OR REPLACE VIEW v_pending_claims AS
SELECT
    sd.store_id,
    sd.shipment_id,
    COUNT(DISTINCT sd.sku) AS skus_affected,
    SUM(sd.quantity_lost) AS total_lost,
    SUM(sd.quantity_damaged) AS total_damaged,
    SUM(sd.estimated_loss) AS total_estimated_loss
FROM shipment_discrepancies sd
WHERE sd.claim_filed = FALSE
GROUP BY sd.store_id, sd.shipment_id
ORDER BY total_estimated_loss DESC;


-- ============================================================================
-- 16. EXPENSES (Manual entry for cash flow — invoices, salaries, etc.)
-- ============================================================================
CREATE TABLE IF NOT EXISTS expenses (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    expense_date        DATE NOT NULL,
    expense_type        VARCHAR(64) NOT NULL CHECK (
                            expense_type IN (
                                'cogs', 'logistics', 'salary', 'software',
                                'marketing', 'office', 'shipping_supplies',
                                'tax', 'insurance', 'professional_fees',
                                'storage_rent', 'travel', 'other'
                            )
                        ),
    category            VARCHAR(128) NOT NULL,
    description         TEXT,
    vendor              VARCHAR(255),
    invoice_number      VARCHAR(128),
    amount              NUMERIC(12,2) NOT NULL,
    tax_amount          NUMERIC(12,2) DEFAULT 0,
    currency            VARCHAR(8) DEFAULT 'CNY',
    payment_method      VARCHAR(64),
    payment_date        DATE,
    payment_status      VARCHAR(32) DEFAULT 'unpaid' CHECK (
                            payment_status IN ('unpaid', 'paid', 'partial')
                        ),
    is_recurring        BOOLEAN DEFAULT FALSE,
    recurring_frequency VARCHAR(32),
    notes               TEXT,
    attachment_path     TEXT,
    created_by          VARCHAR(128),
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_expenses_store_date ON expenses(store_id, expense_date);
CREATE INDEX IF NOT EXISTS idx_expenses_type ON expenses(expense_type);
CREATE INDEX IF NOT EXISTS idx_expenses_status ON expenses(payment_status);


-- ============================================================================
-- 17. PURCHASE ORDERS (Manufacturing batches — uploaded by Master Admin)
-- ============================================================================

-- ============================================================================
-- 17. PURCHASE ORDERS (Manufacturing batches — tracked by shipping status)
-- ============================================================================
CREATE TABLE IF NOT EXISTS purchase_orders (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    po_number           VARCHAR(128) NOT NULL,
    supplier_name       VARCHAR(255) NOT NULL,
    order_date          DATE NOT NULL,
    expected_completion DATE,              -- when manufacturer finishes
    shipped_date        DATE,              -- when goods actually leave factory
    actual_delivery     DATE,              -- when goods arrive
    po_status           VARCHAR(32) DEFAULT 'in_production' CHECK (
                            po_status IN (
                                'in_production',      -- being manufactured
                                'ready_to_ship',      -- sitting at manufacturer warehouse
                                'shipped',            -- on the way
                                'delivered',          -- arrived
                                'cancelled'
                            )
                        ),
    payment_terms       VARCHAR(64),
    payment_status      VARCHAR(32) DEFAULT 'unpaid' CHECK (
                            payment_status IN ('unpaid', 'partial', 'paid')
                        ),
    payment_date        DATE,
    total_amount        NUMERIC(12,2) NOT NULL,
    currency            VARCHAR(8) DEFAULT 'CNY',
    exchange_rate       NUMERIC(10,4) DEFAULT 1,
    amount_usd          NUMERIC(12,2) GENERATED ALWAYS AS (total_amount * exchange_rate) STORED,
    shipping_cost       NUMERIC(12,2) DEFAULT 0,
    shipping_status     VARCHAR(32) DEFAULT 'not_arranged' CHECK (
                            shipping_status IN (
                                'not_arranged',       -- haven't booked freight yet
                                'arranged',           -- freight booked
                                'shipped',            -- goods on vessel/plane
                                'delivered'           -- arrived at warehouse
                            )
                        ),
    shipping_vendor     VARCHAR(255),
    shipping_invoice    VARCHAR(128),
    shipping_due_date   DATE,
    shipping_paid       BOOLEAN DEFAULT FALSE,
    notes               TEXT,
    attachment_path     TEXT,
    line_items          JSONB DEFAULT '[]',
    created_by          VARCHAR(128),
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_po_store_date ON purchase_orders(store_id, order_date);
CREATE INDEX idx_po_status ON purchase_orders(po_status);
CREATE INDEX idx_po_payment ON purchase_orders(payment_status);

CREATE TABLE IF NOT EXISTS po_line_items (
    id                  BIGSERIAL PRIMARY KEY,
    po_id               BIGINT REFERENCES purchase_orders(id) ON DELETE CASCADE,
    store_id            VARCHAR(64) REFERENCES stores(store_id),
    sku                 VARCHAR(255) NOT NULL,
    product_name        VARCHAR(255),
    quantity_ordered    INTEGER NOT NULL,
    quantity_received   INTEGER DEFAULT 0,
    unit_cost           NUMERIC(12,4) NOT NULL,
    line_total          NUMERIC(12,2) GENERATED ALWAYS AS (quantity_ordered * unit_cost) STORED,
    expected_delivery   DATE,
    received_date       DATE,
    status              VARCHAR(32) DEFAULT 'pending' CHECK (status IN ('pending','partial','received','cancelled')),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_po_line_sku ON po_line_items(sku);
CREATE INDEX idx_po_line_po ON po_line_items(po_id);

-- ============================================================================
-- 18. RETURNS (Operational return events from Returns API)
-- ============================================================================
CREATE TABLE IF NOT EXISTS returns (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    return_id           VARCHAR(128) NOT NULL,
    amazon_order_id     VARCHAR(64),
    sku                 VARCHAR(255),
    asin                VARCHAR(32),
    return_request_date TIMESTAMPTZ,
    return_reason       TEXT,
    return_status       VARCHAR(64),
    return_quantity     INTEGER DEFAULT 0,
    return_type         VARCHAR(64),
    resolution          VARCHAR(64),
    rma_id              VARCHAR(128),
    label_cost          NUMERIC(12,2) DEFAULT 0,
    label_currency      VARCHAR(8),
    in_policy           BOOLEAN,
    a_to_z_claim        BOOLEAN,
    is_prime            BOOLEAN,
    return_carrier      VARCHAR(128),
    tracking_id         VARCHAR(128),
    safe_t_action_reason VARCHAR(255),
    safe_t_claim_id     VARCHAR(128),
    safe_t_claim_state  VARCHAR(64),
    safe_t_reimbursement NUMERIC(12,2),
    refunded_amount     NUMERIC(12,2),
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, return_id)
);
CREATE INDEX idx_returns_store_date ON returns(store_id, return_request_date);
CREATE INDEX idx_returns_order ON returns(amazon_order_id);
CREATE INDEX idx_returns_sku ON returns(sku);
CREATE INDEX idx_returns_status ON returns(return_status);

-- ============================================================================
-- 19. REFUNDS (Financial truth from Finances API)
-- ============================================================================
CREATE TABLE IF NOT EXISTS refunds (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    refund_id           VARCHAR(128) NOT NULL,
    amazon_order_id     VARCHAR(64),
    sku                 VARCHAR(255),
    refund_amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
    currency            VARCHAR(8) DEFAULT 'USD',
    refund_date         TIMESTAMPTZ,
    refund_reason_code  VARCHAR(128),
    refund_type         VARCHAR(64),
    posted_date         TIMESTAMPTZ,
    quantity_refunded   INTEGER DEFAULT 0,
    fee_adjustment      NUMERIC(12,2) DEFAULT 0,
    return_shipping     NUMERIC(12,2) DEFAULT 0,
    tax_amount          NUMERIC(12,2) DEFAULT 0,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, marketplace_id, refund_id)
);
CREATE INDEX idx_refunds_store_date ON refunds(store_id, refund_date);
CREATE INDEX idx_refunds_order ON refunds(amazon_order_id);
CREATE INDEX idx_refunds_sku ON refunds(sku);

-- ============================================================================
-- 20. RETURN_ITEMS (Granular breakdown per return)
-- ============================================================================
CREATE TABLE IF NOT EXISTS return_items (
    id                  BIGSERIAL PRIMARY KEY,
    return_id           BIGINT NOT NULL REFERENCES returns(id) ON DELETE CASCADE,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    sku                 VARCHAR(255),
    asin                VARCHAR(32),
    quantity            INTEGER DEFAULT 0,
    item_condition      VARCHAR(64),
    reason_code         VARCHAR(128),
    disposition         VARCHAR(64),
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ret_items_return ON return_items(return_id);
CREATE INDEX idx_ret_items_sku ON return_items(sku);

-- ============================================================================
-- 21. FINANCIAL_TRANSACTIONS (Source-of-truth from Finances API)
-- ============================================================================
CREATE TABLE IF NOT EXISTS financial_transactions (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    transaction_id      VARCHAR(128),
    amazon_order_id     VARCHAR(64),
    sku                 VARCHAR(255),
    transaction_type    VARCHAR(64) NOT NULL CHECK (
                            transaction_type IN (
                                'refund', 'fee', 'adjustment', 'reimbursement',
                                'charge', 'payment', 'promotion', 'tax',
                                'shipping', 'other'
                            )
                        ),
    transaction_subtype VARCHAR(128),
    amount              NUMERIC(12,2) NOT NULL,
    currency            VARCHAR(8) DEFAULT 'USD',
    posted_date         TIMESTAMPTZ,
    settlement_id       VARCHAR(64),
    fee_type            VARCHAR(64),
    fee_amount          NUMERIC(12,2),
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ft_store_date ON financial_transactions(store_id, posted_date);
CREATE INDEX idx_ft_order ON financial_transactions(amazon_order_id);
CREATE INDEX idx_ft_type ON financial_transactions(transaction_type);
CREATE INDEX idx_ft_sku ON financial_transactions(sku);
CREATE INDEX idx_ft_settlement ON financial_transactions(settlement_id);

-- ============================================================================
-- 22. CUSTOMER_LOSS_EVENTS (Unified analysis table for AI)
-- ============================================================================
CREATE TABLE IF NOT EXISTS customer_loss_events (
    id                  BIGSERIAL PRIMARY KEY,
    store_id            VARCHAR(64) NOT NULL REFERENCES stores(store_id),
    marketplace_id      VARCHAR(32) NOT NULL DEFAULT 'ATVPDKIKX0DER',
    return_id           VARCHAR(128),
    refund_id           VARCHAR(128),
    amazon_order_id     VARCHAR(64),
    sku                 VARCHAR(255),
    asin                VARCHAR(32),
    loss_type           VARCHAR(32) NOT NULL CHECK (
                            loss_type IN (
                                'return_damage', 'refund', 'lost_in_fba',
                                'partial_refund', 'customer_return',
                                'disposal', 'reimbursement'
                            )
                        ),
    amount_loss         NUMERIC(12,2) NOT NULL DEFAULT 0,
    currency            VARCHAR(8) DEFAULT 'USD',
    reason              TEXT,
    event_date          TIMESTAMPTZ,
    linked_to_return    BOOLEAN DEFAULT FALSE,
    linked_to_refund    BOOLEAN DEFAULT FALSE,
    is_anomaly          BOOLEAN DEFAULT FALSE,
    anomaly_reason      TEXT,
    raw_data            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_loss_store_date ON customer_loss_events(store_id, event_date);
CREATE INDEX idx_loss_type ON customer_loss_events(loss_type);
CREATE INDEX idx_loss_sku ON customer_loss_events(sku);
CREATE INDEX idx_loss_order ON customer_loss_events(amazon_order_id);
CREATE INDEX idx_loss_anomaly ON customer_loss_events(is_anomaly) WHERE is_anomaly = TRUE;

-- ============================================================================
-- VIEW: Returns with refunds (merged for AI queries)
-- ============================================================================
CREATE OR REPLACE VIEW v_return_refund_merged AS
SELECT
    r.id AS return_id,
    r.store_id,
    r.marketplace_id,
    r.amazon_order_id,
    r.sku,
    r.return_request_date,
    r.return_reason,
    r.return_status,
    r.refunded_amount AS return_refund_amount,
    rf.refund_amount,
    rf.refund_date,
    rf.refund_reason_code,
    rf.fee_adjustment,
    rf.tax_amount,
    r.a_to_z_claim,
    r.in_policy,
    r.return_type,
    r.resolution
FROM returns r
LEFT JOIN refunds rf ON r.amazon_order_id = rf.amazon_order_id AND r.sku = rf.sku
ORDER BY r.return_request_date DESC;

-- ============================================================================
-- VIEW: Profit leakage by SKU
-- ============================================================================
CREATE OR REPLACE VIEW v_profit_leakage AS
SELECT
    cle.store_id,
    cle.sku,
    cle.asin,
    cle.loss_type,
    COUNT(*) AS event_count,
    SUM(cle.amount_loss) AS total_loss,
    MIN(cle.event_date) AS first_event,
    MAX(cle.event_date) AS last_event,
    COUNT(DISTINCT cle.amazon_order_id) AS orders_affected
FROM customer_loss_events cle
GROUP BY cle.store_id, cle.sku, cle.asin, cle.loss_type
ORDER BY total_loss DESC;

-- ============================================================================
-- VIEW: SKU return loss layer
-- ============================================================================
CREATE OR REPLACE VIEW v_sku_return_loss AS
SELECT
    cle.store_id,
    cle.sku,
    COUNT(*) AS return_count,
    COUNT(DISTINCT cle.amazon_order_id) AS order_count,
    SUM(CASE WHEN cle.loss_type = 'return_damage' THEN 1 ELSE 0 END) AS damage_count,
    SUM(CASE WHEN cle.loss_type = 'return_damage' THEN cle.amount_loss ELSE 0 END) AS damage_loss,
    SUM(cle.amount_loss) AS total_loss,
    CASE WHEN COUNT(*) > 0
         THEN SUM(CASE WHEN cle.loss_type = 'return_damage' THEN 1 ELSE 0 END)::NUMERIC / COUNT(*)
         ELSE 0 END AS damage_rate,
    cle.reason AS reason_top
FROM customer_loss_events cle
WHERE cle.loss_type IN ('return_damage', 'customer_return', 'refund', 'partial_refund')
GROUP BY cle.store_id, cle.sku, cle.reason
ORDER BY total_loss DESC;

-- ============================================================================
-- VIEW: SKU ads waste layer
-- ============================================================================
CREATE OR REPLACE VIEW v_sku_ads_waste AS
SELECT
    ap.store_id,
    ap.campaign_id,
    ap.sku,
    SUM(ap.spend) AS total_spend,
    SUM(ap.sales) AS attributed_sales,
    CASE WHEN SUM(ap.spend) > 0
         THEN SUM(ap.sales) / SUM(ap.spend)
         ELSE 0 END AS roas,
    SUM(CASE WHEN ap.sales = 0 THEN ap.spend ELSE 0 END) AS wasted_spend,
    COUNT(DISTINCT ap.keyword) FILTER (WHERE ap.sales = 0) AS wasted_keywords
FROM ads_performance ap
WHERE ap.sku IS NOT NULL
GROUP BY ap.store_id, ap.campaign_id, ap.sku
ORDER BY wasted_spend DESC;

-- ============================================================================
-- VIEW: FBA leakage layer
-- ============================================================================
CREATE OR REPLACE VIEW v_fba_leakage AS
SELECT
    sd.store_id,
    sd.sku,
    COUNT(*) AS incident_count,
    SUM(sd.quantity_lost) AS total_lost,
    SUM(sd.quantity_damaged) AS total_damaged,
    SUM(sd.estimated_loss) AS total_loss,
    SUM(CASE WHEN sd.claim_filed THEN 0 ELSE sd.estimated_loss END) AS missing_reimbursement
FROM shipment_discrepancies sd
GROUP BY sd.store_id, sd.sku
HAVING SUM(sd.estimated_loss) > 0
ORDER BY total_loss DESC;

COMMIT;
