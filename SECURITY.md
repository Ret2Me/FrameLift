# Security policy

This is an offline receiver under qualification, not a hardened network service.
Treat recordings, metadata, codec files and external-decoder output as untrusted.
Run as an unprivileged account with read-only input mounts, isolated output and
OS-level resource limits. Keep FFmpeg, NVIDIA components and reference decoders
under the deployment's patch policy as well as Rust dependencies.

## Sensitive boundaries

- Preserve bounded reads, source-identity checks, no-clobber writes and path
  ownership checks. A failed integrity check must not become a warning-only path.
- Do not interpolate metadata into a shell command. External tools receive
  explicit argument vectors and have bounded, owned process lifetimes.
- Review every unsafe block, signal handler and CUDA device operation against
  its actual ownership/lifetime assumptions. Do not catch panics to accept data.
- Checksums detect corruption and local inconsistency; they do not authenticate
  the satellite or an adversary who can rewrite an entire evidence tree.
- Repaired/list-search candidates are not independent integrity evidence.

## Reporting

Report vulnerabilities privately to the maintainer or the deploying
organization through its established security channel. Do not attach private
recordings, credentials or live endpoint details to a public issue. Include
the executable hash, version/toolchain, affected command, a minimal sanitized
input and the expected versus observed behavior.

A public reporting address and response-time commitment have not been
established in this workspace. Establish them before commercial distribution;
this file does not promise an incident SLA or a security certification.

## Supply chain

Build with the pinned toolchain and `--locked`. Review dependency/codec changes
separately from numerical refactors. Run a current vulnerability audit and keep
its database timestamp with release evidence; lint and unit tests are not a
vulnerability scan.

The checked-in CI workflow has read-only repository permissions, does not retain
checkout credentials, and pins its checkout action to a full commit. This follows
[GitHub's workflow security guidance](https://docs.github.com/en/actions/reference/security/secure-use).
It does not deploy, publish artifacts, or execute untrusted pull requests on a
self-hosted GPU. Trusted hardware qualification is a separate controlled run.
