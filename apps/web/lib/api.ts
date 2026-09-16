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