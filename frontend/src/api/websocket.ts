import type { WebSocketMessage } from '../types/ict';

type MessageHandler = (msg: WebSocketMessage) => void;
type StatusHandler = (connected: boolean) => void;

class ICTWebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private messageHandlers: Set<MessageHandler> = new Set();
  private statusHandlers: Set<StatusHandler> = new Set();
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 2000;
  private maxReconnectDelay = 30000;
  private shouldReconnect = true;
  private pingInterval: ReturnType<typeof setInterval> | null = null;

  constructor(url: string = 'ws://localhost:8000/ws/ict/signals') {
    this.url = url;
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return;
    this.shouldReconnect = true;
    this._connect();
  }

  private _connect(): void {
    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        console.log('[WS] Connected to ICT signal stream');
        this.reconnectDelay = 2000;
        this.notifyStatus(true);
        this.startPing();
      };

      this.ws.onmessage = (event) => {
        try {
          const msg: WebSocketMessage = JSON.parse(event.data);
          this.messageHandlers.forEach((h) => h(msg));
        } catch (e) {
          console.error('[WS] Failed to parse message:', e);
        }
      };

      this.ws.onclose = () => {
        console.log('[WS] Disconnected');
        this.notifyStatus(false);
        this.stopPing();
        if (this.shouldReconnect) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = (error) => {
        console.error('[WS] Error:', error);
      };
    } catch (e) {
      console.error('[WS] Failed to create WebSocket:', e);
      if (this.shouldReconnect) {
        this.scheduleReconnect();
      }
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    console.log(`[WS] Reconnecting in ${this.reconnectDelay}ms...`);
    this.reconnectTimeout = setTimeout(() => {
      this._connect();
      this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, this.maxReconnectDelay);
    }, this.reconnectDelay);
  }

  private startPing(): void {
    this.pingInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);
  }

  private stopPing(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    this.stopPing();
    this.ws?.close();
    this.ws = null;
  }

  onMessage(handler: MessageHandler): () => void {
    this.messageHandlers.add(handler);
    return () => this.messageHandlers.delete(handler);
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    return () => this.statusHandlers.delete(handler);
  }

  private notifyStatus(connected: boolean): void {
    this.statusHandlers.forEach((h) => h(connected));
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

export const wsClient = new ICTWebSocketClient();
export default wsClient;
