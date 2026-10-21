"""B5 execution, scoped retrieval, coverage, grounding and routing records.

Revision ID: 0011_b5_executor
Revises: 0010_b4_review_release
"""

from alembic import op

revision = "0011_b5_executor"
down_revision = "0010_b4_review_release"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE analysis_runs ADD COLUMN job_id uuid REFERENCES jobs(id) ON DELETE RESTRICT;
    ALTER TABLE analysis_runs ADD COLUMN manifest_snapshot_json jsonb NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE analysis_runs ADD COLUMN stage_snapshot_json jsonb NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE analysis_runs ADD COLUMN usage_snapshot_json jsonb NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE analysis_runs ADD COLUMN completion_kind varchar(32);
    CREATE UNIQUE INDEX uq_analysis_runs_job ON analysis_runs(job_id) WHERE job_id IS NOT NULL;

    CREATE TABLE analysis_run_stages (
      id uuid PRIMARY KEY,
      analysis_run_id uuid NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
      stage_name varchar(40) NOT NULL CHECK(stage_name IN ('validate_manifest','parse','build_evidence','index','execute_checks','verify','ready_for_review')),
      input_hash varchar(64) NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
      status varchar(16) NOT NULL CHECK(status IN ('queued','running','succeeded','blocked','failed')),
      output_refs_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      limitation_codes_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      started_at timestamptz,
      finished_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(analysis_run_id,stage_name,input_hash)
    );

    CREATE TABLE retrieval_chunks (
      id uuid PRIMARY KEY,
      submission_version_id uuid NOT NULL REFERENCES submission_versions(id) ON DELETE RESTRICT,
      submission_id uuid NOT NULL REFERENCES submissions(id) ON DELETE RESTRICT,
      course_id uuid NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
      assignment_id uuid NOT NULL REFERENCES assignments(id) ON DELETE RESTRICT,
      evidence_item_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
      source_sha256 varchar(64) NOT NULL CHECK(source_sha256 ~ '^[a-f0-9]{64}$'),
      chunk_no integer NOT NULL CHECK(chunk_no >= 0),
      tokenizer_manifest varchar(80) NOT NULL,
      token_start integer NOT NULL CHECK(token_start >= 0),
      token_end integer NOT NULL CHECK(token_end >= token_start),
      chunk_text text NOT NULL,
      chunk_hash varchar(64) NOT NULL CHECK(chunk_hash ~ '^[a-f0-9]{64}$'),
      anchor_json jsonb NOT NULL,
      source_order integer NOT NULL,
      parent_chunk_id uuid REFERENCES retrieval_chunks(id) ON DELETE RESTRICT,
      structural_metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      retrieval_manifest_id uuid REFERENCES method_manifests(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(submission_version_id,evidence_item_id,chunk_no,chunk_hash),
      UNIQUE(id,submission_version_id)
    );
    CREATE INDEX ix_retrieval_chunks_scope_order ON retrieval_chunks
      (submission_version_id,course_id,assignment_id,source_order,evidence_item_id,chunk_no);

    CREATE TABLE chunk_evidence_links (
      chunk_id uuid NOT NULL REFERENCES retrieval_chunks(id) ON DELETE CASCADE,
      evidence_item_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
      submission_version_id uuid NOT NULL REFERENCES submission_versions(id) ON DELETE RESTRICT,
      relation varchar(24) NOT NULL CHECK(relation IN ('source','parent_context','table_context')),
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(chunk_id,evidence_item_id),
      FOREIGN KEY(chunk_id,submission_version_id) REFERENCES retrieval_chunks(id,submission_version_id) ON DELETE CASCADE
    );

    CREATE TABLE evidence_embeddings (
      chunk_id uuid NOT NULL REFERENCES retrieval_chunks(id) ON DELETE CASCADE,
      submission_version_id uuid NOT NULL REFERENCES submission_versions(id) ON DELETE RESTRICT,
      course_id uuid NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
      assignment_id uuid NOT NULL REFERENCES assignments(id) ON DELETE RESTRICT,
      owner_submission_id uuid NOT NULL REFERENCES submissions(id) ON DELETE RESTRICT,
      embedding_manifest varchar(120) NOT NULL,
      model_manifest_id uuid REFERENCES method_manifests(id) ON DELETE RESTRICT,
      dimension integer NOT NULL DEFAULT 1536 CHECK(dimension=1536),
      content_hash varchar(64) NOT NULL DEFAULT repeat('0',64) CHECK(content_hash ~ '^[a-f0-9]{64}$'),
      embedding vector(1536) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(chunk_id,embedding_manifest)
    );
    CREATE INDEX ix_evidence_embeddings_scope ON evidence_embeddings
      (submission_version_id,course_id,assignment_id,owner_submission_id,embedding_manifest);

    CREATE TABLE coverage_records (
      id uuid PRIMARY KEY,
      element_check_id uuid UNIQUE REFERENCES element_checks(id) ON DELETE CASCADE,
      analysis_run_id uuid NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
      check_element_id uuid NOT NULL REFERENCES check_elements(id) ON DELETE RESTRICT,
      retrieval_status varchar(32) NOT NULL CHECK(retrieval_status IN ('hybrid','bm25_fallback','structural_fallback','empty_top_k_expanded','unavailable')),
      compatible_count integer NOT NULL CHECK(compatible_count >= 0),
      inspected_count integer NOT NULL CHECK(inspected_count >= 0),
      unresolved_count integer NOT NULL CHECK(unresolved_count >= 0),
      sufficient_for_positive boolean NOT NULL,
      exhaustive_for_negative boolean NOT NULL,
      inventory_json jsonb NOT NULL,
      retrieval_json jsonb NOT NULL,
      limitations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(analysis_run_id,check_element_id)
    );
    CREATE TABLE coverage_inspections (
      id uuid PRIMARY KEY,
      coverage_record_id uuid NOT NULL REFERENCES coverage_records(id) ON DELETE CASCADE,
      source_unit_id uuid NOT NULL REFERENCES source_units(id) ON DELETE RESTRICT,
      evidence_item_id uuid REFERENCES evidence_items(id) ON DELETE RESTRICT,
      state varchar(24) NOT NULL CHECK(state IN ('compatible','inspected','excluded','unresolved','failed','unreadable','truncated')),
      method varchar(40) NOT NULL,
      reason_code varchar(100),
      detail_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      inspection_status varchar(24) CHECK(inspection_status IN ('inspected','excluded','unresolved')),
      observation_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      rule_or_call_id uuid,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(coverage_record_id,source_unit_id)
    );

    CREATE TABLE grounding_evaluations (
      id uuid PRIMARY KEY,
      analysis_run_id uuid NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
      element_check_id uuid NOT NULL REFERENCES element_checks(id) ON DELETE CASCADE,
      mechanical_status varchar(24) NOT NULL CHECK(mechanical_status IN ('valid','invalid','not_applicable')),
      semantic_status varchar(24) NOT NULL CHECK(semantic_status IN ('supported','unsupported','insufficient','not_run','human_required')),
      evaluation_json jsonb NOT NULL,
      limitation_codes_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      contract_schema_version integer NOT NULL DEFAULT 1 CHECK(contract_schema_version=1),
      mode varchar(32) NOT NULL CHECK(mode IN ('anchored_content','linked_relationship','comparable_numeric','human_only')),
      status varchar(24) NOT NULL CHECK(status IN ('supported','insufficient','human_required','not_run')),
      decision_basis varchar(32) CHECK(decision_basis IN ('positive_support','coverage_absence','numeric_comparison')),
      mechanical_validation_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      semantic_support varchar(24) NOT NULL CHECK(semantic_support IN ('supported','insufficient','not_required','not_run')),
      coverage_record_id uuid REFERENCES coverage_records(id) ON DELETE RESTRICT,
      validator_manifest_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(element_check_id)
    );
    CREATE TABLE evidence_support_bindings (
      id uuid PRIMARY KEY,
      element_check_id uuid NOT NULL REFERENCES element_checks(id) ON DELETE CASCADE,
      relationship_code varchar(64),
      role varchar(64) NOT NULL,
      observation_codes_json jsonb NOT NULL,
      evidence_item_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
      quote_spans_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      validator_refs_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE numeric_links (
      id uuid PRIMARY KEY,
      element_check_id uuid NOT NULL REFERENCES element_checks(id) ON DELETE CASCADE,
      evidence_item_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
      numeric_engine_record_json jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(element_check_id,evidence_item_id)
    );

    CREATE TABLE routing_decisions (
      id uuid PRIMARY KEY,
      analysis_run_id uuid NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
      check_element_id uuid REFERENCES check_elements(id) ON DELETE RESTRICT,
      task varchar(40) NOT NULL,
      action varchar(40) NOT NULL,
      selected_role varchar(24),
      reason_code varchar(100) NOT NULL,
      feature_snapshot_json jsonb NOT NULL,
      guard_snapshot_json jsonb NOT NULL,
      remaining_limits_json jsonb NOT NULL,
      policy_manifest_id uuid REFERENCES method_manifests(id) ON DELETE RESTRICT,
      outcome varchar(40) NOT NULL,
      job_id uuid REFERENCES jobs(id) ON DELETE RESTRICT,
      batching_manifest_id uuid REFERENCES method_manifests(id) ON DELETE RESTRICT,
      timing_refs_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE provider_call_records (
      id uuid PRIMARY KEY,
      analysis_run_id uuid NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
      routing_decision_id uuid REFERENCES routing_decisions(id) ON DELETE RESTRICT,
      logical_call_id uuid NOT NULL,
      attempt_no integer NOT NULL CHECK(attempt_no >= 1),
      task varchar(40) NOT NULL,
      provider_mode varchar(16) NOT NULL CHECK(provider_mode IN ('off','mock','live')),
      status varchar(32) NOT NULL,
      request_manifest_json jsonb NOT NULL,
      response_manifest_json jsonb NOT NULL,
      usage_json jsonb NOT NULL,
      cost_usd numeric(18,8),
      provider_request_id varchar(240),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(logical_call_id,attempt_no)
    );
    CREATE TABLE semantic_batch_members (
      id uuid PRIMARY KEY,
      analysis_run_id uuid NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
      batch_call_id uuid NOT NULL,
      check_element_id uuid NOT NULL REFERENCES check_elements(id) ON DELETE RESTRICT,
      entry_status varchar(24) NOT NULL CHECK(entry_status IN ('valid','omitted','duplicate','foreign','invalid')),
      response_hash varchar(64),
      detail_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(analysis_run_id,batch_call_id,check_element_id)
    );
    CREATE TABLE routing_call_links (
      routing_decision_id uuid NOT NULL REFERENCES routing_decisions(id) ON DELETE CASCADE,
      call_id uuid NOT NULL,
      PRIMARY KEY(routing_decision_id,call_id)
    );
    CREATE TABLE finding_coverage_links (
      finding_candidate_id uuid NOT NULL REFERENCES finding_candidates(id) ON DELETE CASCADE,
      coverage_record_id uuid NOT NULL REFERENCES coverage_records(id) ON DELETE RESTRICT,
      PRIMARY KEY(finding_candidate_id,coverage_record_id)
    );

    -- B5's scoped worker reads only the approved specification and source-side
    -- registry tables. It receives no write access to requirements or review state.
    GRANT SELECT ON courses,assignments,specification_versions,check_elements,
      element_requirement_links,requirements,criteria,numeric_policy_versions TO align_worker;
    REVOKE INSERT,UPDATE,DELETE ON courses,assignments,specification_versions,check_elements,
      element_requirement_links,requirements,criteria,numeric_policy_versions FROM align_worker;

    GRANT SELECT,INSERT,UPDATE ON analysis_runs,element_checks,analysis_result_evidence,finding_candidates,
      finding_element_links,finding_requirement_links,finding_criterion_links,finding_evidence_links,
      analysis_run_stages,retrieval_chunks,chunk_evidence_links,evidence_embeddings,coverage_records,
      coverage_inspections,grounding_evaluations,evidence_support_bindings,numeric_links,routing_decisions,
      provider_call_records,semantic_batch_members,routing_call_links,finding_coverage_links TO align_worker;
    REVOKE DELETE ON analysis_run_stages,retrieval_chunks,chunk_evidence_links,evidence_embeddings,
      coverage_records,coverage_inspections,grounding_evaluations,evidence_support_bindings,numeric_links,
      routing_decisions,provider_call_records,semantic_batch_members,routing_call_links,finding_coverage_links FROM align_worker;
    GRANT SELECT,INSERT,UPDATE,DELETE ON analysis_run_stages,retrieval_chunks,chunk_evidence_links,
      evidence_embeddings,coverage_records,coverage_inspections,grounding_evaluations,evidence_support_bindings,
      numeric_links,routing_decisions,provider_call_records,semantic_batch_members,routing_call_links,finding_coverage_links TO align_api;
    GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO align_api,align_worker;
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE SELECT,INSERT,UPDATE,DELETE ON analysis_run_stages,retrieval_chunks,chunk_evidence_links,
      evidence_embeddings,coverage_records,coverage_inspections,grounding_evaluations,evidence_support_bindings,
      numeric_links,routing_decisions,provider_call_records,semantic_batch_members,routing_call_links,finding_coverage_links FROM align_api;
    REVOKE SELECT ON courses,assignments,specification_versions,check_elements,
      element_requirement_links,requirements,criteria,numeric_policy_versions FROM align_worker;
    REVOKE SELECT,INSERT,UPDATE ON analysis_run_stages,retrieval_chunks,chunk_evidence_links,
      evidence_embeddings,coverage_records,coverage_inspections,grounding_evaluations,evidence_support_bindings,
      numeric_links,routing_decisions,provider_call_records,semantic_batch_members,routing_call_links,finding_coverage_links FROM align_worker;
    DROP TABLE IF EXISTS finding_coverage_links,routing_call_links,semantic_batch_members,provider_call_records,routing_decisions,numeric_links,
      evidence_support_bindings,grounding_evaluations,coverage_inspections,coverage_records,
      evidence_embeddings,chunk_evidence_links,retrieval_chunks,analysis_run_stages CASCADE;
    DROP INDEX IF EXISTS uq_analysis_runs_job;
    ALTER TABLE analysis_runs DROP COLUMN IF EXISTS completion_kind;
    ALTER TABLE analysis_runs DROP COLUMN IF EXISTS usage_snapshot_json;
    ALTER TABLE analysis_runs DROP COLUMN IF EXISTS stage_snapshot_json;
    ALTER TABLE analysis_runs DROP COLUMN IF EXISTS manifest_snapshot_json;
    ALTER TABLE analysis_runs DROP COLUMN IF EXISTS job_id;
    """)
