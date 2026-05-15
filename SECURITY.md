# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅        |

Older versions will not receive security patches. Always use the latest release.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability, email **akadeepesh@gmail.com** with:

- A description of the vulnerability
- Steps to reproduce it
- The potential impact
- Any suggested fix (optional)

You can expect an acknowledgement within **48 hours** and a full response within **7 days**.

If the vulnerability is confirmed, a patch will be released as soon as possible and you will be credited in the release notes (unless you prefer to remain anonymous).

## Scope

contextzip is a local CLI tool that reads your filesystem and creates ZIP archives. It does not make network requests, collect data, or transmit anything anywhere. The primary security considerations are:

- **Path traversal** — ensuring files outside the project directory are not included
- **Symlink handling** — dangling or malicious symlinks are skipped, not followed blindly
- **Secrets in archives** — `.env` and key files are excluded by default, but users should always run `--dry-run` before sharing archives containing sensitive projects
