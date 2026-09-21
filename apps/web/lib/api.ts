const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ApiErrorPayload = {
  error?: {
    code?: string;
    message?: string;
  };
  detail?: string;
};

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(
    message: string,
    code: string,
    status: number,
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

export type Trip = {
  id: string;
  destination: string;
  start_date: string;
  end_date: string;
  budget: string | number | null;
  pace_level: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type TripCreateInput = {
  destination: string;
  start_date: string;
  end_date: string;
  budget?: number | null;
  pace_level: number;
};

async function parseError(
  response: Response,
): Promise<ApiError> {
  let payload: ApiErrorPayload | null = null;

  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    payload = null;
  }

  const message =
    payload?.error?.message ??
    payload?.detail ??
    `请求失败：${response.status}`;

  const code =
    payload?.error?.code ??
    `HTTP_${response.status}`;

  return new ApiError(
    message,
    code,
    response.status,
  );
}

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {}),
    },
  });

  if (!response.ok) {
    throw await parseError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export async function listTrips(): Promise<Trip[]> {
  return request<Trip[]>("/api/v1/trips");
}

export async function createTrip(
  input: TripCreateInput,
): Promise<Trip> {
  return request<Trip>("/api/v1/trips", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function getTrip(
  tripId: string,
): Promise<Trip> {
  return request<Trip>(`/api/v1/trips/${tripId}`);
}
export type WishlistItem = {
  id: string; trip_id: string; poi_id: string; name: string; category: string;
  address: string | null; latitude: number; longitude: number;
  suggested_duration_min: number; priority: number; interest_score: number | null;
  notes: string | null; source_type: string; source_url: string | null;
};
export type WishlistInput = {
  name: string; category: string; address?: string | null;
  latitude: number; longitude: number; suggested_duration_min: number;
  priority?: number; notes?: string | null; source_type?: string;
};
export const listWishlist = (tripId: string) => request<WishlistItem[]>(`/api/v1/trips/${tripId}/wishlist`);
export const addWishlistItem = (tripId: string, input: WishlistInput) =>
  request<WishlistItem>(`/api/v1/trips/${tripId}/wishlist`, {
    method: "POST", body: JSON.stringify(input),
  });

export type Place = {
  provider: string; provider_id: string; name: string; address: string | null;
  category: string; latitude: number; longitude: number;
  opening_hours: Record<string, string> | null; suggested_duration_min: number;
};
export const searchPlaces = (keyword: string, city: string) =>
  request<Place[]>(`/api/v1/places/search?keyword=${encodeURIComponent(keyword)}&city=${encodeURIComponent(city)}`);
export type PlanNode = {
  id: string; poi_id: string; name: string; day: string; start_time: string;
  end_time: string; priority: number; status: string; note: string | null;
};
export type Plan = {
  id: string; trip_id: string; version: number; status: string;
  warnings: string[]; created_at: string; nodes: PlanNode[];
};
export const createPlanRun = (tripId: string) => request<Plan>(`/api/v1/trips/${tripId}/plan-runs`, { method: "POST" });
export const listPlans = (tripId: string) => request<Plan[]>(`/api/v1/trips/${tripId}/plans`);
export const getPlan = (tripId: string, planId: string) => request<Plan>(`/api/v1/trips/${tripId}/plans/${planId}`);

export type Candidate = {
  id: string; entity_id: string; provider: string; provider_id: string;
  name: string; address: string | null; latitude: number; longitude: number;
  category: string; duration_min: number; score: number; confirmed: boolean;
};
export type ContentEntity = {
  id: string; name: string; category: string; suggestion: string | null;
  confidence: number; candidates: Candidate[];
};
export type ImportedContent = {
  id: string; trip_id: string; content_type: string; text: string;
  status: string; error: string | null; created_at: string; entities: ContentEntity[];
};
export const importContent = (tripId: string, text: string) =>
  request<ImportedContent>(`/api/v1/trips/${tripId}/content`, {
    method: "POST", body: JSON.stringify({ content_type: "TEXT", text }),
  });
export const confirmCandidates = (contentId: string, candidateIds: string[]) =>
  request<ImportedContent>(`/api/v1/content/${contentId}/confirm`, {
    method: "POST", body: JSON.stringify({ candidate_ids: candidateIds }),
  });

export type TripEvent = { id: string; trip_id: string; event_type: string; effective_at: string; payload: Record<string, unknown>; created_at: string };
export type PlanDiff = { stable_key: string; operation: string; reason_code: string; before: Record<string, unknown> | null; after: Record<string, unknown> | null; change_cost: number };
export type ReplanResult = { run_id: string; plan: Plan; diff: PlanDiff[] };
export const createEvent = (tripId: string, eventType: string, effectiveAt: string, payload: Record<string, unknown>) =>
  request<TripEvent>(`/api/v1/trips/${tripId}/events`, { method: "POST", body: JSON.stringify({ event_type: eventType, effective_at: effectiveAt, payload }) });
export const replan = (tripId: string, eventId: string) =>
  request<ReplanResult>(`/api/v1/trips/${tripId}/replan-runs`, { method: "POST", body: JSON.stringify({ event_id: eventId }) });
export const getPlanDiff = (tripId: string, planId: string) =>
  request<PlanDiff[]>(`/api/v1/trips/${tripId}/plans/${planId}/diff`);
export type Memory = { id: string; trip_id: string; feedback_id: string | null; memory_type: string; text: string; importance: number; confidence: number; confirmed: boolean; created_at: string };
export type Feedback = { id: string; trip_id: string; text: string; rating: number | null; created_at: string; candidate_memory: Memory | null };
export const submitFeedback = (tripId: string, text: string, rating?: number) =>
  request<Feedback>(`/api/v1/trips/${tripId}/feedback`, { method: "POST", body: JSON.stringify({ text, rating }) });
export const listMemories = (tripId: string) => request<Memory[]>(`/api/v1/trips/${tripId}/memories`);
export const updateMemory = (tripId: string, memoryId: string, input: { text?: string; confirmed?: boolean }) =>
  request<Memory>(`/api/v1/trips/${tripId}/memories/${memoryId}`, { method: "PATCH", body: JSON.stringify(input) });
export const deleteMemory = (tripId: string, memoryId: string) =>
  request<void>(`/api/v1/trips/${tripId}/memories/${memoryId}`, { method: "DELETE" });
