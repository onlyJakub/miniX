BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS replies (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content VARCHAR(280) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT replies_content_not_blank CHECK (content ~ '[^[:space:]]')
);

CREATE INDEX IF NOT EXISTS replies_post_created_idx ON replies (post_id, created_at, id);
CREATE INDEX IF NOT EXISTS replies_user_id_idx ON replies (user_id);

COMMIT;