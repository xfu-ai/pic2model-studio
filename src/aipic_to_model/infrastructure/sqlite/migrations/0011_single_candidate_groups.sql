BEGIN;
DROP TRIGGER candidate_group_min_two_before_ready;
DROP TRIGGER candidate_group_not_ready_on_insert;

CREATE TABLE candidate_groups_v2(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  source_asset_id TEXT REFERENCES assets(id) ON DELETE RESTRICT,
  prompt_asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  requested_count INTEGER NOT NULL CHECK(requested_count BETWEEN 1 AND 8),
  request_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('created','ready','partial_ready','selected','cancelled')),
  warnings_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE TABLE candidate_items_v2(
  group_id TEXT NOT NULL REFERENCES candidate_groups_v2(id) ON DELETE RESTRICT,
  asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
  ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 8),
  selected INTEGER NOT NULL DEFAULT 0 CHECK(selected IN(0,1)),
  PRIMARY KEY(group_id,asset_id),
  UNIQUE(group_id,ordinal)
);
CREATE TABLE candidate_assessments_v2(
  group_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  evaluation_status TEXT NOT NULL CHECK(evaluation_status IN('evaluated','not_evaluated','failed')),
  short_evaluation TEXT,
  anomalies_json TEXT NOT NULL DEFAULT '[]',
  provider_request_id TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(group_id,asset_id),
  FOREIGN KEY(group_id,asset_id) REFERENCES candidate_items_v2(group_id,asset_id) ON DELETE RESTRICT
);

INSERT INTO candidate_groups_v2 SELECT * FROM candidate_groups;
INSERT INTO candidate_items_v2 SELECT * FROM candidate_items;
INSERT INTO candidate_assessments_v2 SELECT * FROM candidate_assessments;

DROP TABLE candidate_assessments;
DROP TABLE candidate_items;
DROP TABLE candidate_groups;
ALTER TABLE candidate_groups_v2 RENAME TO candidate_groups;
ALTER TABLE candidate_items_v2 RENAME TO candidate_items;
ALTER TABLE candidate_assessments_v2 RENAME TO candidate_assessments;

CREATE TRIGGER candidate_group_not_ready_on_insert
BEFORE INSERT ON candidate_groups WHEN NEW.status<>'created'
BEGIN SELECT RAISE(ABORT,'candidate_group_must_start_created'); END;
CREATE TRIGGER candidate_group_count_before_ready
BEFORE UPDATE OF status ON candidate_groups
WHEN NEW.status IN('ready','partial_ready','selected')
 AND (SELECT COUNT(*) FROM candidate_items WHERE group_id=NEW.id) NOT BETWEEN 1 AND 8
BEGIN SELECT RAISE(ABORT,'candidate_count_must_be_1_to_8'); END;
COMMIT;
