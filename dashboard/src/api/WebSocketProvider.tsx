/**
 * React context wrapping the singleton PipelineSocket.
 *
 * Exposes:
 *   useWebSocketStatus()       — connection state
 *   useLatestEvents()          — the full dedup'd event list received this session
 *   useSubscribeToEvents(fn)   — imperative subscription (for modal live streams)
 *
 * Dedup key: `${run_id}::${type}::${timestamp}` — matches the backend's
 * replay semantics (see src/tenth_floor/api/ws.py).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { PipelineSocket, type ConnectionStatus } from "./websocket";
import type { PipelineEvent } from "./types";

const WS_URL = `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;

interface WebSocketContextValue {
  status: ConnectionStatus;
  events: PipelineEvent[];
  subscribe: (listener: (event: PipelineEvent) => void) => () => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

function eventKey(event: PipelineEvent): string {
  return `${event.run_id}::${event.type}::${event.timestamp}`;
}

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ConnectionStatus>("closed");
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const seenKeys = useRef(new Set<string>());
  const socketRef = useRef<PipelineSocket | null>(null);

  useEffect(() => {
    const socket = new PipelineSocket(WS_URL);
    socketRef.current = socket;

    const offStatus = socket.onStatus(setStatus);
    const offEvents = socket.onEvent((event) => {
      const key = eventKey(event);
      if (seenKeys.current.has(key)) return;
      seenKeys.current.add(key);
      setEvents((prev) => [...prev, event]);
    });

    socket.start();

    return () => {
      offStatus();
      offEvents();
      socket.stop();
      socketRef.current = null;
    };
  }, []);

  const subscribe = useCallback(
    (listener: (event: PipelineEvent) => void) => {
      const socket = socketRef.current;
      if (!socket) return () => {};
      return socket.onEvent(listener);
    },
    [],
  );

  const value = useMemo(
    () => ({ status, events, subscribe }),
    [status, events, subscribe],
  );

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  );
}

function useWebSocketContext(): WebSocketContextValue {
  const ctx = useContext(WebSocketContext);
  if (!ctx) {
    throw new Error("useWebSocket* must be used inside <WebSocketProvider>");
  }
  return ctx;
}

export function useWebSocketStatus(): ConnectionStatus {
  return useWebSocketContext().status;
}

export function useLatestEvents(): PipelineEvent[] {
  return useWebSocketContext().events;
}

export function useSubscribeToEvents(
  listener: (event: PipelineEvent) => void,
): void {
  const { subscribe } = useWebSocketContext();
  const listenerRef = useRef(listener);
  listenerRef.current = listener;

  useEffect(() => {
    return subscribe((event) => listenerRef.current(event));
  }, [subscribe]);
}
