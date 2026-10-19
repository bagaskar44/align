import Link from "next/link";
import { redirect } from "next/navigation";

import { AdminWorkspace } from "@/components/AdminWorkspace";
import { LogoutButton } from "@/components/LogoutButton";
import { getAdminCourses, getAdminUsers, getProfile } from "@/lib/api";

export default async function AdminPage() {
  const profileResult = await getProfile();
  if (profileResult.kind === "unauthenticated") redirect("/login");
  if (profileResult.kind === "unavailable") return <PageError />;
  if (profileResult.data.user.must_change_password) redirect("/change-password");
  if (profileResult.data.user.global_role !== "admin") redirect("/");
  const [usersResult, coursesResult] = await Promise.all([getAdminUsers(), getAdminCourses()]);
  if (usersResult.kind === "unauthenticated" || coursesResult.kind === "unauthenticated") redirect("/login");
  if (usersResult.kind === "unavailable" || coursesResult.kind === "unavailable") return <PageError />;
  return (
    <main className="page-shell">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="ALIGN, beranda"><span className="brand-mark" aria-hidden="true">A</span><span>ALIGN</span></Link>
        <div className="topbar-actions"><Link className="button button-quiet" href="/">Ruang kerja</Link><span className="user-name">{profileResult.data.user.display_name}</span><LogoutButton csrfToken={profileResult.data.csrf_token} /></div>
      </header>
      <div className="content-shell">
        <section className="page-intro"><p className="eyebrow">ADMINISTRASI</p><h1>Identitas dan mata kuliah</h1><p className="intro-copy">Provisioning akun dengan sandi sementara, pengelolaan katalog, dan pembaruan roster mata kuliah.</p></section>
        <AdminWorkspace csrfToken={profileResult.data.csrf_token} initialUsers={usersResult.data.items} initialCourses={coursesResult.data.items} />
      </div>
    </main>
  );
}

function PageError() {
  return <main className="centered-page"><section className="auth-card" role="alert"><h1>Layanan belum siap</h1><p className="intro-copy">Area admin tidak dapat dimuat. Muat ulang halaman beberapa saat lagi.</p><Link className="button button-primary button-full" href="/">Kembali</Link></section></main>;
}
