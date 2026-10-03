// Server-side base URL of the FastAPI backend (the browser never talks to the broker).
export const API_URL = process.env.API_URL ?? "http://localhost:8000";

export type Health = { status: string; database: string; broker_env: string };

export async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}
