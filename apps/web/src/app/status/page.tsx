import Link from "next/link";

import { fetchSystemStatus, type SystemStatus } from "@/lib/api";

export const dynamic = "force-dynamic";

function stateClasses(state: SystemStatus["state"]): string {
  switch (state) {
    case "operational":
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100";
    case "degraded":
      return "border-amber-500/40 bg-amber-500/10 text-amber-100";
    case "down":
      return "border-rose-500/40 bg-rose-500/10 text-rose-100";
    default:
      return "border-slate-600 bg-slate-900 text-slate-200";
  }
}

function stateLabel(state: SystemStatus["state"]): string {
  switch (state) {
    case "operational":
      return "Operational";
    case "degraded":
      return "Degraded";
    case "down":
      return "Down";
    default:
      return "Unknown";
  }
}

function componentLabel(name: string): string {
  if (name.startsWith("source:")) return name.replace("source:", "Source: ");
  if (name === "clickhouse") return "ClickHouse";
  if (name === "postgres") return "Postgres";
  return name;
}

export default async function StatusPage() {
  let status: SystemStatus;
  try {
    status = await fetchSystemStatus();
  } catch {
    status = {
      state: "down",
      checked_at: new Date().toISOString(),
      components: [
        {
          name: "api",
          state: "down",
          detail: "status endpoint unreachable",
          last_observed_at: null,
        },
      ],
    };
  }

  return (
    <main className="min-h-screen bg-[#0b0d10] px-5 py-8 text-slate-100">
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-5">
          <div>
            <p className="text-sm text-slate-400">PolyPredictor status</p>
            <h1 className="mt-2 text-3xl font-semibold">{stateLabel(status.state)}</h1>
          </div>
          <Link
            href="/"
            className="rounded border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-900"
          >
            Dashboard
          </Link>
        </div>

        <section className="mt-6">
          <div className={`rounded border px-4 py-3 ${stateClasses(status.state)}`}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="font-medium">
                Overall system is {stateLabel(status.state).toLowerCase()}
              </span>
              <span className="text-sm">
                Checked {new Date(status.checked_at).toLocaleString()}
              </span>
            </div>
          </div>
        </section>

        <section className="mt-8">
          <h2 className="text-lg font-semibold">Components</h2>
          <div className="mt-3 overflow-hidden rounded border border-slate-800">
            <table className="w-full border-collapse text-left text-sm">
              <thead className="bg-slate-950 text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Component</th>
                  <th className="px-4 py-3 font-medium">State</th>
                  <th className="px-4 py-3 font-medium">Detail</th>
                  <th className="px-4 py-3 font-medium">Last seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {status.components.map((component) => (
                  <tr key={component.name} className="bg-slate-950/40">
                    <td className="px-4 py-3 font-medium text-slate-100">
                      {componentLabel(component.name)}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex rounded border px-2 py-1 text-xs ${stateClasses(
                          component.state,
                        )}`}
                      >
                        {stateLabel(component.state)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{component.detail}</td>
                    <td className="px-4 py-3 text-slate-400">
                      {component.last_observed_at
                        ? new Date(component.last_observed_at).toLocaleString()
                        : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="mt-8 text-sm text-slate-400">
          <p>
            Uptime monitors should check <span className="tabular">/v1/status</span>.
            Load balancers can continue using <span className="tabular">/healthz</span>.
          </p>
        </section>
      </div>
    </main>
  );
}
