import { useQuery } from '@tanstack/react-query';
import { ictApi } from '../api/ict';
import type { ScannerResult } from '../types/ict';

export function useScanner() {
  return useQuery({
    queryKey: ['scanner'],
    queryFn: ictApi.getScannerResults,
    refetchInterval: 30000,
    retry: 2,
    staleTime: 25000,
    // Bulletproof: guarantee an array even if the API shape changes
    select: (data): ScannerResult[] => (Array.isArray(data) ? data : []),
  });
}
