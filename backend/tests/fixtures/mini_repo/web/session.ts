export interface Session {
  accessKey: string;
  expiresAt: number;
}

export async function refreshToken(session: Session): Promise<Session> {
  const res = await fetch("/session/renew", { method: "POST" });
  return (await res.json()) as Session;
}
