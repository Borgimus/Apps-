/** Safe JSON helpers for String-typed JSON columns (SQLite-friendly). */

export function parseJson<T>(raw: string | null | undefined, fallback: T): T {
  if (raw == null || raw === '') return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function toJson(value: unknown): string {
  return JSON.stringify(value ?? null);
}

/** Redact obvious secrets before persisting or displaying external payloads. */
const SECRET_PATTERNS: RegExp[] = [
  /sk-[A-Za-z0-9_-]{16,}/g, // OpenAI-style keys
  /sk-ant-[A-Za-z0-9_-]{16,}/g, // Anthropic keys
  /(api[_-]?key|authorization|bearer)["'\s:=]+[A-Za-z0-9._-]{16,}/gi,
];

export function redactSecrets(text: string): string {
  let out = text;
  for (const p of SECRET_PATTERNS) out = out.replace(p, '[REDACTED]');
  return out;
}
