"use client";

import Link from "next/link";
import { useState } from "react";
import { submitWaitlistSignup } from "@/lib/api";

export default function SignupPage() {
  const [form, setForm] = useState({ email: "", name: "", use_case: "" });
  const [status, setStatus] = useState<"idle" | "submitting" | "success" | "error">("idle");
  const [errorText, setErrorText] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus("submitting");
    try {
      await submitWaitlistSignup({ ...form, source: "waitlist_page" });
      setStatus("success");
    } catch (err: any) {
      setStatus("error");
      setErrorText(err.message || "Failed to submit. Please try again.");
    }
  };

  if (status === "success") {
    return (
      <main className="mx-auto flex min-h-screen max-w-sm flex-col items-center justify-center p-6 text-center">
        <div className="mb-4 text-4xl">✨</div>
        <h1 className="mb-2 text-2xl font-semibold">You&apos;re on the list!</h1>
        <p className="mb-6 text-gray-400">
          We&apos;ll keep you updated on our progress and let you know when we have a spot available.
        </p>
        <Link href="/landing" className="text-sky-400 hover:underline">
          Return to home
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center p-6">
      <div className="mb-8">
        <Link href="/landing" className="mb-4 block text-sm text-gray-500 hover:text-gray-300">
          &larr; Back
        </Link>
        <h1 className="text-2xl font-semibold">Join the waitlist</h1>
        <p className="mt-2 text-gray-400">
          PolyPredictor is currently in closed beta. Leave your details below and we&apos;ll reach out when we expand capacity.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <div className="mb-1 text-sm font-medium text-gray-300">Email address</div>
          <input
            required
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-white outline-none focus:border-sky-500 disabled:opacity-50"
            disabled={status === "submitting"}
          />
        </label>
        
        <label className="block">
          <div className="mb-1 text-sm font-medium text-gray-300">Name <span className="text-gray-500">(optional)</span></div>
          <input
            type="text"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-white outline-none focus:border-sky-500 disabled:opacity-50"
            disabled={status === "submitting"}
          />
        </label>

        <label className="block">
          <div className="mb-1 text-sm font-medium text-gray-300">How do you trade? <span className="text-gray-500">(optional)</span></div>
          <textarea
            rows={3}
            value={form.use_case}
            onChange={(e) => setForm({ ...form, use_case: e.target.value })}
            placeholder="e.g. Discretionary crypto, systematic macro..."
            className="w-full rounded border border-gray-700 bg-black/20 px-3 py-2 text-sm text-white outline-none focus:border-sky-500 disabled:opacity-50"
            disabled={status === "submitting"}
          />
        </label>

        {status === "error" && (
          <div className="rounded border border-rose-900/50 bg-rose-900/20 p-3 text-sm text-rose-200">
            {errorText}
          </div>
        )}

        <button
          type="submit"
          disabled={status === "submitting"}
          className="w-full rounded bg-sky-600 px-4 py-2 font-medium text-white hover:bg-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500 focus:ring-offset-2 focus:ring-offset-gray-900 disabled:opacity-50"
        >
          {status === "submitting" ? "Joining..." : "Join Waitlist"}
        </button>
      </form>
    </main>
  );
}
