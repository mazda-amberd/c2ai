/* A full image reference ("docker.io/amberd/chat:1.2.3") split into the
 * register wizard's container fields: Container Registry, Image Registry
 * and Default Image Tag. */

export type ImageParts = { registry: string; imageRegistry: string; tag: string };

const ECR_HOST = /^[0-9]{12}\.dkr\.ecr(?:-fips)?\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?$/;
const DOCKER_HUB_HOSTS = new Set(["docker.io", "index.docker.io", "registry-1.docker.io"]);

/** The fields for `text`, or null when it is not an image reference. A
 *  reference pinned by digest (`@sha256:…`) has no tag. */
export function parseImageReference(text: string): ImageParts | null {
  let ref = text.trim().replace(/^docker\s+pull\s+/i, "").replace(/^[a-z]+:\/\//i, "");
  if (!ref || /\s/.test(ref)) return null;
  ref = ref.split("@")[0];

  let tag = "";
  const colon = ref.lastIndexOf(":");
  if (colon > ref.lastIndexOf("/")) {
    tag = ref.slice(colon + 1);
    ref = ref.slice(0, colon);
  }
  const parts = ref.split("/");
  if (parts.some((part) => !part)) return null;

  const first = parts[0].toLowerCase();
  const hasHost =
    parts.length > 1 && (first.includes(".") || first.includes(":") || first === "localhost");
  if (!hasHost || DOCKER_HUB_HOSTS.has(first)) {
    const path = hasHost ? parts.slice(1) : parts;
    // Docker Hub's official images live under "library".
    const repository = path.length === 1 ? `library/${path[0]}` : path.join("/");
    return { registry: "Docker Hub", imageRegistry: repository, tag };
  }
  const path = parts.slice(1).join("/");
  if (ECR_HOST.test(first)) return { registry: "Amazon ECR", imageRegistry: `${first}/${path}`, tag };
  if (first === "ghcr.io") return { registry: "GitHub Container Registry", imageRegistry: path, tag };
  return { registry: "Private Registry", imageRegistry: `${first}/${path}`, tag };
}
