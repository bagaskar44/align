from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from align_api.config import get_settings
from align_api.models import FeedbackDraft, Job, JobStage, ReviewVersion
from align_api.reviews import _feedback_hash, _generate_model_feedback
from align_worker.database import SessionFactory
from align_worker.finance import WorkerPaidCallLedger


def execute_feedback_job(
    job_id: UUID, expected_fence: int, expected_fingerprint: str
) -> dict[str, Any]:
    """Run a fenced feedback draft job and keep human review state immutable."""
    with SessionFactory.begin() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or job.type != "feedback_draft":
            return {"status": "missing"}
        if job.lease_fence != expected_fence or job.input_fingerprint != expected_fingerprint:
            return {"status": "stale", "fenced": True}
        if job.status != "running" or job.stage != "draft_feedback":
            return {"status": job.status, "duplicate": True}

        stage = db.scalar(
            select(JobStage)
            .where(JobStage.job_id == job.id, JobStage.stage_name == "draft_feedback")
            .with_for_update()
        )
        draft = db.scalar(
            select(FeedbackDraft).where(FeedbackDraft.id == job.subject_id).with_for_update()
        )
        review_id = job.scope_json.get("review_version_id")
        review = db.get(ReviewVersion, UUID(review_id)) if isinstance(review_id, str) else None
        failure_code: str | None
        if stage is None or draft is None or review is None or draft.review_version_id != review.id:
            failure_code = "FEEDBACK_SCOPE_INVALID"
            generated_text = None
            generated_blocks = None
        elif review.state != "complete" or review.content_hash != draft.source_review_hash:
            failure_code = "FEEDBACK_SOURCE_STALE"
            generated_text = None
            generated_blocks = None
        else:
            generated_text, generated_blocks, failure_code = _generate_model_feedback(
                db,
                review,
                draft,
                job,
                list(draft.included_review_finding_ids_json),
                dict(draft.exclusion_reasons_json),
                paid_call_ledger=WorkerPaidCallLedger(enabled=get_settings().paid_calls_enabled),
            )

        if draft is not None and review is not None:
            if generated_text is not None and generated_blocks is not None:
                draft.generation_mode = "model"
                draft.generation_status = "completed"
                draft.generation_failure_code = None
                draft.body_markdown = generated_text
                draft.blocks_json = generated_blocks
                completion_kind = "feedback_draft_completed"
            else:
                draft.generation_mode = "template"
                draft.generation_status = "fallback"
                draft.generation_failure_code = (failure_code or "provider_abstained")[:80]
                completion_kind = "fallback_template"
            draft.content_hash = _feedback_hash(
                draft.generation_mode,
                draft.source_review_hash,
                draft.body_markdown,
                draft.blocks_json,
                list(draft.included_review_finding_ids_json),
                dict(draft.exclusion_reasons_json),
            )

        job.status = "ready_for_review"
        job.lease_until = None
        job.completion_kind = completion_kind if draft is not None and review is not None else "fallback_template"
        job.last_error_code = failure_code
        if stage is not None:
            stage.status = "succeeded"
            stage.failure_code = failure_code
            stage.finished_at = datetime.now(UTC)
            stage.output_refs_json = {
                "generation_status": draft.generation_status if draft is not None else "fallback",
                "failure_code": failure_code,
            }
        return {
            "status": "ready_for_review",
            "job_id": str(job.id),
            "generation_status": draft.generation_status if draft is not None else "fallback",
            "failure_code": failure_code,
        }
