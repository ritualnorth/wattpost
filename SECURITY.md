# Security policy

## Reporting a vulnerability

Please report security issues privately. Do not open a public issue or
discussion for a suspected vulnerability.

- Preferred: use GitHub's private vulnerability reporting on this
  repository (the **Security** tab, then **Report a vulnerability**).
- Alternatively, email **ritual@ritualnorth.com**.

We aim to acknowledge reports within a few days and will keep you posted on
the fix and disclosure timeline. Please allow a reasonable window to ship a
fix before any public disclosure.

## Supported versions

WattPost ships as a rolling release. Security fixes land in the latest
tagged release and the `:latest` Docker tag. Please upgrade to the newest
version before reporting, in case the issue is already addressed.

## Scope

This repository is the local-first appliance: the daemon, the local
dashboard, the update mechanism, and packaging. Issues in any of those, or
in the appliance's local and remote authentication, are in scope. The
hosted cloud service is operated separately.
