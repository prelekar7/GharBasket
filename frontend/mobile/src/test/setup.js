// IndexedDB for Dexie (the outbox), and a clean slate for the localStorage-backed mocks.
import "fake-indexeddb/auto";
import { beforeEach } from "vitest";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
