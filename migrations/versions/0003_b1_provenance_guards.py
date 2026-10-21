"""Serialize approval with provenance writes and retain approved source inventory.

Revision ID: 0003_b1_provenance_guards
Revises: 0002_b1_identity_context
"""
from alembic import op

revision = "0003_b1_provenance_guards"
down_revision = "0002_b1_identity_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION protect_b1_context_child() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE target_state varchar(16);
    BEGIN
      IF TG_OP IN ('UPDATE','DELETE') THEN
        SELECT state INTO target_state FROM context_versions WHERE id=OLD.context_version_id FOR UPDATE;
        IF target_state='approved' THEN RAISE EXCEPTION 'APPROVED_CONTEXT_IMMUTABLE'; END IF;
      END IF;
      IF TG_OP IN ('INSERT','UPDATE') THEN
        SELECT state INTO target_state FROM context_versions WHERE id=NEW.context_version_id FOR UPDATE;
        IF target_state='approved' THEN RAISE EXCEPTION 'APPROVED_CONTEXT_IMMUTABLE'; END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER normative_links_draft_only BEFORE INSERT ON normative_file_links
      FOR EACH ROW EXECUTE FUNCTION protect_b1_context_child();
    CREATE TRIGGER parse_artifacts_draft_only BEFORE INSERT OR UPDATE ON parse_artifacts
      FOR EACH ROW EXECUTE FUNCTION protect_b1_context_child();
    CREATE TRIGGER evidence_draft_only BEFORE INSERT ON evidence_items
      FOR EACH ROW EXECUTE FUNCTION protect_b1_context_child();
    CREATE FUNCTION protect_b1_source_unit() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE target_context uuid; target_state varchar(16); parent_artifact uuid;
    BEGIN
      IF TG_OP IN ('UPDATE','DELETE') THEN
        SELECT context_version_id INTO target_context FROM parse_artifacts WHERE id=OLD.parse_artifact_id;
        SELECT state INTO target_state FROM context_versions WHERE id=target_context FOR UPDATE;
        IF target_state='approved' THEN RAISE EXCEPTION 'APPROVED_CONTEXT_IMMUTABLE'; END IF;
      END IF;
      IF TG_OP IN ('INSERT','UPDATE') THEN
        SELECT context_version_id INTO target_context FROM parse_artifacts WHERE id=NEW.parse_artifact_id;
        SELECT state INTO target_state FROM context_versions WHERE id=target_context FOR UPDATE;
        IF target_state='approved' THEN RAISE EXCEPTION 'APPROVED_CONTEXT_IMMUTABLE'; END IF;
        IF NEW.parent_id IS NOT NULL THEN
          SELECT parse_artifact_id INTO parent_artifact FROM source_units WHERE id=NEW.parent_id;
          IF parent_artifact IS DISTINCT FROM NEW.parse_artifact_id THEN RAISE EXCEPTION 'SOURCE_UNIT_PARENT_SCOPE_MISMATCH'; END IF;
        END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER source_units_draft_only BEFORE INSERT OR UPDATE OR DELETE ON source_units
      FOR EACH ROW EXECUTE FUNCTION protect_b1_source_unit();
    CREATE FUNCTION protect_b1_evidence_integrity() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE expected_sha varchar(64); expected_anchor jsonb; expected_manifest uuid;
    BEGIN
      SELECT sha256 INTO expected_sha FROM file_assets WHERE id=NEW.file_asset_id;
      SELECT u.anchor_json, p.parser_manifest_id INTO expected_anchor, expected_manifest
        FROM source_units u JOIN parse_artifacts p ON p.id=u.parse_artifact_id WHERE u.id=NEW.source_unit_id;
      IF NEW.source_sha256 IS DISTINCT FROM expected_sha OR NEW.anchor_json IS DISTINCT FROM expected_anchor
        OR NEW.parser_manifest_id IS DISTINCT FROM expected_manifest THEN
        RAISE EXCEPTION 'EVIDENCE_INTEGRITY_MISMATCH';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER evidence_integrity BEFORE INSERT ON evidence_items
      FOR EACH ROW EXECUTE FUNCTION protect_b1_evidence_integrity();
    REVOKE INSERT ON context_versions FROM align_worker;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER evidence_integrity ON evidence_items;
    DROP FUNCTION protect_b1_evidence_integrity();
    DROP TRIGGER source_units_draft_only ON source_units;
    DROP FUNCTION protect_b1_source_unit();
    DROP TRIGGER evidence_draft_only ON evidence_items;
    DROP TRIGGER parse_artifacts_draft_only ON parse_artifacts;
    DROP TRIGGER normative_links_draft_only ON normative_file_links;
    GRANT INSERT ON context_versions TO align_worker;
    """)
