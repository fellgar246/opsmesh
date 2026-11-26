import { describe, expect, it } from "vitest";

import { parseApiError } from "./api";

describe("API errors", () => {
  it("names the field on a validation error", () => {
    const error = parseApiError(422, {
      detail: [
        {
          loc: ["body", "service_level"],
          msg: "Input should be less than or equal to 1",
          type: "less_than_equal",
        },
      ],
    });
    expect(error.fieldErrors.service_level).toMatch(/Service level/);
    expect(error.message).toMatch(/Service level/);
  });

  it("keeps a string detail readable", () => {
    const error = parseApiError(422, { detail: "tenant does not exist" });
    expect(error.message).toBe("tenant does not exist");
  });
});
