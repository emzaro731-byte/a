-- V9 PostgreSQL foundation: Auth sessions, RLS-backed data, storage metadata,
-- realtime notifications, function registry and background jobs.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS sb_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE NOT NULL,
  name text NOT NULL DEFAULT '',
  password_hash text NOT NULL,
  email_verified boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sb_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES sb_users(id) ON DELETE CASCADE,
  refresh_token_hash text UNIQUE NOT NULL,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sb_sessions_user_idx ON sb_sessions(user_id);

CREATE TABLE IF NOT EXISTS sb_data (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES sb_users(id) ON DELETE CASCADE,
  table_name text NOT NULL,
  row_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sb_data_owner_table_idx ON sb_data(owner_id, table_name);
CREATE INDEX IF NOT EXISTS sb_data_row_gin_idx ON sb_data USING gin(row_data);

ALTER TABLE sb_data ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sb_data_owner_policy ON sb_data;
CREATE POLICY sb_data_owner_policy ON sb_data
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

CREATE TABLE IF NOT EXISTS sb_storage_objects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES sb_users(id) ON DELETE CASCADE,
  bucket text NOT NULL,
  object_name text NOT NULL,
  mime text NOT NULL DEFAULT 'application/octet-stream',
  size_bytes bigint NOT NULL DEFAULT 0,
  storage_key text NOT NULL UNIQUE,
  is_public boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, bucket, object_name)
);
ALTER TABLE sb_storage_objects ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sb_storage_owner_policy ON sb_storage_objects;
CREATE POLICY sb_storage_owner_policy ON sb_storage_objects
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid OR is_public)
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

CREATE TABLE IF NOT EXISTS sb_functions (
  name text PRIMARY KEY,
  code text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sb_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES sb_users(id) ON DELETE CASCADE,
  job_type text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed')),
  attempts integer NOT NULL DEFAULT 0,
  result jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sb_jobs_queue_idx ON sb_jobs(status, created_at);

CREATE OR REPLACE FUNCTION sb_touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$;
DROP TRIGGER IF EXISTS sb_data_touch ON sb_data;
CREATE TRIGGER sb_data_touch BEFORE UPDATE ON sb_data FOR EACH ROW EXECUTE FUNCTION sb_touch_updated_at();

CREATE OR REPLACE FUNCTION sb_notify_data_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE payload json;
BEGIN
  payload = json_build_object('op', TG_OP, 'id', COALESCE(NEW.id, OLD.id), 'table_name', COALESCE(NEW.table_name, OLD.table_name));
  PERFORM pg_notify('sb_realtime', payload::text);
  RETURN COALESCE(NEW, OLD);
END $$;
DROP TRIGGER IF EXISTS sb_data_realtime ON sb_data;
CREATE TRIGGER sb_data_realtime AFTER INSERT OR UPDATE OR DELETE ON sb_data FOR EACH ROW EXECUTE FUNCTION sb_notify_data_change();

CREATE TABLE IF NOT EXISTS sb_audit_log (
  id bigserial PRIMARY KEY,
  user_id uuid,
  action text NOT NULL,
  resource text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sb_audit_user_idx ON sb_audit_log(user_id, created_at DESC);
