# polyArbitrage

Reusable Polymarket scanner for:

- binary market dislocations where buying `YES` and `NO` appears to cost less than `$1`
- negative-risk event baskets where buying every `YES` leg appears to cost less than `$1`

The scanner uses Polymarket's public Gamma and CLOB APIs and writes a JSON report.

## Requirements

- Python 3.10+

## Usage

Run with defaults:

```bash
python3 scanner.py
```

Scan more markets and save a report:

```bash
python3 scanner.py --pages 4 --page-size 500 --output report.json
```

Show only the top 10 candidates per section:

```bash
python3 scanner.py --top 10
```

## Output

The script prints a compact summary and can also write the full JSON payload to disk with:

```bash
python3 scanner.py --output report.json
```

## Notes

- Results are only screening candidates, not guaranteed executable arbitrage.
- Binary market pricing on Polymarket can be noisy, so negative-risk baskets are generally the stronger signal.
- Always verify order book depth and market rules before trading.
