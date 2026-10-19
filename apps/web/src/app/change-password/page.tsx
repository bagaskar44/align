import { redirect } from "next/navigation";

import { PasswordChangeForm } from "@/components/PasswordChangeForm";
import { getProfile } from "@/lib/api";

export default async function ChangePasswordPage() {
  const result = await getProfile();
  if (result.kind === "unauthenticated") redirect("/login");
  if (result.kind === "unavailable") {
    return <main className="centered-page"><p role="alert">Layanan sementara tidak tersedia.</p></main>;
  }
  if (!result.data.user.must_change_password) redirect("/");

  return (
    <main className="centered-page">
      <section className="auth-card" aria-labelledby="change-password-title">
        <div className="brand brand-static"><span className="brand-mark" aria-hidden="true">A</span><span>ALIGN</span></div>
        <p className="eyebrow">LANGKAH PERTAMA</p>
        <h1 id="change-password-title">Buat kata sandi baru</h1>
        <p className="intro-copy">Perbarui kata sandi sementara sebelum melanjutkan.</p>
        <PasswordChangeForm csrfToken={result.data.csrf_token} />
      </section>
    </main>
  );
}

