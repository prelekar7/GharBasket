import { getFairness } from "./data.js";
import { FairnessView } from "./FairnessView.jsx";

export const metadata = { title: "Fairness" };

/** @param {{ searchParams: Promise<{ source?: string }> }} props */
export default async function Page({ searchParams }) {
  const { source } = await searchParams;
  return <FairnessView fairness={await getFairness(source)} />;
}
