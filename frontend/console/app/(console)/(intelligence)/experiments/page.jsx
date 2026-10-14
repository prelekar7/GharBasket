import { apiGet } from "@/lib/api.js";
import { apiSource, parseSource } from "@/lib/data-source.js";
import { ExperimentsView } from "./ExperimentsView.jsx";

export const metadata = { title: "Experiments" };

/**
 * M10 experiments: champion vs challenger model runs and product A/B tests, one data source at a
 * time (`?source=`).
 * @param {{ searchParams: Promise<{ source?: string }> }} props
 */
export default async function Page({ searchParams }) {
  const source = parseSource((await searchParams).source);
  const experiments = await apiGet("/v1/intel/experiments", {
    searchParams: { data_source: apiSource(source) },
  });
  return <ExperimentsView experiments={experiments} source={source} />;
}
