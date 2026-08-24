"""Make login email globally unique."""

from alembic import op


revision = "20260824_0003"
down_revision = "20260822_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_user_workspace_email", type_="unique")
        batch.create_unique_constraint("uq_user_email", ["email"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_user_email", type_="unique")
        batch.create_unique_constraint("uq_user_workspace_email", ["workspace_id", "email"])
