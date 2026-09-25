## Relevant Files

- `passgen.py` - Main entry point and CLI argument parsing via argparse.
- `passgen_test.py` - Unit tests for all passgen functionality.
- `requirements.txt` - Optional dependencies (pyperclip).

### Notes

- **This project follows Test-Driven Development (TDD).** For each feature or component, write the tests first, verify they fail, then write the implementation to make them pass.
- Unit tests should be placed alongside the code file (e.g., `passgen.py` and `passgen_test.py` in the same directory).
- Use `python -m pytest passgen_test.py` to run tests.

## Tasks

- [ ] 1.0 Project Setup and CLI Argument Parsing
  - [ ] 1.1 Write tests for CLI argument parsing (verify `--length`/`-l` defaults to 16, `--no-symbols` and `--no-digits` flags are `False` by default, `--copy`/`-c` flag is `False` by default, and all flags are correctly parsed when provided)
  - [ ] 1.2 Run tests to confirm they fail (red phase — validates tests are non-trivial)
  - [ ] 1.3 Implement `passgen.py` with argparse setup defining all flags (`--length`, `--no-symbols`, `--no-digits`, `--copy`) and a `parse_args()` function
  - [ ] 1.4 Refactor if needed while keeping tests green

- [ ] 2.0 Password Generation Logic
  - [ ] 2.1 Write tests for password generation (verify: default output is 16 chars containing letters/digits/symbols; `--no-symbols` excludes special characters; `--no-digits` excludes numbers; custom `--length` produces correct length; length < 4 raises an error or returns non-zero exit code with helpful message; generated passwords use characters only from the expected character set)
  - [ ] 2.2 Run tests to confirm they fail (red phase — validates tests are non-trivial)
  - [ ] 2.3 Implement `generate_password(length, use_symbols, use_digits)` function using the `secrets` module, with validation that length >= 4
  - [ ] 2.4 Wire up `generate_password` to the CLI so running `python passgen.py` prints the password to stdout
  - [ ] 2.5 Refactor if needed while keeping tests green

- [ ] 3.0 Clipboard Support
  - [ ] 3.1 Write tests for clipboard functionality (verify: when `--copy` is passed and pyperclip is available, the password is copied; when pyperclip is not installed, the tool still prints the password and does not crash)
  - [ ] 3.2 Run tests to confirm they fail (red phase — validates tests are non-trivial)
  - [ ] 3.3 Implement clipboard copy using `pyperclip` wrapped in a try/except ImportError for graceful fallback
  - [ ] 3.4 Refactor if needed while keeping tests green

## Verification Criteria

How we know the tasks have been successfully implemented:

- [ ] Run `python passgen.py` and confirm a 16-character password is printed containing letters, digits, and symbols
- [ ] Run `python passgen.py -l 32` and confirm a 32-character password is printed
- [ ] Run `python passgen.py --no-symbols` and confirm the output contains no special characters
- [ ] Run `python passgen.py --no-digits` and confirm the output contains no numbers
- [ ] Run `python passgen.py -l 2` and confirm the tool exits with a non-zero code and a helpful error message
- [ ] Run `python -m pytest passgen_test.py` and confirm all tests pass
- [ ] Run `python passgen.py --copy` and confirm the password is copied to the clipboard (or a graceful message is shown if pyperclip is missing)
