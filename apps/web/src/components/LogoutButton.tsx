"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function LogoutButton({ csrfToken }: { csrfToken: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function logout() {
    setBusy(true);
    try {
      const response = await fetch("/api/v1/auth/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        credentials: "same-origin",
      });
      if (response.ok) {
        router.replace("/login");
        router.refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <button className="button button-quiet" type="button" onClick={logout} disabled={busy}>
      {busy ? "Keluar…" : "Keluar"}
    </button>
  );
}

