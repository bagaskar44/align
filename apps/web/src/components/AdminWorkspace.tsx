"use client";

import { useState, type FormEvent } from "react";
import type { ApiMethod, ApiRequestPath } from "@/lib/api-contract.generated";

type AdminUser = { id: string; email: string; display_name: string; global_role: "admin" | "member"; active: boolean; must_change_password: boolean; revision: number };
type Course = { id: string; code: string; title: string; active: boolean; revision: number };
type Enrollment = { user_id: string; display_name: string; role: "lecturer" | "student"; active: boolean };
type Props = { csrfToken: string; initialUsers: AdminUser[]; initialCourses: Course[] };

async function apiRequest<T>(path: ApiRequestPath, csrfToken: string, options: { method: ApiMethod; body?: unknown; idempotency?: boolean }): Promise<T> {
  const headers = new Headers({ "Content-Type": "application/json", "X-CSRF-Token": csrfToken });
  if (options.idempotency) headers.set("Idempotency-Key", crypto.randomUUID());
  const response = await fetch(`/api/v1${path}`, {
    method: options.method,
    headers,
    ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
    cache: "no-store",
  });
  const payload = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) throw new Error(payload.detail ?? `Permintaan gagal (${response.status}).`);
  return payload as T;
}

export function AdminWorkspace({ csrfToken, initialUsers, initialCourses }: Props) {
  const [users, setUsers] = useState(initialUsers);
  const [courses, setCourses] = useState(initialCourses);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [newRole, setNewRole] = useState<"admin" | "member">("member");
  const [newCourseCode, setNewCourseCode] = useState("");
  const [newCourseTitle, setNewCourseTitle] = useState("");
  const [selectedCourseId, setSelectedCourseId] = useState("");
  const [roster, setRoster] = useState<Enrollment[]>([]);
  const [rosterRevision, setRosterRevision] = useState(0);
  const [rosterChanges, setRosterChanges] = useState<Record<string, { active: boolean; role: "lecturer" | "student" }>>({});
  const [courseChanges, setCourseChanges] = useState<Record<string, { code: string; title: string; active: boolean }>>({});
  const [credential, setCredential] = useState<{ email: string; password: string } | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload() {
    const [userResponse, courseResponse] = await Promise.all([
      fetch("/api/v1/admin/users", { cache: "no-store" }),
      fetch("/api/v1/admin/courses", { cache: "no-store" }),
    ]);
    if (!userResponse.ok || !courseResponse.ok) throw new Error("Daftar admin tidak dapat dimuat ulang.");
    const userPayload = (await userResponse.json()) as { items: AdminUser[] };
    const coursePayload = (await courseResponse.json()) as { items: Course[] };
    setUsers(userPayload.items);
    setCourses(coursePayload.items);
    setCourseChanges(Object.fromEntries(coursePayload.items.map((course) => [course.id, { code: course.code, title: course.title, active: course.active }])));
  }

  async function runAction(action: () => Promise<void>, success: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(success);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Permintaan tidak berhasil.");
    } finally {
      setBusy(false);
    }
  }

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const result = await apiRequest<{ user: AdminUser; temporary_password: string }>("/admin/users", csrfToken, {
        method: "POST", idempotency: true, body: { email, display_name: displayName, role: newRole },
      });
      setCredential({ email: result.user.email, password: result.temporary_password });
      setEmail("");
      setDisplayName("");
      await reload();
    }, "Akun dibuat. Berikan kata sandi sementara secara aman kepada pemilik akun.");
  }

  async function createCourse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      await apiRequest<Course>("/courses", csrfToken, { method: "POST", body: { code: newCourseCode, title: newCourseTitle } });
      setNewCourseCode("");
      setNewCourseTitle("");
      await reload();
    }, "Mata kuliah dibuat.");
  }

  async function updateUser(user: AdminUser, change: { active?: boolean; global_role?: "admin" | "member" }) {
    await runAction(async () => {
      await apiRequest(`/admin/users/${user.id}`, csrfToken, {
        method: "PATCH", body: { expected_revision: user.revision, ...change },
      });
      await reload();
    }, "Akun diperbarui.");
  }

  async function resetPassword(user: AdminUser) {
    await runAction(async () => {
      const result = await apiRequest<{ user: AdminUser; temporary_password: string }>(`/admin/users/${user.id}/reset-password`, csrfToken, {
        method: "POST", idempotency: true, body: { expected_revision: user.revision },
      });
      setCredential({ email: user.email, password: result.temporary_password });
      await reload();
    }, "Kata sandi sementara dibuat. Berikan secara aman kepada pemilik akun.");
  }

  async function saveCourse(course: Course) {
    const changes = courseChanges[course.id];
    if (!changes) return;
    await runAction(async () => {
      await apiRequest(`/admin/courses/${course.id}`, csrfToken, {
        method: "PATCH", body: { expected_revision: course.revision, ...changes },
      });
      await reload();
    }, "Mata kuliah diperbarui.");
  }

  async function selectCourse(courseId: string) {
    setSelectedCourseId(courseId);
    setRoster([]);
    setRosterChanges({});
    if (!courseId) return;
    try {
      const response = await fetch(`/api/v1/courses/${courseId}/enrollments`, { cache: "no-store" });
      if (!response.ok) throw new Error("Roster tidak dapat dimuat.");
      const data = (await response.json()) as { revision?: number; items: Enrollment[] };
      const course = courses.find((item) => item.id === courseId);
      setRoster(data.items);
      setRosterRevision(data.revision ?? course?.revision ?? 1);
      setRosterChanges(Object.fromEntries(data.items.map((item) => [item.user_id, { active: item.active, role: item.role }] )));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Roster tidak dapat dimuat.");
    }
  }

  async function saveRoster() {
    const course = courses.find((item) => item.id === selectedCourseId);
    if (!course) return;
    const activeEnrollments = Object.entries(rosterChanges)
      .filter(([, change]) => change.active)
      .map(([user_id, change]) => ({ user_id, role: change.role, active: true }));
    await runAction(async () => {
      const result = await apiRequest<{ revision: number }>(`/courses/${course.id}/enrollments`, csrfToken, {
        method: "PUT", body: { expected_revision: rosterRevision, enrollments: activeEnrollments },
      });
      setRosterRevision(result.revision);
      await reload();
    }, "Roster diperbarui.");
  }

  return (
    <div className="admin-layout">
      <div className="admin-panels">
        <section className="panel" aria-labelledby="accounts-title">
          <p className="eyebrow">IDENTITAS</p><h2 id="accounts-title">Akun pengguna</h2>
          <form className="form-stack" onSubmit={createUser}>
            <div className="field-row"><label className="field">Nama<input required maxLength={160} value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label><label className="field">Email<input required type="email" maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} /></label></div>
            <label className="field">Peran global<select value={newRole} onChange={(event) => setNewRole(event.target.value as "admin" | "member")}><option value="member">Member</option><option value="admin">Admin</option></select></label>
            <button className="button button-primary" type="submit" disabled={busy}>Buat akun</button>
          </form>
          {credential && <div className="credential-box" role="status"><strong>Kata sandi sementara · {credential.email}</strong><code>{credential.password}</code><small>Kata sandi hanya ditampilkan pada respons pembuatan/reset ini. Pengguna wajib menggantinya saat masuk.</small></div>}
          <div className="admin-table-wrap"><table className="admin-table"><caption>Pengguna yang dapat dikelola</caption><thead><tr><th>Pengguna</th><th>Peran</th><th>Status</th><th>Tindakan</th></tr></thead><tbody>{users.map((user) => <tr key={user.id}>
            <td><strong>{user.display_name}</strong><small>{user.email}</small>{user.must_change_password && <small>Menunggu penggantian sandi</small>}</td>
            <td><select aria-label={`Peran ${user.email}`} value={user.global_role} disabled={busy} onChange={(event) => void updateUser(user, { global_role: event.target.value as "admin" | "member" })}><option value="member">Member</option><option value="admin">Admin</option></select></td>
            <td>{user.active ? "Aktif" : "Nonaktif"}</td>
            <td><div className="table-actions"><button className="text-button" type="button" disabled={busy} onClick={() => void updateUser(user, { active: !user.active })}>{user.active ? "Nonaktifkan" : "Aktifkan"}</button><button className="text-button" type="button" disabled={busy} onClick={() => void resetPassword(user)}>Reset sandi</button></div></td>
          </tr>)}</tbody></table></div>
        </section>

        <section className="panel" aria-labelledby="courses-title">
          <p className="eyebrow">MATA KULIAH</p><h2 id="courses-title">Katalog dan roster</h2>
          <form className="form-stack" onSubmit={createCourse}>
            <div className="field-row"><label className="field">Kode<input required maxLength={64} value={newCourseCode} onChange={(event) => setNewCourseCode(event.target.value)} /></label><label className="field">Nama mata kuliah<input required maxLength={240} value={newCourseTitle} onChange={(event) => setNewCourseTitle(event.target.value)} /></label></div>
            <button className="button button-primary" type="submit" disabled={busy}>Buat mata kuliah</button>
          </form>
          <div className="course-admin-list">{courses.map((course) => <article className="course-admin-card" key={course.id}>
            <div className="field-row"><label className="field">Kode<input value={courseChanges[course.id]?.code ?? course.code} maxLength={64} onChange={(event) => setCourseChanges({ ...courseChanges, [course.id]: { ...courseChanges[course.id], code: event.target.value, title: courseChanges[course.id]?.title ?? course.title, active: courseChanges[course.id]?.active ?? course.active } })} /></label><label className="field">Nama<input value={courseChanges[course.id]?.title ?? course.title} maxLength={240} onChange={(event) => setCourseChanges({ ...courseChanges, [course.id]: { ...courseChanges[course.id], code: courseChanges[course.id]?.code ?? course.code, title: event.target.value, active: courseChanges[course.id]?.active ?? course.active } })} /></label></div>
            <div className="course-admin-actions"><label className="check-field"><input type="checkbox" checked={courseChanges[course.id]?.active ?? course.active} onChange={(event) => setCourseChanges({ ...courseChanges, [course.id]: { ...courseChanges[course.id], code: courseChanges[course.id]?.code ?? course.code, title: courseChanges[course.id]?.title ?? course.title, active: event.target.checked } })} />Aktif</label><button className="button button-quiet" type="button" disabled={busy} onClick={() => void saveCourse(course)}>Simpan mata kuliah</button><button className="text-button" type="button" onClick={() => void selectCourse(course.id)}>Kelola roster</button></div>
          </article>)}</div>
          {selectedCourseId && <section className="subsection" aria-labelledby="roster-title"><h3 id="roster-title">Roster mata kuliah</h3><label className="field">Mata kuliah<select value={selectedCourseId} onChange={(event) => void selectCourse(event.target.value)}>{courses.map((course) => <option key={course.id} value={course.id}>{course.code} · {course.title}</option>)}</select></label><p className="muted-copy">Pilih dosen dan mahasiswa yang boleh mengakses ruang mata kuliah.</p>
            <div className="roster-list">{users.filter((user) => user.active).map((user) => {
              const change = rosterChanges[user.id] ?? { active: roster.some((item) => item.user_id === user.id && item.active), role: "student" as const };
              return <div className="roster-row" key={user.id}><label className="check-field"><input type="checkbox" checked={change.active} onChange={(event) => setRosterChanges({ ...rosterChanges, [user.id]: { ...change, active: event.target.checked } })} />{user.display_name}<small>{user.email}</small></label><label className="field">Role<select aria-label={`Role mata kuliah ${user.email}`} value={change.role} disabled={!change.active} onChange={(event) => setRosterChanges({ ...rosterChanges, [user.id]: { ...change, role: event.target.value as "lecturer" | "student" } })}><option value="lecturer">Dosen</option><option value="student">Mahasiswa</option></select></label></div>;
            })}</div><button className="button button-primary" type="button" disabled={busy} onClick={() => void saveRoster()}>Simpan roster</button>
          </section>}
        </section>
      </div>
      {error && <p className="form-error workspace-message" role="alert">{error}</p>}
      {message && <p className="notice notice-success workspace-message" role="status">{message}</p>}
    </div>
  );
}
