import { useQuery } from '@tanstack/react-query';
import { ictApi } from '../api/ict';

export function useScanner() {
  return useQuery({
    queryKey: ['scanner'],
    queryFn: ictApi.getScannerResults,
    refetchInterval: 30000,
    retry: 2,
    staleTime: 25000,
  });
}
