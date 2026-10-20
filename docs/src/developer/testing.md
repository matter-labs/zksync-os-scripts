# Testing the scripts

Run the unit tests without generating proofs or modifying external repositories:

```bash
uv run python -m unittest discover -s tests -v
```

These cover VK workflow version selection, binary replacement and download failures,
and VK/hash artifact generation with the expensive wrapper and contract tools mocked.
The ZiSK pin rotation tests cover record parsing, limb encoding, the 0.0.9 binding digest,
the PLONK key comparison and the commit step, with GitHub and the contract tools stubbed.
The `Check scripts` workflow runs these tests alongside formatting and license checks.
