import { useEffect, useRef, useState } from "react";
import { type AuditEvent } from "@/api/client";

export function useAuditStream(sessionId: string | null) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setEvents([]);
      return;
    }

    setEvents([]);
    const es = new EventSource(`/api/stream/${sessionId}`);
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as AuditEvent;
        setEvents((prev) => [...prev.slice(-200), event]);
      } catch {
        // ignore malformed frames
      }
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [sessionId]);

  return events;
}
