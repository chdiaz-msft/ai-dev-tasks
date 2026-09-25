# PRD: Password Generator CLI

## Introduction/Overview

A simple command-line tool written in Python that generates secure, random passwords. Users can customize the length and character set of generated passwords. This solves the common need for quickly generating strong passwords without leaving the terminal.

## Goals

1. Provide a fast, reliable way to generate random passwords from the command line.
2. Allow users to customize password length and character composition.
3. Optionally copy the generated password to the clipboard.

## User Stories

- As a developer, I want to run a single command to generate a secure password so that I don't have to think one up myself.
- As a user, I want to specify the password length so that I can meet different sites' requirements.
- As a user, I want to exclude certain character types (e.g., symbols) so that I can comply with restrictive password policies.

## Functional Requirements

1. The tool must be runnable via `python -m passgen` or `python passgen.py`.
2. The tool must accept a `--length` / `-l` flag to set password length (default: 16).
3. The tool must accept a `--no-symbols` flag to exclude special characters.
4. The tool must accept a `--no-digits` flag to exclude numeric characters.
5. The tool must accept a `--copy` / `-c` flag to copy the result to the system clipboard.
6. The tool must print the generated password to stdout.
7. The tool must use `secrets` module (not `random`) for cryptographically secure generation.
8. The tool must exit with a non-zero code and a helpful message if the user requests a length less than 4.

## Non-Goals (Out of Scope)

- No GUI or web interface.
- No password storage or management.
- No passphrase generation (word-based passwords).

## Technical Considerations

- Python 3.9+ only.
- Use `argparse` for CLI argument parsing.
- Use `pyperclip` for optional clipboard support (graceful fallback if not installed).
- No other external dependencies.

## Success Metrics

- All unit tests pass.
- The tool generates a valid password matching the requested constraints in under 100ms.

## Open Questions

- None — this is a self-contained utility for testing purposes.
