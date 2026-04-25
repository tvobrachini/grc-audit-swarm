import { useQuery } from "@tanstack/react-query";
import { api, type SessionDetail } from "@/api/client";

export function useAuditDetail(sessionId: string | null) {
  return useQuery<SessionDetail>({
    queryKey: ["session", sessionId],
    queryFn: () => api.sessions.get(sessionId!),
    enabled: !!sessionId,
    refetchInterval: 3000,
    staleTime: 1000,
  });
}
