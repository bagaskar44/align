"""B7 synthetic retention tombstones and scoped content redaction.

Revision ID: 0018_b7_retention
Revises: 0017_b6_finance_numeric
"""

from alembic import op

revision = "0018_b7_retention"
down_revision = "0017_b6_finance_numeric"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE retention_tombstones (
      id uuid PRIMARY KEY,
      file_asset_id uuid NOT NULL UNIQUE REFERENCES file_assets(id) ON DELETE RESTRICT,
      source_sha256 varchar(64) NOT NULL CHECK(source_sha256 ~ '^[a-f0-9]{64}$'),
      actor_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      policy_ref varchar(240) NOT NULL,
      purged_at timestamptz NOT NULL DEFAULT now(),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TRIGGER retention_tombstones_are_immutable
      BEFORE UPDATE OR DELETE ON retention_tombstones
      FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();
    ALTER TABLE publication_events DROP CONSTRAINT publication_events_event_check;
    ALTER TABLE publication_events ADD CONSTRAINT ck_publication_event_type
      CHECK(event IN ('released','withdrawn','purged'));

    CREATE FUNCTION retention_purge_active() RETURNS boolean
      LANGUAGE sql STABLE AS $$
        SELECT current_user='align_migration'
          AND current_setting('align.retention_purge', true)='on'
      $$;

    CREATE OR REPLACE FUNCTION reject_b1_evidence_change() RETURNS trigger
      LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_TABLE_NAME='evidence_items' AND TG_OP='UPDATE' AND retention_purge_active()
           AND EXISTS(SELECT 1 FROM retention_tombstones WHERE file_asset_id=OLD.file_asset_id)
           AND (NEW.id,NEW.source_unit_id,NEW.submission_version_id,NEW.context_version_id,
                NEW.assignment_version_id,NEW.file_asset_id,NEW.source_sha256,NEW.type,
                NEW.parse_status,NEW.parser_manifest_id,NEW.created_at)
               IS NOT DISTINCT FROM
               (OLD.id,OLD.source_unit_id,OLD.submission_version_id,OLD.context_version_id,
                OLD.assignment_version_id,OLD.file_asset_id,OLD.source_sha256,OLD.type,
                OLD.parse_status,OLD.parser_manifest_id,OLD.created_at)
           AND NEW.content_original_json='{}'::jsonb
           AND NEW.content_normalized_json='{}'::jsonb
           AND NEW.anchor_json=jsonb_build_object('purged',true)
        THEN RETURN NEW; END IF;
        RAISE EXCEPTION 'EVIDENCE_IMMUTABLE';
      END $$;

    CREATE OR REPLACE FUNCTION guard_submission_rows() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE assignment_course uuid; assignment_mode varchar(16); group_assignment uuid;
      submission_owner uuid; submission_group uuid; prior_version_no integer; next_version_no integer;
      scoped_submission uuid;
    BEGIN
      IF TG_OP='UPDATE' AND retention_purge_active() THEN
        IF TG_TABLE_NAME='submission_versions' THEN
          IF EXISTS(SELECT 1 FROM retention_tombstones t JOIN file_assets f ON f.id=t.file_asset_id
                    WHERE f.submission_id=OLD.submission_id)
             AND (NEW.id,NEW.submission_id,NEW.version_no,NEW.assignment_version_id,NEW.manifest_hash,
                  NEW.finalized_by,NEW.finalized_at,NEW.is_late,NEW.created_at)
                 IS NOT DISTINCT FROM
                 (OLD.id,OLD.submission_id,OLD.version_no,OLD.assignment_version_id,OLD.manifest_hash,
                  OLD.finalized_by,OLD.finalized_at,OLD.is_late,OLD.created_at)
             AND NEW.manifest_json=jsonb_build_object('source_purged',true) THEN RETURN NEW; END IF;
        END IF;
        IF TG_TABLE_NAME='submission_version_members' THEN
          SELECT s.id INTO scoped_submission FROM submission_versions sv
            JOIN submissions s ON s.id=sv.submission_id
            WHERE sv.id=OLD.submission_version_id;
          IF EXISTS(SELECT 1 FROM retention_tombstones t JOIN file_assets f ON f.id=t.file_asset_id
                    WHERE f.submission_id=scoped_submission)
             AND (NEW.submission_version_id,NEW.user_id,NEW.created_at)
                 IS NOT DISTINCT FROM (OLD.submission_version_id,OLD.user_id,OLD.created_at)
             AND NEW.display_name_snapshot='SOURCE_PURGED'
             AND NEW.email_snapshot='purged@invalid.example' THEN RETURN NEW; END IF;
        END IF;
      END IF;
      IF TG_TABLE_NAME='submissions' THEN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'SUBMISSION_THREAD_IMMUTABLE'; END IF;
        IF TG_OP='UPDATE' AND (NEW.assignment_id,NEW.individual_user_id,NEW.group_id)
          IS DISTINCT FROM (OLD.assignment_id,OLD.individual_user_id,OLD.group_id)
          THEN RAISE EXCEPTION 'SUBMISSION_OWNER_IMMUTABLE'; END IF;
        IF TG_OP='UPDATE' AND NEW.active_final_version_id IS DISTINCT FROM OLD.active_final_version_id
          AND OLD.active_final_version_id IS NOT NULL THEN
          SELECT version_no INTO prior_version_no FROM submission_versions WHERE id=OLD.active_final_version_id;
          SELECT version_no INTO next_version_no FROM submission_versions WHERE id=NEW.active_final_version_id
            AND submission_id=NEW.id;
          IF next_version_no IS NULL OR next_version_no <= prior_version_no
            THEN RAISE EXCEPTION 'SUBMISSION_FINAL_POINTER_MUST_ADVANCE'; END IF;
        END IF;
        SELECT course_id INTO assignment_course FROM assignments WHERE id=NEW.assignment_id;
        IF NEW.group_id IS NOT NULL THEN
          SELECT assignment_id INTO group_assignment FROM groups WHERE id=NEW.group_id AND active;
          IF group_assignment IS DISTINCT FROM NEW.assignment_id THEN RAISE EXCEPTION 'SUBMISSION_GROUP_SCOPE_MISMATCH'; END IF;
        END IF;
        IF NEW.draft_assignment_version_id IS NOT NULL THEN
          SELECT mode INTO assignment_mode FROM assignment_versions
           WHERE id=NEW.draft_assignment_version_id AND assignment_id=NEW.assignment_id AND state='published';
          IF assignment_mode IS NULL OR (assignment_mode='individual') <> (NEW.individual_user_id IS NOT NULL)
             OR (assignment_mode='group') <> (NEW.group_id IS NOT NULL)
            THEN RAISE EXCEPTION 'SUBMISSION_ASSIGNMENT_MODE_MISMATCH'; END IF;
        END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME='submission_versions' THEN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'SUBMISSION_VERSION_IMMUTABLE'; END IF;
        IF NOT EXISTS (SELECT 1 FROM submissions s JOIN assignment_versions av ON av.id=NEW.assignment_version_id
          WHERE s.id=NEW.submission_id AND s.assignment_id=av.assignment_id)
          THEN RAISE EXCEPTION 'SUBMISSION_VERSION_ASSIGNMENT_MISMATCH'; END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME='submission_version_files' THEN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'SUBMISSION_VERSION_FILE_IMMUTABLE'; END IF;
        IF NOT EXISTS (SELECT 1 FROM file_assets f WHERE f.id=NEW.file_asset_id AND f.submission_id=NEW.submission_id
          AND f.purpose='submission' AND f.validation_state='validated')
          THEN RAISE EXCEPTION 'SUBMISSION_FILE_SCOPE_MISMATCH'; END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME='submission_version_members' THEN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'SUBMISSION_ROSTER_IMMUTABLE'; END IF;
        SELECT s.individual_user_id,s.group_id INTO submission_owner,submission_group
          FROM submission_versions sv JOIN submissions s ON s.id=sv.submission_id
          WHERE sv.id=NEW.submission_version_id;
        IF submission_owner IS NOT NULL THEN
          IF submission_owner<>NEW.user_id THEN RAISE EXCEPTION 'SUBMISSION_ROSTER_OWNER_MISMATCH'; END IF;
        ELSIF NOT EXISTS (
          SELECT 1 FROM submission_versions sv JOIN submissions s ON s.id=sv.submission_id
          JOIN group_memberships gm ON gm.group_id=s.group_id AND gm.assignment_id=s.assignment_id
          JOIN assignments a ON a.id=s.assignment_id
          JOIN enrollments e ON e.course_id=a.course_id AND e.user_id=gm.user_id
          WHERE sv.id=NEW.submission_version_id AND gm.user_id=NEW.user_id
            AND gm.active AND e.active AND e.role='student'
        ) THEN RAISE EXCEPTION 'SUBMISSION_ROSTER_MEMBER_MISMATCH'; END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME='file_assets' THEN
        IF TG_OP='UPDATE' AND OLD.purpose='submission'
          THEN RAISE EXCEPTION 'SUBMISSION_FILE_ASSET_IMMUTABLE'; END IF;
        IF TG_OP='UPDATE' AND (NEW.id,NEW.submission_id,NEW.course_id,NEW.assignment_id,NEW.purpose,
          NEW.uploaded_by,NEW.original_name,NEW.size_bytes,NEW.mime,NEW.sha256,NEW.object_key)
          IS DISTINCT FROM (OLD.id,OLD.submission_id,OLD.course_id,OLD.assignment_id,OLD.purpose,
          OLD.uploaded_by,OLD.original_name,OLD.size_bytes,OLD.mime,OLD.sha256,OLD.object_key)
          THEN RAISE EXCEPTION 'FILE_ASSET_IDENTITY_IMMUTABLE'; END IF;
        IF TG_OP='DELETE' AND (OLD.purpose<>'submission' OR EXISTS(
          SELECT 1 FROM submission_version_files WHERE file_asset_id=OLD.id))
          THEN RAISE EXCEPTION 'FINAL_SUBMISSION_ASSET_IMMUTABLE'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END IF;
      RETURN COALESCE(NEW,OLD);
    END $$;

    CREATE OR REPLACE FUNCTION guard_submission_assets() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' THEN
        IF OLD.purpose='submission' AND retention_purge_active()
           AND EXISTS(SELECT 1 FROM retention_tombstones WHERE file_asset_id=OLD.id)
           AND (NEW.id,NEW.submission_id,NEW.course_id,NEW.assignment_id,NEW.purpose,NEW.uploaded_by,
                NEW.sha256,NEW.created_at)
               IS NOT DISTINCT FROM
               (OLD.id,OLD.submission_id,OLD.course_id,OLD.assignment_id,OLD.purpose,OLD.uploaded_by,
                OLD.sha256,OLD.created_at)
           AND NEW.original_name='SOURCE_PURGED' AND NEW.size_bytes=1
           AND NEW.mime='application/octet-stream' AND NEW.object_key='purged/'||OLD.id::text
           AND NEW.preflight_json='{}'::jsonb AND NEW.validation_state='quarantined'
           AND NEW.quarantined_at IS NOT NULL THEN RETURN NEW; END IF;
        IF OLD.purpose<>'submission' THEN RAISE EXCEPTION 'FILE_ASSET_IMMUTABLE'; END IF;
        IF (NEW.id,NEW.submission_id,NEW.course_id,NEW.assignment_id,NEW.purpose,NEW.uploaded_by,
            NEW.original_name,NEW.size_bytes,NEW.mime,NEW.sha256,NEW.object_key,NEW.preflight_json,NEW.created_at)
           IS DISTINCT FROM
           (OLD.id,OLD.submission_id,OLD.course_id,OLD.assignment_id,OLD.purpose,OLD.uploaded_by,
            OLD.original_name,OLD.size_bytes,OLD.mime,OLD.sha256,OLD.object_key,OLD.preflight_json,OLD.created_at)
          THEN RAISE EXCEPTION 'FILE_ASSET_IDENTITY_IMMUTABLE'; END IF;
        IF NEW.validation_state<>'quarantined' OR NEW.quarantined_at IS NULL
          THEN RAISE EXCEPTION 'SUBMISSION_ASSET_LIFECYCLE_INVALID'; END IF;
        RETURN NEW;
      END IF;
      IF TG_OP='DELETE' THEN
        IF OLD.purpose<>'submission' OR EXISTS(
          SELECT 1 FROM submission_version_files WHERE file_asset_id=OLD.id)
          THEN RAISE EXCEPTION 'FINAL_SUBMISSION_ASSET_IMMUTABLE'; END IF;
        RETURN OLD;
      END IF;
      RETURN NEW;
    END $$;

    CREATE OR REPLACE FUNCTION reject_review_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE scoped_submission uuid;
    BEGIN
      IF TG_OP='UPDATE' AND retention_purge_active() THEN
        IF TG_TABLE_NAME='publications' THEN scoped_submission:=OLD.submission_id;
        ELSIF TG_TABLE_NAME='review_annotation_revisions' THEN
          SELECT sv.submission_id INTO scoped_submission FROM review_annotations a
            JOIN review_versions rv ON rv.id=a.review_version_id
            JOIN submission_versions sv ON sv.id=rv.submission_version_id WHERE a.id=OLD.annotation_id;
        ELSIF TG_TABLE_NAME='review_element_decision_events' THEN
          SELECT sv.submission_id INTO scoped_submission FROM review_versions rv
            JOIN submission_versions sv ON sv.id=rv.submission_version_id WHERE rv.id=OLD.review_version_id;
        ELSIF TG_TABLE_NAME='review_finding_decisions' THEN
          SELECT sv.submission_id INTO scoped_submission FROM review_findings rf
            JOIN review_versions rv ON rv.id=rf.review_version_id
            JOIN submission_versions sv ON sv.id=rv.submission_version_id WHERE rf.id=OLD.review_finding_id;
        ELSIF TG_TABLE_NAME='manual_evidence' THEN
          SELECT sv.submission_id INTO scoped_submission FROM review_versions rv
            JOIN submission_versions sv ON sv.id=rv.submission_version_id WHERE rv.id=OLD.review_version_id;
        ELSIF TG_TABLE_NAME='numeric_observations' OR TG_TABLE_NAME='numeric_checks' THEN
          IF OLD.review_version_id IS NOT NULL THEN
            SELECT sv.submission_id INTO scoped_submission FROM review_versions rv
              JOIN submission_versions sv ON sv.id=rv.submission_version_id WHERE rv.id=OLD.review_version_id;
          ELSE
            SELECT sv.submission_id INTO scoped_submission FROM element_checks ec
              JOIN analysis_runs ar ON ar.id=ec.analysis_run_id
              JOIN submission_versions sv ON sv.id=ar.submission_version_id WHERE ec.id=OLD.element_check_id;
          END IF;
        END IF;
        IF EXISTS(SELECT 1 FROM retention_tombstones t JOIN file_assets f ON f.id=t.file_asset_id
                  WHERE f.submission_id=scoped_submission) THEN RETURN NEW; END IF;
      END IF;
      RAISE EXCEPTION 'REVIEW_HISTORY_IMMUTABLE';
    END $$;

    CREATE OR REPLACE FUNCTION protect_feedback_draft() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE scoped_submission uuid;
    BEGIN
      IF TG_OP='UPDATE' AND retention_purge_active()
         AND (NEW.id,NEW.review_version_id,NEW.sequence_no,NEW.generation_mode,NEW.source_review_hash,
              NEW.content_hash,NEW.state,
              NEW.created_at,NEW.approved_by,NEW.approved_at)
             IS NOT DISTINCT FROM
             (OLD.id,OLD.review_version_id,OLD.sequence_no,OLD.generation_mode,OLD.source_review_hash,
              OLD.content_hash,OLD.state,
              OLD.created_at,OLD.approved_by,OLD.approved_at)
         AND NEW.body_markdown='SOURCE_PURGED' AND NEW.blocks_json='[]'::jsonb
         AND NEW.included_review_finding_ids_json='[]'::jsonb
         AND NEW.exclusion_reasons_json='{}'::jsonb THEN
        SELECT sv.submission_id INTO scoped_submission FROM review_versions rv
          JOIN submission_versions sv ON sv.id=rv.submission_version_id
          WHERE rv.id=OLD.review_version_id;
        IF EXISTS(SELECT 1 FROM retention_tombstones t JOIN file_assets f ON f.id=t.file_asset_id
                  WHERE f.submission_id=scoped_submission) THEN RETURN NEW; END IF;
      END IF;
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'FEEDBACK_DRAFT_HISTORY_IMMUTABLE'; END IF;
      IF OLD.state='approved' THEN RAISE EXCEPTION 'APPROVED_FEEDBACK_IMMUTABLE'; END IF;
      RETURN NEW;
    END $$;

    CREATE OR REPLACE FUNCTION retention_purge_submission(
      target_submission_id uuid, actor_id uuid, policy_ref varchar,
      source_days integer, review_days integer
    ) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    DECLARE asset_ids uuid[]; version_ids uuid[]; review_ids uuid[]; analysis_ids uuid[];
      asset_count integer; publication_count integer;
    BEGIN
      IF session_user<>'align_operator' THEN RAISE EXCEPTION 'RETENTION_OPERATOR_REQUIRED'; END IF;
      IF NOT EXISTS(SELECT 1 FROM users WHERE id=actor_id AND active AND global_role='admin')
        THEN RAISE EXCEPTION 'RETENTION_ADMIN_REQUIRED'; END IF;
      IF policy_ref IS NULL OR length(trim(policy_ref))=0 OR length(policy_ref)>240
         OR source_days<1 OR source_days>3650 OR review_days<1 OR review_days>3650
        THEN RAISE EXCEPTION 'RETENTION_POLICY_INVALID'; END IF;
      IF policy_ref NOT LIKE 'synthetic:%' THEN RAISE EXCEPTION 'RETENTION_SYNTHETIC_ONLY'; END IF;
      PERFORM 1 FROM submissions WHERE id=target_submission_id FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'RETENTION_SUBMISSION_NOT_FOUND'; END IF;
      SELECT array_agg(id) INTO asset_ids FROM file_assets WHERE submission_id=target_submission_id;
      IF asset_ids IS NULL OR cardinality(asset_ids)=0 THEN RAISE EXCEPTION 'RETENTION_SOURCE_ASSET_MISSING'; END IF;
      IF EXISTS(SELECT 1 FROM file_assets WHERE id=ANY(asset_ids)
                AND created_at>now()-make_interval(days=>source_days))
        THEN RAISE EXCEPTION 'RETENTION_SOURCE_NOT_OLD_ENOUGH'; END IF;
      SELECT array_agg(id) INTO version_ids FROM submission_versions WHERE submission_id=target_submission_id;
      SELECT array_agg(id) INTO review_ids FROM review_versions WHERE submission_version_id=ANY(version_ids);
      SELECT array_agg(id) INTO analysis_ids FROM analysis_runs WHERE submission_version_id=ANY(version_ids);
      IF review_ids IS NOT NULL AND EXISTS(SELECT 1 FROM review_versions WHERE id=ANY(review_ids)
                AND created_at>now()-make_interval(days=>review_days))
        THEN RAISE EXCEPTION 'RETENTION_REVIEW_NOT_OLD_ENOUGH'; END IF;
      IF EXISTS(SELECT 1 FROM parse_artifacts WHERE file_asset_id=ANY(asset_ids)
                AND status IN ('queued','running'))
         OR (analysis_ids IS NOT NULL AND EXISTS(SELECT 1 FROM analysis_runs WHERE id=ANY(analysis_ids)
                AND status IN ('queued','running')))
        THEN RAISE EXCEPTION 'RETENTION_WORK_STILL_ACTIVE'; END IF;
      INSERT INTO retention_tombstones(id,file_asset_id,source_sha256,actor_user_id,policy_ref)
        SELECT gen_random_uuid(),id,sha256,actor_id,policy_ref FROM file_assets WHERE id=ANY(asset_ids)
        ON CONFLICT(file_asset_id) DO NOTHING;
      PERFORM set_config('align.retention_purge','on',true);
      GET DIAGNOSTICS asset_count = ROW_COUNT;

      UPDATE publications SET snapshot_json=jsonb_build_object('status','purged','source_purged',true)
        WHERE submission_id=target_submission_id;
      GET DIAGNOSTICS publication_count = ROW_COUNT;
      INSERT INTO publication_events(id,publication_id,event,actor_id,reason,event_at)
        SELECT gen_random_uuid(),p.id,'purged',actor_id,'SOURCE_PURGED',now()
          FROM publications p WHERE p.submission_id=target_submission_id
            AND NOT EXISTS(SELECT 1 FROM publication_events pe WHERE pe.publication_id=p.id AND pe.event='purged');

      UPDATE submission_versions SET manifest_json=jsonb_build_object('source_purged',true) WHERE id=ANY(version_ids);
      UPDATE submission_version_members SET display_name_snapshot='SOURCE_PURGED',email_snapshot='purged@invalid.example'
        WHERE submission_version_id=ANY(version_ids);
      UPDATE review_versions SET lecturer_notes='',limitations_json='[]'::jsonb WHERE id=ANY(review_ids);
      UPDATE review_findings SET claim='',reason=NULL,coverage_attestation_json=NULL
        WHERE review_version_id=ANY(review_ids);
      UPDATE review_finding_decisions SET payload_json='{}'::jsonb
        WHERE review_finding_id IN (SELECT id FROM review_findings WHERE review_version_id=ANY(review_ids));
      UPDATE review_element_decision_events SET payload_json='{}'::jsonb WHERE review_version_id=ANY(review_ids);
      UPDATE review_element_decisions SET reason=NULL WHERE review_version_id=ANY(review_ids);
      UPDATE review_annotation_revisions SET anchor_json=NULL,transcription=NULL,reason=NULL
        WHERE annotation_id IN (SELECT id FROM review_annotations WHERE review_version_id=ANY(review_ids));
      UPDATE review_annotation_targets SET relationship_code=NULL,role=NULL
        WHERE annotation_revision_id IN (SELECT id FROM review_annotation_revisions
          WHERE annotation_id IN (SELECT id FROM review_annotations WHERE review_version_id=ANY(review_ids)));
      UPDATE manual_evidence SET source_anchor_json='{}'::jsonb,transcription=NULL,reason=NULL
        WHERE review_version_id=ANY(review_ids);
      UPDATE feedback_drafts SET body_markdown='SOURCE_PURGED',blocks_json='[]'::jsonb,
        included_review_finding_ids_json='[]'::jsonb,exclusion_reasons_json='{}'::jsonb
        WHERE review_version_id=ANY(review_ids);
      UPDATE numeric_observations SET literal='0',canonical_decimal_text=NULL,context_json='{}'::jsonb,
        verification_ref_json='{}'::jsonb WHERE review_version_id=ANY(review_ids)
          OR element_check_id IN (SELECT id FROM element_checks WHERE analysis_run_id=ANY(analysis_ids));
      UPDATE numeric_checks SET normalized_context_json='{}'::jsonb,operands_snapshot_json='{}'::jsonb,
        result_decimal_text=NULL,difference_text=NULL,allowed_difference_text=NULL,inconsistent=NULL
        WHERE review_version_id=ANY(review_ids)
          OR element_check_id IN (SELECT id FROM element_checks WHERE analysis_run_id=ANY(analysis_ids));
      UPDATE evidence_items SET content_original_json='{}'::jsonb,content_normalized_json='{}'::jsonb,
        anchor_json=jsonb_build_object('purged',true),limitations_json='[]'::jsonb WHERE file_asset_id=ANY(asset_ids);
      DELETE FROM evidence_embeddings WHERE submission_version_id=ANY(version_ids);
      UPDATE source_units SET anchor_json=jsonb_build_object('purged',true),limitations_json='[]'::jsonb
        WHERE parse_artifact_id IN (SELECT id FROM parse_artifacts WHERE file_asset_id=ANY(asset_ids));
      UPDATE parser_checkpoints SET canonical_unit_locator_json=jsonb_build_object('purged',true),
        output_refs_json='{}'::jsonb,covered_unit_refs_json='[]'::jsonb,limitations_json='[]'::jsonb
        WHERE parse_artifact_id IN (SELECT id FROM parse_artifacts WHERE file_asset_id=ANY(asset_ids));
      UPDATE parse_artifacts SET artifact_object_key=NULL,limitations_json='[]'::jsonb WHERE file_asset_id=ANY(asset_ids);
      UPDATE retrieval_chunks SET chunk_text='',anchor_json=jsonb_build_object('purged',true),structural_metadata_json='{}'::jsonb
        WHERE submission_version_id=ANY(version_ids);
      UPDATE element_checks SET observations_json='[]'::jsonb,interpretation=NULL,reason_codes_json='[]'::jsonb
        WHERE analysis_run_id=ANY(analysis_ids);
      UPDATE finding_candidates SET claim='',limitations_json='[]'::jsonb WHERE analysis_run_id=ANY(analysis_ids);
      UPDATE analysis_run_stages SET output_refs_json='{}'::jsonb,limitation_codes_json='[]'::jsonb
        WHERE analysis_run_id=ANY(analysis_ids);
      UPDATE coverage_records SET inventory_json='{}'::jsonb,retrieval_json='{}'::jsonb,limitations_json='[]'::jsonb
        WHERE analysis_run_id=ANY(analysis_ids);
      UPDATE coverage_inspections SET detail_json='{}'::jsonb,observation_json='{}'::jsonb
        WHERE coverage_record_id IN (SELECT id FROM coverage_records WHERE analysis_run_id=ANY(analysis_ids));
      UPDATE grounding_evaluations SET evaluation_json='{}'::jsonb,mechanical_validation_json='{}'::jsonb
        WHERE analysis_run_id=ANY(analysis_ids);
      UPDATE evidence_support_bindings SET quote_spans_json='[]'::jsonb
        WHERE element_check_id IN (SELECT id FROM element_checks WHERE analysis_run_id=ANY(analysis_ids));
      UPDATE numeric_links SET numeric_engine_record_json='{}'::jsonb
        WHERE element_check_id IN (SELECT id FROM element_checks WHERE analysis_run_id=ANY(analysis_ids));
      UPDATE routing_decisions SET feature_snapshot_json='{}'::jsonb,guard_snapshot_json='{}'::jsonb,
        remaining_limits_json='{}'::jsonb,timing_refs_json='{}'::jsonb WHERE analysis_run_id=ANY(analysis_ids);
      UPDATE semantic_batch_members SET detail_json='{}'::jsonb WHERE analysis_run_id=ANY(analysis_ids);
      UPDATE provider_call_records SET request_manifest_json='{}'::jsonb,response_manifest_json='{}'::jsonb
        WHERE analysis_run_id=ANY(analysis_ids);
      UPDATE analysis_runs SET limitations_json='[]'::jsonb,stage_snapshot_json='{}'::jsonb
        WHERE id=ANY(analysis_ids);
      UPDATE idempotency_records SET response_json=NULL
        WHERE route LIKE '%'||target_submission_id::text||'%'
          OR EXISTS(SELECT 1 FROM unnest(coalesce(version_ids,'{}'::uuid[])||coalesce(asset_ids,'{}'::uuid[])||coalesce(review_ids,'{}'::uuid[])) related(id)
                    WHERE route LIKE '%'||related.id::text||'%' OR response_json::text LIKE '%'||related.id::text||'%');
      UPDATE file_assets SET original_name='SOURCE_PURGED',size_bytes=1,mime='application/octet-stream',
        object_key='purged/'||id::text,preflight_json='{}'::jsonb,validation_state='quarantined',
        quarantined_at=now() WHERE id=ANY(asset_ids);
      RETURN jsonb_build_object('assets_purged',cardinality(asset_ids),'publications_redacted',publication_count,
        'tombstones_created',asset_count,'unknown_billing_preserved',true,'source_purged',true);
    END $$;
    ALTER FUNCTION retention_purge_submission(uuid,uuid,varchar,integer,integer) OWNER TO align_migration;
    REVOKE ALL ON FUNCTION retention_purge_submission(uuid,uuid,varchar,integer,integer)
      FROM PUBLIC, align_api, align_worker, align_migration;
    GRANT SELECT ON retention_tombstones TO align_api;
    REVOKE INSERT,UPDATE,DELETE ON retention_tombstones FROM align_api,align_worker,align_operator;
    GRANT EXECUTE ON FUNCTION retention_purge_submission(uuid,uuid,varchar,integer,integer) TO align_operator;
    """)


def downgrade() -> None:
    raise RuntimeError("B7 retention redaction is irreversible; restore from a pre-migration backup")
