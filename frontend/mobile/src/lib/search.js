import { z } from "zod";

/**
 * `?next=/path` after sign-in. Only same-app paths are accepted ("/x", never "//host" or a URL),
 * so the parameter cannot send people off-site.
 */
export const nextSearch = z.object({
  next: z
    .string()
    .regex(/^\/(?![/\\])/)
    .optional()
    .catch(undefined),
});
