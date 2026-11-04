CREATE TABLE collection_control (
    key TEXT PRIMARY KEY, enabled INTEGER NOT NULL, changed_at TEXT NOT NULL
);
CREATE TRIGGER records_versioned_insert BEFORE INSERT ON records
WHEN EXISTS (SELECT 1 FROM collection_control WHERE key='require_versioned_writes' AND enabled=1)
AND (NEW.observed_at IS NULL OR NOT EXISTS (
    SELECT 1 FROM record_versions v WHERE v.dataset=NEW.dataset AND v.season=NEW.season
    AND v.record_key=NEW.record_key AND v.observed_at=NEW.observed_at AND v.payload=NEW.payload))
BEGIN SELECT RAISE(ABORT, 'Unversioned NFL collection writer blocked'); END;
CREATE TRIGGER records_versioned_update BEFORE UPDATE ON records
WHEN EXISTS (SELECT 1 FROM collection_control WHERE key='require_versioned_writes' AND enabled=1)
AND (NEW.observed_at IS NULL OR NOT EXISTS (
    SELECT 1 FROM record_versions v WHERE v.dataset=NEW.dataset AND v.season=NEW.season
    AND v.record_key=NEW.record_key AND v.observed_at=NEW.observed_at AND v.payload=NEW.payload))
BEGIN SELECT RAISE(ABORT, 'Unversioned NFL collection writer blocked'); END;
CREATE TRIGGER records_preserved_delete BEFORE DELETE ON records
WHEN EXISTS (SELECT 1 FROM collection_control WHERE key='require_versioned_writes' AND enabled=1)
BEGIN SELECT RAISE(ABORT, 'NFL collection cannot delete preserved source records'); END;
