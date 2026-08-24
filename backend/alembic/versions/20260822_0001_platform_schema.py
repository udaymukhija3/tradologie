"""Create the multi-tenant voice platform schema."""

from alembic import op
import sqlalchemy as sa

revision = "20260822_0001"
down_revision = None
branch_labels = None
depends_on = None

user_role = sa.Enum("ADMIN", "AGENT", "VIEWER", name="userrole")
call_status = sa.Enum("QUEUED", "RINGING", "ACTIVE", "TRANSFERRED", "COMPLETED", "FAILED", name="callstatus")
confirmation_status = sa.Enum("PENDING", "CONFIRMED", "CONSUMED", "CANCELLED", "EXPIRED", name="confirmationstatus")


def upgrade() -> None:
    op.create_table("workspaces", sa.Column("id", sa.String(32), primary_key=True), sa.Column("name", sa.String(160), nullable=False, unique=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("users", sa.Column("id", sa.String(32), primary_key=True), sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("email", sa.String(320), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("password_hash", sa.String(256), nullable=False), sa.Column("role", user_role, nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("workspace_id", "email", name="uq_user_workspace_email"))
    op.create_index("ix_users_workspace_id", "users", ["workspace_id"])
    op.create_table("distributors", sa.Column("id", sa.String(32), primary_key=True), sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("external_id", sa.String(64), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("location", sa.String(160), nullable=False), sa.Column("categories", sa.JSON(), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.UniqueConstraint("workspace_id", "external_id", name="uq_distributor_external"))
    op.create_index("ix_distributors_workspace_id", "distributors", ["workspace_id"])
    op.create_table("voice_agents", sa.Column("id", sa.String(32), primary_key=True), sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("provider", sa.String(40), nullable=False), sa.Column("voice", sa.String(80), nullable=False), sa.Column("system_prompt", sa.Text(), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_voice_agents_workspace_id", "voice_agents", ["workspace_id"])
    op.create_table("confirmations", sa.Column("id", sa.String(32), primary_key=True), sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("session_id", sa.String(64), nullable=False), sa.Column("action", sa.String(80), nullable=False), sa.Column("payload_hash", sa.String(64), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("status", confirmation_status, nullable=False), sa.Column("idempotency_key", sa.String(160), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("confirmed_at", sa.DateTime(timezone=True)), sa.Column("consumed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_confirmation_idempotency"))
    op.create_index("ix_confirmations_workspace_id", "confirmations", ["workspace_id"])
    op.create_index("ix_confirmations_user_id", "confirmations", ["user_id"])
    op.create_index("ix_confirmation_session_status", "confirmations", ["session_id", "status"])
    op.create_table("enquiries", sa.Column("id", sa.String(32), primary_key=True), sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("buyer_id", sa.String(32), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("display_id", sa.String(32), nullable=False), sa.Column("product", sa.String(160), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False), sa.Column("unit", sa.String(32), nullable=False), sa.Column("destination", sa.String(160), nullable=False), sa.Column("status", sa.String(64), nullable=False), sa.Column("distributor_id", sa.String(32), sa.ForeignKey("distributors.id", ondelete="SET NULL")), sa.Column("confirmation_id", sa.String(32), sa.ForeignKey("confirmations.id", ondelete="RESTRICT"), nullable=False, unique=True), sa.Column("idempotency_key", sa.String(160), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("quantity > 0", name="ck_enquiry_positive_quantity"), sa.UniqueConstraint("workspace_id", "display_id", name="uq_enquiry_display_id"), sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_enquiry_idempotency"))
    op.create_index("ix_enquiries_workspace_id", "enquiries", ["workspace_id"])
    op.create_index("ix_enquiries_buyer_id", "enquiries", ["buyer_id"])
    op.create_index("ix_enquiry_workspace_created", "enquiries", ["workspace_id", "created_at"])
    op.create_table("support_requests", sa.Column("id", sa.String(32), primary_key=True), sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("display_id", sa.String(32), nullable=False), sa.Column("reason", sa.String(1000), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_support_requests_workspace_id", "support_requests", ["workspace_id"])
    op.create_index("ix_support_requests_user_id", "support_requests", ["user_id"])
    op.create_table("calls", sa.Column("id", sa.String(32), primary_key=True), sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("agent_id", sa.String(32), sa.ForeignKey("voice_agents.id", ondelete="RESTRICT"), nullable=False), sa.Column("created_by_id", sa.String(32), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("provider", sa.String(40), nullable=False), sa.Column("provider_call_id", sa.String(160), nullable=False), sa.Column("direction", sa.String(16), nullable=False), sa.Column("from_number", sa.String(32), nullable=False), sa.Column("to_number", sa.String(32), nullable=False), sa.Column("status", call_status, nullable=False), sa.Column("transcript", sa.Text(), nullable=False), sa.Column("summary", sa.Text()), sa.Column("outcome", sa.String(80)), sa.Column("idempotency_key", sa.String(160), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("ended_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("provider", "provider_call_id", name="uq_provider_call"), sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_call_idempotency"))
    op.create_index("ix_calls_workspace_id", "calls", ["workspace_id"])
    op.create_index("ix_calls_agent_id", "calls", ["agent_id"])
    op.create_index("ix_calls_created_by_id", "calls", ["created_by_id"])
    op.create_index("ix_call_workspace_created", "calls", ["workspace_id", "created_at"])
    op.create_table("audit_events", sa.Column("id", sa.String(32), primary_key=True), sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("actor_id", sa.String(32), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("event_type", sa.String(100), nullable=False), sa.Column("resource_type", sa.String(80), nullable=False), sa.Column("resource_id", sa.String(64), nullable=False), sa.Column("details", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_audit_events_workspace_id", "audit_events", ["workspace_id"])
    op.create_index("ix_audit_workspace_created", "audit_events", ["workspace_id", "created_at"])


def downgrade() -> None:
    for table in ("audit_events", "calls", "support_requests", "enquiries", "confirmations", "voice_agents", "distributors", "users", "workspaces"):
        op.drop_table(table)
    confirmation_status.drop(op.get_bind(), checkfirst=True)
    call_status.drop(op.get_bind(), checkfirst=True)
    user_role.drop(op.get_bind(), checkfirst=True)
