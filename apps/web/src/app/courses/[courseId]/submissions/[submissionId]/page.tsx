import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { getCourse, getProfile, getStaffSubmission } from "@/lib/api";
import { LecturerSubmissionReview } from "@/components/LecturerSubmissionReview";
import { LogoutButton } from "@/components/LogoutButton";
import type { QueueQuery } from "@/components/SubmissionQueue";

export default async function SubmissionReviewPage({ params, searchParams }: {
  params: Promise<{ courseId: string; submissionId: string }>;
  searchParams: Promise<QueueQuery>;
}) {
  const { courseId, submissionId } = await params;
  const query = await searchParams;
  const profile = await getProfile();
  if (profile.kind === "unauthenticated") redirect("/login");
  if (profile.kind !== "ok") return <main className="centered-page"><p role="alert">Akun belum dapat dimuat. Coba muat ulang.</p></main>;
  if (profile.data.user.must_change_password) redirect("/change-password");
  if (!profile.data.memberships.some((item) => item.course_id === courseId && item.role === "lecturer")) notFound();
  const [course, submission] = await Promise.all([getCourse(courseId), getStaffSubmission(submissionId)]);
  if (course.kind === "unauthenticated" || submission.kind === "unauthenticated") redirect("/login");
  if (course.kind !== "ok" || submission.kind !== "ok") return <main className="centered-page"><section className="auth-card"><h1>Pengumpulan belum dapat dibuka</h1><p role="alert">Pengumpulan tidak tersedia atau akses Anda telah berubah.</p><Link href={`/courses/${courseId}?tab=submissions`}>Kembali ke pengumpulan</Link></section></main>;
  if (submission.data.course_id !== courseId) notFound();
  const back = new URLSearchParams({ tab: "submissions", assignment: submission.data.assignment_id });
  for (const key of ["q", "review", "publication", "cursor"] as const) if (typeof query[key] === "string") back.set(key, query[key]);
  return <main className="page-shell">
    <header className="topbar"><Link className="brand" href="/"><span className="brand-mark" aria-hidden="true">A</span>ALIGN</Link><div className="topbar-actions"><span className="user-name">{profile.data.user.display_name}</span><LogoutButton csrfToken={profile.data.csrf_token} /></div></header>
    <div className="review-page-shell"><nav aria-label="Lokasi halaman"><Link href={`/courses/${courseId}?${back}`}>← Kembali ke pengumpulan</Link><span> · {course.data.code} · {course.data.title}</span></nav>
      <header className="review-page-heading"><div><h1>{submission.data.owner.label ?? "Pekerjaan mahasiswa"}</h1><p>{submission.data.assignment_title}</p></div><div><p>Pengumpulan versi {submission.data.active_final_version?.version_no ?? "—"}</p>{submission.data.active_final_version?.is_late && <span className="state-pill">Terlambat</span>}</div></header>
      <LecturerSubmissionReview submission={submission.data} csrfToken={profile.data.csrf_token} />
    </div>
  </main>;
}
