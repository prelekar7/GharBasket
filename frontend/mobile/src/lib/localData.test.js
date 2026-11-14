import { describe, expect, it } from "vitest";
import { clearLocalData } from "@/lib/localData.js";

describe("sign-out clears the Ghar Saathi chat (DPDP)", () => {
  it("removes the chat this build keeps and the one older builds kept", async () => {
    sessionStorage.setItem("gb-assistant-v2", JSON.stringify({ messages: [{ text: "diabetic" }] }));
    sessionStorage.setItem("gb-assistant", JSON.stringify({ messages: [{ text: "diabetic" }] }));
    await clearLocalData();
    expect(sessionStorage.getItem("gb-assistant-v2")).toBeNull();
    expect(sessionStorage.getItem("gb-assistant")).toBeNull();
  });
});
