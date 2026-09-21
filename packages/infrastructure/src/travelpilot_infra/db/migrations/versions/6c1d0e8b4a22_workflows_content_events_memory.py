"""content import, agent traces, events and traveler memory

Revision ID: 6c1d0e8b4a22
Revises: 5a9c1e7d2b44
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "6c1d0e8b4a22"
down_revision = "5a9c1e7d2b44"
branch_labels = None
depends_on = None


def uid(name, *, primary=False, nullable=False):
    return sa.Column(name, postgresql.UUID(as_uuid=True), primary_key=primary, nullable=nullable)


def reference(name, table, *, nullable=False):
    return sa.Column(name, postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{table}.id", ondelete="SET NULL" if nullable else "CASCADE"), nullable=nullable)


def created():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def upgrade():
    op.create_table("content_sources", uid("id", primary=True), reference("trip_id", "trips"),
                    sa.Column("content_type", sa.String(30), nullable=False), sa.Column("text", sa.Text(), nullable=False),
                    sa.Column("status", sa.String(32), nullable=False), sa.Column("error", sa.Text()), created())
    op.create_index("ix_content_sources_trip_id", "content_sources", ["trip_id"])
    op.create_table("content_items", uid("id", primary=True), reference("source_id", "content_sources"),
                    sa.Column("text", sa.Text(), nullable=False), sa.Column("kind", sa.String(32), nullable=False))
    op.create_index("ix_content_items_source_id", "content_items", ["source_id"])
    op.create_table("extracted_entities", uid("id", primary=True), reference("source_id", "content_sources"),
                    sa.Column("name", sa.String(200), nullable=False), sa.Column("category", sa.String(50), nullable=False),
                    sa.Column("suggestion", sa.Text()), sa.Column("confidence", sa.Float(), nullable=False))
    op.create_index("ix_extracted_entities_source_id", "extracted_entities", ["source_id"])
    op.create_table("entity_candidates", uid("id", primary=True), reference("entity_id", "extracted_entities"),
                    sa.Column("provider", sa.String(50), nullable=False), sa.Column("provider_id", sa.String(200), nullable=False),
                    sa.Column("name", sa.String(200), nullable=False), sa.Column("address", sa.String(300)),
                    sa.Column("latitude", sa.Float(), nullable=False), sa.Column("longitude", sa.Float(), nullable=False),
                    sa.Column("category", sa.String(50), nullable=False), sa.Column("duration_min", sa.Integer(), nullable=False),
                    sa.Column("score", sa.Float(), nullable=False), sa.Column("confirmed", sa.Boolean(), nullable=False))
    op.create_index("ix_entity_candidates_entity_id", "entity_candidates", ["entity_id"])
    op.create_table("workflow_runs", uid("id", primary=True), reference("trip_id", "trips"),
                    sa.Column("kind", sa.String(50), nullable=False), sa.Column("status", sa.String(32), nullable=False),
                    sa.Column("input_data", postgresql.JSONB()), sa.Column("output_data", postgresql.JSONB()),
                    sa.Column("error", sa.Text()), created())
    op.create_index("ix_workflow_runs_trip_id", "workflow_runs", ["trip_id"])
    op.create_table("agent_runs", uid("id", primary=True), reference("trip_id", "trips"),
                    sa.Column("agent", sa.String(50), nullable=False), sa.Column("status", sa.String(32), nullable=False),
                    sa.Column("output_data", postgresql.JSONB()), sa.Column("error", sa.Text()),
                    sa.Column("token_usage", sa.Integer(), nullable=False), created())
    op.create_index("ix_agent_runs_trip_id", "agent_runs", ["trip_id"])
    op.create_table("tool_calls", uid("id", primary=True), reference("run_id", "agent_runs"),
                    sa.Column("tool", sa.String(80), nullable=False), sa.Column("status", sa.String(32), nullable=False),
                    sa.Column("latency_ms", sa.Integer(), nullable=False), sa.Column("error_code", sa.String(50)),
                    sa.Column("input_data", postgresql.JSONB()), sa.Column("output_data", postgresql.JSONB()))
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"])
    op.create_table("trip_events", uid("id", primary=True), reference("trip_id", "trips"),
                    sa.Column("event_type", sa.String(40), nullable=False),
                    sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("payload", postgresql.JSONB(), nullable=False), created())
    op.create_index("ix_trip_events_trip_id", "trip_events", ["trip_id"])
    op.create_table("user_feedback", uid("id", primary=True), reference("trip_id", "trips"),
                    sa.Column("text", sa.Text(), nullable=False), sa.Column("rating", sa.Integer()), created())
    op.create_index("ix_user_feedback_trip_id", "user_feedback", ["trip_id"])
    op.create_table("memories", uid("id", primary=True), reference("trip_id", "trips"),
                    reference("feedback_id", "user_feedback", nullable=True),
                    sa.Column("memory_type", sa.String(40), nullable=False), sa.Column("text", sa.Text(), nullable=False),
                    sa.Column("importance", sa.Float(), nullable=False), sa.Column("confidence", sa.Float(), nullable=False),
                    sa.Column("confirmed", sa.Boolean(), nullable=False), created())
    op.create_index("ix_memories_trip_id", "memories", ["trip_id"])


def downgrade():
    for table, index in [
        ("memories", "trip_id"), ("user_feedback", "trip_id"),
        ("trip_events", "trip_id"), ("tool_calls", "run_id"),
        ("agent_runs", "trip_id"), ("workflow_runs", "trip_id"),
        ("entity_candidates", "entity_id"), ("extracted_entities", "source_id"),
        ("content_items", "source_id"), ("content_sources", "trip_id"),
    ]:
        op.drop_index(f"ix_{table}_{index}", table_name=table)
        op.drop_table(table)
