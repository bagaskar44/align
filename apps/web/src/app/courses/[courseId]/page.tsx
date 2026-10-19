import Link from "next/link";
import { redirect } from "next/navigation";

import { ContextWorkspace } from "@/components/ContextWorkspace";
import { LogoutButton } from "@/components/LogoutButton";
import { getContextVersions, getCourse, getProfile } from "@/lib/api";

export default async function CourseContextPage({ params }: { params: Promise<{ courseId: string }> }) {
  const { courseId } = await params;
  const profileResult = await getProfile();
  if (profileResult.kind === "unauthenticated") redirect("/login");
  if (profileResult.kind === "unavailable") return <PageError />;
  if (profileResult.data.user.must_change_password) redirect("/change-password");
  const membership = profileResult.data.memberships.find((item) => item.course_id === courseId && item.role === "lecturer");
  if (!membership) {
    return <main className="centered-page"><section className="auth-card"><Link href="/">← Kembali ke mata kuliah</Link><h1>Ruang dosen</h1><p className="intro-copy">Akun ini tidak memiliki keanggotaan dosen aktif pada mata kuliah tersebut.</p></section></main>;
  }
  const [courseResult, versionsResult] = await Promise.all([getCourse(courseId), getContextVersions(courseId)]);
  if (courseResult.kind === "unauthenticated" || versionsResult.kind === "unauthenticated") redirect("/login");
  if (courseResult.kind === "unavailable" || versionsResult.kind === "unavailable") return <PageError />;

  return (
    <main className="page-shell">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="ALIGN, beranda"><span className="brand-mark" aria-hidden="true">A</span><span>ALIGN</span></Link>
        <div className="topbar-actions"><Link className="button button-quiet" href="/">Mata kuliah</Link><span className="user-name">{profileResult.data.user.display_name}</span><LogoutButton csrfToken={profileResult.data.csrf_token} /></div>
      </header>
      <div className="content-shell">
        <section className="page-intro" aria-labelledby="course-title"><p className="eyebrow">KONTEKS KURIKULUM · {courseResult.data.code}</p><h1 id="course-title">{courseResult.data.title}</h1><p className="intro-copy">Kelola versi konteks, sumber normatif, usulan parser, dan keputusan akademik.</p></section>
        <ContextWorkspace course={courseResult.data} csrfToken={profileResult.data.csrf_token} initialVersions={versionsResult.data.items} />
      </div>
    </main>
  );
}

function PageError() {
  return <main className="centered-page"><section className="auth-card" role="alert"><h1>Layanan belum siap</h1><p className="intro-copy">Konteks mata kuliah tidak dapat dimuat. Muat ulang halaman beberapa saat lagi.</p><Link className="button button-primary button-full" href="/">Kembali</Link></section></main>;
}
