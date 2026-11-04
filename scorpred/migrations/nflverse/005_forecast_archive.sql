CREATE TABLE nfl_frozen_models (
    model_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE nfl_forecast_studies (
    study_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE nfl_forecast_inputs (
    fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE nfl_forecast_batches (
    study_id TEXT NOT NULL, game_id TEXT NOT NULL, archived_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL REFERENCES nfl_forecast_inputs(fingerprint),
    PRIMARY KEY(study_id,game_id)
);
CREATE TABLE nfl_forecasts (
    study_id TEXT NOT NULL, game_id TEXT NOT NULL, model_id TEXT NOT NULL,
    sha256 TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(study_id,game_id,model_id),
    FOREIGN KEY(study_id,game_id) REFERENCES nfl_forecast_batches(study_id,game_id),
    FOREIGN KEY(model_id) REFERENCES nfl_frozen_models(model_id)
);
CREATE TABLE nfl_forecast_events (
    id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
    study_id TEXT NOT NULL, game_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TRIGGER frozen_models_no_update BEFORE UPDATE ON nfl_frozen_models BEGIN SELECT RAISE(ABORT, 'Frozen NFL model is immutable'); END;
CREATE TRIGGER frozen_models_no_delete BEFORE DELETE ON nfl_frozen_models BEGIN SELECT RAISE(ABORT, 'Frozen NFL model is immutable'); END;
CREATE TRIGGER forecast_studies_no_update BEFORE UPDATE ON nfl_forecast_studies BEGIN SELECT RAISE(ABORT, 'NFL study is immutable'); END;
CREATE TRIGGER forecast_studies_no_delete BEFORE DELETE ON nfl_forecast_studies BEGIN SELECT RAISE(ABORT, 'NFL study is immutable'); END;
CREATE TRIGGER forecast_inputs_no_update BEFORE UPDATE ON nfl_forecast_inputs BEGIN SELECT RAISE(ABORT, 'NFL forecast input is immutable'); END;
CREATE TRIGGER forecast_inputs_no_delete BEFORE DELETE ON nfl_forecast_inputs BEGIN SELECT RAISE(ABORT, 'NFL forecast input is immutable'); END;
CREATE TRIGGER forecast_batches_no_update BEFORE UPDATE ON nfl_forecast_batches BEGIN SELECT RAISE(ABORT, 'NFL forecast batch is immutable'); END;
CREATE TRIGGER forecast_batches_no_delete BEFORE DELETE ON nfl_forecast_batches BEGIN SELECT RAISE(ABORT, 'NFL forecast batch is immutable'); END;
CREATE TRIGGER forecasts_no_update BEFORE UPDATE ON nfl_forecasts BEGIN SELECT RAISE(ABORT, 'NFL forecast is immutable'); END;
CREATE TRIGGER forecasts_no_delete BEFORE DELETE ON nfl_forecasts BEGIN SELECT RAISE(ABORT, 'NFL forecast is immutable'); END;
CREATE TRIGGER forecast_events_no_update BEFORE UPDATE ON nfl_forecast_events BEGIN SELECT RAISE(ABORT, 'NFL forecast audit is immutable'); END;
CREATE TRIGGER forecast_events_no_delete BEFORE DELETE ON nfl_forecast_events BEGIN SELECT RAISE(ABORT, 'NFL forecast audit is immutable'); END;
CREATE TRIGGER frozen_models_no_replace BEFORE INSERT ON nfl_frozen_models WHEN EXISTS(SELECT 1 FROM nfl_frozen_models WHERE model_id=NEW.model_id) BEGIN SELECT RAISE(ABORT, 'Frozen NFL model already registered'); END;
CREATE TRIGGER forecast_studies_no_replace BEFORE INSERT ON nfl_forecast_studies WHEN EXISTS(SELECT 1 FROM nfl_forecast_studies WHERE study_id=NEW.study_id) BEGIN SELECT RAISE(ABORT, 'NFL study already registered'); END;
CREATE TRIGGER forecast_inputs_no_replace BEFORE INSERT ON nfl_forecast_inputs WHEN EXISTS(SELECT 1 FROM nfl_forecast_inputs WHERE fingerprint=NEW.fingerprint) BEGIN SELECT RAISE(ABORT, 'NFL inputs already registered'); END;
CREATE TRIGGER forecast_batches_no_replace BEFORE INSERT ON nfl_forecast_batches WHEN EXISTS(SELECT 1 FROM nfl_forecast_batches WHERE study_id=NEW.study_id AND game_id=NEW.game_id) BEGIN SELECT RAISE(ABORT, 'NFL batch already archived'); END;
CREATE TRIGGER forecasts_no_replace BEFORE INSERT ON nfl_forecasts WHEN EXISTS(SELECT 1 FROM nfl_forecasts WHERE study_id=NEW.study_id AND game_id=NEW.game_id AND model_id=NEW.model_id) BEGIN SELECT RAISE(ABORT, 'NFL forecast already archived'); END;
CREATE TRIGGER forecast_events_no_replace BEFORE INSERT ON nfl_forecast_events WHEN EXISTS(SELECT 1 FROM nfl_forecast_events WHERE event_key=NEW.event_key OR id=NEW.id) BEGIN SELECT RAISE(ABORT, 'NFL audit event already archived'); END;
