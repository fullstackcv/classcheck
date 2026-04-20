/**
 * Typed client for the ClassCheck FastAPI backend.
 *
 * Every backend call goes through here so there's exactly one place that
 * knows the shape of the API, the base URL, and how errors are mapped to
 * exceptions. Pages and components call these functions — never fetch()
 * directly.
 */

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---- Types (mirror the Pydantic models in classcheck/api.py) ----

export interface ClassOut {
  id: number;
  label: string;
  time_of_day: string; // "HH:MM"
  teacher_id: number | null;
  teacher_name: string | null;
  student_count: number;
}

export interface ClassDetailOut {
  id: number;
  label: string;
  time_of_day: string;
  teacher_id: number | null;
  student_ids: number[];
}

export interface PersonOut {
  id: number;
  name: string;
  role: string;
  email: string | null;
  class_count: number;
}

export interface PersonDetailOut {
  id: number;
  name: string;
  role: string;
  email: string | null;
  teaches_ids: number[];
  enrolled_ids: number[];
  recent_observations: Array<{
    date: string;
    time: string;
    class: string;
    seen: string;
    score: string;
    status: string;
  }>;
}

export interface SnapshotSummary {
  id: number;
  schedule_id: number;
  class_label: string;
  scheduled_date: string;
  scheduled_time: string;
  actual_time: string;
  present_count: number;
  total_count: number;
  thumbnail_url: string | null;
}

export interface Observation {
  person_id: number;
  name: string;
  role: string;
  frames_seen: number;
  total_frames: number;
  avg_score: number;
  is_present: boolean;
}

export interface SnapshotDetail {
  id: number;
  class_label: string;
  scheduled_date: string;
  scheduled_time: string;
  actual_time: string;
  n_frames: number;
  thumbnail_url: string | null;
  observations: Observation[];
}

export interface CaptureResult {
  snapshot_id: number | null;
  ok: boolean;
}

// ---- Error + fetch helpers ----

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { json?: unknown }
): Promise<T> {
  const headers = new Headers(init?.headers);
  let body: BodyInit | undefined = init?.body as BodyInit | undefined;

  if (init?.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }

  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    body,
  });

  if (!res.ok) {
    let detail: unknown;
    let message = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      detail = j;
      if (typeof j?.detail === "string") message = j.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message, detail);
  }

  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  if (ct.startsWith("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

// ---- Classes ----

export const listClasses = () => request<ClassOut[]>("/classes");
export const getClass = (id: number) =>
  request<ClassDetailOut>(`/classes/${id}`);
export const createClass = (body: {
  label: string;
  time_of_day: string;
  teacher_id?: number | null;
  student_ids?: number[];
}) => request<ClassDetailOut>("/classes", { method: "POST", json: body });
export const updateClass = (
  id: number,
  body: {
    label?: string;
    time_of_day?: string;
    teacher_id?: number | null;
    student_ids?: number[];
  }
) =>
  request<ClassDetailOut>(`/classes/${id}`, { method: "PATCH", json: body });
export const deleteClass = (id: number) =>
  request<void>(`/classes/${id}`, { method: "DELETE" });
export const captureNow = (id: number, showPreview = true) =>
  request<CaptureResult>(
    `/classes/${id}/capture?show_preview=${showPreview}`,
    { method: "POST" }
  );

// ---- People ----

export const listPeople = () => request<PersonOut[]>("/people");
export const getPerson = (id: number) =>
  request<PersonDetailOut>(`/people/${id}`);
export const updatePerson = (
  id: number,
  body: { name?: string; role?: string; email?: string | null }
) =>
  request<PersonDetailOut>(`/people/${id}`, { method: "PATCH", json: body });
export const deletePerson = (id: number) =>
  request<void>(`/people/${id}`, { method: "DELETE" });
export const setPersonClasses = (id: number, classIds: number[]) =>
  request<PersonDetailOut>(`/people/${id}/classes`, {
    method: "PUT",
    json: { class_ids: classIds },
  });

// ---- Enroll ----

export async function enroll(form: {
  name: string;
  role: string;
  email?: string;
  photos: Blob[];
}): Promise<{
  id: number;
  name: string;
  role: string;
  email: string | null;
  facestack_person_id: number | null;
}> {
  const fd = new FormData();
  fd.append("name", form.name);
  fd.append("role", form.role);
  if (form.email) fd.append("email", form.email);
  form.photos.forEach((p, i) =>
    fd.append("photos", p, `photo_${i + 1}.jpg`)
  );

  const res = await fetch(`${API_URL}/enroll`, { method: "POST", body: fd });
  if (!res.ok) {
    let detail: unknown;
    let message = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      detail = j;
      if (typeof j?.detail === "string") message = j.detail;
    } catch { /* */ }
    throw new ApiError(res.status, message, detail);
  }
  return res.json();
}

// ---- Attendance ----

export const listSnapshots = (opts: { date: string; class_id?: number }) => {
  const params = new URLSearchParams({ date: opts.date });
  if (opts.class_id !== undefined) params.set("class_id", String(opts.class_id));
  return request<SnapshotSummary[]>(`/snapshots?${params}`);
};
export const getSnapshot = (id: number) =>
  request<SnapshotDetail>(`/snapshots/${id}`);
export const thumbnailUrl = (id: number) =>
  `${API_URL}/snapshots/${id}/thumbnail`;
export const rollCsvUrl = (id: number) =>
  `${API_URL}/snapshots/${id}/roll.csv`;
export const teachersCsvUrl = (id: number) =>
  `${API_URL}/snapshots/${id}/teachers.csv`;

export { API_URL };
