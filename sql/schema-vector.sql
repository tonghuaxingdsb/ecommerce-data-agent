-- Optional pgvector setup. Apply after schema.sql on a server with pgvector installed.
BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE data_dictionary ADD COLUMN IF NOT EXISTS embedding vector(1536);
ALTER TABLE metric_dictionary ADD COLUMN IF NOT EXISTS embedding vector(1536);

CREATE INDEX IF NOT EXISTS idx_data_dictionary_embedding
  ON data_dictionary USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
CREATE INDEX IF NOT EXISTS idx_metric_dictionary_embedding
  ON metric_dictionary USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

COMMIT;
