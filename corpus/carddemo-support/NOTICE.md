# Third-party notice: AWS CardDemo

Files under `golden/` and `data/` in this directory are derived from the AWS
Mainframe Modernization CardDemo application
(https://github.com/aws-samples/aws-mainframe-modernization-carddemo,
commit `59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e`):

- `golden/` holds outputs produced by running CardDemo programs on CardDemo's
  sample data under GnuCOBOL;
- `data/` holds inputs derived from those outputs and from CardDemo's JCL.

CardDemo is Copyright Amazon.com, Inc. or its affiliates, licensed under the
Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0).
The CardDemo sources themselves are not redistributed here; they are fetched
by `scripts/fetch_corpus.sh`.

`stubs/CEE3ABD.cbl`, `runs.toml` and `prepare.py` are original to cobalt.
