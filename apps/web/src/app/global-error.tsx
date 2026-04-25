"use client";

import { useEffect } from "react";

import { reportClientError } from "@/lib/api";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    void reportClientError({
      message: error.message,
      stack: error.stack,
      url: window.location.href,
      user_agent: navigator.userAgent,
      context: { kind: "react.global-error", digest: error.digest },
    });
  }, [error]);

  return (
    <html lang="en">
      <body className="min-h-screen bg-[#0b0d10] text-slate-100">
        <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center px-6">
          <p className="text-sm uppercase text-rose-300">Application error</p>
          <h1 className="mt-3 text-3xl font-semibold">The dashboard hit an error.</h1>
          <p className="mt-3 text-sm leading-6 text-slate-300">
            The incident was recorded for review. Retry the current view, or open
            the status page if the problem persists.
          </p>
          <div className="mt-6 flex gap-3">
            <button
              type="button"
              onClick={reset}
              className="rounded border border-emerald-400/60 px-4 py-2 text-sm text-emerald-100 hover:bg-emerald-400/10"
            >
              Retry
            </button>
            <a
              href="/status"
              className="rounded border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-900"
            >
              Status
            </a>
          </div>
        </main>
      </body>
    </html>
  );
}
