import { cookies } from "next/headers";
import type { ApiMethod, ApiRequestPath } from "@/lib/api-contract.generated";

export type UserSummary = {
  id: string;
  email: string;
  display_name: string;
  global_role: "admin" | "member";
  must_change_password: boolean;
};

export type Course = {
  id: string;
  code: string;
  title: string;
  active: boolean;
  revision: number;
};

export type AdminUser = UserSummary & {
  active: boolean;
  revision: number;
};

export type Profile = {
  user: UserSummary;
  memberships: Array<{ course_id: string; code: string; title: string; role: "lecturer" | "student" }>;
  csrf_token: string;
  runtime_limits: Record<string, number>;
  capabilities?: { lms: boolean };
};

export type AssignmentSummary = {
  id: string;
  title: string;
  administrative_state?: string;
  revision?: number;
  active_version_id?: string | null;
  version_no?: number | null;
  version_state?: string | null;
  draft_version_id?: string | null;
  draft_version_no?: number | null;
  version_id?: string;
  state?: string;
  mode?: "individual" | "group";
  opens_at?: string;
  due_at?: string;
};

export type MoodlePlatform = { id: string; issuer: string; moodle_origin: string };
export type MoodleMapping = {
  id: string;
  platform_id: string;
  lti_context_id: string;
  lti_resource_link_id: string;
  moodle_course_id: number;
  moodle_assignment_id: number;
  local_course_id: string;
  assignment_version_id: string;
  active: boolean;
  revision: number;
  groups: Array<{ id: string; moodle_group_id: number; local_group_id: string; active: boolean; revision: number }>;
};
export type MoodleImportList = {
  candidate_status: string;
  candidates: Array<Record<string, unknown>>;
  items: Array<Record<string, unknown>>;
};

type ApiResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "unauthenticated" }
  | { kind: "unavailable" };

function apiOrigin(): string {
  return process.env.API_INTERNAL_URL ?? "http://localhost:8000";
}

async function requestServer<T>(path: ApiRequestPath, method: ApiMethod = "GET"): Promise<ApiResult<T>> {
  const cookieStore = await cookies();
  const sessionCookie = cookieStore.get("align_session");
  if (!sessionCookie) return { kind: "unauthenticated" };

  try {
    const response = await fetch(`${apiOrigin()}/api/v1${path}`, {
      method,
      headers: { Cookie: `align_session=${sessionCookie.value}` },
      cache: "no-store",
    });
    if (response.status === 401) return { kind: "unauthenticated" };
    if (!response.ok) return { kind: "unavailable" };
    return { kind: "ok", data: (await response.json()) as T };
  } catch {
    return { kind: "unavailable" };
  }
}

export function getProfile(): Promise<ApiResult<Profile>> {
  return requestServer<Profile>("/me");
}

export function getCourses(): Promise<ApiResult<{ items: Course[]; next_cursor: string | null }>> {
  return requestServer("/courses");
}

export function getCourse(courseId: string): Promise<ApiResult<Course>> {
  return requestServer(`/courses/${courseId}`);
}

export function getContextVersions(courseId: string): Promise<ApiResult<{ items: Array<{ id: string; version_no: number; state: string; revision: number; content_hash: string | null }>; next_cursor: string | null }>> {
  return requestServer(`/courses/${courseId}/context-versions`);
}

export function getCourseAssignments(courseId: string): Promise<ApiResult<{ items: AssignmentSummary[]; next_cursor: string | null }>> {
  return requestServer(`/courses/${courseId}/assignments`);
}

export function getMoodlePlatforms(courseId: string): Promise<ApiResult<{ items: MoodlePlatform[] }>> {
  return requestServer(`/integrations/moodle/platforms?course_id=${encodeURIComponent(courseId)}`);
}

export function getMoodleMappings(courseId: string): Promise<ApiResult<{ items: MoodleMapping[] }>> {
  return requestServer(`/integrations/moodle/mappings?course_id=${encodeURIComponent(courseId)}`);
}

export function getMoodleImports(mappingId: string): Promise<ApiResult<MoodleImportList>> {
  return requestServer(`/integrations/moodle/imports?mapping_id=${encodeURIComponent(mappingId)}`);
}

export function getPublishedResult(submissionId: string): Promise<ApiResult<Record<string, unknown>>> {
  return requestServer(`/submissions/${submissionId}/published-result`);
}

export function getAdminUsers(): Promise<ApiResult<{ items: AdminUser[]; next_cursor: string | null }>> {
  return requestServer("/admin/users");
}

export function getAdminCourses(): Promise<ApiResult<{ items: Course[]; next_cursor: string | null }>> {
  return requestServer("/admin/courses");
}

export async function getProviderMode(): Promise<"mock" | "off" | "live" | "unknown"> {
  try {
    const response = await fetch(`${apiOrigin()}/health/providers`, { cache: "no-store" });
    if (!response.ok) return "unknown";
    const value = (await response.json()) as { mode?: string };
    return value.mode === "mock" || value.mode === "off" || value.mode === "live" ? value.mode : "unknown";
  } catch {
    return "unknown";
  }
}


export function getStaffSubmission(submissionId: string) {
  return requestServer<import("@/components/SubmissionQueue").StaffSubmission>(`/submissions/${submissionId}/status`);
}
