import test from "node:test";
import assert from "node:assert/strict";
import {
  GIT_PROVIDER_GITHUB,
  GIT_PROVIDER_GITLAB,
  defaultGitHostForProvider,
  evaluateGitPatPermissions,
  gitPatSetupUrl,
  inferGitProviderFromHost,
  normalizeGitProvider,
  preferredGitProviderFromAuthStatus,
  splitTokenScopes
} from "./gitProviderAuth.js";

test("normalizeGitProvider and host defaults stay deterministic", () => {
  assert.equal(normalizeGitProvider("gitlab"), GIT_PROVIDER_GITLAB);
  assert.equal(normalizeGitProvider("GITHUB"), GIT_PROVIDER_GITHUB);
  assert.equal(normalizeGitProvider("unknown", GIT_PROVIDER_GITLAB), GIT_PROVIDER_GITLAB);
  assert.equal(defaultGitHostForProvider(GIT_PROVIDER_GITLAB), "gitlab.com");
  assert.equal(defaultGitHostForProvider("other"), "github.com");
});

test("inferGitProviderFromHost handles URL and ssh-style host input", () => {
  assert.equal(inferGitProviderFromHost("https://gitlab.example.com:8443"), GIT_PROVIDER_GITLAB);
  assert.equal(inferGitProviderFromHost("git@github.com:org/repo.git"), GIT_PROVIDER_GITHUB);
  assert.equal(inferGitProviderFromHost("scm.internal", GIT_PROVIDER_GITLAB), GIT_PROVIDER_GITLAB);
});

test("preferredGitProviderFromAuthStatus honors explicit provider first", () => {
  assert.equal(
    preferredGitProviderFromAuthStatus({
      personalAccessTokenProvider: "gitlab",
      personalAccessTokenHost: "github.com"
    }),
    GIT_PROVIDER_GITLAB
  );
  assert.equal(
    preferredGitProviderFromAuthStatus({
      personalAccessTokenHost: "gitlab.company"
    }),
    GIT_PROVIDER_GITLAB
  );
});

test("splitTokenScopes de-duplicates and normalizes", () => {
  assert.deepEqual(splitTokenScopes("api, write_repository read_repository read_repository"), [
    "api",
    "write_repository",
    "read_repository"
  ]);
});

test("evaluateGitPatPermissions validates GitLab scope requirements", () => {
  assert.equal(evaluateGitPatPermissions("gitlab", "api").status, "ok");
  assert.equal(evaluateGitPatPermissions("gitlab", "read_repository,write_repository").status, "ok");
  assert.equal(evaluateGitPatPermissions("gitlab", "read_repository").status, "insufficient");
  assert.equal(evaluateGitPatPermissions("gitlab", "").status, "unknown");
});

test("gitPatSetupUrl resolves provider-specific token setup paths", () => {
  assert.equal(
    gitPatSetupUrl("gitlab", "gitlab.example.com"),
    "https://gitlab.example.com/-/user_settings/personal_access_tokens"
  );
  assert.equal(
    gitPatSetupUrl("github", "github.enterprise.local"),
    "https://github.enterprise.local/settings/personal-access-tokens/new"
  );
});
