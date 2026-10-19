import { redirect } from "next/navigation";
import Link from "next/link";

import { LoginForm } from "@/components/LoginForm";
import { getProfile } from "@/lib/api";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ changed?: string }> }) {
  const [profile, query] = await Promise.all([getProfile(), searchParams]);
  if (profile.kind === "ok") {
    redirect(profile.data.user.must_change_password ? "/change-password" : "/");
  }
  return (
    <main className="centered-page">
      <section className="auth-card" aria-labelledby="login-title">
        <Link className="brand brand-static" href="/" aria-label="ALIGN">
          <span className="brand-mark" aria-hidden="true">A</span><span>ALIGN</span>
        </Link>
        <p className="eyebrow">RUANG KERJA AKADEMIK</p>
        <h1 id="login-title">Masuk ke ALIGN</h1>
        <p className="intro-copy">Gunakan akun yang diberikan oleh administrator.</p>
        {query.changed === "1" ? <p className="notice notice-success" role="status">Kata sandi telah diperbarui. Masuk kembali.</p> : null}
        <LoginForm />
        <p className="auth-note">Akun demo bersifat sintetis dan hanya tersedia di lingkungan development.</p>
      </section>
    </main>
  );
}

