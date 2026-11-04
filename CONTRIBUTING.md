# Contributing to nvml-exporter

Bug reports, ideas and pull requests are welcome.

## Getting started

- Open an issue for bugs, ideas or questions.
- Fork, branch, and submit a pull request.
- The whole project is `exporter.py` and `test_exporter.py`; the tests stub `pynvml` in-process, so `python -m unittest discover -p 'test_*.py'` needs no GPU and no driver.

## Guidelines

- Keep it small. The exporter deliberately has one runtime dependency (`nvidia-ml-py`) and renders the exposition format by hand; a new metric is a tuple in `DEVICE_METRICS` plus a reader, not a new library.
- A reading NVML may refuse on some hardware goes in `optional_readings`, so one refusal leaves that gauge out instead of taking the whole scrape down.
- Run the tests before submitting a PR; CI runs them on every pull request and builds the image.
- **We use [release-please](https://github.com/googleapis/release-please).** Write [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, …) so versions and the changelog derive from the history.

## AI policy

AI coding tools are used in this project, steered by the maintainer, and nothing is merged without a human having reviewed it. Contributions written with such tools are welcome under the same rule: you are responsible for what you submit, so understand it and make sure it works.
