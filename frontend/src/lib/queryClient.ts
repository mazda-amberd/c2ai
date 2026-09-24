import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000, // Data considered fresh for 30s
      gcTime: 5 * 60 * 1000, // Keep in cache for 5 minutes
      refetchInterval: 30 * 1000, // Background refetch every 30s
      refetchOnWindowFocus: false, // Don't refetch on tab focus
      retry: 2,
    },
  },
});
