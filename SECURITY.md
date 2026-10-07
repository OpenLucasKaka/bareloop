# Security Policy

## Scope & Nature of the Project

BareLoop is an experimental coding agent harness that executes model-driven tool calls, including local filesystem operations and shell commands.

> [!WARNING]
> By default, the Bash tool executes commands directly on the host machine within the process environment. Do NOT run BareLoop with untrusted external prompts without human supervision or isolated sandbox environments.

## Reporting a Vulnerability

If you discover a security vulnerability in BareLoop (such as sandbox escape, path containment bypass, or secret leakage), please report it responsibly:

1. **Do not create a public GitHub issue.**
2. Send an advisory report via GitHub Private Vulnerability Reporting or contact the project maintainers directly.
3. Include clear reproduction steps or proof-of-concept scripts.

We appreciate your responsible disclosure and will address confirmed vulnerabilities promptly.
