import { isMockApi } from "@/lib/session.js";

/**
 * Mock mode only: where `POST /v1/files/presign` (mocks/impact.js) tells the browser to PUT a photo, so the
 * upload step of the hand-off form works without object storage. The bytes are dropped. In real mode the
 * presigned URL points at the bucket and this route does not exist.
 */
export async function PUT() {
  return new Response(null, { status: isMockApi() ? 200 : 404 });
}
