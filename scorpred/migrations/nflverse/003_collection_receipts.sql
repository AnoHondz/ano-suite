CREATE TABLE collection_receipts (
    id INTEGER PRIMARY KEY, dataset TEXT NOT NULL, season INTEGER NOT NULL,
    attempted_at TEXT NOT NULL, collected_at TEXT, ingested_at TEXT,
    source_identifier TEXT NOT NULL, source_publication_at TEXT,
    status TEXT NOT NULL, content_sha256 TEXT, row_count INTEGER,
    writer_version TEXT NOT NULL, error TEXT, metadata TEXT NOT NULL
);
CREATE INDEX collection_latest ON collection_receipts(dataset,season,id);
CREATE TABLE collection_games (
    receipt_id INTEGER NOT NULL REFERENCES collection_receipts(id),
    game_id TEXT NOT NULL, content_sha256 TEXT NOT NULL,
    PRIMARY KEY(receipt_id,game_id)
);
CREATE TRIGGER versions_no_update BEFORE UPDATE ON record_versions
BEGIN SELECT RAISE(ABORT, 'Source observation history is immutable'); END;
CREATE TRIGGER versions_no_delete BEFORE DELETE ON record_versions
BEGIN SELECT RAISE(ABORT, 'Source observation history is immutable'); END;
CREATE TRIGGER receipts_no_update BEFORE UPDATE ON collection_receipts
BEGIN SELECT RAISE(ABORT, 'Collection receipts are immutable'); END;
CREATE TRIGGER receipts_no_delete BEFORE DELETE ON collection_receipts
BEGIN SELECT RAISE(ABORT, 'Collection receipts are immutable'); END;
CREATE TRIGGER collection_games_no_update BEFORE UPDATE ON collection_games
BEGIN SELECT RAISE(ABORT, 'Collection associations are immutable'); END;
CREATE TRIGGER collection_games_no_delete BEFORE DELETE ON collection_games
BEGIN SELECT RAISE(ABORT, 'Collection associations are immutable'); END;
