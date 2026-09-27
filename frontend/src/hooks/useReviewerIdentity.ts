import { useContext, useState } from "react";
import { getReviewerToken, setReviewerToken } from "@/api/client";
import { ReviewerIdentityContext, type ReviewerIdentity } from "./reviewerIdentityContext";

/** Falls back to local, ungrouped state outside a provider (e.g. in tests
 * that render a single decision component directly). */
export function useReviewerIdentity(): ReviewerIdentity {
  const ctx = useContext(ReviewerIdentityContext);
  const [localName, setLocalName] = useState("");
  const [localToken, setLocalToken] = useState(() => getReviewerToken());
  if (ctx) return ctx;
  return {
    name: localName,
    setName: setLocalName,
    token: localToken,
    setToken: (t: string) => {
      setLocalToken(t);
      setReviewerToken(t);
    },
  };
}
