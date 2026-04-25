CREATE TABLE IF NOT EXISTS error_reports (
  id UUID PRIMARY KEY,
  app_env TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('web', 'api', 'worker')),
  severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'fatal')),
  message TEXT NOT NULL,
  stack TEXT,
  url TEXT,
  user_agent TEXT,
  context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  reported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_error_reports_reported_at
  ON error_reports (reported_at DESC);

CREATE INDEX IF NOT EXISTS idx_error_reports_source_severity
  ON error_reports (source, severity, reported_at DESC);
