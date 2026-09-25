import { describe, expect, it } from "vitest";

import { parseImageReference } from "./imageReference";

describe("parseImageReference", () => {
  it.each([
    ["amberd/chat:1.2.3", "Docker Hub", "amberd/chat", "1.2.3"],
    ["docker.io/amberd/chat:1.2.3", "Docker Hub", "amberd/chat", "1.2.3"],
    ["nginx", "Docker Hub", "library/nginx", ""],
    ["docker pull nginx:1.27", "Docker Hub", "library/nginx", "1.27"],
    [
      "123456789012.dkr.ecr.us-east-1.amazonaws.com/team/chat:v2",
      "Amazon ECR",
      "123456789012.dkr.ecr.us-east-1.amazonaws.com/team/chat",
      "v2",
    ],
    ["ghcr.io/amberd-ai/chat:main", "GitHub Container Registry", "amberd-ai/chat", "main"],
    ["registry.example.com:5000/team/app:1.0", "Private Registry", "registry.example.com:5000/team/app", "1.0"],
    ["ghcr.io/amberd-ai/chat@sha256:abc", "GitHub Container Registry", "amberd-ai/chat", ""],
  ])("splits %s", (text, registry, imageRegistry, tag) => {
    expect(parseImageReference(text)).toEqual({ registry, imageRegistry, tag });
  });

  it.each(["", "two words", "amberd//chat"])("refuses %j", (text) => {
    expect(parseImageReference(text)).toBeNull();
  });
});
