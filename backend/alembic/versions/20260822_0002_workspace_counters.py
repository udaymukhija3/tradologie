"""Add transactionally allocated workspace display-ID counters."""

from alembic import op
import sqlalchemy as sa


revision = "20260822_0002"
down_revision = "20260822_0001"
branch_labels = None
depends_on = None


def _next_number(prefix: str, values: list[str]) -> int:
    numbers: list[int] = []
    for value in values:
        if value.startswith(prefix):
            suffix = value[len(prefix):]
            if suffix.isdigit():
                numbers.append(int(suffix))
    return max(numbers, default=1000) + 1


def upgrade() -> None:
    op.create_index(
        "uq_support_display_id",
        "support_requests",
        ["workspace_id", "display_id"],
        unique=True,
    )
    op.create_table(
        "workspace_counters",
        sa.Column(
            "workspace_id",
            sa.String(32),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enquiry_next", sa.Integer(), nullable=False),
        sa.Column("support_next", sa.Integer(), nullable=False),
    )

    connection = op.get_bind()
    counters = sa.table(
        "workspace_counters",
        sa.column("workspace_id", sa.String(32)),
        sa.column("enquiry_next", sa.Integer()),
        sa.column("support_next", sa.Integer()),
    )
    workspace_ids = connection.execute(sa.text("SELECT id FROM workspaces")).scalars().all()
    for workspace_id in workspace_ids:
        enquiry_ids = connection.execute(
            sa.text("SELECT display_id FROM enquiries WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        ).scalars().all()
        support_ids = connection.execute(
            sa.text("SELECT display_id FROM support_requests WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        ).scalars().all()
        connection.execute(
            counters.insert().values(
                workspace_id=workspace_id,
                enquiry_next=_next_number("ENQ-", list(enquiry_ids)),
                support_next=_next_number("SUP-", list(support_ids)),
            )
        )


def downgrade() -> None:
    op.drop_table("workspace_counters")
    op.drop_index("uq_support_display_id", table_name="support_requests")
