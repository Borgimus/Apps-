import { useQuery } from '@tanstack/react-query';
import { ictApi } from '../api/ict';

export function useBars(symbol: string, limit = 300) {
  return useQuery({
    queryKey: ['bars', symbol, limit],
    queryFn: () => ictApi.getBars(symbol, limit),
    refetchInterval: 60_000,
    staleTime: 55_000,
    retry: 1,
  });
}
