# Vendored NIST OSCAL schema

`oscal_assessment-results_schema-1.2.1.json` is the official JSON Schema for
the OSCAL **Assessment Results** model, release **v1.2.1**, copied byte for
byte (it is excluded from the whitespace pre-commit hooks so it stays that way):

- Source: <https://github.com/usnistgov/OSCAL/releases/download/v1.2.1/oscal_assessment-results_schema.json>
- `$id`: `http://csrc.nist.gov/ns/oscal/1.2.1/oscal-ar-schema.json`
- SHA-256: `4f9e277a177adbcca9527612ce450a33dc6096773fa229d413d801d196c61985`
- Downloaded: 2026-09-27

`tests/test_oscal_ar.py` validates the `oscal.json` export against it, so the
CI test job fails if the export stops conforming.

## Why 1.2.1 and not the latest release (1.2.3)

The export declares `oscal-version: 1.2.1` because compliance-trestle 5.1.0
(the current IBM / oscal-compass Python tooling) accepts only 1.2.0–1.2.1 in
that field. The 1.2.3 assessment-results schema differs from 1.2.1 only by
three extra allowed values for a component's `type` (`region`, `zone`,
`resource-container`), which the export does not use.

## License

NIST's OSCAL repository states (LICENSE.md at tag v1.2.1): as a work of the
United States government, the work is in the public domain within the United
States, and it is additionally dedicated to the public domain worldwide under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).
NIST asks that the source be acknowledged: this schema was created by the
National Institute of Standards and Technology (NIST) and is unmodified.
