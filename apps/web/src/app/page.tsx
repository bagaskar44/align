import { redirect } from "next/navigation";
import Link from "next/link";

import { LogoutButton } from "@/components/LogoutButton";
import { SimulationBanner } from "@/components/SimulationBanner";
import { getCourses, getProfile, getProviderMode } from "@/lib/api";

export default async function HomePage() {
  const profileResult = await getProfile();
  if (profileResult.kind === "unauthenticated") redirect("/login");
  if (profileResult.kind === "unavailable") return <ServiceError />;
  if (profileResult.data.user.must_change_password) redirect("/change-password");

  const [coursesResult, providerMode] = await Promise.all([getCourses(), getProviderMode()]);
  if (coursesResult.kind === "unavailable") return <ServiceError />;
  if (coursesResult.kind === "unauthenticated") redirect("/login");

  return (
    <main className="page-shell">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="ALIGN, beranda">
          <span className="brand-mark" aria-hidden="true">A</span>
          <span>ALIGN</span>
        </Link>
        <div className="topbar-actions">
          <span className="user-name">{profileResult.data.user.display_name}</span>
          <LogoutButton csrfToken={profileResult.data.csrf_token} />
        </div>
      </header>

      <div className="content-shell">
        <SimulationBanner mode={providerMode} />
        {profileResult.data.user.global_role === "admin" && (
          <p className="admin-shortcut"><Link className="button button-quiet" href="/admin">Kelola akun, mata kuliah, dan roster</Link></p>
        )}
        <section className="page-intro" aria-labelledby="page-title">
          <p className="eyebrow">RUANG KERJA</p>
          <h1 id="page-title">Mata kuliah</h1>
          <p className="intro-copy">Pilih mata kuliah untuk melihat ruang kerja yang tersedia bagi akunmu.</p>
        </section>

        {coursesResult.data.items.length === 0 ? (
          <section className="empty-card" aria-labelledby="empty-title">
            <div className="empty-icon" aria-hidden="true">—</div>
            <h2 id="empty-title">Belum ada mata kuliah</h2>
              <p>{profileResult.data.user.global_role === "admin" ? "Belum ada mata kuliah. Buat mata kuliah dari area admin." : "Akun ini belum memiliki keanggotaan mata kuliah aktif."}</p>
          </section>
        ) : (
          <ul className="course-list" aria-label="Daftar mata kuliah">
            {coursesResult.data.items.map((course) => (
              <li key={course.id}>
                <Link className="course-card course-card-link" href={`/courses/${course.id}`}>
                  <div className="course-code">{course.code}</div>
                  <h2>{course.title}</h2>
                  <span className="course-open-label">Buka konteks mata kuliah →</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
        <footer className="page-footer">ALIGN · ruang kerja akademik</footer>
      </div>
    </main>
  );
}

function ServiceError() {
  return (
    <main className="centered-page">
      <section className="auth-card" role="alert">
        <div className="brand brand-static"><span className="brand-mark" aria-hidden="true">A</span><span>ALIGN</span></div>
        <h1>Layanan belum siap</h1>
        <p className="intro-copy">Database, penyimpanan, atau antrean sementara tidak dapat dijangkau. Muat ulang halaman beberapa saat lagi.</p>
        <Link className="button button-primary button-full" href="/">Coba lagi</Link>
      </section>
    </main>
  );
}

