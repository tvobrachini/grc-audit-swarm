import { createContext } from "react";

/** The declared reviewer identity used across the decision forms and the
 * approval gate for one audit view, so a reviewer doesn't retype their name.
 * The name lives only in memory (component state); the optional personal
 * token is kept in sessionStorage (see api/client.ts) and never localStorage. */
export interface ReviewerIdentity {
  name: string;
  setName: (name: string) => void;
  token: string;
  setToken: (token: string) => void;
}

export const ReviewerIdentityContext = createContext<ReviewerIdentity | null>(null);
