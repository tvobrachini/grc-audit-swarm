import { useQuery } from "@tanstack/react-query";
import { api, type SessionSummary } from "@/api/client";

export function useAuditList() {
  return useQuery<SessionSummary[]>({
    queryKey: ["sessions"],
    queryFn: api.sessions.list,
    refetchInterval: 5000,
    staleTime: 2000,
  });
}
