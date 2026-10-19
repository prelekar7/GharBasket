import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { findBag } from "./lines.js";
import { usePackingActions } from "./queries.js";

/**
 * Scanning or typing a bag's code opens it on Pick and pack; a bag not packed yet is started.
 * `error` says why a code could not be opened (not today's, already packed).
 * @param {import("./queries.js").PackingTask[]} tasks
 */
export function useOpenBag(tasks) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { start } = usePackingActions();
  const [error, setError] = useState("");
  /** @param {string} raw */
  const open = (raw) => {
    const code = raw.trim();
    const task = findBag(tasks, code);
    if (!task) return setError(t("packer.queue.unknownBag", { code }));
    if (task.status === "packed") return setError(t("packer.queue.alreadyPacked", { code }));
    setError("");
    if (task.status === "queued") start(task);
    navigate({ to: "/packer/pick", search: { bag: task.bag_qr } });
  };
  /** The bag to scan next: the one open, else the first not packed. */
  const next =
    tasks.find((x) => x.status === "picking") ?? tasks.find((x) => x.status === "queued");
  return { open, error, next };
}
