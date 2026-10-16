import { productConfig } from "./product-config";

const API_URL = productConfig.apiBaseUrl;

const TOKEN_KEY = "travelpilot_access_token";

export function setAccessToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
  else window.sessionStorage.removeItem(TOKEN_KEY);
}

export function getAccessToken() {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(TOKEN_KEY);
}

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
  const method = (options?.method ?? "GET").toUpperCase();
  const csrf = typeof document === "undefined" ? "" : document.cookie.split("; ").find((item) => item.startsWith("travelpilot_csrf="))?.split("=").slice(1).join("=") ?? "";
  const isFormData = typeof FormData !== "undefined" && options?.body instanceof FormData;
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      ...(!isFormData ? { "Content-Type": "application/json" } : {}),
      ...(getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {}),
      ...(csrf && !["GET", "HEAD", "OPTIONS"].includes(method) ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
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

export type AuthSession = { access_token: string; token_type: string; expires_at: string; user: { id: string; email: string; created_at: string } };
export type AuthNotice = { message: string };
export const register = (email: string, password: string, invite_code?: string) => request<AuthNotice>("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ email, password, invite_code }) });
export const login = (email: string, password: string) => request<AuthSession>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
export const verifyEmail = (token: string) => request<AuthNotice>("/api/v1/auth/verify-email", { method: "POST", body: JSON.stringify({ token }) });
export const resendVerification = (email: string) => request<AuthNotice>("/api/v1/auth/verify-email/resend", { method: "POST", body: JSON.stringify({ email }) });
export const requestPasswordReset = (email: string) => request<AuthNotice>("/api/v1/auth/password-reset/request", { method: "POST", body: JSON.stringify({ email }) });
export const confirmPasswordReset = (token: string, password: string) => request<AuthNotice>("/api/v1/auth/password-reset/confirm", { method: "POST", body: JSON.stringify({ token, password }) });
export const logout = () => request<void>("/api/v1/auth/logout", { method: "POST" });
export const exportAccount = () => request<Record<string, unknown>>("/api/v1/account/export");
export const deleteAccount = () => request<void>("/api/v1/account", { method: "DELETE" });
export const getCurrentUser = () => request<AuthSession["user"]>("/api/v1/auth/me");

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

export type AdvisorCandidate = {
  id: string; provider: string; provider_id: string; name: string; address: string | null;
  category: string; latitude: number; longitude: number; duration_min: number;
  opening_hours: Record<string, string> | null; confidence: number; suggestion: string | null;
  in_current_plan?: boolean; existing_poi_id?: string;
};
export type AdvisorMessage = { id: string; role: "user" | "assistant"; content: string; payload: Record<string, unknown> | null; created_at: string };
export type AdvisorStop = { candidate_id: string; name: string; day: string; start_time: string; end_time: string; latitude: number; longitude: number; category: string; address: string | null };
export type AdvisorPlanSnapshot = { id: string; version: number; status: string; warnings: string[]; stops: (AdvisorStop & { poi_id: string; priority: number; status: string; note: string | null })[] };
export type AdvisorMemorySnapshot = { id: string; type: string; text: string; importance: number; confidence: number };
export type AdvisorContext = { revision: number; selected_candidate_ids: string[]; current_plan: AdvisorPlanSnapshot | null; confirmed_memories: AdvisorMemorySnapshot[] };
export type AdvisorChange = { stable_key: string; operation: "ADD" | "REMOVE" | "MOVE" | "KEEP"; name: string; reason: string; change_cost: number; before: Record<string, unknown> | null; after: Record<string, unknown> | null };
export type AdvisorDraft = { version: number; context_revision: number; generated_at: string; selected_candidate_ids: string[]; warnings: string[]; pace_change: { before: number; after: number } | null; changes: AdvisorChange[]; change_summary: Record<string, number>; stops: AdvisorStop[] };
export type AdvisorSession = {
  id: string; mode: "discover" | "destination" | "existing"; trip_id: string | null; destination: string;
  start_date: string | null; end_date: string | null; budget: string | number | null; pace_level: number;
  preference_text: string; candidates: AdvisorCandidate[]; context: AdvisorContext; draft: AdvisorDraft | null; draft_version: number; draft_stale: boolean;
  status: "ACTIVE" | "CONFIRMED"; confirmed_trip_id: string | null; confirmed_plan_id: string | null;
  created_at: string; updated_at: string; messages: AdvisorMessage[];
};
export type AdvisorSessionInput = {
  mode: AdvisorSession["mode"]; trip_id?: string; destination?: string; start_date?: string; end_date?: string;
  budget?: number | null; pace_level: number; preference_text?: string;
};
export const listAdvisorSessions = () => request<AdvisorSession[]>("/api/v1/advisor/sessions");
export const createAdvisorSession = (input: AdvisorSessionInput) => request<AdvisorSession>("/api/v1/advisor/sessions", { method: "POST", body: JSON.stringify(input) });
export const getAdvisorSession = (sessionId: string) => request<AdvisorSession>(`/api/v1/advisor/sessions/${sessionId}`);
export const sendAdvisorMessage = (sessionId: string, content: string) => request<AdvisorSession>(`/api/v1/advisor/sessions/${sessionId}/messages`, { method: "POST", body: JSON.stringify({ content }) });
export const createAdvisorDraft = (sessionId: string, candidate_ids: string[]) => request<AdvisorSession>(`/api/v1/advisor/sessions/${sessionId}/draft`, { method: "POST", body: JSON.stringify({ candidate_ids }) });
export const confirmAdvisorDraft = (sessionId: string, draft_version: number) => request<{ trip_id: string; plan_id: string; plan_version: number; already_confirmed: boolean }>(`/api/v1/advisor/sessions/${sessionId}/confirm`, { method: "POST", body: JSON.stringify({ draft_version }) });
export const deleteAdvisorSession = (sessionId: string) => request<void>(`/api/v1/advisor/sessions/${sessionId}`, { method: "DELETE" });
export async function updateTrip(tripId: string, input: Partial<TripCreateInput>): Promise<Trip> {
  return request<Trip>(`/api/v1/trips/${tripId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}
export const deleteTrip = (tripId: string) =>
  request<void>(`/api/v1/trips/${tripId}`, { method: "DELETE" });
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
export type WishlistUpdateInput = { priority?: number; interest_score?: number | null; notes?: string | null };
export const updateWishlistItem = (itemId: string, input: WishlistUpdateInput) =>
  request<WishlistItem>(`/api/v1/wishlist/${itemId}`, {
    method: "PATCH", body: JSON.stringify(input),
  });
export const deleteWishlistItem = (itemId: string) =>
  request<void>(`/api/v1/wishlist/${itemId}`, { method: "DELETE" });

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
export type OcrText = { text: string; confidence: number | null; stored: boolean };
export const recognizeContentImage = (tripId: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<OcrText>(`/api/v1/trips/${tripId}/content/ocr`, { method: "POST", body: form });
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

export type Friendship = { id: string; friend_user_id: string; email: string; status: "PENDING" | "ACCEPTED" | "BLOCKED"; direction: "INCOMING" | "OUTGOING" | "NONE"; created_at: string };
export type TripShare = { id: string; trip_id: string; grantee_user_id: string; email: string; scope: "TRIP_SUMMARY" | "TRIP_AND_PLAN"; created_at: string };
export type SharedTrip = { id: string; destination: string; start_date: string; end_date: string; scope: "TRIP_SUMMARY" | "TRIP_AND_PLAN"; owner_email: string };
export type SharedTripDetail = SharedTrip & { plan_version: number | null; plan_nodes: { name: string; day: string; start_time: string; end_time: string; status: string }[] };
export const listFriends = () => request<Friendship[]>("/api/v1/friends");
export const listFriendRequests = () => request<Friendship[]>("/api/v1/friends/requests");
export const listBlockedFriends = () => request<Friendship[]>("/api/v1/friends/blocked");
export const createFriendRequest = (email: string) => request<{ message: string }>("/api/v1/friends/requests", { method: "POST", body: JSON.stringify({ email }) });
export const acceptFriendRequest = (friendshipId: string) => request<Friendship>(`/api/v1/friends/requests/${friendshipId}/accept`, { method: "POST" });
export const declineFriendRequest = (friendshipId: string) => request<void>(`/api/v1/friends/requests/${friendshipId}`, { method: "DELETE" });
export const removeFriend = (friendshipId: string) => request<void>(`/api/v1/friends/${friendshipId}`, { method: "DELETE" });
export const blockFriend = (friendshipId: string) => request<void>(`/api/v1/friends/${friendshipId}/block`, { method: "POST" });
export const unblockFriend = (friendshipId: string) => request<void>(`/api/v1/friends/${friendshipId}/block`, { method: "DELETE" });
export const listTripShares = (tripId: string) => request<TripShare[]>(`/api/v1/trips/${tripId}/shares`);
export const createTripShare = (tripId: string, friend_user_id: string, scope: TripShare["scope"]) => request<TripShare>(`/api/v1/trips/${tripId}/shares`, { method: "POST", body: JSON.stringify({ friend_user_id, scope }) });
export const revokeTripShare = (tripId: string, grantId: string) => request<void>(`/api/v1/trips/${tripId}/shares/${grantId}`, { method: "DELETE" });
export const listSharedTrips = () => request<SharedTrip[]>("/api/v1/shared/trips");
export const getSharedTrip = (tripId: string) => request<SharedTripDetail>(`/api/v1/shared/trips/${tripId}`);

export type MomentMedia = { id: string; mime_type: string; size_bytes: number; created_at: string };
export type TravelMoment = {
  id: string; owner_user_id: string; author_email: string; body: string; audience: "PRIVATE" | "FRIENDS"; memory_extraction_enabled: boolean;
  day: string | null; place_name: string | null; trip_id: string | null; created_at: string;
  media: MomentMedia[]; reaction_count: number; reacted_by_me: boolean; comment_count: number;
};
export type MomentComment = { id: string; author_user_id: string; author_email: string; body: string; created_at: string };
export type MemorySuggestion = { id: string; candidate: string; evidence: string; status: "PENDING" | "CONFIRMED" | "REJECTED"; created_at: string };
export const listMoments = () => request<TravelMoment[]>("/api/v1/moments");
export const createMoment = (input: { body: string; trip_id?: string | null; day?: string | null; place_name?: string | null; audience?: "PRIVATE" | "FRIENDS"; memory_extraction_enabled?: boolean }) =>
  request<TravelMoment>("/api/v1/moments", { method: "POST", body: JSON.stringify(input) });
export const updateMoment = (momentId: string, input: Partial<Pick<TravelMoment, "body" | "audience" | "day" | "place_name">> & { memory_extraction_enabled?: boolean }) =>
  request<TravelMoment>(`/api/v1/moments/${momentId}`, { method: "PATCH", body: JSON.stringify(input) });
export const deleteMoment = (momentId: string) => request<void>(`/api/v1/moments/${momentId}`, { method: "DELETE" });
export const uploadMomentMedia = (momentId: string, file: File) => request<MomentMedia>(`/api/v1/moments/${momentId}/media`, {
  method: "POST", body: file, headers: { "Content-Type": file.type },
});
export async function getMomentMediaBlob(assetId: string, thumbnail = true): Promise<string> {
  const csrf = typeof document === "undefined" ? "" : document.cookie.split("; ").find((item) => item.startsWith("travelpilot_csrf="))?.split("=").slice(1).join("=") ?? "";
  const response = await fetch(`${API_URL}/api/v1/media/${assetId}${thumbnail ? "/thumbnail" : ""}`, {
    credentials: "include",
    headers: {
      ...(getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {}),
      ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
    },
  });
  if (!response.ok) throw await parseError(response);
  return URL.createObjectURL(await response.blob());
}
export const listMomentComments = (momentId: string) => request<MomentComment[]>(`/api/v1/moments/${momentId}/comments`);
export const createMomentComment = (momentId: string, body: string) => request<MomentComment>(`/api/v1/moments/${momentId}/comments`, { method: "POST", body: JSON.stringify({ body }) });
export const deleteMomentComment = (momentId: string, commentId: string) => request<void>(`/api/v1/moments/${momentId}/comments/${commentId}`, { method: "DELETE" });
export const setMomentReaction = (momentId: string) => request<void>(`/api/v1/moments/${momentId}/reaction`, { method: "PUT" });
export const deleteMomentReaction = (momentId: string) => request<void>(`/api/v1/moments/${momentId}/reaction`, { method: "DELETE" });
export const draftMomentCaption = (momentId: string) => request<{ captions: string[]; status: string; requires_review: boolean }>(`/api/v1/moments/${momentId}/caption`, { method: "POST" });
export const createMemorySuggestion = (momentId: string) => request<MemorySuggestion>(`/api/v1/moments/${momentId}/memory-suggestions`, { method: "POST" });
export const listMemorySuggestions = (momentId: string) => request<MemorySuggestion[]>(`/api/v1/moments/${momentId}/memory-suggestions`);
export const reviewMemorySuggestion = (momentId: string, suggestionId: string, candidate: string, accepted: boolean) =>
  request<MemorySuggestion>(`/api/v1/moments/${momentId}/memory-suggestions/${suggestionId}`, { method: "PATCH", body: JSON.stringify({ candidate, accepted }) });
