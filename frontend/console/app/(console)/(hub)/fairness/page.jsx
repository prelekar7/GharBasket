import { getFairness } from "./data.js";
import { FairnessView } from "./FairnessView.jsx";

export const metadata = { title: "Fairness" };

export default async function Page() {
  const { fairness, canEdit } = await getFairness();
  return <FairnessView fairness={fairness} canEdit={canEdit} />;
}
