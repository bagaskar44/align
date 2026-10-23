import Link from "next/link";
import { redirect } from "next/navigation";

import { ContextWorkspace } from "@/components/ContextWorkspace";
import { AssignmentWorkspace, StudentAssignments } from "@/components/AssignmentWorkspace";
import { SubmissionWorkspace } from "@/components/SubmissionWorkspace";
import { SubmissionQueue, type QueueQuery } from "@/components/SubmissionQueue";
import { LogoutButton } from "@/components/LogoutButton";
import { CourseWorkspaceTabs } from "@/components/CourseWorkspaceTabs";
import { getContextVersions, getCourse, getCourseAssignments, getProfile, getProviderMode } from "@/lib/api";

export default async function CourseContextPage({ params, searchParams }: { params: Promise<{ courseId: string }>; searchParams: Promise<QueueQuery & { tab?: string }> }) {
  const { courseId } = await params;
  const query = await searchParams;
  const profileResult = await getProfile();
  if (profileResult.kind === "unauthenticated") redirect("/login");
  if (profileResult.kind === "unavailable") return <PageError message="Akun tidak dapat dimuat. Muat ulang halaman beberapa saat lagi." />;
  if (profileResult.data.user.must_change_password) redirect("/change-password");
  const membership = profileResult.data.memberships.find((item) => item.course_id === courseId);
  if (!membership) {
    return <main className="centered-page"><section className="auth-card"><Link href="/">← Kembali ke mata kuliah</Link><h1>Ruang mata kuliah</h1><p className="intro-copy">Akun ini tidak memiliki keanggotaan aktif pada mata kuliah tersebut.</p></section></main>;
  }
  const [courseResult, assignmentsResult, versionsResult, providerMode] = await Promise.all([
    getCourse(courseId),
    getCourseAssignments(courseId),
    membership.role === "lecturer" ? getContextVersions(courseId) : Promise.resolve(null),
    membership.role === "lecturer" ? getProviderMode() : Promise.resolve("unknown" as const),
  ]);
  if (courseResult.kind === "unauthenticated" || assignmentsResult.kind === "unauthenticated" || versionsResult?.kind === "unauthenticated") redirect("/login");
  if (courseResult.kind === "unavailable") return <PageError message="Data mata kuliah tidak dapat dimuat. Muat ulang halaman beberapa saat lagi." />;

  return (
    <main className="page-shell">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="ALIGN, beranda"><span className="brand-mark" aria-hidden="true">A</span><span>ALIGN</span></Link>
        <div className="topbar-actions"><Link className="button button-quiet" href="/">Mata kuliah</Link><span className="user-name">{profileResult.data.user.display_name}</span><LogoutButton csrfToken={profileResult.data.csrf_token} /></div>
      </header>
      <div className="content-shell">
        <section className="page-intro course-page-intro" aria-labelledby="course-title">
          <p className="eyebrow">{membership.role === "lecturer" ? "RUANG DOSEN" : "RUANG MAHASISWA"} · {courseResult.data.code}</p>
          <h1 id="course-title">{courseResult.data.title}</h1>
          <p className="intro-copy">{membership.role === "lecturer" ? "Kelola materi, tugas, dan pengumpulan untuk mata kuliah ini." : "Lihat tugas dan kelola pengumpulanmu."}</p>
        </section>
        {membership.role === "lecturer" && versionsResult?.kind === "ok" ? (
          <CourseWorkspaceTabs initialPanel={query.tab} panels={[
            {
              id: "context",
              label: "Konteks",
              content: <ContextWorkspace course={courseResult.data} csrfToken={profileResult.data.csrf_token} initialVersions={versionsResult.data.items} initialCursor={versionsResult.data.next_cursor} />,
            },
            {
              id: "assignments",
              label: "Tugas",
              content: assignmentsResult.kind === "ok"
                ? <AssignmentWorkspace course={courseResult.data} csrfToken={profileResult.data.csrf_token} approvedContexts={versionsResult.data.items} initialAssignments={assignmentsResult.data.items} providerMode={providerMode} />
                : <WorkspaceUnavailable title="Daftar tugas belum dapat dimuat" />,
            },
            {
              id: "submissions",
              label: "Pengumpulan",
              content: assignmentsResult.kind === "ok"
                ? <SubmissionQueue courseId={courseId} initialQuery={query} assignments={assignmentsResult.data.items} />
                : <WorkspaceUnavailable title="Antrean pengumpulan belum dapat dimuat" />,
            },
          ]} />
        ) : membership.role === "lecturer" ? (
          <CourseWorkspaceTabs initialPanel={query.tab} panels={[
            { id: "context", label: "Konteks", content: <WorkspaceUnavailable title="Konteks mata kuliah belum dapat dimuat" /> },
            {
              id: "assignments",
              label: "Tugas",
              content: assignmentsResult.kind === "ok"
                ? <AssignmentWorkspace course={courseResult.data} csrfToken={profileResult.data.csrf_token} approvedContexts={[]} contextsUnavailable initialAssignments={assignmentsResult.data.items} providerMode={providerMode} />
                : <WorkspaceUnavailable title="Daftar tugas belum dapat dimuat" />,
            },
            {
              id: "submissions",
              label: "Pengumpulan",
              content: assignmentsResult.kind === "ok"
                ? <SubmissionQueue courseId={courseId} initialQuery={query} assignments={assignmentsResult.data.items} />
                : <WorkspaceUnavailable title="Antrean pengumpulan belum dapat dimuat" />,
            },
          ]} />
        ) : assignmentsResult.kind === "ok" ? (
          <CourseWorkspaceTabs initialPanel={query.tab} panels={[
            {
              id: "assignments",
              label: "Tugas",
              content: <StudentAssignments courseId={courseId} csrfToken={profileResult.data.csrf_token} initialAssignments={assignmentsResult.data.items} />,
            },
            {
              id: "submissions",
              label: "Pengumpulan",
              content: <SubmissionWorkspace assignments={assignmentsResult.data.items} csrfToken={profileResult.data.csrf_token} />,
            },
          ]} />
        ) : (
          <CourseWorkspaceTabs initialPanel={query.tab} panels={[
            { id: "assignments", label: "Tugas", content: <WorkspaceUnavailable title="Daftar tugas belum dapat dimuat" /> },
            { id: "submissions", label: "Pengumpulan", content: <WorkspaceUnavailable title="Pengumpulan belum dapat dimuat tanpa data tugas" /> },
          ]} />
        )}
      </div>
    </main>
  );
}

function PageError({ message }: { message: string }) {
  return <main className="centered-page"><section className="auth-card" role="alert"><h1>Layanan belum siap</h1><p className="intro-copy">{message}</p><Link className="button button-primary button-full" href="/">Kembali</Link></section></main>;
}

function WorkspaceUnavailable({ title }: { title: string }) {
  return <section className="workspace-unavailable" role="status"><h2>{title}</h2><p>Bagian lain tetap bisa dibuka. Muat ulang halaman untuk mencoba lagi.</p></section>;
}
