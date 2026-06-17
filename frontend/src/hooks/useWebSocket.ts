import { useEffect, useRef, useCallback } from 'react';
import wsClient from '../api/websocket';
import { useSignalStore } from '../store/signalStore';
import type { ICTSignal, SignalStatus } from '../types/ict';
import toast from 'react-hot-toast';

export function useWebSocket() {
  const { addSignal, updateSignalStatus, setWsConnected } = useSignalStore();
  const connectedRef = useRef(false);

  const handleStatus = useCallback(
    (connected: boolean) => {
      setWsConnected(connected);
      connectedRef.current = connected;
      if (connected) {
        toast.success('Live signal feed connected', { id: 'ws-status', duration: 2000 });
      } else {
        toast.error('Signal feed disconnected - reconnecting...', { id: 'ws-status', duration: 3000 });
      }
    },
    [setWsConnected]
  );

  const handleMessage = useCallback(
    (msg: { type: string; data: unknown; timestamp: string }) => {
      if (msg.type === 'signal' && msg.data) {
        const signal = msg.data as ICTSignal;
        addSignal(signal);
        toast(
          `${signal.direction.toUpperCase()} signal: ${signal.symbol} @ ${signal.entry_price.toFixed(2)}`,
          {
            icon: signal.direction === 'long' ? '▲' : '▼',
            style: {
              background: signal.direction === 'long' ? '#26a69a22' : '#ef535022',
              border: `1px solid ${signal.direction === 'long' ? '#26a69a' : '#ef5350'}`,
              color: '#e2e8f0',
            },
          }
        );
      } else if (msg.type === 'signal_update' && msg.data) {
        const update = msg.data as { id: string; status: SignalStatus };
        updateSignalStatus(update.id, update.status);
      }
    },
    [addSignal, updateSignalStatus]
  );

  useEffect(() => {
    const unsubMsg = wsClient.onMessage(handleMessage);
    const unsubStatus = wsClient.onStatus(handleStatus);
    wsClient.connect();

    return () => {
      unsubMsg();
      unsubStatus();
    };
  }, [handleMessage, handleStatus]);

  return { isConnected: connectedRef.current };
}
