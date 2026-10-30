import { getFairness } from "./data.js";
import { FairnessView } from "./FairnessView.jsx";

export const metadata = { title: "Fairness" };

export default async function Page() {
  return <FairnessView fairness={await getFairness()} />;
}
