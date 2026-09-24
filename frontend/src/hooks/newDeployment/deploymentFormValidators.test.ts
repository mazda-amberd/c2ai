import { describe, expect, it } from "vitest";

import {
  createCustomerNameValidator,
  createEnvInstanceValidator,
  validateBranchCharacters,
  validateSubdomainWhenUpdate,
} from "./deploymentFormValidators";

describe("validateBranchCharacters", () => {
  it("accepts typical branch names", () => {
    expect(validateBranchCharacters("main")).toBe(true);
    expect(validateBranchCharacters("feature/my-feature")).toBe(true);
    expect(validateBranchCharacters("release-1.2.3")).toBe(true);
    expect(validateBranchCharacters("fix/some_issue")).toBe(true);
  });

  it("rejects branch names with internal spaces", () => {
    expect(validateBranchCharacters("my branch")).not.toBe(true);
    expect(validateBranchCharacters("a b c")).not.toBe(true);
  });

  it("accepts branch names with only leading/trailing spaces (trimmed before validation)", () => {
    expect(validateBranchCharacters(" leading")).toBe(true);
    expect(validateBranchCharacters("trailing ")).toBe(true);
  });

  it("rejects branch names with control characters", () => {
    expect(validateBranchCharacters("bad\x00name")).not.toBe(true);
    expect(validateBranchCharacters("bad\x1fname")).not.toBe(true);
    expect(validateBranchCharacters("bad\x7fname")).not.toBe(true);
  });

  it("returns an error string (not false) for invalid input", () => {
    const result = validateBranchCharacters("has space");
    expect(typeof result).toBe("string");
  });
});

describe("createEnvInstanceValidator (new mode)", () => {
  it("accepts valid lowercase alphanumeric values", () => {
    const validate = createEnvInstanceValidator(false, () => "");
    expect(validate("prod")).toBe(true);
    expect(validate("ada-1")).toBe(true);
    expect(validate("a")).toBe(true);
  });

  it("accepts uppercase input (treated as lowercase internally)", () => {
    const validate = createEnvInstanceValidator(false, () => "");
    expect(validate("ADA")).toBe(true);
  });

  it("rejects empty value", () => {
    const validate = createEnvInstanceValidator(false, () => "");
    expect(validate("")).not.toBe(true);
    expect(validate("   ")).not.toBe(true);
  });

  it("rejects values starting with a hyphen", () => {
    const validate = createEnvInstanceValidator(false, () => "");
    expect(validate("-bad")).not.toBe(true);
  });

  it("rejects values with underscores", () => {
    const validate = createEnvInstanceValidator(false, () => "");
    expect(validate("bad_val")).not.toBe(true);
  });

  it("rejects values with spaces", () => {
    const validate = createEnvInstanceValidator(false, () => "");
    expect(validate("my env")).not.toBe(true);
  });

  it("rejects when combined host label with customer name would be invalid", () => {
    const longCustomer = "a".repeat(60);
    const validate = createEnvInstanceValidator(false, () => longCustomer);
    expect(validate("prod")).not.toBe(true);
  });
});

describe("createEnvInstanceValidator (update mode)", () => {
  const validate = createEnvInstanceValidator(true, () => "acme");

  it("accepts valid values", () => {
    expect(validate("prod")).toBe(true);
    expect(validate("staging")).toBe(true);
    expect(validate("ada-1")).toBe(true);
  });

  it("rejects empty in update mode", () => {
    expect(validate("")).not.toBe(true);
    expect(validate("   ")).not.toBe(true);
  });

  it("rejects values with underscores in update mode", () => {
    expect(validate("bad_env")).not.toBe(true);
  });

  it("rejects values starting with a hyphen in update mode", () => {
    expect(validate("-bad")).not.toBe(true);
  });
});

describe("createCustomerNameValidator (new mode)", () => {
  it("always returns true when env_instance is empty (no combined check yet)", () => {
    const validate = createCustomerNameValidator(false, () => "");
    expect(validate("Acme Corp")).toBe(true);
    expect(validate("")).toBe(true);
    expect(validate("anything goes when env is empty")).toBe(true);
  });

  it("accepts valid customer names that produce a valid combined label", () => {
    const validate = createCustomerNameValidator(false, () => "prod");
    expect(validate("acme")).toBe(true);
    expect(validate("my-customer")).toBe(true);
  });

  it("rejects when combined host label with env_instance would be too long", () => {
    const validate = createCustomerNameValidator(false, () => "prod");
    const longName = "a".repeat(200);
    expect(validate(longName)).not.toBe(true);
  });
});

describe("createCustomerNameValidator (update mode)", () => {
  const validate = createCustomerNameValidator(true, () => "prod");

  it("always returns true regardless of value", () => {
    expect(validate("anything")).toBe(true);
    expect(validate("")).toBe(true);
    expect(validate("!invalid@characters#")).toBe(true);
  });
});

describe("validateSubdomainWhenUpdate", () => {
  describe("new mode (isUpdate=false)", () => {
    it("always returns true regardless of value", () => {
      expect(validateSubdomainWhenUpdate("", false)).toBe(true);
      expect(validateSubdomainWhenUpdate("invalid!", false)).toBe(true);
      expect(validateSubdomainWhenUpdate("amberd-acme-ada", false)).toBe(true);
    });
  });

  describe("update mode (isUpdate=true)", () => {
    it("requires a non-empty value", () => {
      expect(validateSubdomainWhenUpdate("", true)).not.toBe(true);
      expect(validateSubdomainWhenUpdate("   ", true)).not.toBe(true);
    });

    it("accepts a valid subdomain", () => {
      expect(validateSubdomainWhenUpdate("amberd-acme-ada", true)).toBe(true);
      expect(validateSubdomainWhenUpdate("abc", true)).toBe(true);
    });

    it("rejects a subdomain with special characters", () => {
      expect(validateSubdomainWhenUpdate("invalid!", true)).not.toBe(true);
      expect(validateSubdomainWhenUpdate("has space", true)).not.toBe(true);
      expect(validateSubdomainWhenUpdate("has_underscore", true)).not.toBe(true);
    });

    it("rejects a subdomain starting or ending with a hyphen", () => {
      expect(validateSubdomainWhenUpdate("-bad", true)).not.toBe(true);
      expect(validateSubdomainWhenUpdate("bad-", true)).not.toBe(true);
    });
  });
});
