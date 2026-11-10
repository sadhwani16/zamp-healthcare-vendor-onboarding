# Blank packet layouts

These are the three templates needed by `generate_test_packets.py`:

- `blank_W9.pdf` is a blank IRS W-9 form.
- `blank_cp575.pdf` is a flattened notice layout with the earlier addressee, EIN, and notice code removed.
- `blank_clia.pdf` is a flattened certificate layout with the earlier facility, ID, dates, director, and signature removed.

The filled source PDFs used to prepare these layouts are not included in the public repository. The generated packet PDFs are visibly marked as samples and are not valid for payment or credentialing.

After installing `requirements.txt`, run `python generate_test_packets.py --output-dir data/packets_current` from the repository root to rebuild the fixture packets.
