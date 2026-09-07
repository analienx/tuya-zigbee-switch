# Security policy

This repository contains firmware for mains-powered Zigbee devices. Security issues can therefore affect both network behavior and physical device operation.

## Supported scope

Security reports are accepted for the current `main` branch and currently published BSEED firmware paths documented in [README.md](README.md).

Historical or upstream-only firmware may still be relevant when a vulnerability also affects the current fork, but reports should identify the affected version/commit whenever possible.

## Reporting a vulnerability

Please do **not** publish exploit details, credentials, private device identifiers, or sensitive network information in a public issue.

Use GitHub private vulnerability reporting / Security Advisories when that option is available for this repository. If private reporting is unavailable, contact the repository owner through GitHub and disclose only enough information publicly to establish a private communication channel.

Include, where relevant:

- affected firmware version or commit;
- exact device/board identity;
- attack prerequisites;
- expected vs actual security boundary;
- minimal reproduction information;
- whether physical access is required.

## Firmware safety

Do not demonstrate a vulnerability by instructing users to flash unverified firmware or mutate unrelated stock devices. Proofs of concept should use the least-destructive method possible and clearly separate software findings from hardware-risky validation.
