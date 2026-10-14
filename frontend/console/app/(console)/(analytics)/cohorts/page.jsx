import { CohortsView } from "./CohortsView.jsx";
import { loadCohorts } from "./data.js";

export const metadata = { title: "Cohorts" };

/** @param {{ searchParams: Promise<{ source?: string }> }} props */
export default async function Page({ searchParams }) {
  const { source } = await searchParams;
  return <CohortsView report={await loadCohorts(source)} />;
}
