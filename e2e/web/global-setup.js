// Start each run with an empty list of the endpoints the apps called that are not shipped yet.
import { rmSync } from "node:fs";

export default function globalSetup() {
  rmSync(`${import.meta.dirname}/test-results/unshipped.json`, { force: true });
}
