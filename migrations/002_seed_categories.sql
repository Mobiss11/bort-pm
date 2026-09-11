BEGIN;
INSERT OR IGNORE INTO expense_categories (code, title_ru, sort_order) VALUES
    ('contractors',   'Подрядчики', 10),
    ('subscriptions', 'Подписки',   20),
    ('ads',           'Реклама',    30),
    ('other',         'Прочее',     99);
INSERT INTO schema_migrations (version, applied_at)
VALUES ('002_seed_categories', strftime('%Y-%m-%dT%H:%M:%SZ','now'));
COMMIT;
