import Link from "next/link";
import { redirect } from "next/navigation";

import { LogoutButton } from "@/components/LogoutButton";
import { getProfile, getPublishedResult } from "@/lib/api";

type PublicResult = {
  publication_id?: string;
  status: "released" | "not_published" | "withdrawn" | "purged";
  message?: string;
  submission_version?: number | null;
  assignment_version?: number;
  published_at?: string;
  feedback_markdown?: string;
  limitations?: Array<{ message?: string }>;
  feedback_blocks?: Array<{ message: string; next_action?: string | null; limitation?: string | null }>;
  confirmed_findings?: Array<{ category: string; criterion_title: string; claim: string; evidence: Array<{ file_name: string; content: Record<string, unknown> }> }>;
  source_labels?: string[];
  source?: { origin?: string; origin_label?: string; submission_version?: number | null; finalized_at?: string };
};

export default async function FeedbackPage({ params }: { params: Promise<{ submissionId: string }> }) {
  const { submissionId } = await params;
  const [profile, result] = await Promise.all([getProfile(), getPublishedResult(submissionId)]);
  if (profile.kind === "unauthenticated" || result.kind === "unauthenticated") redirect("/login");
  if (profile.kind === "unavailable") return <PageError message="Akun tidak dapat dimuat. Muat ulang halaman beberapa saat lagi." />;
  if (profile.data.user.must_change_password) redirect("/change-password");
  if (result.kind === "unavailable") return <PageError message="Hasil tugas tidak dapat dimuat atau aksesnya sudah berakhir." />;
  const feedback = result.data as PublicResult;

  return (
    <main className="page-shell">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="ALIGN, beranda"><span className="brand-mark" aria-hidden="true">A</span><span>ALIGN</span></Link>
        <div className="topbar-actions"><Link className="button button-quiet" href="/">Mata kuliah</Link><span className="user-name">{profile.data.user.display_name}</span><LogoutButton csrfToken={profile.data.csrf_token} /></div>
      </header>
      <div className="content-shell feedback-shell">
        <section className="page-intro" aria-labelledby="feedback-title">
          <p className="eyebrow">HASIL TUGAS · VERSI {feedback.submission_version ?? "—"}</p>
          <h1 id="feedback-title">Feedback dosen</h1>
          {feedback.status === "released" && feedback.source?.origin_label ? <p className="feedback-source-label">{feedback.source.origin_label}</p> : null}
          {feedback.status === "released" && feedback.published_at ? <p className="intro-copy">Diterbitkan {new Date(feedback.published_at).toLocaleString("id-ID")}</p> : null}
        </section>
        {feedback.status !== "released" ? (
          <section className="feedback-state" role="status">
            <h2>{feedback.status === "withdrawn" ? "Feedback ditarik" : feedback.status === "purged" ? "Sumber tidak tersedia" : "Feedback belum diterbitkan"}</h2>
            <p>{feedback.message ?? "Belum ada feedback yang dapat ditampilkan."}</p>
          </section>
        ) : (
          <div className="feedback-content">
            {feedback.feedback_markdown ? <section className="feedback-card"><h2>Catatan dosen</h2><div className="feedback-markdown">{feedback.feedback_markdown}</div></section> : null}
            {feedback.feedback_blocks?.length ? <section className="feedback-card"><h2>Langkah berikutnya</h2><ul>{feedback.feedback_blocks.map((block, index) => <li key={`${index}-${block.message}`}><p>{block.message}</p>{block.next_action ? <p><strong>Tindakan:</strong> {block.next_action}</p> : null}{block.limitation ? <p className="muted-copy">Batas: {block.limitation}</p> : null}</li>)}</ul></section> : null}
            {feedback.confirmed_findings?.length ? <section className="feedback-card"><h2>Temuan terkonfirmasi</h2>{feedback.confirmed_findings.map((finding, index) => <article className="feedback-finding" key={`${finding.category}-${index}`}><p className="eyebrow">{finding.category} · {finding.criterion_title}</p><p>{finding.claim}</p>{finding.evidence.length ? <ul>{finding.evidence.map((evidence, evidenceIndex) => <li key={`${evidence.file_name}-${evidenceIndex}`}><strong>{evidence.file_name}</strong>{typeof evidence.content.text === "string" ? <p>{evidence.content.text}</p> : null}</li>)}</ul> : null}</article>)}</section> : null}
            {feedback.source_labels?.length ? <p className="feedback-source-label">Sumber: {feedback.source_labels.join(", ")}</p> : null}
            {feedback.limitations?.length ? <section className="feedback-limitations"><h2>Batasan</h2><ul>{feedback.limitations.map((item, index) => <li key={`${item.message ?? "batasan"}-${index}`}>{item.message ?? "Sebagian informasi tidak dapat dinilai."}</li>)}</ul></section> : null}
          </div>
        )}
      </div>
    </main>
  );
}

function PageError({ message }: { message: string }) {
  return <main className="centered-page"><section className="auth-card" role="alert"><h1>Hasil belum dapat dibuka</h1><p className="intro-copy">{message}</p><Link className="button button-primary button-full" href="/">Kembali ke ALIGN</Link></section></main>;
}
