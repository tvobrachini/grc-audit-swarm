import { useMemo, useState, type ReactNode } from "react";
import { getReviewerToken, setReviewerToken } from "@/api/client";
import { ReviewerIdentityContext, type ReviewerIdentity } from "./reviewerIdentityContext";

export function ReviewerIdentityProvider({ children }: { children: ReactNode }) {
  const [name, setName] = useState("");
  const [token, setTokenState] = useState(() => getReviewerToken());

  const value = useMemo<ReviewerIdentity>(
    () => ({
      name,
      setName,
      token,
      setToken: (t: string) => {
        setTokenState(t);
        setReviewerToken(t);
      },
    }),
    [name, token]
  );

  return (
    <ReviewerIdentityContext.Provider value={value}>
      {children}
    </ReviewerIdentityContext.Provider>
  );
}
