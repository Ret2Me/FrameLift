# Read-only observation API contract checked 2026-09-07

Current upstream source declares `start` as an inclusive lower bound on
observation start, and `start__lt` as an exclusive upper bound on start.
`end` instead bounds observation end and must not be substituted for the
upper bound when the cohort is defined by start time. The transmitter
UUID and current NORAD filter are available in the same filter class.
[Official filter implementation](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/master/network/api/filters.py).

The view selects cursor pagination and explicitly rejects the obsolete
`page` parameter. Follow the server's pagination links, detect cycles,
retain responses, and validate every returned observation locally.
[Official API view implementation](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/master/network/api/views.py).

These links follow upstream master and are not proof of the deployed
server revision or a transactionally consistent historical snapshot.
The campaign's retained responses and explicit local eligibility checks
are the evidence for its actual selection. No create/update/delete API
operation is part of this study.
