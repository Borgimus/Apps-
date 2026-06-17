import { useQuery } from '@tanstack/react-query';
import { ictApi } from '../api/ict';

export function useSessionLevels(symbol: string) {
  return useQuery({
    queryKey: ['session-levels', symbol],
    queryFn: () => ictApi.getSessionLevels(symbol),
    refetchInterval: 60000,
    retry: 2,
    staleTime: 30000,
  });
}
