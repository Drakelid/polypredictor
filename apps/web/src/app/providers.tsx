"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { reportClientError } from "@/lib/api";

function ClientErrorReporter() {
  useEffect(() => {
    const reportError = (event: ErrorEvent) => {
      void reportClientError({
        message: event.message,
        stack: event.error instanceof Error ? event.error.stack : null,
        url: window.location.href,
        user_agent: navigator.userAgent,
        context: { kind: "window.error", filename: event.filename, lineno: event.lineno },
      });
    };
    const reportRejection = (event: PromiseRejectionEvent) => {
      const reason = event.reason;
      void reportClientError({
        message: reason instanceof Error ? reason.message : String(reason),
        stack: reason instanceof Error ? reason.stack : null,
        url: window.location.href,
        user_agent: navigator.userAgent,
        context: { kind: "unhandledrejection" },
      });
    };
    window.addEventListener("error", reportError);
    window.addEventListener("unhandledrejection", reportRejection);
    return () => {
      window.removeEventListener("error", reportError);
      window.removeEventListener("unhandledrejection", reportRejection);
    };
  }, []);

  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Dashboard data lives or dies by freshness — we always refetch on focus.
            staleTime: 2_000,
            refetchOnWindowFocus: true,
            retry: 1,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ClientErrorReporter />
      {children}
    </QueryClientProvider>
  );
}
