import { create } from 'zustand';
import type { ICTSignal, SignalStatus } from '../types/ict';

interface SignalStore {
  signals: ICTSignal[];
  wsConnected: boolean;
  lastUpdate: Date | null;
  setSignals: (signals: ICTSignal[]) => void;
  addSignal: (signal: ICTSignal) => void;
  updateSignalStatus: (id: string, status: SignalStatus) => void;
  setWsConnected: (connected: boolean) => void;
  clearSignals: () => void;
}

export const useSignalStore = create<SignalStore>((set) => ({
  signals: [],
  wsConnected: false,
  lastUpdate: null,

  setSignals: (signals) =>
    set({ signals, lastUpdate: new Date() }),

  addSignal: (signal) =>
    set((state) => ({
      signals: [signal, ...state.signals.filter((s) => s.id !== signal.id)],
      lastUpdate: new Date(),
    })),

  updateSignalStatus: (id, status) =>
    set((state) => ({
      signals: state.signals.map((s) =>
        s.id === id ? { ...s, status } : s
      ),
      lastUpdate: new Date(),
    })),

  setWsConnected: (wsConnected) => set({ wsConnected }),

  clearSignals: () => set({ signals: [], lastUpdate: null }),
}));
