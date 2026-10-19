"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

type LoginResponse = { user: { must_change_password: boolean }; csrf_token: string };

export function LoginForm() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          email: String(form.get("email") ?? ""),
          password: String(form.get("password") ?? ""),
        }),
      });
      if (!response.ok) {
        setError("Email atau kata sandi tidak sesuai. Periksa kembali lalu coba lagi.");
        return;
      }
      const result = (await response.json()) as LoginResponse;
      if (result.user.must_change_password) router.replace("/change-password");
      else router.replace("/");
      router.refresh();
    } catch {
      setError("Layanan sementara tidak tersedia. Coba kembali beberapa saat lagi.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form-stack" onSubmit={submit}>
      <label className="field">
        <span>Email</span>
        <input autoComplete="username" name="email" type="email" required maxLength={320} />
      </label>
      <label className="field">
        <span>Kata sandi</span>
        <input autoComplete="current-password" name="password" type="password" required maxLength={128} />
      </label>
      {error ? (
        <p className="form-error" role="alert" aria-live="polite">
          {error}
        </p>
      ) : null}
      <button className="button button-primary button-full" type="submit" disabled={busy}>
        {busy ? "Memeriksa…" : "Masuk"}
      </button>
    </form>
  );
}

