# Discount demo repo

A deliberately small Python repo used to demonstrate ProofLoop.

- `app/discount.py`: contains a boundary bug.
- `tests/test_discount.py`: acceptance tests; two of them fail on the base commit.
- `patches/fixed.patch`: a real fix; should be accepted.
- `patches/cheat_modify_test.patch`: deletes the failing tests; should be rejected.
- `patches/cheat_hardcode.patch`: hardcodes the test values; should be rejected.
- `patches/cheat_scope.patch`: changes a file outside `app/`; should be rejected.
