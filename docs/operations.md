# Operating the offline receiver

The supported unit of deployment is an unprivileged Linux worker processing
local recordings. No public HTTP API, account system or multi-tenant service is
provided by this repository. Do not expose the CLI or its working directory as
an unauthenticated upload-and-execute service.

## Prepare an execution

1. Freeze the executable and its SHA-256 outside the Cargo target directory.
   Retain source/toolchain/lockfile identity and external codec versions.
2. Provide read-only inputs with enough free disk for checkpoints and prepared
   WAV data. Compressed OGG size is not an estimate of working-space needs.
3. Use a fresh output directory owned by the worker's account. Do not share a
   session between different executables or backends.
4. Probe the intended backend with `compute-info`. For CUDA, record device,
   driver, NVRTC and kernel/PTX identity. A CPU fallback is not an acceptable
   substitute for a failed GPU job.
5. Apply process CPU, memory, disk and wall-time controls outside the receiver.
   Thread/cache/device-buffer options are not total process or GPU memory caps.

Record configuration and input identity with every execution. Do not run a
benchmark while rebuilding the binary it hashes or while another workload
consumes the resources being measured.

## Automation contract

The general CLI writes its successful result as one JSON record to stdout and
diagnostics to stderr. Argument errors are handled by Clap. Runtime errors
exit 1. A produced result with `pass: false`, `status: "failed"`, or a positive
`failed` count exits 2. Check the command's schema and completion fields as well
as its exit code: a budgeted progressive snapshot may legitimately be incomplete.

Capture stdout/stderr separately. Do not parse human error text as a stable
error-code API, and do not treat lack of output as a zero-frame result. Failures
must remain distinct from complete observations containing no accepted frames.

## Stop and resume

Send SIGTERM or SIGINT to the progressive supervisor. It cleans up its owned
process group, preserves committed tasks and writes a stop receipt. Do not
delete a session lock or task file to force a restart. Resume explicitly with
the same executable, input and backend/policy identity.

Power loss and SIGKILL can leave no final receipt. Verify committed evidence
before resuming. A changed executable starts a new session; use the previous
frozen executable for historical checkpoints. See the
[compute/session contract](enterprise-compute-and-benchmark-v1.md).

## Incident and release boundaries

Keep the failed run, diagnostic logs, input checksum and executable checksum.
Logs and payloads may contain private mission information; restrict access.
Investigate source mutation, disk exhaustion, worker termination and checksum
failures before retrying. Never repair evidence by editing its manifest.

The current tests do not establish an SLA, total-resource bound, multi-tenant
isolation or GPU speedup. Define and test those requirements on the intended
deployment hardware before offering them. No production service is installed
or modified by the repository's quality gate.
