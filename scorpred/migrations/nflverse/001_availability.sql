CREATE TABLE IF NOT EXISTS record_versions (
    dataset TEXT NOT NULL, season INTEGER NOT NULL, record_key TEXT NOT NULL,
    observed_at TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(dataset, season, record_key, observed_at)
);
CREATE INDEX IF NOT EXISTS versions_cutoff ON record_versions(dataset, season, observed_at);
CREATE TABLE IF NOT EXISTS refresh_attempts (
    id INTEGER PRIMARY KEY, dataset TEXT NOT NULL, season INTEGER NOT NULL,
    attempted_at TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
    source TEXT NOT NULL DEFAULT 'nflverse/nflreadpy'
);
CREATE INDEX IF NOT EXISTS refresh_latest ON refresh_attempts(dataset, season, id);
