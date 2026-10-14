import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, setCsrfToken } from "./client";

export interface Session {
  user: { id: string; username: string; display_name: string; role: string; role_label: string; permissions: string[] };
  preferences: { language: string | null; theme: "light" | "dark" };
  branding: { portal_name: string; tagline: string; primary_color: string; sidebar_color: string; highlight_color: string; has_logo: boolean; logo_version: string };
  csrf_token: string;
  version: string;
  languages: string[];
}

export function useSession() {
  return useQuery({
    queryKey: ["session"],
    queryFn: async () => {
      const session = await api<Session>("/api/ui/v1/session");
      setCsrfToken(session.csrf_token);
      return session;
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function can(session: Session | undefined, permission: string): boolean {
  const granted = session?.user.permissions ?? [];
  return granted.includes("*") || granted.includes(permission);
}

export function useUpdatePreferences() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { language?: string; theme?: string }) =>
      api<{ language: string | null; theme: string }>("/api/ui/v1/session/preferences", { method: "PATCH", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["session"] }),
  });
}
