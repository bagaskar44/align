"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

export function PasswordChangeForm({ csrfToken }: { csrfToken: string }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setBusy(true);
    const form = new FormData(event.currentTarget);
    const currentPassword = String(form.get("current_password") ?? "");
    const newPassword = String(form.get("new_password") ?? "");
    if (newPassword !== String(form.get("confirm_password") ?? "")) {
      setMessage("Kata sandi baru belum sama.");
      setBusy(false);
      return;
    }
    try {
      const response = await fetch("/api/v1/me/password", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        credentials: "same-origin",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      if (!response.ok) {
        setMessage("Perubahan kata sandi gagal. Pastikan kata sandi saat ini benar dan kata sandi baru memenuhi ketentuan.");
        return;
      }
      router.replace("/login?changed=1");
      router.refresh();
    } catch {
      setMessage("Layanan sementara tidak tersedia. Coba kembali beberapa saat lagi.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form-stack" onSubmit={submit}>
      <label className="field">
        <span>Kata sandi sementara</span>
        <input autoComplete="current-password" name="current_password" type="password" required maxLength={128} />
      </label>
      <label className="field">
        <span>Kata sandi baru</span>
        <input autoComplete="new-password" name="new_password" type="password" required minLength={12} maxLength={128} />
        <small>Gunakan 12 sampai 128 karakter.</small>
      </label>
      <label className="field">
        <span>Ulangi kata sandi baru</span>
        <input autoComplete="new-password" name="confirm_password" type="password" required minLength={12} maxLength={128} />
      </label>
      {message ? <p className="form-error" role="alert" aria-live="polite">{message}</p> : null}
      <button className="button button-primary button-full" type="submit" disabled={busy}>
        {busy ? "Menyimpan…" : "Simpan kata sandi"}
      </button>
    </form>
  );
}

