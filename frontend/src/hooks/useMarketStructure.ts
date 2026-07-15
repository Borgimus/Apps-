import { useQuery } from '@tanstack/react-query';
import { ictApi } from '../api/ict';

export function useMarketStructure(symbol: string) {
  return useQuery({
    queryKey: ['market-structure', symbol],
    queryFn: () => ictApi.getMarketStructure(symbol),
    refetchInterval: 30000,
    retry: 2,
    staleTime: 15000,
  });
}
