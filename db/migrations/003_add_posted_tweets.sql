-- Add posted_tweets table for tracking tweets posted via the social module.
CREATE TABLE IF NOT EXISTS posted_tweets (
    tweet_id         TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    report_date      TEXT NOT NULL,
    tweet_text       TEXT NOT NULL,
    tweet_type       TEXT NOT NULL,
    thread_tweets    TEXT,
    draft_file       TEXT,
    image_paths      TEXT,
    UNIQUE(report_date, tweet_type)
);
