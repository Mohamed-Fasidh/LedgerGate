"""Create tenant-scoped durable agent tasks and fenced queue claim function."""
from alembic import op
import sqlalchemy as sa

revision = "0029_durable_agent_tasks"
down_revision = "0028_currency_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("worker_id", sa.String(length=80), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'timed_out')", name="ck_agent_tasks_status"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 5", name="ck_agent_tasks_attempts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_agent_tasks_tenant_requester",
        ),
    )
    op.create_index(
        "ix_agent_tasks_claim", "agent_tasks",
        ["status", "available_at", "lease_expires_at", "id"],
    )
    op.create_index(
        "ix_agent_tasks_tenant_created", "agent_tasks",
        ["tenant_id", "created_at", "id"],
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("""
        ALTER TABLE public.agent_tasks ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.agent_tasks FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON public.agent_tasks
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));
    """)

    # The queue claim function is the only cross-tenant queue operation. Its
    # NOLOGIN BYPASSRLS owner has privileges on agent_tasks only; it cannot
    # read invoices, users, policies, or any other tenant-owned table.
    op.execute("""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ledgergate_agent_queue_owner') THEN
            CREATE ROLE ledgergate_agent_queue_owner
              NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
          ELSE
            ALTER ROLE ledgergate_agent_queue_owner
              NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
          END IF;
        END
        $$;
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO ledgergate_agent_queue_owner")
    op.execute("GRANT SELECT, UPDATE ON public.agent_tasks TO ledgergate_agent_queue_owner")
    op.execute("GRANT CREATE ON SCHEMA public TO ledgergate_agent_queue_owner")
    op.execute("GRANT ledgergate_agent_queue_owner TO CURRENT_USER")
    op.execute("SET LOCAL ROLE ledgergate_agent_queue_owner")
    op.execute("""
        CREATE OR REPLACE FUNCTION public.claim_agent_task(
            p_worker_id text,
            p_lease_token text,
            p_lease_seconds integer
        )
        RETURNS TABLE(task_id integer, task_tenant_id varchar)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
          v_now timestamptz := clock_timestamp();
          v_lease_seconds integer := GREATEST(30, LEAST(COALESCE(p_lease_seconds, 180), 600));
        BEGIN
          IF p_worker_id IS NULL OR length(p_worker_id) < 1 OR length(p_worker_id) > 80
             OR p_lease_token IS NULL OR length(p_lease_token) < 32 OR length(p_lease_token) > 64 THEN
            RAISE EXCEPTION 'invalid agent worker lease identity' USING ERRCODE = '22023';
          END IF;

          UPDATE public.agent_tasks
          SET status = 'failed',
              error_code = 'WORKER_LEASE_EXHAUSTED',
              finished_at = v_now,
              worker_id = NULL,
              lease_token = NULL,
              locked_at = NULL,
              lease_expires_at = NULL,
              updated_at = v_now
          WHERE status = 'running'
            AND lease_expires_at <= v_now
            AND attempts >= max_attempts;

          RETURN QUERY
          UPDATE public.agent_tasks AS claimed
          SET status = 'running',
              attempts = claimed.attempts + 1,
              worker_id = p_worker_id,
              lease_token = p_lease_token,
              locked_at = v_now,
              lease_expires_at = v_now + make_interval(secs => v_lease_seconds),
              started_at = COALESCE(claimed.started_at, v_now),
              updated_at = v_now
          WHERE claimed.id = (
            SELECT candidate.id
            FROM public.agent_tasks AS candidate
            WHERE candidate.attempts < candidate.max_attempts
              AND (
                (candidate.status = 'queued' AND candidate.available_at <= v_now)
                OR
                (candidate.status = 'running' AND candidate.lease_expires_at <= v_now)
              )
            ORDER BY candidate.available_at, candidate.created_at, candidate.id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
          )
          RETURNING claimed.id, claimed.tenant_id;
        END;
        $function$
    """)
    op.execute("RESET ROLE")
    op.execute("REVOKE CREATE ON SCHEMA public FROM ledgergate_agent_queue_owner")
    op.execute("REVOKE ALL ON FUNCTION public.claim_agent_task(text, text, integer) FROM PUBLIC")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS public.claim_agent_task(text, text, integer)")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.agent_tasks")
        op.execute("ALTER TABLE public.agent_tasks NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE public.agent_tasks DISABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ledgergate_agent_queue_owner FROM CURRENT_USER")
        op.execute("REVOKE ALL ON public.agent_tasks FROM ledgergate_agent_queue_owner")
        op.execute("REVOKE CREATE ON SCHEMA public FROM ledgergate_agent_queue_owner")
        op.execute("DROP ROLE IF EXISTS ledgergate_agent_queue_owner")
    op.drop_index("ix_agent_tasks_tenant_created", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_claim", table_name="agent_tasks")
    op.drop_table("agent_tasks")
