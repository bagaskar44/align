"""Manual review, annotation history and immutable publication snapshots.

Revision ID: 0010_b4_review_release
Revises: 0009_b3_upload_retention
"""

from alembic import op

revision = "0010_b4_review_release"
down_revision = "0009_b3_upload_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE analysis_runs (
      id uuid PRIMARY KEY,
      submission_version_id uuid NOT NULL REFERENCES submission_versions(id) ON DELETE RESTRICT,
      specification_version_id uuid NOT NULL REFERENCES specification_versions(id) ON DELETE RESTRICT,
      release_manifest_id uuid REFERENCES release_manifests(id) ON DELETE RESTRICT,
      config_fingerprint varchar(64) NOT NULL,
      status varchar(16) NOT NULL CHECK(status IN ('queued','running','partial','completed','failed','blocked')),
      provider_mode varchar(16) NOT NULL CHECK(provider_mode IN ('off','mock','live')),
      data_origin varchar(16) NOT NULL CHECK(data_origin IN ('synthetic','production')),
      limitations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(submission_version_id,config_fingerprint)
    );
    CREATE TABLE element_checks (
      id uuid PRIMARY KEY,
      analysis_run_id uuid NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
      check_element_id uuid NOT NULL REFERENCES check_elements(id) ON DELETE RESTRICT,
      applicability_status varchar(24) NOT NULL CHECK(applicability_status IN ('applicable','not_applicable','unresolved')),
      source_coverage_status varchar(32) NOT NULL CHECK(source_coverage_status IN ('sufficient_for_check','partial','unreadable','unresolved')),
      evidence_relationship varchar(24) NOT NULL CHECK(evidence_relationship IN ('supported','partial','not_found','conflicting','unclear')),
      check_status varchar(32) CHECK(check_status IS NULL OR check_status IN ('no_issue_detected','finding_candidate','unassessable')),
      observations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      interpretation varchar(4000),
      reason_codes_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(analysis_run_id,check_element_id)
    );
    CREATE TABLE analysis_result_evidence (
      element_check_id uuid NOT NULL REFERENCES element_checks(id) ON DELETE CASCADE,
      evidence_item_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(element_check_id,evidence_item_id)
    );
    CREATE TABLE finding_candidates (
      id uuid PRIMARY KEY,
      analysis_run_id uuid NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
      primary_category varchar(40) NOT NULL CHECK(primary_category IN ('missing_requirement','rubric_coverage_gap','numerical_inconsistency')),
      primary_criterion_id uuid NOT NULL REFERENCES criteria(id) ON DELETE RESTRICT,
      obligation_key varchar(240) NOT NULL,
      issue_signature varchar(240) NOT NULL,
      dedup_key varchar(240) NOT NULL,
      claim varchar(4000) NOT NULL,
      technical_validation varchar(16) NOT NULL CHECK(technical_validation IN ('valid','invalid')),
      semantic_support varchar(32) NOT NULL CHECK(semantic_support IN ('supported','unsupported','insufficient','not_required_deterministic')),
      limitations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(analysis_run_id,dedup_key)
    );
    CREATE TABLE finding_element_links (
      finding_candidate_id uuid NOT NULL REFERENCES finding_candidates(id) ON DELETE CASCADE,
      check_element_id uuid NOT NULL REFERENCES check_elements(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(finding_candidate_id,check_element_id)
    );
    CREATE TABLE finding_requirement_links (
      finding_candidate_id uuid NOT NULL REFERENCES finding_candidates(id) ON DELETE CASCADE,
      requirement_id uuid NOT NULL REFERENCES requirements(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(finding_candidate_id,requirement_id)
    );
    CREATE TABLE finding_criterion_links (
      finding_candidate_id uuid NOT NULL REFERENCES finding_candidates(id) ON DELETE CASCADE,
      criterion_id uuid NOT NULL REFERENCES criteria(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(finding_candidate_id,criterion_id)
    );
    CREATE TABLE finding_evidence_links (
      finding_candidate_id uuid NOT NULL REFERENCES finding_candidates(id) ON DELETE CASCADE,
      evidence_item_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(finding_candidate_id,evidence_item_id)
    );
    CREATE TABLE review_versions (
      id uuid PRIMARY KEY,
      submission_version_id uuid NOT NULL REFERENCES submission_versions(id) ON DELETE RESTRICT,
      analysis_run_id uuid REFERENCES analysis_runs(id) ON DELETE RESTRICT,
      version_no integer NOT NULL CHECK(version_no >= 1),
      state varchar(16) NOT NULL CHECK(state IN ('draft','complete')),
      content_hash varchar(64) NOT NULL,
      lecturer_notes varchar(20000) NOT NULL DEFAULT '',
      limitations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      completed_by uuid REFERENCES users(id), completed_at timestamptz,
      revision integer NOT NULL DEFAULT 1 CHECK(revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(submission_version_id,version_no), UNIQUE(id,submission_version_id)
    );
    CREATE TABLE review_element_decisions (
      id uuid PRIMARY KEY,
      review_version_id uuid NOT NULL REFERENCES review_versions(id) ON DELETE CASCADE,
      check_element_id uuid NOT NULL REFERENCES check_elements(id) ON DELETE RESTRICT,
      review_status varchar(16) NOT NULL CHECK(review_status IN ('pending','reviewed')),
      resolution varchar(32) CHECK(resolution IS NULL OR resolution IN ('observed_no_issue','confirmed_issue','not_applicable','unable_to_conclude')),
      applicability_override varchar(24) CHECK(applicability_override IS NULL OR applicability_override IN ('applicable','not_applicable','unresolved')),
      reason varchar(2000), decided_by uuid REFERENCES users(id), decided_at timestamptz,
      revision integer NOT NULL DEFAULT 1 CHECK(revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(review_version_id,check_element_id),
      CHECK((review_status='pending' AND resolution IS NULL) OR (review_status='reviewed' AND resolution IS NOT NULL))
    );
    CREATE TABLE review_element_decision_events (
      id uuid PRIMARY KEY,
      review_version_id uuid NOT NULL REFERENCES review_versions(id) ON DELETE RESTRICT,
      check_element_id uuid NOT NULL REFERENCES check_elements(id) ON DELETE RESTRICT,
      revision_no integer NOT NULL CHECK(revision_no >= 1), payload_json jsonb NOT NULL,
      actor_id uuid NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(review_version_id,check_element_id,revision_no)
    );
    CREATE TABLE review_element_decision_evidence (
      review_element_decision_id uuid NOT NULL REFERENCES review_element_decisions(id) ON DELETE CASCADE,
      evidence_item_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(review_element_decision_id,evidence_item_id)
    );
    CREATE TABLE numeric_observations (
      id uuid PRIMARY KEY,
      element_check_id uuid REFERENCES element_checks(id) ON DELETE RESTRICT,
      review_version_id uuid REFERENCES review_versions(id) ON DELETE RESTRICT,
      evidence_item_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
      literal varchar(240) NOT NULL, canonical_decimal_text varchar(240), context_json jsonb NOT NULL,
      source_quality varchar(24) NOT NULL CHECK(source_quality IN ('native_literal','ocr_verified','lecturer_confirmed','unverified')),
      verification_ref_json jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((element_check_id IS NOT NULL)::integer + (review_version_id IS NOT NULL)::integer = 1)
    );
    CREATE TABLE numeric_checks (
      id uuid PRIMARY KEY,
      element_check_id uuid REFERENCES element_checks(id) ON DELETE RESTRICT,
      review_version_id uuid REFERENCES review_versions(id) ON DELETE RESTRICT,
      numeric_policy_version_id uuid NOT NULL REFERENCES numeric_policy_versions(id) ON DELETE RESTRICT,
      comparability_status varchar(24) NOT NULL CHECK(comparability_status IN ('comparable','unresolved','not_comparable')),
      operation varchar(32) NOT NULL CHECK(operation IN ('pairwise','sum_against_reported','ratio_against_reported')),
      normalized_context_json jsonb NOT NULL, operands_snapshot_json jsonb NOT NULL,
      result_decimal_text varchar(240), difference_text varchar(240), allowed_difference_text varchar(240), inconsistent boolean,
      rule_manifest_id uuid REFERENCES method_manifests(id), created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((element_check_id IS NOT NULL)::integer + (review_version_id IS NOT NULL)::integer = 1)
    );
    CREATE TABLE review_findings (
      id uuid PRIMARY KEY,
      review_version_id uuid NOT NULL REFERENCES review_versions(id) ON DELETE CASCADE,
      candidate_id uuid REFERENCES finding_candidates(id) ON DELETE RESTRICT,
      origin varchar(16) NOT NULL CHECK(origin IN ('ai_review','lecturer')),
      action varchar(16) NOT NULL CHECK(action IN ('pending','accept','edit','reject','defer')),
      primary_category varchar(40) NOT NULL CHECK(primary_category IN ('missing_requirement','rubric_coverage_gap','numerical_inconsistency')),
      primary_criterion_id uuid NOT NULL REFERENCES criteria(id) ON DELETE RESTRICT,
      claim varchar(4000) NOT NULL,
      targets_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      coverage_attestation_json jsonb,
      numeric_check_id uuid REFERENCES numeric_checks(id) ON DELETE RESTRICT,
      reason varchar(2000), actor_id uuid REFERENCES users(id), acted_at timestamptz,
      revision integer NOT NULL DEFAULT 1 CHECK(revision >= 1),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(review_version_id,candidate_id)
    );
    CREATE TABLE review_finding_targets (
      id uuid PRIMARY KEY,
      review_finding_id uuid NOT NULL REFERENCES review_findings(id) ON DELETE CASCADE,
      check_element_id uuid REFERENCES check_elements(id) ON DELETE RESTRICT,
      requirement_id uuid REFERENCES requirements(id) ON DELETE RESTRICT,
      criterion_id uuid REFERENCES criteria(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((check_element_id IS NOT NULL)::integer + (requirement_id IS NOT NULL)::integer + (criterion_id IS NOT NULL)::integer = 1)
    );
    CREATE TABLE review_finding_decisions (
      id uuid PRIMARY KEY,
      review_finding_id uuid NOT NULL REFERENCES review_findings(id) ON DELETE RESTRICT,
      revision_no integer NOT NULL CHECK(revision_no >= 1), action varchar(16) NOT NULL CHECK(action IN ('pending','accept','edit','reject','defer')),
      payload_json jsonb NOT NULL, actor_id uuid NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(review_finding_id,revision_no)
    );
    CREATE TABLE review_annotations (
      id uuid PRIMARY KEY,
      review_version_id uuid NOT NULL REFERENCES review_versions(id) ON DELETE RESTRICT,
      kind varchar(24) NOT NULL CHECK(kind IN ('span_selection','transcription','evidence_binding')),
      current_revision_id uuid,
      created_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      revision integer NOT NULL DEFAULT 1 CHECK(revision >= 1), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE review_annotation_revisions (
      id uuid PRIMARY KEY,
      annotation_id uuid NOT NULL REFERENCES review_annotations(id) ON DELETE RESTRICT,
      revision_no integer NOT NULL CHECK(revision_no >= 1), previous_revision_id uuid REFERENCES review_annotation_revisions(id) ON DELETE RESTRICT,
      operation varchar(16) NOT NULL CHECK(operation IN ('upsert','remove')),
      file_asset_id uuid NOT NULL REFERENCES file_assets(id) ON DELETE RESTRICT,
      source_sha256 varchar(64) NOT NULL, anchor_json jsonb,
      original_evidence_id uuid REFERENCES evidence_items(id) ON DELETE RESTRICT,
      corrected_evidence_id uuid REFERENCES evidence_items(id) ON DELETE RESTRICT,
      transcription varchar(20000), reason varchar(2000), author_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(annotation_id,revision_no)
    );
    ALTER TABLE review_annotations ADD CONSTRAINT fk_review_annotation_current_revision
      FOREIGN KEY(current_revision_id) REFERENCES review_annotation_revisions(id) ON DELETE RESTRICT;
    ALTER TABLE review_element_decision_evidence ADD COLUMN annotation_revision_id uuid
      REFERENCES review_annotation_revisions(id) ON DELETE RESTRICT;
    CREATE TABLE review_annotation_targets (
      id uuid PRIMARY KEY,
      annotation_revision_id uuid NOT NULL REFERENCES review_annotation_revisions(id) ON DELETE CASCADE,
      check_element_id uuid REFERENCES check_elements(id) ON DELETE RESTRICT,
      review_finding_id uuid REFERENCES review_findings(id) ON DELETE RESTRICT,
      relationship_code varchar(120), role varchar(120), created_at timestamptz NOT NULL DEFAULT now(),
      CHECK(check_element_id IS NOT NULL OR review_finding_id IS NOT NULL)
    );
    CREATE TABLE manual_evidence (
      id uuid PRIMARY KEY,
      evidence_item_id uuid NOT NULL UNIQUE REFERENCES evidence_items(id) ON DELETE RESTRICT,
      review_version_id uuid NOT NULL REFERENCES review_versions(id) ON DELETE RESTRICT,
      annotation_revision_id uuid NOT NULL REFERENCES review_annotation_revisions(id) ON DELETE RESTRICT,
      created_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      origin varchar(32) NOT NULL CHECK(origin IN ('lecturer_selected','lecturer_transcription')),
      source_anchor_json jsonb NOT NULL, transcription varchar(20000), reason varchar(2000),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE feedback_drafts (
      id uuid PRIMARY KEY,
      review_version_id uuid NOT NULL REFERENCES review_versions(id) ON DELETE RESTRICT,
      sequence_no integer NOT NULL CHECK(sequence_no >= 1),
      generation_mode varchar(16) NOT NULL CHECK(generation_mode IN ('model','template','manual')),
      source_review_hash varchar(64) NOT NULL,
      blocks_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      body_markdown varchar(20000) NOT NULL,
      included_review_finding_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      exclusion_reasons_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      content_hash varchar(64) NOT NULL,
      state varchar(16) NOT NULL CHECK(state IN ('draft','approved')),
      approved_by uuid REFERENCES users(id), approved_at timestamptz,
      model_manifest_id uuid REFERENCES method_manifests(id),
      revision integer NOT NULL DEFAULT 1 CHECK(revision >= 1), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(review_version_id,sequence_no)
    );
    CREATE TABLE publications (
      id uuid PRIMARY KEY,
      submission_id uuid NOT NULL REFERENCES submissions(id) ON DELETE RESTRICT,
      submission_version_id uuid NOT NULL REFERENCES submission_versions(id) ON DELETE RESTRICT,
      review_version_id uuid NOT NULL REFERENCES review_versions(id) ON DELETE RESTRICT,
      feedback_draft_id uuid NOT NULL REFERENCES feedback_drafts(id) ON DELETE RESTRICT,
      sequence_no integer NOT NULL CHECK(sequence_no >= 1), snapshot_json jsonb NOT NULL, snapshot_hash varchar(64) NOT NULL,
      approved_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT, approved_at timestamptz NOT NULL,
      published_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT, published_at timestamptz NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(submission_id,sequence_no), UNIQUE(id,submission_id)
    );
    CREATE TABLE publication_events (
      id uuid PRIMARY KEY,
      publication_id uuid NOT NULL REFERENCES publications(id) ON DELETE RESTRICT,
      event varchar(16) NOT NULL CHECK(event IN ('released','withdrawn')),
      actor_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
      reason varchar(2000), event_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX uq_publication_released_event ON publication_events(publication_id) WHERE event='released';
    CREATE UNIQUE INDEX uq_publication_withdrawn_event ON publication_events(publication_id) WHERE event='withdrawn';
    CREATE INDEX ix_publication_events_latest ON publication_events(publication_id,event_at DESC);
    CREATE TABLE evidence_links (
      id uuid PRIMARY KEY,
      evidence_item_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
      finding_candidate_id uuid REFERENCES finding_candidates(id) ON DELETE RESTRICT,
      review_finding_id uuid REFERENCES review_findings(id) ON DELETE RESTRICT,
      feedback_draft_id uuid REFERENCES feedback_drafts(id) ON DELETE RESTRICT,
      publication_id uuid REFERENCES publications(id) ON DELETE RESTRICT,
      annotation_revision_id uuid REFERENCES review_annotation_revisions(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((finding_candidate_id IS NOT NULL)::integer + (review_finding_id IS NOT NULL)::integer + (feedback_draft_id IS NOT NULL)::integer + (publication_id IS NOT NULL)::integer = 1)
    );
    CREATE UNIQUE INDEX uq_evidence_link_review_finding_revision ON evidence_links(evidence_item_id,review_finding_id,annotation_revision_id) WHERE review_finding_id IS NOT NULL;
    ALTER TABLE submissions ADD COLUMN latest_published_id uuid;
    ALTER TABLE submissions ADD CONSTRAINT fk_submissions_latest_publication
      FOREIGN KEY(latest_published_id,id) REFERENCES publications(id,submission_id) ON DELETE RESTRICT;
    ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_items_type;
    ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_type CHECK
      (type IN ('paragraph','table','diagram','image_text','spreadsheet_cell','spreadsheet_range','numeric_observation','lecturer_transcription'));

    CREATE FUNCTION reject_review_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'REVIEW_HISTORY_IMMUTABLE'; END $$;
    CREATE TRIGGER review_annotation_revision_immutable BEFORE UPDATE OR DELETE ON review_annotation_revisions
      FOR EACH ROW EXECUTE FUNCTION reject_review_history_change();
    CREATE TRIGGER review_element_decision_event_immutable BEFORE UPDATE OR DELETE ON review_element_decision_events
      FOR EACH ROW EXECUTE FUNCTION reject_review_history_change();
    CREATE TRIGGER review_finding_decision_immutable BEFORE UPDATE OR DELETE ON review_finding_decisions
      FOR EACH ROW EXECUTE FUNCTION reject_review_history_change();
    CREATE TRIGGER manual_evidence_immutable BEFORE UPDATE OR DELETE ON manual_evidence
      FOR EACH ROW EXECUTE FUNCTION reject_review_history_change();
    CREATE TRIGGER evidence_link_immutable BEFORE UPDATE OR DELETE ON evidence_links
      FOR EACH ROW EXECUTE FUNCTION reject_review_history_change();
    CREATE TRIGGER publication_immutable BEFORE UPDATE OR DELETE ON publications
      FOR EACH ROW EXECUTE FUNCTION reject_review_history_change();
    CREATE TRIGGER publication_event_immutable BEFORE UPDATE OR DELETE ON publication_events
      FOR EACH ROW EXECUTE FUNCTION reject_review_history_change();
    CREATE TRIGGER numeric_observation_immutable BEFORE UPDATE OR DELETE ON numeric_observations
      FOR EACH ROW EXECUTE FUNCTION reject_review_history_change();
    CREATE TRIGGER numeric_check_immutable BEFORE UPDATE OR DELETE ON numeric_checks
      FOR EACH ROW EXECUTE FUNCTION reject_review_history_change();
    CREATE FUNCTION protect_feedback_draft() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'FEEDBACK_DRAFT_HISTORY_IMMUTABLE'; END IF;
      IF OLD.state='approved' THEN RAISE EXCEPTION 'APPROVED_FEEDBACK_IMMUTABLE'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER feedback_draft_guard BEFORE UPDATE OR DELETE ON feedback_drafts
      FOR EACH ROW EXECUTE FUNCTION protect_feedback_draft();
    CREATE FUNCTION protect_review_annotation_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (NEW.id,NEW.review_version_id,NEW.kind,NEW.created_by,NEW.created_at)
         IS DISTINCT FROM (OLD.id,OLD.review_version_id,OLD.kind,OLD.created_by,OLD.created_at)
        THEN RAISE EXCEPTION 'REVIEW_ANNOTATION_IDENTITY_IMMUTABLE'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER review_annotation_identity BEFORE UPDATE ON review_annotations
      FOR EACH ROW EXECUTE FUNCTION protect_review_annotation_identity();

    GRANT SELECT,INSERT,UPDATE,DELETE ON analysis_runs,element_checks,analysis_result_evidence,finding_candidates,
      finding_element_links,finding_requirement_links,finding_criterion_links,finding_evidence_links,
      numeric_observations,numeric_checks,review_versions,review_element_decisions,review_element_decision_events,review_element_decision_evidence,
      review_findings,review_finding_targets,review_finding_decisions,review_annotations,review_annotation_revisions,
      review_annotation_targets,manual_evidence,feedback_drafts,publications,publication_events,evidence_links TO align_api;
    GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO align_api;
    REVOKE UPDATE,DELETE ON review_element_decision_events,review_finding_decisions,review_annotation_revisions,
      manual_evidence,publications,publication_events,evidence_links FROM align_api,align_worker;
    REVOKE DELETE ON review_versions,review_element_decisions,review_findings,review_annotations,feedback_drafts FROM align_api;
    GRANT SELECT,INSERT,UPDATE ON analysis_runs,element_checks,analysis_result_evidence,finding_candidates,
      finding_element_links,finding_requirement_links,finding_criterion_links,finding_evidence_links TO align_worker;
    GRANT SELECT,INSERT ON numeric_observations,numeric_checks TO align_worker;
    REVOKE DELETE ON analysis_runs,element_checks,analysis_result_evidence,finding_candidates,
      finding_element_links,finding_requirement_links,finding_criterion_links,finding_evidence_links,
      numeric_observations,numeric_checks FROM align_worker;
    REVOKE INSERT,UPDATE,DELETE ON review_versions,review_element_decisions,review_element_decision_events,
      review_element_decision_evidence,review_findings,review_finding_targets,review_finding_decisions,review_annotations,review_annotation_revisions,
      review_annotation_targets,manual_evidence,feedback_drafts,publications,publication_events,evidence_links FROM align_worker;
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE SELECT,INSERT,UPDATE,DELETE ON analysis_runs,element_checks,analysis_result_evidence,finding_candidates,
      finding_element_links,finding_requirement_links,finding_criterion_links,finding_evidence_links,
      numeric_observations,numeric_checks,review_versions,review_element_decisions,review_element_decision_events,review_element_decision_evidence,
      review_findings,review_finding_targets,review_finding_decisions,review_annotations,review_annotation_revisions,
      review_annotation_targets,manual_evidence,feedback_drafts,publications,publication_events,evidence_links FROM align_api;
    REVOKE SELECT,INSERT,UPDATE ON analysis_runs,element_checks,analysis_result_evidence,finding_candidates,
      finding_element_links,finding_requirement_links,finding_criterion_links,finding_evidence_links FROM align_worker;
    REVOKE SELECT,INSERT ON numeric_observations,numeric_checks FROM align_worker;
    ALTER TABLE submissions DROP CONSTRAINT fk_submissions_latest_publication;
    ALTER TABLE submissions DROP COLUMN latest_published_id;
    DROP TRIGGER IF EXISTS review_annotation_revision_immutable ON review_annotation_revisions;
    DROP TRIGGER IF EXISTS review_element_decision_event_immutable ON review_element_decision_events;
    DROP TRIGGER IF EXISTS review_finding_decision_immutable ON review_finding_decisions;
    DROP TRIGGER IF EXISTS manual_evidence_immutable ON manual_evidence;
    DROP TRIGGER IF EXISTS evidence_link_immutable ON evidence_links;
    DROP TRIGGER IF EXISTS publication_immutable ON publications;
    DROP TRIGGER IF EXISTS publication_event_immutable ON publication_events;
    DROP TRIGGER IF EXISTS numeric_observation_immutable ON numeric_observations;
    DROP TRIGGER IF EXISTS numeric_check_immutable ON numeric_checks;
    DROP INDEX IF EXISTS uq_publication_withdrawn_event;
    DROP INDEX IF EXISTS uq_publication_released_event;
    DROP TRIGGER IF EXISTS feedback_draft_guard ON feedback_drafts;
    DROP FUNCTION IF EXISTS protect_feedback_draft();
    DROP TRIGGER IF EXISTS review_annotation_identity ON review_annotations;
    DROP FUNCTION IF EXISTS protect_review_annotation_identity();
    DROP FUNCTION IF EXISTS reject_review_history_change();
    DROP TABLE IF EXISTS evidence_links,publication_events,publications,feedback_drafts,manual_evidence,
      review_annotation_targets,review_annotation_revisions,review_annotations,review_finding_decisions,
      review_finding_targets,review_findings,numeric_checks,numeric_observations,review_element_decision_events,
      review_element_decision_evidence,review_element_decisions,review_versions,finding_evidence_links,finding_criterion_links,
      finding_requirement_links,finding_element_links,finding_candidates,analysis_result_evidence,element_checks,analysis_runs CASCADE;
    ALTER TABLE evidence_items DROP CONSTRAINT IF EXISTS ck_evidence_items_type;
    ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_type CHECK
      (type IN ('paragraph','table','diagram','image_text','spreadsheet_cell','spreadsheet_range','numeric_observation'));
    """)
