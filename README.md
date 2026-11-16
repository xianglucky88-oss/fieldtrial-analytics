# Field Trial Analytics

Aggregate blocked field-trial observations while preserving design and missingness.

## Modules

- `src/fieldtrial_analytics/models.py`: immutable result contract.
- `src/fieldtrial_analytics/parser.py`: input validation and record extraction.
- `src/fieldtrial_analytics/engine.py`: domain calculation only.
- `src/fieldtrial_analytics/report.py`: stable output envelope.
- `src/fieldtrial_analytics/pipeline.py`: orchestration boundary.
- `src/fieldtrial_analytics/cli.py`: deterministic JSON CLI.

## Run

```bash
PYTHONPATH=src python -m fieldtrial_analytics.cli
PYTHONPATH=src python -m unittest discover -s tests -v
```

The fixture is local and deterministic; no network or credentials are required.
