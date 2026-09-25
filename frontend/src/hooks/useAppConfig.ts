import { useQuery } from "@tanstack/react-query";
import { api, type AppConfig } from "@/api/client";

export function useAppConfig() {
  return useQuery<AppConfig>({
    queryKey: ["config"],
    queryFn: api.config,
    staleTime: 60_000,
  });
}
