/**
 * WebSocket client for the `/ws` endpoint.
 *
 * Handles reconnection with exponential backoff and fan-out to
 * subscribers. One instance per app lifecycle — consumed via
 * `WebSocketProvider` in `WebSocketProvider.tsx`.
 *
 * The backend replays the latest run's history on connect, so a
 * reconnect after a transient drop will re-deliver events that were
 * already seen. Consumers MUST dedupe by (run_id, type, timestamp).
 */

import type { PipelineEvent } from "./types";

export type ConnectionStatus = "connecting" | "open" | "closed";

type EventListener = (event: PipelineEvent) => void;
type StatusListener = (status: ConnectionStatus) => void;

const BACKOFF_MS = [500, 1000, 2000, 4000, 8000] as const;
const MAX_BACKOFF = 8000;

export class PipelineSocket {
  private socket: WebSocket | null = null;
  private eventListeners = new Set<EventListener>();
  private statusListeners = new Set<StatusListener>();
  private status: ConnectionStatus = "closed";
  private reconnectAttempt = 0;
  private reconnectTimer: number | null = null;
  private stopped = false;
  private readonly url: string;

  constructor(url: string) {
    this.url = url;
  }

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.setStatus("closed");
  }

  onEvent(listener: EventListener): () => void {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    // Fire current status immediately so the subscriber has a baseline.
    listener(this.status);
    return () => this.statusListeners.delete(listener);
  }

  private connect(): void {
    if (this.stopped) return;
    this.setStatus("connecting");

    const socket = new WebSocket(this.url);
    this.socket = socket;

    socket.onopen = () => {
      this.reconnectAttempt = 0;
      this.setStatus("open");
    };

    socket.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data) as PipelineEvent;
        for (const listener of this.eventListeners) listener(parsed);
      } catch (err) {
        console.error("Failed to parse WebSocket message", err, msg.data);
      }
    };

    socket.onerror = () => {
      // `onclose` will follow and handle reconnection.
    };

    socket.onclose = () => {
      this.socket = null;
      if (this.stopped) return;
      this.setStatus("closed");
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    const delay =
      BACKOFF_MS[Math.min(this.reconnectAttempt, BACKOFF_MS.length - 1)] ??
      MAX_BACKOFF;
    this.reconnectAttempt += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) return;
    this.status = status;
    for (const listener of this.statusListeners) listener(status);
  }
}
