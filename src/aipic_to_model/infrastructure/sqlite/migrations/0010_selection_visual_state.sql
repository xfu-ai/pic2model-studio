BEGIN;
ALTER TABLE selections ADD COLUMN visual_state TEXT NOT NULL DEFAULT 'user_draft'
  CHECK(visual_state IN('agent_suggested','user_draft','user_edited','user_confirmed'));
UPDATE selections
SET visual_state=CASE
  WHEN status='confirmed' THEN 'user_confirmed'
  WHEN source='agent' THEN 'agent_suggested'
  ELSE 'user_draft'
END;
COMMIT;
