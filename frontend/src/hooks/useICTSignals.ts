import { useQuery } from '@tanstack/react-query';
import { ictApi } from '../api/ict';
import { useSignalStore } from '../store/signalStore';
import { useEffect } from 'react';

export function useICTSignals() {
  const { setSignals } = useSignalStore();

  const query = useQuery({
    queryKey: ['ict-signals'],
    queryFn: ictApi.getSignals,
    refetchInterval: 10000,
    retry: 2,
  });

  useEffect(() => {
    if (query.data) {
      setSignals(query.data);
    }
  }, [query.data, setSignals]);

  return query;
}
