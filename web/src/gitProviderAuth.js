export const GIT_PROVIDER_GITHUB = "github";
export const GIT_PROVIDER_GITLAB = "gitlab";

const GITLAB_REQUIRED_SCOPES = Object.freeze(["read_repository", "write_repository"]);

export function normalizeGitProvider(rawProvider, fallback = GIT_PROVIDER_GITHUB) {
  const candidate = String(rawProvider || "").trim().toLowerCase();
  if (candidate === GIT_PROVIDER_GITHUB || candidate === GIT_PROVIDER_GITLAB) {
    return candidate;
  }
  const fallbackValue = String(fallback || "").trim().toLowerCase();
  return fallbackValue === GIT_PROVIDER_GITLAB ? GIT_PROVIDER_GITLAB : GIT_PROVIDER_GITHUB;
}

export function defaultGitHostForProvider(provider) {
  return normalizeGitProvider(provider) === GIT_PROVIDER_GITLAB ? "gitlab.com" : "github.com";
}

export function gitProviderLabel(provider) {
  return normalizeGitProvider(provider) === GIT_PROVIDER_GITLAB ? "GitLab" : "GitHub";
}

function hostTokenFromInput(rawHost) {
  const input = String(rawHost || "").trim().toLowerCase();
  if (!input) {
    return "";
  }
  try {
    if (input.includes("://")) {
      const parsed = new URL(input);
      return String(parsed.hostname || input).toLowerCase();
    }
  } catch {
    // Fall back to string heuristics for partially-typed host input.
  }
  const withoutScheme = input.replace(/^[a-z][a-z0-9+.-]*:\/\//, "");
  const beforeSlash = withoutScheme.split("/")[0] || withoutScheme;
  const afterAt = beforeSlash.includes("@") ? (beforeSlash.split("@").pop() || beforeSlash) : beforeSlash;
  return afterAt.trim();
}

export function inferGitProviderFromHost(rawHost, fallback = GIT_PROVIDER_GITHUB) {
  const hostToken = hostTokenFromInput(rawHost);
  if (!hostToken) {
    return normalizeGitProvider(fallback);
  }
  if (hostToken.includes("gitlab")) {
    return GIT_PROVIDER_GITLAB;
  }
  if (hostToken.includes("github")) {
    return GIT_PROVIDER_GITHUB;
  }
  return normalizeGitProvider(fallback);
}

export function preferredGitProviderFromAuthStatus(providerStatus, fallback = GIT_PROVIDER_GITHUB) {
  const explicit = String(providerStatus?.personalAccessTokenProvider || "").trim().toLowerCase();
  if (explicit === GIT_PROVIDER_GITHUB || explicit === GIT_PROVIDER_GITLAB) {
    return explicit;
  }
  const hostHint = String(providerStatus?.personalAccessTokenHost || providerStatus?.connectionHost || "");
  return inferGitProviderFromHost(hostHint, fallback);
}

export function splitTokenScopes(rawTokenScopes) {
  return Array.from(
    new Set(
      String(rawTokenScopes || "")
        .split(/[\s,]+/)
        .map((value) => value.trim().toLowerCase())
        .filter(Boolean)
    )
  );
}

export function evaluateGitPatPermissions(provider, rawTokenScopes) {
  const normalizedProvider = normalizeGitProvider(provider);
  const scopes = splitTokenScopes(rawTokenScopes);

  if (normalizedProvider === GIT_PROVIDER_GITLAB) {
    if (scopes.length === 0) {
      return {
        status: "unknown",
        summary: "GitLab did not return token scopes. Validate clone and push in a test chat."
      };
    }
    if (scopes.includes("api")) {
      return {
        status: "ok",
        summary: "GitLab scope check passed: token includes api."
      };
    }
    const missing = GITLAB_REQUIRED_SCOPES.filter((scope) => !scopes.includes(scope));
    if (missing.length > 0) {
      return {
        status: "insufficient",
        summary: `GitLab scope check failed: missing ${missing.join(", ")}.`
      };
    }
    return {
      status: "ok",
      summary: "GitLab scope check passed: token includes read_repository and write_repository."
    };
  }

  if (scopes.includes("repo")) {
    return {
      status: "ok",
      summary: "GitHub scope check passed: token includes repo."
    };
  }
  if (scopes.length === 0) {
    return {
      status: "unknown",
      summary: "GitHub did not return token scopes (expected for many fine-grained tokens)."
    };
  }
  return {
    status: "unknown",
    summary: "GitHub scope details were returned. Verify repository write access in a test chat."
  };
}

export function gitPatSetupUrl(provider, rawHost) {
  const normalizedProvider = normalizeGitProvider(provider);
  const fallbackHost = defaultGitHostForProvider(normalizedProvider);
  const trimmedHost = String(rawHost || "").trim();
  const hostWithScheme = trimmedHost
    ? (trimmedHost.includes("://") ? trimmedHost : `https://${trimmedHost}`)
    : `https://${fallbackHost}`;
  const parsed = new URL(hostWithScheme);
  return normalizedProvider === GIT_PROVIDER_GITLAB
    ? `${parsed.protocol}//${parsed.host}/-/user_settings/personal_access_tokens`
    : `${parsed.protocol}//${parsed.host}/settings/personal-access-tokens/new`;
}
